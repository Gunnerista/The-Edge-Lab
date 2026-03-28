#!/usr/bin/env python3
"""
Forward Test Runner — NBA Player Props
========================================
Prediction Market War Machine

Pulls active NBA prop markets from market_data.db for target dates,
runs nba_model.get_prob() on each, and logs predictions to prediction_log.jsonl.

Usage:
    python scripts/forward_test.py                    # today + tomorrow
    python scripts/forward_test.py --date 2026-03-28  # specific date
    python scripts/forward_test.py --days 3            # next 3 days
"""

import sys
import json
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nba_model import NBAModel
from tz import ET

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_DB = PROJECT_ROOT / "data" / "market_data.db"
LOG_FILE = PROJECT_ROOT / "data" / "prediction_log.jsonl"

# Prop types we model
PROP_PREFIXES = ["KXNBAPTS", "KXNBAREB", "KXNBAAST"]
PROP_TYPE_MAP = {"KXNBAPTS": "points", "KXNBAREB": "rebounds", "KXNBAAST": "assists"}


def date_to_ticker_pattern(d: date) -> str:
    """Convert date to ticker date segment pattern: 26MAR28 for 2026-03-28."""
    yy = str(d.year)[-2:]
    mon = d.strftime("%b").upper()
    dd = str(d.day)
    return f"{yy}{mon}{dd}"


def get_active_props(market_db: Path, dates: list[date]) -> list[dict]:
    """Fetch active NBA prop markets for given dates with latest prices."""
    conn = sqlite3.connect(str(market_db))
    conn.row_factory = sqlite3.Row

    all_props = []
    for d in dates:
        pattern = date_to_ticker_pattern(d)
        for prefix in PROP_PREFIXES:
            ticker_like = f"{prefix}-{pattern}%"
            rows = conn.execute("""
                SELECT m.ticker, m.title, m.status, m.close_time,
                       p.yes_price, p.yes_bid, p.yes_ask, p.timestamp as price_time
                FROM markets m
                LEFT JOIN price_snapshots p ON m.ticker = p.ticker
                  AND p.id = (SELECT MAX(id) FROM price_snapshots WHERE ticker = m.ticker)
                WHERE m.ticker LIKE ?
                  AND m.status = 'active'
                ORDER BY m.ticker
            """, (ticker_like,)).fetchall()

            for row in rows:
                all_props.append({
                    "ticker": row["ticker"],
                    "title": row["title"],
                    "status": row["status"],
                    "close_time": row["close_time"],
                    "kalshi_price": row["yes_price"],
                    "yes_bid": row["yes_bid"],
                    "yes_ask": row["yes_ask"],
                    "price_time": row["price_time"],
                    "prop_type": PROP_TYPE_MAP[prefix],
                    "game_date": d.isoformat(),
                })

    conn.close()
    return all_props


def run_forward_test(dates: list[date]):
    """Run model on all active NBA props for given dates and log predictions."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info(f"[ForwardTest] Dates: {[d.isoformat() for d in dates]}")

    # Get active props
    props = get_active_props(MARKET_DB, dates)
    logger.info(f"[ForwardTest] Found {len(props)} active NBA prop markets")

    if not props:
        logger.warning("[ForwardTest] No active props found. Run auto_trader to collect markets first.")
        return

    # Init model
    model = NBAModel()
    prediction_time = datetime.now(timezone.utc).isoformat()

    predictions = []
    skipped = 0

    for prop in props:
        ticker = prop["ticker"]
        title = prop["title"]
        kalshi_price = prop["kalshi_price"] or 0.0

        # Use the model's ticker parser + prediction
        result = model.process_kalshi_nba_ticker(ticker, title, kalshi_price)

        if not result:
            skipped += 1
            logger.debug(f"[ForwardTest] Skip (no prediction): {ticker}")
            continue

        edge = round(result.model_prob - kalshi_price, 4) if kalshi_price else None

        entry = {
            "ticker": ticker,
            "player": result.player_name,
            "prop_type": result.prop_type,
            "line": result.line,
            "projected_value": result.projected_value,
            "model_prob": result.model_prob,
            "kalshi_price": round(kalshi_price, 4) if kalshi_price else None,
            "edge": edge,
            "confidence": result.confidence,
            "game_date": prop["game_date"],
            "prediction_time": prediction_time,
            "reasoning": result.reasoning,
            "actual_result": None,
            "settled": False,
        }
        predictions.append(entry)

    model.close()

    # Write to JSONL
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        for entry in predictions:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(f"[ForwardTest] Logged {len(predictions)} predictions, skipped {skipped}")
    logger.info(f"[ForwardTest] Output: {LOG_FILE}")

    # Print summary
    print()
    print("=" * 80)
    print(f"  FORWARD TEST - {len(predictions)} predictions logged")
    print("=" * 80)

    # Sort by absolute edge
    by_edge = sorted(
        [p for p in predictions if p["edge"] is not None],
        key=lambda x: abs(x["edge"]),
        reverse=True,
    )

    for p in by_edge[:20]:
        direction = "OVER " if p["model_prob"] > 0.5 else "UNDER"
        edge_str = f"{p['edge']:+.1%}"
        print(
            f"  {direction} {p['player']:20s} {p['prop_type']:8s} {p['line']:5.1f} "
            f"| model={p['model_prob']:.1%} kalshi={p['kalshi_price']:.1%} "
            f"edge={edge_str:>6s} [{p['confidence']}]"
        )

    print("=" * 80)

    # Edge distribution
    edges = [p["edge"] for p in predictions if p["edge"] is not None]
    if edges:
        pos_edges = [e for e in edges if e > 0.05]
        neg_edges = [e for e in edges if e < -0.05]
        print(f"  Positive edge (>5%): {len(pos_edges)} props")
        print(f"  Negative edge (<-5%): {len(neg_edges)} props")
        print(f"  Mean edge: {sum(edges)/len(edges):+.1%}")
    print()


def main():
    parser = argparse.ArgumentParser(description="NBA Props Forward Test")
    parser.add_argument("--date", type=str, help="Specific date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=2, help="Number of days from today (default: 2)")
    args = parser.parse_args()

    if args.date:
        dates = [date.fromisoformat(args.date)]
    else:
        today = datetime.now(ET).date()  # ET date, not system local
        dates = [today + timedelta(days=i) for i in range(args.days)]

    run_forward_test(dates)


if __name__ == "__main__":
    main()
