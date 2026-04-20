#!/usr/bin/env python3
"""
Kalshi API Client Module
========================
Kalshi 예측 시장 플랫폼과 연동하는 클라이언트 모듈.
두 가지 방식 지원:
  1) kalshi-py SDK (권장) — pip install kalshi-py
  2) 직접 REST API 호출 (SDK 없이도 동작)

인증: RSA-PSS 서명 방식
Base URL:
  - Demo:  https://demo-api.kalshi.co/trade-api/v2
  - Prod:  https://api.elections.kalshi.com/trade-api/v2
"""

import os
import json
import time
import base64
import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Iterator
from dataclasses import dataclass, asdict, field
from pathlib import Path

from dotenv import load_dotenv

# .env 파일 로드 (프로젝트 루트 기준)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests

# RSA signing (Python standard library + cryptography)
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# Optional: kalshi-py SDK
try:
    from kalshi_py import create_client as create_kalshi_client
    from kalshi_py.api.market import get_markets, get_market, get_event
    from kalshi_py.api.portfolio import get_balance, get_positions
    HAS_KALSHI_SDK = True
except ImportError:
    HAS_KALSHI_SDK = False


logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class KalshiConfig:
    """Kalshi API 설정"""
    api_key_id: str = ""
    private_key_path: str = ""
    private_key_pem: str = ""
    use_demo: bool = True  # True = Demo 환경, False = 실거래

    # Base URLs
    DEMO_URL: str = "https://demo-api.kalshi.co/trade-api/v2"
    PROD_URL: str = "https://api.elections.kalshi.com/trade-api/v2"

    @property
    def base_url(self) -> str:
        return self.DEMO_URL if self.use_demo else self.PROD_URL

    @classmethod
    def from_env(cls) -> "KalshiConfig":
        """환경변수에서 설정 로드"""
        return cls(
            api_key_id=os.environ.get("KALSHI_API_KEY_ID", ""),
            private_key_path=os.environ.get("KALSHI_PRIVATE_KEY_PATH", ""),
            use_demo=os.environ.get("KALSHI_USE_DEMO", "true").lower() == "true"
        )

    @classmethod
    def from_file(cls, config_path: str) -> "KalshiConfig":
        """JSON 설정 파일에서 로드"""
        with open(config_path, "r") as f:
            data = json.load(f)
        return cls(**data)

    def load_private_key(self) -> str:
        """PEM 키 로드"""
        if self.private_key_pem:
            return self.private_key_pem
        if self.private_key_path and os.path.exists(self.private_key_path):
            with open(self.private_key_path, "r") as f:
                self.private_key_pem = f.read()
            return self.private_key_pem
        raise ValueError("Private key not found. Set private_key_path or private_key_pem.")


# ============================================================================
# DATA MODELS — Kalshi 마켓 데이터 구조
# ============================================================================

@dataclass
class KalshiMarket:
    """Kalshi 마켓 (개별 계약) 데이터"""
    ticker: str                    # e.g. "NBA-CELTICS-WIN-2026-03-25"
    event_ticker: str              # 상위 이벤트
    title: str                     # "Will the Celtics win?"
    subtitle: str = ""
    status: str = ""               # "open", "closed", "settled"
    yes_price: float = 0.0         # Yes 가격 (0-100 cents)
    no_price: float = 0.0          # No 가격
    yes_bid: float = 0.0           # 최고 매수 호가
    yes_ask: float = 0.0           # 최저 매도 호가
    no_bid: float = 0.0
    no_ask: float = 0.0
    volume: int = 0                # 거래량
    open_interest: int = 0         # 미결제약정
    last_price: float = 0.0
    implied_prob_yes: float = 0.0  # yes_price / 100
    implied_prob_no: float = 0.0
    category: str = ""             # "Sports", "Economics", etc.
    close_time: str = ""
    expiration_time: str = ""
    result: str = ""               # "yes", "no", "" (미결제)

    def __post_init__(self):
        # 가격을 확률로 변환 (Kalshi 가격은 cents 단위: 1-99)
        if self.yes_price > 0:
            self.implied_prob_yes = self.yes_price / 100.0
        if self.no_price > 0:
            self.implied_prob_no = self.no_price / 100.0

    @property
    def spread(self) -> float:
        """Bid-Ask 스프레드 (유동성 지표)"""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return self.yes_ask - self.yes_bid
        return 0.0

    @property
    def decimal_odds_yes(self) -> float:
        """Yes 배당률 (소수점)"""
        if self.implied_prob_yes > 0:
            return 1.0 / self.implied_prob_yes
        return 0.0

    @property
    def decimal_odds_no(self) -> float:
        """No 배당률 (소수점)"""
        if self.implied_prob_no > 0:
            return 1.0 / self.implied_prob_no
        return 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class KalshiEvent:
    """Kalshi 이벤트 (여러 마켓을 포함하는 상위 개념)"""
    event_ticker: str
    title: str
    category: str = ""
    sub_title: str = ""
    markets: List[KalshiMarket] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "event_ticker": self.event_ticker,
            "title": self.title,
            "category": self.category,
            "sub_title": self.sub_title,
            "markets": [m.to_dict() for m in self.markets]
        }


