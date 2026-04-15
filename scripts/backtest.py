#!/usr/bin/env python3
"""
Backtest: NBA Prop Model vs 45K Settled Markets
=================================================
Prediction Market War Machine

Runs nba_model on every settled KXNBAPTS/REB/AST market and computes:
  - Brier score (overall + per prop type)
  - Calibration table (10% buckets)
  - Edge-based ROI simulation (where price data exists)

NOTE: Uses current-season player stats to predict past games.
      This creates look-ahead bias for early-season games.
      True out-of-sample requires rolling window — this is Phase 1 baseline.

Usage:
    python scripts/backtest.py
    python scripts/backtest.py --limit 1000    # quick test
"""

import sys
import json
import logging
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nba_model import NBAModel
from db import get_connection, put_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_FILE = PROJECT_ROOT / "data" / "backtest_report.json"


def load_settled_markets(limit: int = 0) -> list[dict]:
    """Load all settled NBA prop markets with optional price data."""
    conn = get_connection(dict_cursor=True)

    limit_clause = f"LIMIT {limit}" if limit else ""

    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT s.ticker, s.title, s.result, s.close_time,
                   p.yes_price as kalshi_price
            FROM settled_markets s
            LEFT JOIN (
                SELECT ticker, yes_price,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY id DESC) as rn
                FROM price_snapshots
            ) p ON s.ticker = p.ticker AND p.rn = 1
            WHERE s.ticker LIKE 'KXNBAPTS%'
               OR s.ticker LIKE 'KXNBAREB%'
               OR s.ticker LIKE 'KXNBAAST%'
            ORDER BY s.close_time DESC
            {limit_clause}
        """)
        rows = cur.fetchall()
    finally:
        put_connection(conn)
    return [dict(r) for r in rows]


def run_backtest(limit: int = 0):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    markets = load_settled_markets(limit)
    logger.info(f"[Backtest] Loaded {len(markets)} settled markets")

    model = NBAModel()

    # Counters
    results = []
    skipped = 0
    skip_reasons = defaultdict(int)
    t0 = time.time()
    batch_size = 5000

    for i, mkt in enumerate(markets):
        if (i + 1) % batch_size == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            logger.info(
                f"[Backtest] {i+1}/{len(markets)} processed "
                f"({rate:.0f}/s, {len(results)} predictions, {skipped} skipped)"
            )

        ticker = mkt["ticker"]
        title = mkt["title"]
        actual = mkt["result"]  # "yes" or "no"

        prediction = model.process_kalshi_nba_ticker(ticker, title, 0.5)

        if prediction is None:
            skipped += 1
            # Classify skip reason
            if not title:
                skip_reasons["no_title"] += 1
            else:
                skip_reasons["no_player_data"] += 1
            continue

        # Determine prop type from ticker
        if ticker.startswith("KXNBAPTS"):
            prop_type = "PTS"
        elif ticker.startswith("KXNBAREB"):
            prop_type = "REB"
        else:
            prop_type = "AST"

        actual_binary = 1 if actual == "yes" else 0

        results.append({
            "ticker": ticker,
            "prop_type": prop_type,
            "model_prob": prediction.model_prob,
            "actual": actual_binary,
            "kalshi_price": mkt.get("kalshi_price"),
            "confidence": prediction.confidence,
            "line": prediction.line,
            "projected": prediction.projected_value,
        })

    model.close()
    elapsed = time.time() - t0

    logger.info(
        f"[Backtest] Done in {elapsed:.1f}s: "
        f"{len(results)} predictions, {skipped} skipped"
    )

    # ================================================================
    # METRICS
    # ================================================================

    # --- Brier Score ---
    def brier(preds):
        if not preds:
            return float("nan")
        return sum((p["model_prob"] - p["actual"]) ** 2 for p in preds) / len(preds)

    overall_brier = brier(results)

    brier_by_prop = {}
    for prop in ["PTS", "REB", "AST"]:
        subset = [r for r in results if r["prop_type"] == prop]
        brier_by_prop[prop] = {"brier": round(brier(subset), 4), "n": len(subset)}

    # --- Calibration Table ---
    buckets = []
    for lo in range(0, 100, 10):
        hi = lo + 10
        lo_f, hi_f = lo / 100, hi / 100
        bucket = [r for r in results if lo_f <= r["model_prob"] < hi_f]
        if not bucket:
            buckets.append({
                "range": f"{lo}-{hi}%",
                "n": 0,
                "avg_model_prob": None,
                "actual_hit_rate": None,
                "gap": None,
            })
            continue

        avg_prob = sum(r["model_prob"] for r in bucket) / len(bucket)
        hit_rate = sum(r["actual"] for r in bucket) / len(bucket)
        buckets.append({
            "range": f"{lo}-{hi}%",
            "n": len(bucket),
            "avg_model_prob": round(avg_prob, 4),
            "actual_hit_rate": round(hit_rate, 4),
            "gap": round(hit_rate - avg_prob, 4),
        })

    # --- Edge ROI Simulation ---
    # Only for markets with Kalshi price data
    priced = [r for r in results if r["kalshi_price"] is not None and r["kalshi_price"] > 0]

    def edge_roi(preds, min_edge: float):
        """
        Simulate flat $1 bets on markets where model edge > min_edge.
        Buy YES if model_prob > kalshi_price + min_edge.
        Buy NO if model_prob < kalshi_price - min_edge.
        """
        bets = 0
        pnl = 0.0
        wins = 0

        for p in preds:
            edge = p["model_prob"] - p["kalshi_price"]

            if edge > min_edge:
                # Buy YES at kalshi_price
                bets += 1
                if p["actual"] == 1:
                    pnl += (1.0 - p["kalshi_price"])  # win (1 - cost)
                    wins += 1
                else:
                    pnl -= p["kalshi_price"]  # lose cost

            elif edge < -min_edge:
                # Buy NO at (1 - kalshi_price)
                bets += 1
                if p["actual"] == 0:
                    pnl += p["kalshi_price"]  # win kalshi_price
                    wins += 1
                else:
                    pnl -= (1.0 - p["kalshi_price"])  # lose (1 - kalshi_price)

        roi = (pnl / bets * 100) if bets > 0 else 0
        return {"bets": bets, "wins": wins, "pnl": round(pnl, 2), "roi": round(roi, 2)}

    edge_results = {}
    for threshold in [0.05, 0.10, 0.15, 0.20]:
        edge_results[f">{threshold:.0%}"] = edge_roi(priced, threshold)

    # --- Confidence breakdown ---
    conf_stats = {}
    for conf in ["high", "medium", "low"]:
        subset = [r for r in results if r["confidence"] == conf]
        if subset:
            conf_stats[conf] = {
                "n": len(subset),
                "brier": round(brier(subset), 4),
                "hit_rate": round(sum(r["actual"] for r in subset) / len(subset), 4),
            }

    # ================================================================
    # REPORT
    # ================================================================

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_markets": len(markets),
        "predictions": len(results),
        "skipped": skipped,
        "skip_reasons": dict(skip_reasons),
        "elapsed_seconds": round(elapsed, 1),
        "overall_brier": round(overall_brier, 4),
        "brier_by_prop": brier_by_prop,
        "calibration": buckets,
        "edge_roi": edge_results,
        "priced_markets": len(priced),
        "confidence_breakdown": conf_stats,
    }

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # ================================================================
    # PRINT
    # ================================================================

    print()
    print("=" * 78)
    print(f"  BACKTEST REPORT: NBA Prop Model vs {len(results)} Settled Markets")
    print("=" * 78)
    print()
    print(f"  Total markets:  {len(markets)}")
    print(f"  Predictions:    {len(results)}")
    print(f"  Skipped:        {skipped} ({', '.join(f'{k}={v}' for k,v in skip_reasons.items())})")
    print(f"  Time:           {elapsed:.1f}s")
    print()

    # Brier
    target = "PASS" if overall_brier < 0.20 else "FAIL"
    print(f"  OVERALL BRIER SCORE: {overall_brier:.4f}  [{target} target < 0.20]")
    print()
    for prop, stats in brier_by_prop.items():
        print(f"    {prop}: {stats['brier']:.4f}  (n={stats['n']})")
    print()

    # Calibration
    print("  CALIBRATION TABLE")
    print("  " + "-" * 68)
    print(f"  {'Bucket':>10s} | {'N':>6s} | {'Avg Model':>10s} | {'Actual Hit':>10s} | {'Gap':>8s}")
    print("  " + "-" * 68)
    for b in buckets:
        if b["n"] == 0:
            print(f"  {b['range']:>10s} | {b['n']:>6d} |        n/a |        n/a |      n/a")
        else:
            print(
                f"  {b['range']:>10s} | {b['n']:>6d} | "
                f"{b['avg_model_prob']:>9.1%} | "
                f"{b['actual_hit_rate']:>9.1%} | "
                f"{b['gap']:>+7.1%}"
            )
    print("  " + "-" * 68)
    print()

    # Edge ROI
    print(f"  EDGE ROI SIMULATION (n={len(priced)} markets with price data)")
    print("  " + "-" * 55)
    print(f"  {'Edge Thresh':>12s} | {'Bets':>5s} | {'Wins':>5s} | {'PnL':>8s} | {'ROI':>7s}")
    print("  " + "-" * 55)
    for thresh, stats in edge_results.items():
        pnl_str = f"${stats['pnl']:+.2f}"
        print(
            f"  {thresh:>12s} | {stats['bets']:>5d} | {stats['wins']:>5d} | "
            f"{pnl_str:>8s} | {stats['roi']:>+6.1f}%"
        )
    print("  " + "-" * 55)
    print()

    # Confidence
    print("  CONFIDENCE BREAKDOWN")
    for conf, stats in conf_stats.items():
        print(f"    {conf:>6s}: n={stats['n']:>5d}, brier={stats['brier']:.4f}")
    print()
    print(f"  Full report: {REPORT_FILE}")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(description="Backtest NBA Prop Model")
    parser.add_argument("--limit", type=int, default=0, help="Limit markets (0=all)")
    args = parser.parse_args()
    run_backtest(args.limit)


if __name__ == "__main__":
    main()
