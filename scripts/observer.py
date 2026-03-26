#!/usr/bin/env python3
"""
Observer Mode — 관찰 전용 모드
================================
모든 필터를 풀고, 모든 기회를 기록만 함.
배팅 안 함. 1주일 돌리면 실제 데이터 확보.

기록:
1. data/kalshi_vs_db_comparison.csv — 매 스캔 가격 비교
2. data/observe_log.jsonl — 전체 기회 기록
3. 경기 종료 후 "배팅했으면 얼마를 벌었을까" 가상 계산

사용법:
    python scripts/main.py --mode observe
    python scripts/observer.py              # 직접 실행
"""
import sys
import os
import csv
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, List
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests as req

from live_scores import LiveScoreTracker, LiveGame
from comeback_db import ComebackDatabase, classify_team_quality, MLBComebackBuilder, NBAComebackBuilder
from kalshi_client import KalshiConfig, create_client
from kalshi_ws import KalshiPriceFetcher
from live_trading_engine import KalshiTickerMatcher

logger = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

CSV_FILE = DATA_DIR / "kalshi_vs_db_comparison.csv"
LOG_FILE = DATA_DIR / "observe_log.jsonl"


class Observer:
    """관찰 모드 — 모든 기회 기록, 배팅 없음"""

    def __init__(self):
        self.tracker = LiveScoreTracker()
        self.comeback_db = ComebackDatabase()

        config = KalshiConfig.from_env()
        config.use_demo = False
        self.client = create_client(config)
        self.fetcher = KalshiPriceFetcher(config)
        self.matcher = KalshiTickerMatcher(self.client)

        self.scan_count = 0
        self.total_observations = 0
        self.opportunities_found = 0

        # CSV 헤더 초기화
        if not CSV_FILE.exists():
            with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "game", "sport", "team", "quality",
                    "deficit", "period", "db_prob", "kalshi_price",
                    "kalshi_bid", "kalshi_ask", "kalshi_spread",
                    "edge", "db_sample_size", "filter_pass", "filter_reason",
                    "price_zone",
                ])

    def scan_all(self) -> List[Dict]:
        """모든 경기 관찰 — 필터 없음"""
        self.scan_count += 1
        games = self.tracker.get_live_games()
        live = [g for g in games if g.status == "in_progress"]

        if not live:
            return []

        observations = []

        for g in live:
            if g.home_score == g.away_score:
                continue

            # 양쪽 다 관찰
            for losing, winning, deficit in self._get_trailing_teams(g):
                obs = self._observe_game(g, losing, deficit)
                if obs:
                    observations.append(obs)
                    self._write_csv(obs)
                    self._write_log(obs)
                    self.total_observations += 1
                    if obs.get("edge", 0) >= 0.05:
                        self.opportunities_found += 1

        return observations

    def _get_trailing_teams(self, g: LiveGame):
        """뒤지는 팀 추출"""
        if g.home_score < g.away_score:
            yield g.home_team, g.away_team, g.away_score - g.home_score
        elif g.away_score < g.home_score:
            yield g.away_team, g.home_team, g.home_score - g.away_score

    def _observe_game(self, g: LiveGame, losing_team: str, deficit: int) -> Dict:
        """개별 경기 관찰 — 필터 없이 모든 데이터 수집"""

        # 팀 quality
        if losing_team == g.home_team:
            quality = g.home_team_quality
        else:
            quality = g.away_team_quality

        # Comeback DB
        db_prob = 0
        db_sample = 0
        db_key = ""

        if g.sport == "MLB":
            dl = MLBComebackBuilder._bin_deficit(deficit)
            db_key = f"{quality}_down_{dl}_inning_{g.period}"
            entry = self.comeback_db.mlb_db.get(db_key)
        elif g.sport == "NBA":
            dl = NBAComebackBuilder._bin_nba_deficit(deficit)
            db_key = f"{quality}_down_{dl}_after_Q{g.period}"
            entry = self.comeback_db.nba_db.get(db_key)
        else:
            entry = None

        if entry:
            db_prob = entry["comeback_win_rate"]
            db_sample = entry["sample_size"]

        # Kalshi 가격
        kalshi_price = 0
        kalshi_bid = 0
        kalshi_ask = 0
        kalshi_spread = 999
        ticker = ""

        try:
            ticker = self.matcher.find_ticker(g, losing_team) or ""
            if ticker:
                price = self.fetcher.get_price(ticker)
                if price and price.yes_price > 0:
                    kalshi_price = price.yes_price
                    kalshi_bid = price.yes_bid
                    kalshi_ask = price.yes_ask
                    kalshi_spread = price.spread_cents
        except Exception:
            pass

        # Edge
        edge = db_prob - kalshi_price if db_prob > 0 and kalshi_price > 0 else 0

        # 필터 체크 (기록용)
        filter_pass, filter_reason = self._check_filter(g, quality, deficit)

        obs = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "game": f"{g.away_team} {g.away_score} @ {g.home_team} {g.home_score}",
            "sport": g.sport,
            "team": losing_team,
            "quality": quality,
            "deficit": deficit,
            "period": g.period,
            "period_label": g.period_label,
            "db_key": db_key,
            "db_prob": db_prob,
            "db_sample": db_sample,
            "kalshi_ticker": ticker,
            "kalshi_price": kalshi_price,
            "kalshi_bid": kalshi_bid,
            "kalshi_ask": kalshi_ask,
            "kalshi_spread": kalshi_spread,
            "edge": edge,
            "filter_pass": filter_pass,
            "filter_reason": filter_reason,
            # 가격 구간 태그
            "price_zone": self._classify_price_zone(kalshi_price),
        }

        return obs

    @staticmethod
    def _classify_price_zone(price: float) -> str:
        """가격 구간 분류"""
        if price <= 0:
            return "no_price"
        elif price <= 0.10:
            return "5-10c"
        elif price <= 0.25:
            return "10-25c_BUY_OK"
        elif price <= 0.35:
            return "25-35c_OBSERVE"
        elif price <= 0.40:
            return "35-40c_BUY_OK"
        else:
            return "40c+_NEVER"

    @staticmethod
    def _check_filter(g, quality, deficit) -> tuple:
        """전략 필터 체크 (기록용)

        NOTE: quality 필터 제거됨 — DB가 이미 팀 퀄리티를 반영한 확률을 제공
        quality에 따른 edge 조정은 live_trading_engine에서 처리
        """
        if g.sport == "NBA":
            if deficit < 4 or deficit > 20:
                return False, f"deficit={deficit} (need 4-20)"
            if g.period > 3:
                return False, f"period={g.period} (need Q1-Q3)"
            return True, "PASS"

        elif g.sport == "MLB":
            if deficit < 1 or deficit > 5:
                return False, f"deficit={deficit} (need 1-5)"
            if g.period < 3 or g.period > 8:
                return False, f"period={g.period} (need 3-8)"
            return True, "PASS"

        return False, "unknown sport"

    def _write_csv(self, obs: Dict):
        """CSV에 기록"""
        with open(CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                obs["timestamp"], obs["game"], obs["sport"],
                obs["team"], obs["quality"], obs["deficit"],
                obs["period"], f"{obs['db_prob']:.4f}",
                f"{obs['kalshi_price']:.4f}", f"{obs['kalshi_bid']:.4f}",
                f"{obs['kalshi_ask']:.4f}", f"{obs['kalshi_spread']:.1f}",
                f"{obs['edge']:.4f}", obs["db_sample"],
                obs["filter_pass"], obs["filter_reason"],
                obs.get("price_zone", ""),
            ])

    def _write_log(self, obs: Dict):
        """JSONL에 기록"""
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(obs, ensure_ascii=False) + "\n")

    def monitor(self, interval: int = 30, max_scans: int = None):
        """관찰 모드 모니터링"""
        print(f"\n관찰 모드 | 간격: {interval}초 | 배팅 없음")
        print(f"CSV: {CSV_FILE}")
        print("=" * 60)

        iteration = 0
        try:
            while True:
                if max_scans and iteration >= max_scans:
                    break
                iteration += 1

                now = datetime.now().strftime("%H:%M:%S")
                observations = self.scan_all()
                live_count = len([g for g in self.tracker.get_live_games() if g.status == "in_progress"])

                print(f"\n[{now}] Scan #{iteration} | Live: {live_count} | 관찰: {len(observations)}건 | 누적: {self.total_observations}")

                for obs in observations:
                    edge_str = f"Edge {obs['edge']:+.1%}" if obs['edge'] else "Edge N/A"
                    tag = ">>>" if obs["edge"] >= 0.05 else "   "
                    filt = "PASS" if obs["filter_pass"] else obs["filter_reason"]
                    kalshi_str = f"Kalshi {obs['kalshi_price']*100:.0f}c" if obs['kalshi_price'] > 0 else "Kalshi N/A"

                    print(f"  {tag} {obs['team']} ({obs['quality']}) {obs['deficit']}pt뒤 {obs['period_label']} | "
                          f"DB {obs['db_prob']:.1%} vs {kalshi_str} | {edge_str} | [{filt}]")

                if max_scans and iteration >= max_scans:
                    break
                time.sleep(interval)

        except KeyboardInterrupt:
            pass

        # 요약
        print(f"\n{'='*60}")
        print(f"관찰 종료 | 총 {iteration}회 스캔")
        print(f"  관찰 데이터: {self.total_observations}건")
        print(f"  Edge 5%+ 기회: {self.opportunities_found}건")
        print(f"  CSV: {CSV_FILE}")

    def send_daily_summary(self):
        """오늘의 관찰 요약 Discord 발송"""
        webhook = os.environ.get("DISCORD_WEBHOOK_URL")
        if not webhook:
            return

        # 오늘 CSV 데이터 읽기
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_obs = []
        if CSV_FILE.exists():
            with open(CSV_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("timestamp", "")[:10] == today:
                        today_obs.append(row)

        if not today_obs:
            return

        # 통계
        edges = [float(row["edge"]) for row in today_obs if float(row.get("edge", 0)) != 0]
        edge_5plus = [e for e in edges if e >= 0.05]
        edge_8plus = [e for e in edges if e >= 0.08]
        avg_edge = sum(edges) / len(edges) if edges else 0

        # Kalshi 가격이 있는 것만
        with_kalshi = [row for row in today_obs if float(row.get("kalshi_price", 0)) > 0]
        avg_discount = sum(float(r["edge"]) for r in with_kalshi) / len(with_kalshi) if with_kalshi else 0

        embed = {
            "title": f"📊 관찰 모드 일일 리포트 ({today})",
            "color": 0x2196F3,
            "fields": [
                {"name": "총 관찰", "value": f"{len(today_obs)}건", "inline": True},
                {"name": "Kalshi 가격 확인", "value": f"{len(with_kalshi)}건", "inline": True},
                {"name": "Edge 5%+", "value": f"{len(edge_5plus)}건", "inline": True},
                {"name": "Edge 8%+", "value": f"{len(edge_8plus)}건", "inline": True},
                {"name": "평균 Edge", "value": f"{avg_edge:+.1%}", "inline": True},
                {"name": "평균 시장 할인", "value": f"{avg_discount:+.1%}", "inline": True},
                {"name": "해석", "value": (
                    f"시장이 평균 {avg_discount:.1%} 과잉반응\n"
                    + (f"Edge 5%+ 기회가 하루 {len(edge_5plus)}건 → 실전 가능" if len(edge_5plus) >= 3
                       else "기회 부족 — 필터 조정 필요")
                ), "inline": False},
            ],
            "footer": {"text": "Observer Mode | 1주일 데이터 수집 중"},
        }
        req.post(webhook, json={"embeds": [embed]}, timeout=10)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--scans", type=int, default=None)
    args = parser.parse_args()

    obs = Observer()
    obs.monitor(interval=args.interval, max_scans=args.scans)