# ============================================================================
# RSA-PSS 서명 유틸리티
# ============================================================================

class RSASigner:
    """Kalshi API 요청 서명"""

    def __init__(self, private_key_pem: str):
        if not HAS_CRYPTO:
            raise ImportError(
                "cryptography 패키지가 필요합니다: pip install cryptography"
            )
        self.private_key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None
        )

    def sign_request(self, method: str, path: str, timestamp: str) -> str:
        """
        요청 서명 생성
        서명 대상: timestamp + method + path
        """
        message = f"{timestamp}{method.upper()}{path}"
        signature = self.private_key.sign(
            message.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        return base64.b64encode(signature).decode()


# ============================================================================
# KALSHI API CLIENT — REST 직접 호출
# ============================================================================

class KalshiAPIClient:
    """
    Kalshi REST API v2 클라이언트

    SDK 없이도 동작하는 순수 REST 클라이언트.
    모든 스포츠 마켓 데이터 조회 + 주문 실행 지원.
    """

    def __init__(self, config: KalshiConfig):
        self.config = config
        self.base_url = config.base_url
        self.session = requests.Session()

        # RSA 서명 설정
        private_key = config.load_private_key()
        self.signer = RSASigner(private_key)
        self.api_key_id = config.api_key_id

        logger.info(f"KalshiAPIClient initialized | env={'DEMO' if config.use_demo else 'PROD'}")

    def _get_headers(self, method: str, path: str) -> Dict[str, str]:
        """인증 헤더 생성"""
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        signature = self.signer.sign_request(method, path, timestamp)

        return {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    def _request(self, method: str, endpoint: str, params: dict = None, data: dict = None) -> dict:
        """API 요청 실행"""
        url = f"{self.base_url}{endpoint}"
        path = f"/trade-api/v2{endpoint}"
        headers = self._get_headers(method.upper(), path)

        response = self.session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            params=params,
            json=data,
            timeout=30
        )

        if response.status_code == 429:
            # Rate limit — 잠시 대기 후 재시도
            retry_after = int(response.headers.get("Retry-After", 2))
            logger.warning(f"Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            return self._request(method, endpoint, params, data)

        response.raise_for_status()
        return response.json()

    # ── 마켓 데이터 조회 ─────────────────────────────────────────────

    def get_markets(
        self,
        limit: int = 100,
        cursor: str = None,
        event_ticker: str = None,
        series_ticker: str = None,
        status: str = "open",
        tickers: List[str] = None,
    ) -> Dict[str, Any]:
        """
        마켓 목록 조회

        Args:
            limit: 결과 수 (최대 1000)
            cursor: 페이지네이션 커서
            event_ticker: 특정 이벤트의 마켓만 필터
            series_ticker: 특정 시리즈 마켓만 필터
            status: "open", "closed", "settled"
            tickers: 특정 티커 목록
        """
        params = {"limit": limit, "status": status}
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if tickers:
            params["tickers"] = ",".join(tickers)

        return self._request("GET", "/markets", params=params)

    def get_market(self, ticker: str) -> Dict[str, Any]:
        """단일 마켓 상세 조회"""
        return self._request("GET", f"/markets/{ticker}")

    def get_market_orderbook(self, ticker: str, depth: int = 10) -> Dict[str, Any]:
        """마켓 오더북 (호가창) 조회"""
        return self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})

    def get_market_history(
        self, ticker: str, limit: int = 100, min_ts: int = None, max_ts: int = None
    ) -> Dict[str, Any]:
        """마켓 가격 히스토리"""
        params = {"limit": limit}
        if min_ts:
            params["min_ts"] = min_ts
        if max_ts:
            params["max_ts"] = max_ts
        return self._request("GET", f"/markets/{ticker}/history", params=params)

    def get_trades(self, ticker: str = None, limit: int = 100) -> Dict[str, Any]:
        """최근 체결 내역"""
        params = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        return self._request("GET", "/markets/trades", params=params)

    # ── 이벤트 조회 ──────────────────────────────────────────────────

    def get_event(self, event_ticker: str) -> Dict[str, Any]:
        """이벤트 상세 (하위 마켓 포함)"""
        return self._request("GET", f"/events/{event_ticker}")

    def get_events(
        self,
        limit: int = 100,
        cursor: str = None,
        status: str = "open",
        series_ticker: str = None,
        with_nested_markets: bool = True,
    ) -> Dict[str, Any]:
        """이벤트 목록 조회"""
        params = {
            "limit": limit,
            "status": status,
            "with_nested_markets": with_nested_markets,
        }
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        return self._request("GET", "/events", params=params)

    # ── 주문 (Trading) ───────────────────────────────────────────────

    def get_balance(self) -> Dict[str, Any]:
        """계좌 잔액 조회"""
        return self._request("GET", "/portfolio/balance")

    def get_positions(self, limit: int = 100, status: str = None) -> Dict[str, Any]:
        """보유 포지션 조회"""
        params = {"limit": limit}
        if status:
            params["settlement_status"] = status
        return self._request("GET", "/portfolio/positions", params=params)

    def get_fills(
        self,
        ticker: str = None,
        order_id: str = None,
        min_ts: int = None,
        max_ts: int = None,
        limit: int = 200,
        cursor: str = None,
    ) -> dict:
        """
        GET /portfolio/fills — 체결된 거래 이력.
        min_ts/max_ts는 Unix epoch seconds.
        반환: {"fills": [...], "cursor": "..."}
        """
        params = {"limit": min(max(limit, 1), 1000)}
        if ticker: params["ticker"] = ticker
        if order_id: params["order_id"] = order_id
        if min_ts is not None: params["min_ts"] = int(min_ts)
        if max_ts is not None: params["max_ts"] = int(max_ts)
        if cursor: params["cursor"] = cursor
        return self._request("GET", "/portfolio/fills", params=params)

    def get_settlements(
        self,
        ticker: str = None,
        event_ticker: str = None,
        min_ts: int = None,
        max_ts: int = None,
        limit: int = 200,
        cursor: str = None,
    ) -> dict:
        """
        GET /portfolio/settlements — 정산 완료된 마켓 이력 + 실현손익.
        반환: {"settlements": [...], "cursor": "..."}
        각 settlement: ticker, market_result(yes/no), yes_count, no_count, revenue, settled_time
        """
        params = {"limit": min(max(limit, 1), 1000)}
        if ticker: params["ticker"] = ticker
        if event_ticker: params["event_ticker"] = event_ticker
        if min_ts is not None: params["min_ts"] = int(min_ts)
        if max_ts is not None: params["max_ts"] = int(max_ts)
        if cursor: params["cursor"] = cursor
        return self._request("GET", "/portfolio/settlements", params=params)

    def get_positions_raw(
        self,
        ticker: str = None,
        event_ticker: str = None,
        count_filter: str = None,
        settlement_status: str = None,
        limit: int = 200,
        cursor: str = None,
    ) -> dict:
        """
        GET /portfolio/positions — raw 응답 그대로.
        기존 get_positions가 필드 drop시켜서 None 찍히는 문제 회피용.
        count_filter 유효값: "position,total_traded" (comma-separated subset)
        settlement_status: "all" | "settled" | "unsettled"
        """
        params = {"limit": min(max(limit, 1), 1000)}
        if ticker: params["ticker"] = ticker
        if event_ticker: params["event_ticker"] = event_ticker
        if count_filter: params["count_filter"] = count_filter
        if settlement_status: params["settlement_status"] = settlement_status
        if cursor: params["cursor"] = cursor
        return self._request("GET", "/portfolio/positions", params=params)

    def paginate(self, method_name: str, **kwargs) -> list:
        """
        cursor 기반 페이지네이션 헬퍼. 모든 페이지 합쳐서 리스트 반환.
        사용: client.paginate("get_fills", ticker="KXNBA...", limit=200)
        반환: fills/settlements/positions의 평탄화된 list.
        """
        method = getattr(self, method_name)
        results = []
        cursor = None
        max_pages = 20  # safety: 20 * 200 = 4000건
        for _ in range(max_pages):
            resp = method(cursor=cursor, **kwargs) if cursor else method(**kwargs)
            # 키 자동 탐지
            for key in ("fills", "settlements", "market_positions", "event_positions", "positions"):
                if key in resp and isinstance(resp[key], list):
                    results.extend(resp[key])
                    break
            cursor = resp.get("cursor")
            if not cursor:
                break
        return results

    def create_order(
        self,
        ticker: str,
        side: str,         # "yes" or "no"
        action: str,       # "buy" or "sell"
        count: int,        # 계약 수
        type: str = "limit",  # "limit" or "market"
        yes_price: int = None,  # limit 주문 가격 (cents)
        no_price: int = None,
        expiration_ts: int = None,
    ) -> Dict[str, Any]:
        """
        주문 생성

        ⚠️ 주의: 실거래 환경에서는 실제 돈이 걸립니다!
        반드시 Demo 환경에서 먼저 테스트하세요.

        Args:
            ticker: 마켓 티커
            side: "yes" 또는 "no"
            action: "buy" (매수) 또는 "sell" (매도)
            count: 계약 수량
            type: "limit" (지정가) 또는 "market" (시장가)
            yes_price: Yes 가격 (cents, 1-99)
            no_price: No 가격 (cents, 1-99)
        """
        order_data = {
            "ticker": ticker,
            "side": side,
            "action": action,
            "count": count,
            "type": type,
        }
        if yes_price is not None:
            order_data["yes_price"] = yes_price
        if no_price is not None:
            order_data["no_price"] = no_price
        if expiration_ts is not None:
            order_data["expiration_ts"] = expiration_ts

        return self._request("POST", "/portfolio/orders", data=order_data)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """주문 취소"""
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    def get_orders(self, ticker: str = None, status: str = None) -> Dict[str, Any]:
        """주문 내역 조회"""
        params = {}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        return self._request("GET", "/portfolio/orders", params=params)

    # ── 유틸리티 ─────────────────────────────────────────────────────

    def get_exchange_status(self) -> Dict[str, Any]:
        """거래소 상태 확인"""
        return self._request("GET", "/exchange/status")


