#!/usr/bin/env python3
"""
Backfill Settled NBA Prop Markets from Kalshi API
===================================================
Prediction Market War Machine

Fetches all settled KXNBAPTS/KXNBAREB/KXNBAAST markets from Kalshi API
and stores them in market_data.db (both markets + settled_markets tables).

Usage:
    python scripts/backfill_settled.py              # all 3 prop types
    python scripts/backfill_settled.py --dry-run     # count only, no DB write
    python scripts/backfill_settled.py --props PTS   # just points
"""

import sys
import time
import json
import sqlite3
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kalshi_client import create_client

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_DB = PROJECT_ROOT / "data" / "market_data.db"

SERIES_MAP = {
    "PTS": "KXNBAPTS",
    "REB": "KXNBAREB",
    "AST": "KXNBAAST",
}


def fetch_all_settled(client, series_ticker: str) -> list[dict]:
    """Paginate through all settled markets for a series."""
    all_markets = []
    cursor = None
    page = 0

    while True:
        page += 1
        resp = client.get_markets(
            limit=1000,
            status="settled",
            series_ticker=series_ticker,
            cursor=cursor,
        )

        markets = resp.get("markets", [])
        if not markets:
            break

        all_markets.extend(markets)
        logger.info(f"[Backfill] {series_ticker} page {page}: +{len(markets)} (total: {len(all_markets)})")

        cursor = resp.get("cursor", "")
        if not cursor:
            break

        time.sleep(0.3)  # rate limit

    return all_markets


def store_markets(db_path: Path, markets: list[dict]):
    """Upsert settled markets into both tables."""
    conn = sqlite3.connect(str(db_path))
    now = datetime.now(timezone.utc).isoformat()

    inserted_markets = 0
    inserted_settled = 0
    updated_result = 0

    for m in markets:
        ticker = m.get("ticker", "")
        result = m.get("result", "")

        # Upsert into markets table
        existing = conn.execute("SELECT result FROM markets WHERE ticker = ?", (ticker,)).fetchone()

        if existing is None:
            conn.execute("""
                INSERT INTO markets (ticker, event_ticker, title, subtitle, category,
                    status, close_time, expiration_time, result, first_seen, last_updated,
                    sub_category, market_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                m.get("event_ticker", ""),
                m.get("title", ""),
                m.get("subtitle", ""),
                m.get("category", ""),
                m.get("status", "settled"),
                m.get("close_time", ""),
                m.get("expiration_time", ""),
                result,
                now,
                now,
                m.get("sub_category", ""),
                m.get("market_type", ""),
            ))
            inserted_markets += 1
        else:
            if existing[0] != result:
                conn.execute(
                    "UPDATE markets SET result = ?, status = 'settled', last_updated = ? WHERE ticker = ?",
                    (result, now, ticker)
                )
                updated_result += 1

        # Upsert into settled_markets table
        exists_settled = conn.execute(
            "SELECT 1 FROM settled_markets WHERE ticker = ?", (ticker,)
        ).fetchone()

        if not exists_settled:
            conn.execute("""
                INSERT INTO settled_markets (ticker, event_ticker, title, category, result,
                    last_yes_bid, last_yes_ask, last_yes_mid, volume, close_time, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker,
                m.get("event_ticker", ""),
                m.get("title", ""),
                m.get("category", ""),
                result,
                m.get("yes_bid", 0),
                m.get("yes_ask", 0),
                (m.get("yes_bid", 0) + m.get("yes_ask", 0)) / 2,
                m.get("volume", 0),
                m.get("close_time", ""),
                now,
            ))
            inserted_settled += 1

    conn.commit()
    conn.close()

    return inserted_markets, updated_result, inserted_settled


def main():
    parser = argparse.ArgumentParser(description="Backfill Settled NBA Props")
    parser.add_argument("--props", nargs="+", default=["PTS", "REB", "AST"],
                        choices=["PTS", "REB", "AST"], help="Which prop types")
    parser.add_argument("--dry-run", action="store_true", help="Count only, no DB write")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    client = create_client()
    logger.info("[Backfill] Connected to Kalshi API")

    total_fetched = 0
    total_yes = 0
    total_no = 0

    all_markets = []

    for prop in args.props:
        series = SERIES_MAP[prop]
        logger.info(f"[Backfill] Fetching settled {series} markets...")
        markets = fetch_all_settled(client, series)
        all_markets.extend(markets)

        yes_count = sum(1 for m in markets if m.get("result") == "yes")
        no_count = sum(1 for m in markets if m.get("result") == "no")

        total_fetched += len(markets)
        total_yes += yes_count
        total_no += no_count

        logger.info(f"[Backfill] {series}: {len(markets)} settled (yes={yes_count}, no={no_count})")

    if args.dry_run:
        print(f"\n[DRY RUN] Would store {total_fetched} markets (yes={total_yes}, no={total_no})")
        # Show date range
        if all_markets:
            close_times = sorted(m.get("close_time", "") for m in all_markets if m.get("close_time"))
            print(f"Date range: {close_times[0][:10]} to {close_times[-1][:10]}")
        return

    # Store to DB
    inserted_m, updated_r, inserted_s = store_markets(MARKET_DB, all_markets)

    print()
    print("=" * 70)
    print(f"  BACKFILL COMPLETE")
    print("=" * 70)
    print(f"  Fetched:           {total_fetched} settled markets")
    print(f"  Results:           yes={total_yes}, no={total_no}")
    print(f"  New in markets:    {inserted_m}")
    print(f"  Updated result:    {updated_r}")
    print(f"  New in settled:    {inserted_s}")
    print("=" * 70)
    print()


if __name__ == "__main__":
    main()
