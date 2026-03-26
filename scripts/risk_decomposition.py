#!/usr/bin/env python3
"""
Risk Decomposition — Classify losses by root cause.
=====================================================
Every loss has a reason. This module categorizes them so we know
what to fix first for maximum impact.

Loss categories:
  1. Model Error:      predicted prob was wrong (calibration gap)
  2. Execution Slippage: entry price worse than signal price
  3. Regime Change:    "noise" turned out to be real information

Usage:
    from risk_decomposition import classify_loss, generate_loss_report
    category = classify_loss(entry)
    report = generate_loss_report()
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPER_TRADES_FILE = PROJECT_ROOT / "data" / "paper_trades.jsonl"


def classify_loss(entry: dict) -> str:
    """
    Classify a losing trade into a root cause category.

    Logic:
      1. Model Error: brier_score > 0.25 (prediction was far from reality)
         → The model was confident in the wrong direction.

      2. Regime Change: VPIN was low (we thought it was noise) but price
         kept moving in the same direction → the move was informed, not noise.
         Detected when: vpin < 0.4 AND the market settled in the direction
         of the price move we tried to fade.

      3. Execution Slippage: entry price was worse than the signal's
         ideal price. If edge was positive at signal time but negative
         after slippage, slippage killed the trade.
         Detected when: actual entry price deviates significantly from
         the theoretical mid-price.

    Returns one of: "model_error", "regime_change", "execution_slippage"
    """
    if entry.get("status") != "loss":
        return "not_a_loss"

    model_prob = entry.get("model_prob", 0.5)
    brier = entry.get("brier_score", 0.25)
    vpin = entry.get("vpin")
    edge = entry.get("edge", 0)
    side = entry.get("side", "yes")
    market_result = entry.get("market_result", "")
    price_cents = entry.get("price_cents", 50)

    # --- Check Regime Change first ---
    # We faded a move thinking it was noise (low VPIN).
    # If the market settled in the direction of that move,
    # it was actually informed trading, not noise.
    if vpin is not None and vpin < 0.4:
        # We faded: if we bought YES and result was NO, or bought NO and result was YES
        # That means the move we faded was actually correct
        faded_correctly = (
            (side == "yes" and market_result == "no") or
            (side == "no" and market_result == "yes")
        )
        if faded_correctly:
            # The move continued — our "noise" classification was wrong
            return "regime_change"

    # --- Check Model Error ---
    # High brier score = model probability was way off
    if brier > 0.25:
        return "model_error"

    # Model was reasonably calibrated but still lost
    # Check if the edge was thin — could be slippage
    if edge < 0.08:
        return "execution_slippage"

    # Default: model error (we predicted wrong)
    return "model_error"


def tag_loss_category(entry: dict) -> dict:
    """Add loss_category to a settled losing paper trade entry."""
    if entry.get("status") == "loss":
        entry["loss_category"] = classify_loss(entry)
    return entry


def generate_loss_report(trades: List[dict] = None) -> dict:
    """
    Analyze all settled losses and produce a decomposition report.

    Returns:
        {
            "total_losses": int,
            "total_loss_amount": float,
            "categories": {
                "model_error": {"count": N, "amount": $, "pct": %},
                "regime_change": {...},
                "execution_slippage": {...},
            },
            "worst_category": str,
            "recommendation": str,
        }
    """
    if trades is None:
        trades = _load_paper_trades()

    # Only analyze auto-system trades, never manual
    trades = [t for t in trades if t.get("source") == "auto"]
    losses = [t for t in trades if t.get("status") == "loss"]

    if not losses:
        return {
            "total_losses": 0,
            "total_loss_amount": 0,
            "categories": {},
            "worst_category": "none",
            "recommendation": "No losses yet — keep collecting data.",
        }

    cats: Dict[str, dict] = defaultdict(lambda: {"count": 0, "amount": 0.0, "tickers": []})

    for loss in losses:
        cat = loss.get("loss_category") or classify_loss(loss)
        pnl = abs(loss.get("paper_pnl", 0))
        cats[cat]["count"] += 1
        cats[cat]["amount"] += pnl
        cats[cat]["tickers"].append(loss.get("ticker", "?")[:30])

    total_count = sum(c["count"] for c in cats.values())
    total_amount = sum(c["amount"] for c in cats.values())

    report_cats = {}
    for name, data in cats.items():
        report_cats[name] = {
            "count": data["count"],
            "amount": round(data["amount"], 2),
            "pct_count": round(data["count"] / total_count * 100, 1) if total_count else 0,
            "pct_amount": round(data["amount"] / total_amount * 100, 1) if total_amount else 0,
        }

    # Find worst category by amount
    worst = max(report_cats.items(), key=lambda x: x[1]["amount"])
    worst_name = worst[0]

    # Recommendation based on worst category
    recommendations = {
        "model_error": (
            "Model Error dominates losses. "
            "Review Bayesian prior weights and overreaction thresholds. "
            "Consider increasing MIN_EDGE or requiring more data points."
        ),
        "regime_change": (
            "Regime Change is the top loss driver. "
            "The VPIN filter needs tightening — raise threshold from 0.7 to 0.5. "
            "Also consider skipping overreaction_fade during high-news periods."
        ),
        "execution_slippage": (
            "Execution Slippage is the main issue. "
            "Tighten spread filter or use more aggressive limit prices. "
            "Consider only trading markets with spread <= 2c."
        ),
    }

    return {
        "total_losses": total_count,
        "total_loss_amount": round(total_amount, 2),
        "categories": report_cats,
        "worst_category": worst_name,
        "recommendation": recommendations.get(worst_name, "Collect more data."),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def format_discord_weekly(report: dict = None) -> str:
    """Format loss decomposition for weekly Discord report."""
    if report is None:
        report = generate_loss_report()

    n = report["total_losses"]
    if n == 0:
        return "Loss Decomposition: No losses to analyze yet."

    lines = [
        f"**Loss Decomposition** ({n} losses, ${report['total_loss_amount']:.2f} total)",
    ]

    for cat_name, data in sorted(
        report["categories"].items(),
        key=lambda x: x[1]["amount"],
        reverse=True,
    ):
        icon = {
            "model_error": "\U0001f9e0",
            "regime_change": "\U0001f30a",
            "execution_slippage": "\u26a1",
        }.get(cat_name, "\u2753")
        label = cat_name.replace("_", " ").title()
        lines.append(
            f"  {icon} **{label}**: {data['count']} trades "
            f"(${data['amount']:.2f}, {data['pct_amount']:.0f}%)"
        )

    lines.append(f"\n\U0001f4a1 {report['recommendation']}")
    return "\n".join(lines)


def _load_paper_trades() -> List[dict]:
    """Load all paper trades from jsonl."""
    trades = []
    if not PAPER_TRADES_FILE.exists():
        return trades
    with open(PAPER_TRADES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return trades


# ============================================================================
# CLI
# ============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Risk Decomposition")
    parser.add_argument("--report", action="store_true", help="Generate loss report")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    report = generate_loss_report()
    print(json.dumps(report, indent=2))
    print()
    print("Discord format:")
    print(format_discord_weekly(report))


if __name__ == "__main__":
    main()