# ============================================================================
# SDK 기반 클라이언트 (kalshi-py 설치 시 사용)
# ============================================================================

class KalshiSDKClient:
    """
    kalshi-py SDK 래퍼
    SDK가 설치되어 있으면 이 클라이언트 사용 가능.
    인증/서명을 SDK가 자동 처리.
    """

    def __init__(self, config: KalshiConfig):
        if not HAS_KALSHI_SDK:
            raise ImportError(
                "kalshi-py SDK가 필요합니다: pip install kalshi-py"
            )

        private_key = config.load_private_key()
        self.client = create_kalshi_client(
            access_key_id=config.api_key_id,
            private_key_data=private_key,
        )
        logger.info("KalshiSDKClient initialized via kalshi-py SDK")

    def get_markets(self, limit: int = 100) -> Any:
        return get_markets.sync(client=self.client, limit=limit)

    def get_market(self, ticker: str) -> Any:
        return get_market.sync(client=self.client, ticker=ticker)

    def get_balance(self) -> Any:
        return get_balance.sync(client=self.client)

    def get_positions(self) -> Any:
        return get_positions.sync(client=self.client)


# ============================================================================
# 팩토리 함수 — 환경에 맞는 클라이언트 자동 선택
# ============================================================================

def create_client(config: KalshiConfig = None) -> KalshiAPIClient:
    """
    Kalshi 클라이언트 생성

    사용법:
        # 방법 1: 환경변수 사용
        client = create_client()

        # 방법 2: 직접 설정
        config = KalshiConfig(
            api_key_id="your-key-id",
            private_key_path="/path/to/key.pem",
            use_demo=True
        )
        client = create_client(config)
    """
    if config is None:
        config = KalshiConfig.from_env()

    return KalshiAPIClient(config)


