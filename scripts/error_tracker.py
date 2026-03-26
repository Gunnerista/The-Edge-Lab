#!/usr/bin/env python3
"""
Error Tracker & Learning System
================================
예측 기록 → 결과 매핑 → 패턴 분석 → 모델 개선 방향 도출.

저장: data/predictions/ 폴더에 일별 JSON
분석: 최소 50건 이후부터 패턴 분석

사용법:
    python scripts/error_tracker.py              # 현재 기록 분석
    python scripts/error_tracker.py --weekly     # 주간 리뷰
"""

import sys
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests as req

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "predictions"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class PredictionLog:
    """배팅 전 예측 기록"""
    id: str                     # ticker 또는 고유 ID
    timestamp: str
    sport: str
    strategy: str               # "pregame" or "live"
    team: str
    opponent: str
    model_prob: float
    market_prob: float
    edge: float
    bet_amount: float
    confidence: str
    reasoning: str
    # 팀 정보
    team_quality: str = ""
    deficit: int = 0            # 라이브 전용
    period: str = ""            # 라이브 전용
    # 결과 (나중에 채움)
    actual_result: str = ""     # "win", "loss", ""
    pnl: float = 0
    settled: bool = False


# ============================================================================
# Error Tracker
# ============================================================================

class ErrorTracker:
    """예측 기록 + 결과 추적 + 패턴 분석"""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.predictions: List[PredictionLog] = []
        self._load_all()

    def _load_all(self):
        """모든 저장된 예측 로드"""
        self.predictions = []
        for f in sorted(self.data_dir.glob("*.json")):
            try:
                data = json.load(open(f))
                for entry in data:
                    self.predictions.append(PredictionLog(**entry))
            except Exception:
                pass
        logger.info(f"Loaded {len(self.predictions)} prediction records")

    def log_prediction(self, prediction: dict) -> PredictionLog:
        """배팅 전 예측 기록"""
        log = PredictionLog(
            id=prediction.get("id", prediction.get("ticker", "")),
            timestamp=datetime.now(timezone.utc).isoformat(),
            sport=prediction.get("sport", ""),
            strategy=prediction.get("strategy", "pregame"),
            team=prediction.get("team", ""),
            opponent=prediction.get("opponent", ""),
            model_prob=prediction.get("model_prob", 0),
            market_prob=prediction.get("market_prob", 0),
            edge=prediction.get("edge", 0),
            bet_amount=prediction.get("bet_amount", 0),
            confidence=prediction.get("confidence", "low"),
            reasoning=prediction.get("reasoning", ""),
            team_quality=prediction.get("team_quality", ""),
            deficit=prediction.get("deficit", 0),
            period=prediction.get("period", ""),
        )
        self.predictions.append(log)
        self._save_today()
        return log

    def log_result(self, pred_id: str, won: bool, pnl: float = 0):
        """경기 종료 후 결과 기록"""
        for p in self.predictions:
            if p.id == pred_id and not p.settled:
                p.actual_result = "win" if won else "loss"
                p.pnl = pnl
                p.settled = True
                self._save_today()
                return True
        return False

    def _save_today(self):
        """오늘 날짜 파일에 저장"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filepath = self.data_dir / f"predictions_{today}.json"

        # 오늘 기록만 필터
        today_preds = [p for p in self.predictions if p.timestamp[:10] == today]
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump([asdict(p) for p in today_preds], f, indent=2, ensure_ascii=False)

    # ── 분석 ────────────────────────────────────────────────────

    def analyze_errors(self) -> Dict:
        """전체 패턴 분석"""
        settled = [p for p in self.predictions if p.settled]

        if len(settled) < 5:
            return {"message": f"데이터 부족 ({len(settled)}건). 최소 5건 필요.", "total": len(settled)}

        wins = [p for p in settled if p.actual_result == "win"]
        losses = [p for p in settled if p.actual_result == "loss"]

        analysis = {
            "total": len(settled),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(settled),
            "total_pnl": sum(p.pnl for p in settled),
        }

        # 1. 스포츠별 분석
        by_sport = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0})
        for p in settled:
            key = p.sport
            by_sport[key]["pnl"] += p.pnl
            if p.actual_result == "win":
                by_sport[key]["w"] += 1
            else:
                by_sport[key]["l"] += 1
        analysis["by_sport"] = {k: {**v, "win_rate": v["w"]/(v["w"]+v["l"]) if (v["w"]+v["l"]) > 0 else 0} for k, v in by_sport.items()}

        # 2. 전략별 분석
        by_strategy = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0})
        for p in settled:
            key = p.strategy
            by_strategy[key]["pnl"] += p.pnl
            if p.actual_result == "win":
                by_strategy[key]["w"] += 1
            else:
                by_strategy[key]["l"] += 1
        analysis["by_strategy"] = {k: {**v, "win_rate": v["w"]/(v["w"]+v["l"]) if (v["w"]+v["l"]) > 0 else 0} for k, v in by_strategy.items()}

        # 3. 팀 등급별 분석
        by_quality = defaultdict(lambda: {"w": 0, "l": 0, "pnl": 0})
        for p in settled:
            key = p.team_quality or "unknown"
            by_quality[key]["pnl"] += p.pnl
            if p.actual_result == "win":
                by_quality[key]["w"] += 1
            else:
                by_quality[key]["l"] += 1
        analysis["by_quality"] = {k: {**v, "win_rate": v["w"]/(v["w"]+v["l"]) if (v["w"]+v["l"]) > 0 else 0} for k, v in by_quality.items()}

        # 4. 엣지 구간별 캘리브레이션
        edge_bins = [(0.03, 0.05), (0.05, 0.08), (0.08, 0.12), (0.12, 0.20), (0.20, 1.0)]
        edge_cal = {}
        for lo, hi in edge_bins:
            subset = [p for p in settled if lo <= p.edge < hi]
            if subset:
                wr = sum(1 for p in subset if p.actual_result == "win") / len(subset)
                edge_cal[f"{lo:.0%}-{hi:.0%}"] = {"n": len(subset), "win_rate": wr, "avg_edge": sum(p.edge for p in subset)/len(subset)}
        analysis["edge_calibration"] = edge_cal

        # 5. Closing Line Value (CLV)
        # model_prob > market_prob인 방향으로 배팅했을 때 — 시장이 나중에 우리 방향으로 움직였는지
        # (settled 데이터에선 최종 가격 = 0 or 1이므로 간접 추정)
        clv_positive = sum(1 for p in settled if p.edge > 0 and p.actual_result == "win")
        clv_total = sum(1 for p in settled if p.edge > 0)
        analysis["clv"] = {
            "edge_positive_win_rate": clv_positive / clv_total if clv_total > 0 else 0,
            "sample": clv_total,
        }

        return analysis

    def generate_weekly_review(self) -> Dict:
        """주간 성과 리뷰"""
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        recent = [p for p in self.predictions if p.timestamp >= week_ago and p.settled]

        if not recent:
            return {"message": "이번 주 정산된 배팅 없음"}

        wins = sum(1 for p in recent if p.actual_result == "win")
        total_pnl = sum(p.pnl for p in recent)
        total_bet = sum(p.bet_amount for p in recent)

        review = {
            "period": f"{week_ago[:10]} ~ {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "total_bets": len(recent),
            "wins": wins,
            "losses": len(recent) - wins,
            "win_rate": wins / len(recent),
            "total_pnl": round(total_pnl, 2),
            "roi": total_pnl / total_bet if total_bet > 0 else 0,
        }

        # 가장 큰 수익/손실
        if recent:
            best = max(recent, key=lambda p: p.pnl)
            worst = min(recent, key=lambda p: p.pnl)
            review["best_trade"] = {"team": best.team, "pnl": best.pnl, "edge": best.edge}
            review["worst_trade"] = {"team": worst.team, "pnl": worst.pnl, "edge": worst.edge}

        # 개선 제안
        suggestions = []
        if review["win_rate"] < 0.40:
            suggestions.append("승률이 낮음 — 엣지 임계값을 높여보세요")
        if review["total_pnl"] < 0:
            suggestions.append("손실 중 — 배팅 금액을 줄이거나 모델 보정 필요")
        if wins > 0 and (len(recent) - wins) > wins * 2:
            suggestions.append("승률 대비 패가 많음 — 언더독 배팅 비중 재검토")
        review["suggestions"] = suggestions

        return review

    def send_weekly_discord(self):
        """주간 리뷰 Discord 발송"""
        webhook = os.environ.get("DISCORD_WEBHOOK_URL")
        if not webhook:
            return

        review = self.generate_weekly_review()
        if "message" in review:
            return  # 데이터 없음

        color = 0x4CAF50 if review.get("total_pnl", 0) >= 0 else 0xF44336
        suggestions = "\n".join(f"- {s}" for s in review.get("suggestions", [])) or "없음"

        embed = {
            "title": "📊 주간 성과 리뷰",
            "color": color,
            "fields": [
                {"name": "기간", "value": review["period"], "inline": False},
                {"name": "배팅 수", "value": f"{review['total_bets']}건 ({review['wins']}W/{review['losses']}L)", "inline": True},
                {"name": "승률", "value": f"{review['win_rate']:.1%}", "inline": True},
                {"name": "수익", "value": f"${review['total_pnl']:+.2f}", "inline": True},
                {"name": "ROI", "value": f"{review.get('roi', 0):+.1%}", "inline": True},
                {"name": "최고 거래", "value": f"{review.get('best_trade',{}).get('team','')} ${review.get('best_trade',{}).get('pnl',0):+.2f}", "inline": True},
                {"name": "최악 거래", "value": f"{review.get('worst_trade',{}).get('team','')} ${review.get('worst_trade',{}).get('pnl',0):+.2f}", "inline": True},
                {"name": "개선 제안", "value": suggestions, "inline": False},
            ],
            "footer": {"text": "에러 트래킹 시스템 | 주간 자동 리뷰"},
        }
        req.post(webhook, json={"embeds": [embed]}, timeout=10)


# ============================================================================
# 메인 실행
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="Error Tracker")
    parser.add_argument("--weekly", action="store_true", help="주간 리뷰 생성")
    parser.add_argument("--analyze", action="store_true", help="전체 분석")
    parser.add_argument("--demo", action="store_true", help="데모 데이터로 테스트")
    args = parser.parse_args()

    tracker = ErrorTracker()

    if args.demo:
        # 데모 데이터 생성
        import random
        random.seed(42)
        for i in range(30):
            pred = tracker.log_prediction({
                "id": f"DEMO-{i:03d}",
                "sport": random.choice(["NBA", "MLB"]),
                "strategy": random.choice(["pregame", "live"]),
                "team": random.choice(["Celtics", "Lakers", "Thunder", "Spurs"]),
                "opponent": "Opponent",
                "model_prob": random.uniform(0.3, 0.7),
                "market_prob": random.uniform(0.2, 0.6),
                "edge": random.uniform(0.03, 0.20),
                "bet_amount": random.uniform(2, 5),
                "confidence": random.choice(["high", "medium", "low"]),
                "team_quality": random.choice(["elite", "good", "average"]),
            })
            won = random.random() < pred.model_prob
            tracker.log_result(pred.id, won, pred.bet_amount * 2 if won else -pred.bet_amount)
        print(f"데모 데이터 {len(tracker.predictions)}건 생성")

    print("=" * 60)
    print("Error Tracker & Learning System")
    print(f"총 기록: {len(tracker.predictions)}건")
    print(f"정산: {sum(1 for p in tracker.predictions if p.settled)}건")
    print("=" * 60)

    if args.weekly:
        review = tracker.generate_weekly_review()
        print(json.dumps(review, indent=2, ensure_ascii=False))
        tracker.send_weekly_discord()
        print("주간 리뷰 Discord 발송 완료")

    if args.analyze or args.demo:
        analysis = tracker.analyze_errors()
        print(f"\n--- 전체 분석 ---")
        print(f"  총 거래: {analysis.get('total', 0)}")
        print(f"  승률: {analysis.get('win_rate', 0):.1%}")
        print(f"  총 수익: ${analysis.get('total_pnl', 0):+.2f}")

        print(f"\n  전략별:")
        for k, v in analysis.get("by_strategy", {}).items():
            print(f"    {k}: {v['w']}W/{v['l']}L ({v['win_rate']:.1%}) PnL=${v['pnl']:+.2f}")

        print(f"\n  팀 등급별:")
        for k, v in analysis.get("by_quality", {}).items():
            print(f"    {k}: {v['w']}W/{v['l']}L ({v['win_rate']:.1%}) PnL=${v['pnl']:+.2f}")

        print(f"\n  엣지 캘리브레이션:")
        for k, v in analysis.get("edge_calibration", {}).items():
            print(f"    {k}: 승률 {v['win_rate']:.1%} (n={v['n']}, avg_edge={v['avg_edge']:.1%})")
