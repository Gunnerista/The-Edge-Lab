#!/usr/bin/env python3
"""
Kalshi WebSocket Client
=======================
실시간 가격 스트리밍 via Kalshi WebSocket API.
REST polling 대신 즉시 가격 변동 감지.

Public channels (인증 필요하지만 추가 권한 불필요):
- ticker: 실시간 bid/ask 업데이트
- trade: 체결 내역
- market_lifecycle_v2: 마켓 상태 변경

사용법:
    python scripts/kalshi_ws.py                          # 테스트 연결
    python scripts/kalshi_ws.py --tickers KXNBAGAME-...  # 특정 마켓 구독
"""

import sys
import json
import time
import asyncio
import logging
import base64
from datetime import datetime, timezone
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import websockets

from kalshi_client import KalshiConfig, RSASigner

logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class TickerUpdate:
    """실시간 가격 업데이트"""
    market_ticker: str
    yes_bid: float       # dollars (0.00 - 1.00)
    yes_ask: float
    yes_price: float     # mid-price
    no_bid: float
    no_ask: float
    volume: int
    open_interest: int
    timestamp: str

    @property
    def spread_cents(self) -> float:
        return (self.yes_ask - self.yes_bid) * 100

    @property
    def implied_prob(self) -> float:
        """Mid-price 기반 implied probability"""
        return self.yes_price


@dataclass
class TradeUpdate:
    """체결 내역"""
    market_ticker: str
    yes_price: float
    count: int
    taker_side: str
    timestamp: str


# ============================================================================
# Kalshi WebSocket Client
# ============================================================================