# ============================================================================
# 마켓 데이터 → 내부 모델 변환
# ============================================================================

def parse_market(raw: dict) -> KalshiMarket:
    """Kalshi API 응답 → KalshiMarket 변환"""
    return KalshiMarket(
        ticker=raw.get("ticker", ""),
        event_ticker=raw.get("event_ticker", ""),
        title=raw.get("title", ""),
        subtitle=raw.get("subtitle", ""),
        status=raw.get("status", ""),
        yes_price=raw.get("yes_price", 0) or 0,
        no_price=raw.get("no_price", 0) or 0,
        yes_bid=raw.get("yes_bid", 0) or 0,
        yes_ask=raw.get("yes_ask", 0) or 0,
        no_bid=raw.get("no_bid", 0) or 0,
        no_ask=raw.get("no_ask", 0) or 0,
        volume=raw.get("volume", 0) or 0,
        open_interest=raw.get("open_interest", 0) or 0,
        last_price=raw.get("last_price", 0) or 0,
        category=raw.get("category", ""),
        close_time=raw.get("close_time", ""),
        expiration_time=raw.get("expiration_time", ""),
        result=raw.get("result", ""),
    )


def parse_markets_response(response: dict) -> List[KalshiMarket]:
    """API 응답에서 마켓 리스트 추출"""
    markets_raw = response.get("markets", [])
    return [parse_market(m) for m in markets_raw]


