#!/usr/bin/env python3
"""
실시간 스포츠 오즈 수집기
========================
Kalshi API에서 스포츠 마켓 데이터를 수집하고,
기존 analyzer.py의 EV 분석과 연동하여
양의 기대값(+EV) 배팅 기회를 자동 탐지.

흐름:
  1. Kalshi에서 열린 스포츠 마켓 전체 수집
  2. 카테고리별 정리 (NBA / MLB / Soccer / NFL)
  3. 각 마켓의 implied probability 추출
  4. analyzer.py의 모델 확률과 비교
  5. +EV 기회 발견 시 알림 & 기록

사용법:
  collector = OddsCollector(kalshi_client)
  opportunities = collector.scan_all_sports()
"""

import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from kalshi_client import (
    KalshiAPIClient,
    KalshiConfig,
    KalshiMarket,
    create_client,
    parse_markets_response,
    filter_sports_markets,
    is_sports_market,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 데이터 구조
# ============================================================================

@dataclass
class OddsSnapshot:
    """특정 시점의 마켓 오즈 스냅샷"""
    ticker: str
    title: str
    sport: str
    timestamp: str
    yes_price: float       # Kalshi 가격 (cents)
    no_price: float
    yes_bid: float
    yes_ask: float
    implied_prob_yes: float
    implied_prob_no: float
    volume: int
    spread: float          # bid-ask spread

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValueBet:
    """양의 기대값(+EV) 배팅 기회"""
    ticker: str
    title: str
    sport: str
    side: str                    # "yes" or "no"
    market_implied_prob: float   # Kalshi가 말하는 확률
    model_prob: float            # 우리 모델이 말하는 확률
    edge: float                  # model_prob - market_implied_prob
    expected_value: float        # EV per $1 bet
    decimal_odds: float
    confidence: str              # "low", "medium", "high"
    volume: int
    spread: float
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    @property
    def ev_percentage(self) -> float:
        """EV를 % 로 표현"""
        return self.expected_value * 100

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ev_percentage"] = self.ev_percentage
        return d


@dataclass
class CollectionResult:
    """수집 결과 요약"""
    timestamp: str
    total_markets_scanned: int
    sports_markets_found: int
    value_bets_found: int
    markets_by_sport: Dict[str, int]
    snapshots: List[OddsSnapshot]
    value_bets: List[ValueBet]
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "total_markets_scanned": self.total_markets_scanned,
            "sports_markets_found": self.sports_markets_found,
            "value_bets_found": self.value_bets_found,
            "markets_by_sport": self.markets_by_sport,
            "snapshots": [s.to_dict() for s in self.snapshots],
            "value_bets": [v.to_dict() for v in self.value_bets],
            "errors": self.errors,
        }


# ============================================================================
# 스포츠 분류기
# ============================================================================

SPORT_PATTERNS = {
    "NBA": ["nba", "celtics", "lakers", "warriors", "bucks", "nuggets", "76ers",
            "knicks", "heat", "suns", "mavericks", "clippers", "cavaliers",
            "grizzlies", "timberwolves", "thunder", "kings", "rockets", "pacers",
            "magic", "hawks", "raptors", "nets", "bulls", "hornets", "pistons",
            "wizards", "spurs", "blazers", "jazz", "pelicans"],
    "MLB": ["mlb", "yankees", "dodgers", "astros", "braves", "mets", "phillies",
            "padres", "rangers", "orioles", "twins", "guardians", "mariners",
            "rays", "cubs", "red sox", "cardinals", "brewers", "diamondbacks",
            "giants", "reds", "white sox", "pirates", "royals", "tigers",
            "nationals", "rockies", "marlins", "angels", "athletics", "blue jays"],
    "Soccer": ["epl", "premier league", "la liga", "champions league", "serie a",
               "bundesliga", "arsenal", "liverpool", "manchester", "chelsea",
               "tottenham", "barcelona", "real madrid", "bayern", "psg",
               "brighton", "newcastle", "aston villa", "west ham", "soccer",
               "football", "brentford", "fulham", "wolves", "bournemouth",
               "crystal palace", "everton", "nottingham", "leicester"],
    "NFL": ["nfl", "super bowl", "chiefs", "eagles", "49ers", "cowboys",
            "bills", "ravens", "lions", "bengals", "dolphins", "packers",
            "steelers", "broncos", "chargers", "raiders", "jets", "patriots",
            "commanders", "seahawks", "rams", "cardinals", "saints",
            "panthers", "falcons", "buccaneers", "bears", "vikings",
            "titans", "jaguars", "colts", "texans"],
}


