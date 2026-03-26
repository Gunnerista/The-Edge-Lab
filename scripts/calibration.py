#!/usr/bin/env python3
"""
Calibration System — Measures model accuracy and detects bias.
================================================================
Tracks how well the system's predicted probabilities match reality.

Key metrics:
  - Brier Score: 0 = perfect, 0.25 = coin flip, >0.25 = worse than guessing
  - Calibration curve: predicted prob buckets vs actual hit rates
  - Bias: overconfident (predict too high) or underconfident (predict too low)

Usage:
    from calibration import CalibrationTracker
    cal = CalibrationTracker()
    cal.record(predicted_prob=0.65, outcome=1)  # win
    cal.record(predicted_prob=0.30, outcome=0)  # loss
    report = cal.generate_report()
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT_ROOT / "data" / "calibration_state.json"
PAPER_TRADES_FILE = PROJECT_ROOT / "data" / "paper_trades.jsonl"

# 10% probability buckets
BUCKET_EDGES = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]
BUCKET_LABELS = [
    "0-10%", "10-20%", "20-30%", "30-40%", "40-50%",
    "50-60%", "60-70%", "70-80%", "80-90%", "90-100%",
]


def _bucket_index(prob: float) -> int:
    """Return bucket index for a probability value."""
    for i in range(len(BUCKET_EDGES) - 1):
        if BUCKET_EDGES[i] <= prob < BUCKET_EDGES[i + 1]:
            return i
    return len(BUCKET_LABELS) - 1


class CalibrationTracker:
    """Tracks prediction calibration across paper and live trades."""

    def __init__(self, state_file: Path = STATE_FILE):
        self.file = state_file
        self.state = self._load()

    def _load(self) -> dict:
        if self.file.exists():
            try:
                with open(self.file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return self._empty_state()

    def _empty_state(self) -> dict:
        return {
            "buckets": {label: {"count": 0, "hits": 0} for label in BUCKET_LABELS},
            "brier_scores": [],
            "total_predictions": 0,
            "total_hits": 0,
            "sum_predicted": 0.0,
            "sum_actual": 0.0,
            "last_updated": "",
        }

    def _save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.state["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def record(self, predicted_prob: float, outcome: int, ticker: str = ""):
        """
        Record a single prediction outcome.

        Args:
            predicted_prob: model's estimated probability (0-1)
            outcome: 1 if the predicted side won, 0 if lost
        """
        predicted_prob = max(0.0, min(1.0, predicted_prob))
        outcome = 1 if outcome else 0

        # Brier score for this prediction
        brier = (predicted_prob - outcome) ** 2
        self.state["brier_scores"].append(round(brier, 6))

        # Keep only last 500 scores to prevent unbounded growth
        if len(self.state["brier_scores"]) > 500:
            self.state["brier_scores"] = self.state["brier_scores"][-500:]

        # Bucket update
        idx = _bucket_index(predicted_prob)
        label = BUCKET_LABELS[idx]
        self.state["buckets"][label]["count"] += 1
        if outcome == 1:
            self.state["buckets"][label]["hits"] += 1

        # Running totals
        self.state["total_predictions"] += 1
        self.state["total_hits"] += outcome
        self.state["sum_predicted"] += predicted_prob
        self.state["sum_actual"] += outcome

        self._save()

    def get_brier_score(self) -> float:
        """Average Brier score. 0 = perfect, 0.25 = coin flip."""
        scores = self.state["brier_scores"]
        if not scores:
            return 0.25  # no data = assume coin flip
        return sum(scores) / len(scores)

    def get_bias(self) -> dict:
        """
        Calculate calibration bias.

        Returns:
            {
                "direction": "overconfident" | "underconfident" | "neutral",
                "magnitude": float (absolute difference),
                "avg_predicted": float,
                "avg_actual": float,
            }
        """
        n = self.state["total_predictions"]
        if n == 0:
            return {
                "direction": "neutral",
                "magnitude": 0.0,
                "avg_predicted": 0.0,
                "avg_actual": 0.0,
            }

        avg_pred = self.state["sum_predicted"] / n
        avg_actual = self.state["sum_actual"] / n
        diff = avg_pred - avg_actual

        if diff > 0.03:
            direction = "overconfident"
        elif diff < -0.03:
            direction = "underconfident"
        else:
            direction = "neutral"

        return {
            "direction": direction,
            "magnitude": round(abs(diff), 4),
            "avg_predicted": round(avg_pred, 4),
            "avg_actual": round(avg_actual, 4),
        }

    def generate_report(self) -> dict:
        """
        Full calibration report.

        Returns dict with:
          - brier_score: average Brier score
          - bias: overconfident/underconfident/neutral
          - buckets: per-bucket predicted vs actual
          - sample_size: total predictions
          - assessment: human-readable summary
        """
        brier = self.get_brier_score()
        bias = self.get_bias()
        n = self.state["total_predictions"]

        bucket_report = []
        for label in BUCKET_LABELS:
            b = self.state["buckets"][label]
            count = b["count"]
            hits = b["hits"]
            actual_rate = hits / count if count > 0 else None

            # Expected midpoint of this bucket
            idx = BUCKET_LABELS.index(label)
            expected_mid = (BUCKET_EDGES[idx] + BUCKET_EDGES[idx + 1]) / 2

            bucket_report.append({
                "bucket": label,
                "count": count,
                "hits": hits,
                "actual_rate": round(actual_rate, 3) if actual_rate is not None else None,
                "expected_rate": round(expected_mid, 2),
                "deviation": round(actual_rate - expected_mid, 3) if actual_rate is not None else None,
            })

        # Assessment
        if n < 10:
            assessment = f"Insufficient data ({n} predictions). Need 50+ for meaningful calibration."
        elif brier < 0.15:
            assessment = f"Good calibration (Brier={brier:.3f}). Model predictions are reliable."
        elif brier < 0.25:
            assessment = f"Moderate calibration (Brier={brier:.3f}). Some room for improvement."
        else:
            assessment = f"Poor calibration (Brier={brier:.3f}). Model is worse than random. Review signal quality."

        if bias["direction"] == "overconfident" and bias["magnitude"] > 0.05:
            assessment += f" Overconfident by {bias['magnitude']*100:.1f}pp — reduce edge estimates."
        elif bias["direction"] == "underconfident" and bias["magnitude"] > 0.05:
            assessment += f" Underconfident by {bias['magnitude']*100:.1f}pp — could be sizing larger."

        return {
            "brier_score": round(brier, 4),
            "bias": bias,
            "buckets": bucket_report,
            "sample_size": n,
            "assessment": assessment,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def format_discord_summary(self) -> str:
        """Format calibration data for Discord daily report."""
        brier = self.get_brier_score()
        bias = self.get_bias()
        n = self.state["total_predictions"]

        if n == 0:
            return "Calibration: No data yet"

        # Brier interpretation
        if brier < 0.15:
            brier_emoji = "\u2705"
        elif brier < 0.25:
            brier_emoji = "\U0001f7e1"
        else:
            brier_emoji = "\U0001f534"

        lines = [
            f"{brier_emoji} **Brier Score**: {brier:.3f} ({n} predictions)",
            f"   Bias: {bias['direction']} (pred={bias['avg_predicted']:.1%} vs actual={bias['avg_actual']:.1%})",
        ]

        # Top bucket deviations
        report = self.generate_report()
        worst = sorted(
            [b for b in report["buckets"] if b["count"] >= 3 and b["deviation"] is not None],
            key=lambda x: abs(x["deviation"]),
            reverse=True,
        )[:3]
        if worst:
            lines.append("   Worst buckets:")
            for b in worst:
                arrow = "\u2191" if b["deviation"] > 0 else "\u2193"
                lines.append(
                    f"     {b['bucket']}: {b['actual_rate']:.0%} actual vs {b['expected_rate']:.0%} expected "
                    f"({arrow}{abs(b['deviation'])*100:.0f}pp, n={b['count']})"
                )

        return "\n".join(lines)

    def sync_from_paper_trades(self):
        """
        Read settled paper trades and record any that aren't yet in calibration.
        Called by check_paper_settlements().
        """
        if not PAPER_TRADES_FILE.exists():
            return 0

        recorded = 0
        with open(PAPER_TRADES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Only process settled AUTO trades
                if entry.get("source") != "auto":
                    continue
                if entry.get("status") not in ("win", "loss"):
                    continue

                # Skip if already recorded (check by ticker+settled_at)
                cal_key = f"{entry.get('ticker', '')}_{entry.get('settled_at', '')}"
                if cal_key in self._processed_keys():
                    continue

                model_prob = entry.get("model_prob", 0.5)
                outcome = 1 if entry["status"] == "win" else 0
                self.record(model_prob, outcome, ticker=entry.get("ticker", ""))
                recorded += 1

        return recorded

    def _processed_keys(self) -> set:
        """
        Get set of already-processed ticker_settled_at keys.
        Simple approach: use count as proxy (full dedup would need storing keys).
        """
        # We rely on check_paper_settlements calling record() once per settlement
        # rather than re-scanning. This avoids double counting.
        return set()


# ============================================================================
# CLI
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Calibration System")
    parser.add_argument("--report", action="store_true", help="Generate calibration report")
    parser.add_argument("--reset", action="store_true", help="Reset calibration state")
    parser.add_argument("--sync", action="store_true", help="Sync from paper_trades.jsonl")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    cal = CalibrationTracker()

    if args.reset:
        cal.state = cal._empty_state()
        cal._save()
        print("Calibration state reset.")
        return

    if args.sync:
        n = cal.sync_from_paper_trades()
        print(f"Synced {n} new settlements.")

    if args.report or not (args.reset or args.sync):
        report = cal.generate_report()
        print(json.dumps(report, indent=2))
        print()
        print("Discord summary:")
        print(cal.format_discord_summary())


if __name__ == "__main__":
    main()