# ============================================================================
# 스포츠 마켓 필터링
# ============================================================================

SPORTS_KEYWORDS = [
    # NBA
    "nba", "celtics", "lakers", "warriors", "bucks", "nuggets", "76ers",
    "knicks", "heat", "suns", "mavericks", "clippers", "nets", "bulls",
    "cavaliers", "raptors", "hawks", "pacers", "magic", "grizzlies",
    "timberwolves", "pelicans", "thunder", "kings", "rockets", "spurs",
    "blazers", "pistons", "hornets", "wizards", "jazz",
    # MLB
    "mlb", "yankees", "dodgers", "astros", "braves", "mets", "phillies",
    "padres", "rangers", "orioles", "twins", "guardians", "mariners",
    "rays", "cubs", "red sox", "cardinals", "brewers", "diamondbacks",
    "blue jays", "giants", "reds", "white sox", "pirates", "royals",
    "tigers", "nationals", "rockies", "marlins", "angels", "athletics",
    # Soccer / Football
    "epl", "premier league", "la liga", "champions league", "serie a",
    "bundesliga", "arsenal", "liverpool", "manchester", "chelsea",
    "tottenham", "barcelona", "real madrid", "bayern", "psg",
    "brighton", "newcastle", "aston villa", "west ham",
    # NFL
    "nfl", "super bowl", "chiefs", "eagles", "49ers", "cowboys",
    "bills", "ravens", "lions", "bengals", "dolphins", "packers",
    # General
    "sports", "game", "match", "win", "score", "points", "goals",
    "championship", "playoff", "finals", "series", "season",
]


def is_sports_market(market: KalshiMarket) -> bool:
    """마켓이 스포츠 관련인지 판별"""
    text = f"{market.title} {market.subtitle} {market.category} {market.ticker}".lower()
    return any(kw in text for kw in SPORTS_KEYWORDS)


def filter_sports_markets(markets: List[KalshiMarket]) -> List[KalshiMarket]:
    """스포츠 마켓만 필터링"""
    return [m for m in markets if is_sports_market(m)]


def filter_open_sports_markets(markets: List[KalshiMarket]) -> List[KalshiMarket]:
    """열려있는 스포츠 마켓만 필터링"""
    return [m for m in markets if is_sports_market(m) and m.status == "open"]


# ============================================================================
# 테스트 / 데모 실행
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Kalshi API Client — Connection Test")
    print("=" * 60)
    print()

    # 설정 확인
    config = KalshiConfig.from_env()

    if not config.api_key_id:
        print("⚠️  API 키가 설정되지 않았습니다.")
        print()
        print("설정 방법:")
        print("  1. Kalshi 계정 → Settings → API Keys → Create Key")
        print("  2. RSA 키 페어를 다운로드 (private_key.pem)")
        print("  3. 환경변수 설정:")
        print("     export KALSHI_API_KEY_ID='your-key-id'")
        print("     export KALSHI_PRIVATE_KEY_PATH='/path/to/private_key.pem'")
        print("     export KALSHI_USE_DEMO='true'")
        print()
        print("또는 config.json 파일 사용:")
        print('  {"api_key_id": "...", "private_key_path": "...", "use_demo": true}')
    else:
        print(f"✅ API Key ID: {config.api_key_id[:8]}...")
        print(f"✅ Environment: {'DEMO' if config.use_demo else 'PRODUCTION'}")
        print(f"✅ Base URL: {config.base_url}")
        print()

        try:
            client = create_client(config)
            status = client.get_exchange_status()
            print(f"✅ Exchange Status: {status}")

            # 마켓 데이터 테스트
            response = client.get_markets(limit=20, status="open")
            markets = parse_markets_response(response)
            sports = filter_sports_markets(markets)

            print(f"\n📊 총 {len(markets)}개 마켓 조회됨")
            print(f"🏀 스포츠 마켓: {len(sports)}개")

            for m in sports[:5]:
                print(f"  • {m.ticker}: {m.title}")
                print(f"    Yes: {m.yes_price}¢ | No: {m.no_price}¢ | Vol: {m.volume}")

        except Exception as e:
            print(f"❌ 연결 실패: {e}")
            print("   Demo 환경에서 먼저 테스트해보세요.")