def classify_sport(market: KalshiMarket) -> str:
    """마켓을 스포츠 카테고리로 분류"""
    text = f"{market.title} {market.subtitle} {market.ticker}".lower()
    for sport, keywords in SPORT_PATTERNS.items():
        if any(kw in text for kw in keywords):
            return sport
    return "Other"


# ============================================================================
# 오즈 수집기 (핵심 클래스)
# ============================================================================

class OddsCollector:
    """
    Kalshi 스포츠 마켓 오즈 수집기

    주요 기능:
    1. 전체 스포츠 마켓 스캔
    2. 오즈 스냅샷 저장 (시간별 추적)
    3. +EV 기회 탐지
    4. 오즈 변동 감지 (CLV 추적)
    """

    def __init__(
        self,
        client: KalshiAPIClient,
        data_dir: str = None,
        min_volume: int = 10,          # 최소 거래량 필터
        max_spread: float = 15.0,      # 최대 스프레드 (cents)
        min_edge: float = 0.03,        # 최소 엣지 (3%)
    ):
        self.client = client
        self.data_dir = Path(data_dir) if data_dir else Path(__file__).parent.parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 필터 설정
        self.min_volume = min_volume
        self.max_spread = max_spread
        self.min_edge = min_edge

        # 오즈 히스토리 (메모리)
        self.odds_history: Dict[str, List[OddsSnapshot]] = {}

        # 수집 통계
        self.scan_count = 0
        self.total_value_bets_found = 0

    def scan_all_markets(self, max_pages: int = 5) -> List[KalshiMarket]:
        """
        Kalshi에서 열린 마켓 전체 수집 (페이지네이션 처리)
        """
        all_markets = []
        cursor = None

        for page in range(max_pages):
            try:
                response = self.client.get_markets(
                    limit=200,
                    cursor=cursor,
                    status="open"
                )
                markets = parse_markets_response(response)
                all_markets.extend(markets)

                # 다음 페이지 커서
                cursor = response.get("cursor")
                if not cursor or len(markets) < 200:
                    break

                time.sleep(0.5)  # Rate limit 존중

            except Exception as e:
                logger.error(f"Page {page} fetch error: {e}")
                break

        logger.info(f"Scanned {len(all_markets)} total markets across {page + 1} pages")
        return all_markets

    def collect_sports_odds(self, max_pages: int = 5) -> List[OddsSnapshot]:
        """
        스포츠 마켓 오즈 수집 → OddsSnapshot 리스트 반환
        """
        all_markets = self.scan_all_markets(max_pages)
        sports_markets = filter_sports_markets(all_markets)

        snapshots = []
        now = datetime.now(timezone.utc).isoformat()

        for market in sports_markets:
            sport = classify_sport(market)

            snapshot = OddsSnapshot(
                ticker=market.ticker,
                title=market.title,
                sport=sport,
                timestamp=now,
                yes_price=market.yes_price,
                no_price=market.no_price,
                yes_bid=market.yes_bid,
                yes_ask=market.yes_ask,
                implied_prob_yes=market.implied_prob_yes,
                implied_prob_no=market.implied_prob_no,
                volume=market.volume,
                spread=market.spread,
            )
            snapshots.append(snapshot)

            # 히스토리에 추가
            if market.ticker not in self.odds_history:
                self.odds_history[market.ticker] = []
            self.odds_history[market.ticker].append(snapshot)

        self.scan_count += 1
        logger.info(f"Collected {len(snapshots)} sports odds snapshots")
        return snapshots

    def find_value_bets(
        self,
        snapshots: List[OddsSnapshot],
        model_probabilities: Dict[str, float] = None,
    ) -> List[ValueBet]:
        """
        +EV 배팅 기회 탐지

        Args:
            snapshots: 수집된 오즈 스냅샷들
            model_probabilities: {ticker: our_model_probability}
                                 없으면 기본 엣지 탐지 로직 사용

        Returns:
            양의 기대값을 가진 배팅 기회 리스트
        """
        value_bets = []

        for snap in snapshots:
            # 유동성 필터
            if snap.volume < self.min_volume:
                continue
            if snap.spread > self.max_spread:
                continue

            # 모델 확률이 있으면 비교
            if model_probabilities and snap.ticker in model_probabilities:
                model_prob = model_probabilities[snap.ticker]
                market_prob = snap.implied_prob_yes

                # Yes 사이드 체크
                edge_yes = model_prob - market_prob
                if edge_yes >= self.min_edge:
                    decimal_odds = 1.0 / market_prob if market_prob > 0 else 0
                    ev = (model_prob * (decimal_odds - 1)) - (1 - model_prob)

                    value_bets.append(ValueBet(
                        ticker=snap.ticker,
                        title=snap.title,
                        sport=snap.sport,
                        side="yes",
                        market_implied_prob=market_prob,
                        model_prob=model_prob,
                        edge=edge_yes,
                        expected_value=ev,
                        decimal_odds=decimal_odds,
                        confidence=self._assess_confidence(edge_yes, snap.volume, snap.spread),
                        volume=snap.volume,
                        spread=snap.spread,
                    ))

                # No 사이드 체크
                edge_no = (1 - model_prob) - snap.implied_prob_no
                if edge_no >= self.min_edge:
                    decimal_odds_no = 1.0 / snap.implied_prob_no if snap.implied_prob_no > 0 else 0
                    ev_no = ((1 - model_prob) * (decimal_odds_no - 1)) - model_prob

                    value_bets.append(ValueBet(
                        ticker=snap.ticker,
                        title=snap.title,
                        sport=snap.sport,
                        side="no",
                        market_implied_prob=snap.implied_prob_no,
                        model_prob=1 - model_prob,
                        edge=edge_no,
                        expected_value=ev_no,
                        decimal_odds=decimal_odds_no,
                        confidence=self._assess_confidence(edge_no, snap.volume, snap.spread),
                        volume=snap.volume,
                        spread=snap.spread,
                    ))
            else:
                # 모델 확률 없이도 시장 비효율성 탐지
                # (Yes + No 가격 합이 100에서 벗어나면 차익거래 가능성)
                total_price = snap.yes_price + snap.no_price
                if total_price > 0 and total_price < 95:
                    # 시장이 과소평가하는 경우 → 가격 합이 100 미만
                    logger.info(
                        f"Potential arbitrage: {snap.ticker} "
                        f"Yes={snap.yes_price}¢ + No={snap.no_price}¢ = {total_price}¢"
                    )

        # EV 기준 내림차순 정렬
        value_bets.sort(key=lambda x: x.expected_value, reverse=True)
        self.total_value_bets_found += len(value_bets)

        return value_bets

    def _assess_confidence(self, edge: float, volume: int, spread: float) -> str:
        """배팅 기회의 신뢰도 평가"""
        score = 0

        # 엣지 크기
        if edge >= 0.10:
            score += 3
        elif edge >= 0.05:
            score += 2
        else:
            score += 1

        # 거래량 (유동성)
        if volume >= 500:
            score += 3
        elif volume >= 100:
            score += 2
        else:
            score += 1

        # 스프레드 (좁을수록 좋음)
        if spread <= 3:
            score += 3
        elif spread <= 7:
            score += 2
        else:
            score += 1

        if score >= 7:
            return "high"
        elif score >= 5:
            return "medium"
        return "low"

    # ── 오즈 변동 추적 (CLV) ────────────────────────────────────────

    def get_odds_movement(self, ticker: str) -> Optional[Dict]:
        """특정 마켓의 오즈 변동 분석"""
        history = self.odds_history.get(ticker, [])
        if len(history) < 2:
            return None

        first = history[0]
        latest = history[-1]
        move = latest.implied_prob_yes - first.implied_prob_yes

        return {
            "ticker": ticker,
            "title": latest.title,
            "first_price": first.yes_price,
            "latest_price": latest.yes_price,
            "price_change": latest.yes_price - first.yes_price,
            "prob_change": move,
            "direction": "up" if move > 0 else "down" if move < 0 else "flat",
            "snapshots_count": len(history),
            "first_seen": first.timestamp,
            "last_updated": latest.timestamp,
        }

    def get_all_movements(self) -> List[Dict]:
        """모든 추적 중인 마켓의 오즈 변동"""
        movements = []
        for ticker in self.odds_history:
            m = self.get_odds_movement(ticker)
            if m:
                movements.append(m)
        movements.sort(key=lambda x: abs(x["prob_change"]), reverse=True)
        return movements

    # ── 전체 스캔 (메인 워크플로우) ──────────────────────────────────

    def scan_all_sports(
        self,
        model_probabilities: Dict[str, float] = None,
        save_results: bool = True,
    ) -> CollectionResult:
        """
        전체 스포츠 스캔 워크플로우

        1. 모든 스포츠 마켓 수집
        2. +EV 기회 탐지
        3. 결과 저장 & 반환
        """
        now = datetime.now(timezone.utc).isoformat()

        # 1. 오즈 수집
        snapshots = self.collect_sports_odds()

        # 2. 스포츠별 집계
        by_sport: Dict[str, int] = {}
        for snap in snapshots:
            by_sport[snap.sport] = by_sport.get(snap.sport, 0) + 1

        # 3. +EV 탐지
        value_bets = self.find_value_bets(snapshots, model_probabilities)

        # 4. 결과 구성
        result = CollectionResult(
            timestamp=now,
            total_markets_scanned=self.scan_count,
            sports_markets_found=len(snapshots),
            value_bets_found=len(value_bets),
            markets_by_sport=by_sport,
            snapshots=snapshots,
            value_bets=value_bets,
        )

        # 5. 저장
        if save_results:
            self._save_result(result)

        return result

    def _save_result(self, result: CollectionResult):
        """수집 결과를 JSON으로 저장"""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        filepath = self.data_dir / f"odds_scan_{date_str}.json"

        with open(filepath, "w") as f:
            json.dump(result.to_dict(), f, indent=2)

        logger.info(f"Results saved to {filepath}")

    # ── 연속 모니터링 ────────────────────────────────────────────────

    def monitor_loop(
        self,
        interval_seconds: int = 300,  # 5분 간격
        max_iterations: int = None,
        model_probabilities: Dict[str, float] = None,
        on_value_bet_found=None,       # 콜백 함수
    ):
        """
        연속 모니터링 루프

        Args:
            interval_seconds: 스캔 간격 (초)
            max_iterations: 최대 반복 횟수 (None = 무한)
            model_probabilities: 모델 확률 딕셔너리
            on_value_bet_found: +EV 발견 시 호출할 콜백 함수
        """
        iteration = 0
        print(f"\n🔍 오즈 모니터링 시작 | 간격: {interval_seconds}s")
        print(f"   필터: 최소 거래량={self.min_volume}, 최대 스프레드={self.max_spread}¢, 최소 엣지={self.min_edge*100}%")
        print("-" * 60)

        try:
            while True:
                if max_iterations and iteration >= max_iterations:
                    break

                iteration += 1
                print(f"\n[Scan #{iteration}] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

                result = self.scan_all_sports(model_probabilities)

                # 요약 출력
                print(f"  📊 스포츠 마켓: {result.sports_markets_found}개")
                for sport, count in sorted(result.markets_by_sport.items()):
                    print(f"     {sport}: {count}")

                if result.value_bets:
                    print(f"\n  🎯 +EV 기회 발견: {len(result.value_bets)}개!")
                    for vb in result.value_bets[:5]:
                        print(f"     [{vb.confidence.upper()}] {vb.title}")
                        print(f"       Side: {vb.side} | Edge: {vb.edge:.1%} | EV: {vb.ev_percentage:+.1f}%")

                    if on_value_bet_found:
                        for vb in result.value_bets:
                            on_value_bet_found(vb)
                else:
                    print("  ⏳ +EV 기회 없음. 계속 모니터링 중...")

                if max_iterations and iteration >= max_iterations:
                    break

                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            print(f"\n\n⏹️  모니터링 중지 | 총 {iteration}회 스캔, {self.total_value_bets_found}개 +EV 발견")


# ============================================================================
# 편의 함수
# ============================================================================

def quick_scan(config: KalshiConfig = None) -> CollectionResult:
    """
    빠른 1회 스캔

    사용법:
        from odds_collector import quick_scan
        result = quick_scan()
        for vb in result.value_bets:
            print(f"{vb.title}: EV={vb.ev_percentage:+.1f}%")
    """
    client = create_client(config)
    collector = OddsCollector(client)
    return collector.scan_all_sports()


def get_sports_summary(config: KalshiConfig = None) -> Dict:
    """
    현재 스포츠 마켓 요약

    Returns:
        {"NBA": [...markets], "MLB": [...], ...}
    """
    client = create_client(config)
    collector = OddsCollector(client, min_volume=0, max_spread=100)
    snapshots = collector.collect_sports_odds()

    by_sport: Dict[str, List[Dict]] = {}
    for snap in snapshots:
        if snap.sport not in by_sport:
            by_sport[snap.sport] = []
        by_sport[snap.sport].append({
            "ticker": snap.ticker,
            "title": snap.title,
            "yes_price": snap.yes_price,
            "no_price": snap.no_price,
            "implied_prob": snap.implied_prob_yes,
            "volume": snap.volume,
        })

    return by_sport


# ============================================================================
# 메인 실행
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("🏀 스포츠 오즈 수집기 — Kalshi")
    print("=" * 60)

    config = KalshiConfig.from_env()

    if not config.api_key_id:
        print("\n⚠️  API 키가 설정되지 않았습니다.")
        print("먼저 kalshi_client.py를 실행하여 설정 방법을 확인하세요.")
        print("\n--- Demo 모드 (API 없이 구조 테스트) ---")
        print()

        # API 없이 구조 테스트
        demo_snapshot = OddsSnapshot(
            ticker="NBA-CELTICS-WIN-20260325",
            title="Will the Celtics beat the Lakers?",
            sport="NBA",
            timestamp=datetime.now(timezone.utc).isoformat(),
            yes_price=62,
            no_price=40,
            yes_bid=61,
            yes_ask=63,
            implied_prob_yes=0.62,
            implied_prob_no=0.40,
            volume=1523,
            spread=2.0,
        )

        # 모델이 "Celtics 68% 확률로 이긴다"고 예측한 경우
        model_probs = {"NBA-CELTICS-WIN-20260325": 0.68}

        collector = OddsCollector.__new__(OddsCollector)
        collector.min_volume = 10
        collector.max_spread = 15.0
        collector.min_edge = 0.03
        collector.odds_history = {}
        collector.total_value_bets_found = 0

        value_bets = collector.find_value_bets([demo_snapshot], model_probs)

        print(f"📊 데모 스냅샷: {demo_snapshot.title}")
        print(f"   Kalshi: Yes={demo_snapshot.yes_price}¢ (확률 {demo_snapshot.implied_prob_yes:.0%})")
        print(f"   모델:  확률 {model_probs['NBA-CELTICS-WIN-20260325']:.0%}")
        print()

        if value_bets:
            for vb in value_bets:
                print(f"🎯 +EV 발견!")
                print(f"   Side: {vb.side}")
                print(f"   시장 확률: {vb.market_implied_prob:.0%}")
                print(f"   모델 확률: {vb.model_prob:.0%}")
                print(f"   Edge: {vb.edge:.1%}")
                print(f"   EV: {vb.ev_percentage:+.1f}%")
                print(f"   배당: {vb.decimal_odds:.2f}x")
                print(f"   신뢰도: {vb.confidence}")
        else:
            print("❌ +EV 기회 없음 (엣지 부족)")

    else:
        # 실제 API 연결
        client = create_client(config)
        collector = OddsCollector(client)

        print(f"\n환경: {'DEMO' if config.use_demo else 'PRODUCTION'}")
        print("5분 간격 모니터링 시작...\n")

        collector.monitor_loop(
            interval_seconds=300,
            max_iterations=3,  # 테스트용 3회
        )