class KalshiWebSocket:
    """Kalshi WebSocket 실시간 가격 스트리밍"""

    PROD_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    DEMO_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"

    def __init__(self, config: KalshiConfig = None):
        self.config = config or KalshiConfig.from_env()
        self.ws_url = self.DEMO_URL if self.config.use_demo else self.PROD_URL

        # RSA 서명
        private_key = self.config.load_private_key()
        self.signer = RSASigner(private_key)
        self.api_key_id = self.config.api_key_id

        # 콜백
        self._on_ticker: Optional[Callable] = None
        self._on_trade: Optional[Callable] = None
        self._on_lifecycle: Optional[Callable] = None

        # 상태
        self._ws = None
        self._subscriptions: Dict[str, List[str]] = {}
        self._latest_prices: Dict[str, TickerUpdate] = {}
        self._msg_id = 0

    def _get_auth_headers(self) -> Dict[str, str]:
        """WebSocket 인증 헤더 생성"""
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        path = "/trade-api/ws/v2"
        signature = self.signer.sign_request("GET", path, timestamp)

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    # ── Public API ──────────────────────────────────────────────

    def on_ticker(self, callback: Callable[[TickerUpdate], None]):
        """가격 업데이트 콜백 등록"""
        self._on_ticker = callback

    def on_trade(self, callback: Callable[[TradeUpdate], None]):
        """체결 업데이트 콜백 등록"""
        self._on_trade = callback

    def on_lifecycle(self, callback: Callable[[dict], None]):
        """마켓 라이프사이클 콜백 등록"""
        self._on_lifecycle = callback

    def get_latest_price(self, ticker: str) -> Optional[TickerUpdate]:
        """마지막으로 수신한 가격"""
        return self._latest_prices.get(ticker)

    def get_all_prices(self) -> Dict[str, TickerUpdate]:
        """모든 구독 마켓의 최신 가격"""
        return dict(self._latest_prices)

    async def connect_and_subscribe(
        self,
        tickers: List[str],
        channels: List[str] = None,
        duration_seconds: int = None,
    ):
        """
        WebSocket 연결 + 구독 + 메시지 수신

        Args:
            tickers: 구독할 마켓 ticker 목록
            channels: 구독할 채널 (기본: ["ticker"])
            duration_seconds: 연결 유지 시간 (None=무한)
        """
        if channels is None:
            channels = ["ticker"]

        headers = self._get_auth_headers()

        logger.info(f"Connecting to {self.ws_url}")

        try:
            async with websockets.connect(
                self.ws_url,
                additional_headers=headers,
                ping_interval=30,
                ping_timeout=10,
            ) as ws:
                self._ws = ws
                logger.info("WebSocket connected")

                # 구독
                await self._subscribe(ws, channels, tickers)

                # 메시지 수신 루프
                start = time.time()
                async for message in ws:
                    self._handle_message(message)

                    if duration_seconds and (time.time() - start) > duration_seconds:
                        break

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket closed: {e}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            self._ws = None

    async def _subscribe(self, ws, channels: List[str], tickers: List[str]):
        """채널 구독"""
        msg = {
            "id": self._next_id(),
            "cmd": "subscribe",
            "params": {
                "channels": channels,
                "market_tickers": tickers,
            },
        }
        await ws.send(json.dumps(msg))
        logger.info(f"Subscribed to {channels} for {len(tickers)} tickers")
        self._subscriptions = {"channels": channels, "tickers": tickers}

    def _handle_message(self, raw: str):
        """수신 메시지 처리"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = data.get("type", "")

        if msg_type == "ticker":
            self._handle_ticker(data.get("msg", {}))
        elif msg_type == "trade":
            self._handle_trade(data.get("msg", {}))
        elif msg_type == "market_lifecycle_v2":
            if self._on_lifecycle:
                self._on_lifecycle(data.get("msg", {}))
        elif msg_type == "subscribed":
            logger.info(f"Subscription confirmed: {data}")
        elif msg_type == "error":
            logger.error(f"WebSocket error: {data}")

    def _handle_ticker(self, msg: dict):
        """ticker 메시지 → TickerUpdate"""
        ticker = msg.get("market_ticker", "")
        yes_bid = float(msg.get("yes_bid_dollars", 0) or 0)
        yes_ask = float(msg.get("yes_ask_dollars", 0) or 0)
        no_bid = float(msg.get("no_bid_dollars", 0) or 0)
        no_ask = float(msg.get("no_ask_dollars", 0) or 0)
        mid = (yes_bid + yes_ask) / 2 if yes_bid > 0 and yes_ask > 0 else 0

        update = TickerUpdate(
            market_ticker=ticker,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            yes_price=mid,
            no_bid=no_bid,
            no_ask=no_ask,
            volume=int(msg.get("volume", 0) or 0),
            open_interest=int(msg.get("open_interest", 0) or 0),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self._latest_prices[ticker] = update

        if self._on_ticker:
            self._on_ticker(update)

    def _handle_trade(self, msg: dict):
        """trade 메시지 → TradeUpdate"""
        update = TradeUpdate(
            market_ticker=msg.get("market_ticker", ""),
            yes_price=float(msg.get("yes_price", 0) or 0),
            count=int(msg.get("count", 0) or 0),
            taker_side=msg.get("taker_side", ""),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        if self._on_trade:
            self._on_trade(update)

    # ── Sync wrapper ────────────────────────────────────────────

    def run(self, tickers: List[str], channels: List[str] = None,
            duration_seconds: int = None):
        """동기 실행 (asyncio.run 래퍼)"""
        asyncio.run(self.connect_and_subscribe(tickers, channels, duration_seconds))


# ============================================================================
# REST Fallback (WebSocket 연결 불가 시)
# ============================================================================

class KalshiPriceFetcher:
    """REST API 기반 가격 조회 (WebSocket 대안)"""

    def __init__(self, config: KalshiConfig = None):
        from kalshi_client import create_client
        self.config = config or KalshiConfig.from_env()
        self.config.use_demo = False  # Production
        self.client = create_client(self.config)

    def get_price(self, ticker: str) -> Optional[TickerUpdate]:
        """오더북에서 가격 조회"""
        try:
            ob = self.client.get_market_orderbook(ticker, depth=3)
            fp = ob.get("orderbook_fp", {})
            yes_bids = fp.get("yes_dollars", [])
            no_asks = fp.get("no_dollars", [])

            yes_bid = float(yes_bids[0][0]) if yes_bids else 0
            best_no_bid = float(no_asks[0][0]) if no_asks else 0
            yes_ask = 1.0 - best_no_bid if best_no_bid > 0 else 0
            mid = (yes_bid + yes_ask) / 2 if yes_bid > 0 and yes_ask > 0 else 0

            return TickerUpdate(
                market_ticker=ticker,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                yes_price=mid,
                no_bid=best_no_bid,
                no_ask=1.0 - yes_bid if yes_bid > 0 else 0,
                volume=0,
                open_interest=0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error(f"Price fetch error for {ticker}: {e}")
            return None

    def get_prices(self, tickers: List[str], delay: float = 0.15) -> Dict[str, TickerUpdate]:
        """여러 마켓 가격 일괄 조회"""
        results = {}
        for t in tickers:
            price = self.get_price(t)
            if price:
                results[t] = price
            time.sleep(delay)
        return results


# ============================================================================
# 메인 실행 (테스트)
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="Kalshi WebSocket Client")
    parser.add_argument("--tickers", nargs="+", help="Market tickers to subscribe")
    parser.add_argument("--duration", type=int, default=30, help="Duration in seconds")
    parser.add_argument("--rest", action="store_true", help="Use REST fallback instead of WebSocket")
    args = parser.parse_args()

    config = KalshiConfig.from_env()

    print("=" * 60)
    print("Kalshi Price Monitor")
    print(f"Mode: {'REST' if args.rest else 'WebSocket'}")
    print(f"Env: {'DEMO' if config.use_demo else 'PRODUCTION'}")
    print("=" * 60)

    if args.rest or not args.tickers:
        # REST 모드: NBA 마켓 가격 조회
        print("\nREST mode — fetching NBA game prices...")
        config.use_demo = False
        fetcher = KalshiPriceFetcher(config)

        # 오늘/내일 KXNBAGAME 마켓 찾기
        from kalshi_client import create_client
        client = create_client(config)
        try:
            resp = client.get_markets(series_ticker="KXNBAGAME", status="open", limit=20)
            tickers = [m["ticker"] for m in resp.get("markets", [])]
        except Exception:
            tickers = args.tickers or []

        if tickers:
            print(f"Found {len(tickers)} tickers")
            prices = fetcher.get_prices(tickers[:10])
            for ticker, p in prices.items():
                print(f"  {ticker}")
                print(f"    Bid={p.yes_bid*100:.0f}c Ask={p.yes_ask*100:.0f}c Mid={p.yes_price*100:.1f}c Spread={p.spread_cents:.0f}c")
        else:
            print("No tickers found")

    else:
        # WebSocket 모드
        ws = KalshiWebSocket(config)

        def on_tick(update: TickerUpdate):
            print(f"  TICK | {update.market_ticker} | Bid={update.yes_bid*100:.0f}c Ask={update.yes_ask*100:.0f}c Mid={update.yes_price*100:.1f}c")

        def on_trade(update: TradeUpdate):
            print(f"  TRADE | {update.market_ticker} | Price={update.yes_price*100:.0f}c Count={update.count} Side={update.taker_side}")

        ws.on_ticker(on_tick)
        ws.on_trade(on_trade)

        print(f"\nSubscribing to {len(args.tickers)} tickers for {args.duration}s...")
        ws.run(args.tickers, channels=["ticker", "trade"], duration_seconds=args.duration)
