#!/usr/bin/env python3
"""
MLB Kalshi Market Collector
=============================
War Machine MLB

Collects MLB prop markets from Kalshi API, parses tickers,
and stores in mlb_kalshi_markets + mlb_kalshi_prices tables.

Usage:
    python mlb/scripts/mlb_market_collector.py              # one-shot
    python mlb/scripts/mlb_market_collector.py --monitor     # continuous (15min)
"""

import sys
import os
import re
import json
import time
import logging
import argparse
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

from db import get_connection, put_connection
from kalshi_client import KalshiConfig, create_client
from tz import ET

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Prop type mapping from ticker prefix
PROP_PREFIX_MAP = {
    'KXMLBKS': 'strikeouts',
    'KXMLBHIT': 'hits',
    'KXMLBTB': 'total_bases',
    'KXMLBHR': 'home_runs',
    'KXMLBHRR': 'hrr',
    'KXMLBRFI': 'first_inning_run',
    'KXMLBTOTALRUNS': 'total_runs',
    'KXMLBTEAMTOTALRUNS': 'team_runs',
    'KXMLBSPREAD': 'spread',
    'KXMLBF5TOTALRUNS': 'f5_total_runs',
    'KXMLBF5SPREAD': 'f5_spread',
    'KXMLBF5': 'f5_winner',
    'KXMLBGAME': 'game_winner',
}

# Month abbreviation to number
MONTH_MAP = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4,
    'MAY': 5, 'JUN': 6, 'JUL': 7, 'AUG': 8,
    'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}


def parse_ticker(ticker):
    """
    Parse MLB Kalshi ticker into components.

    Example: KXMLBKS-26APR051310CHCCLE-CHCECABRERA30-6
    Returns dict with prop_type, game_date, game_time, away_team, home_team,
    player_team, player_name, jersey_number, line.
    """
    result = {
        'prop_type': '', 'game_date': None, 'game_time': None,
        'away_team': '', 'home_team': '',
        'player_team': '', 'player_name': '', 'jersey_number': None, 'line': None,
    }

    parts = ticker.split('-')
    if len(parts) < 2:
        return result

    # Prop type: everything before first '-', after 'KXMLB'
    prefix = parts[0]
    # Find the longest matching prefix
    matched_type = ''
    matched_prefix = ''
    for pfx, ptype in sorted(PROP_PREFIX_MAP.items(), key=lambda x: -len(x[0])):
        if prefix.startswith(pfx):
            matched_type = ptype
            matched_prefix = pfx
            break
    result['prop_type'] = matched_type or prefix.replace('KXMLB', '').lower()

    # Date/time/teams from second part
    date_part = parts[1] if len(parts) > 1 else ''
    # Pattern: 26APR051310CHCCLE
    m = re.match(r'(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]{3,})([A-Z]{2,3})$', date_part)
    if not m:
        # Try without time (futures markets)
        m2 = re.match(r'(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]{6})', date_part)
        if m2:
            yy, mon, dd, time_str, matchup = m2.groups()
            month_num = MONTH_MAP.get(mon, 1)
            try:
                result['game_date'] = date(2000 + int(yy), month_num, int(dd))
            except ValueError:
                pass
            try:
                result['game_time'] = f"{time_str[:2]}:{time_str[2:]}"
            except (IndexError, ValueError):
                pass
            result['away_team'] = matchup[:3]
            result['home_team'] = matchup[3:6]
    else:
        yy, mon, dd, time_str = m.group(1), m.group(2), m.group(3), m.group(4)
        teams_str = m.group(5) + m.group(6)
        month_num = MONTH_MAP.get(mon, 1)
        try:
            result['game_date'] = date(2000 + int(yy), month_num, int(dd))
        except ValueError:
            pass
        try:
            result['game_time'] = f"{time_str[:2]}:{time_str[2:]}"
        except (IndexError, ValueError):
            pass
        # Teams: first 3 chars = away, next 3 = home (but total can be 6+)
        if len(teams_str) >= 6:
            result['away_team'] = teams_str[:3]
            result['home_team'] = teams_str[3:6]

    # Player info from third part: CHCECABRERA30
    if len(parts) > 2:
        player_part = parts[2]
        # Team prefix (3 chars) + player name + jersey
        pm = re.match(r'([A-Z]{2,3})([A-Z][A-Z]+?)(\d+)$', player_part)
        if pm:
            result['player_team'] = pm.group(1)
            result['player_name'] = pm.group(2).title()
            try:
                result['jersey_number'] = int(pm.group(3))
            except ValueError:
                pass

    # Line from last part
    if len(parts) > 3:
        try:
            result['line'] = float(parts[-1])
        except (ValueError, TypeError):
            pass
    elif len(parts) == 3 and result['player_name'] == '':
        # Might be a team market (no player)
        try:
            result['line'] = float(parts[-1])
        except (ValueError, TypeError):
            pass

    return result


def collect_markets():
    """
    Sync MLB markets from the shared markets/price_snapshots tables
    (already collected by NBA market_recorder) into mlb_kalshi_markets
    and mlb_kalshi_prices tables with parsed ticker data.
    """
    logger.info("[Markets] Syncing MLB markets from shared DB tables...")

    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()

    # Read MLB markets from shared markets + price_snapshots tables
    cur.execute("""
        SELECT m.ticker, m.title, m.status,
               p.yes_bid, p.yes_ask, p.yes_price, p.timestamp as price_time
        FROM markets m
        LEFT JOIN price_snapshots p ON m.ticker = p.ticker
            AND p.id = (SELECT MAX(id) FROM price_snapshots WHERE ticker = m.ticker)
        WHERE m.ticker LIKE 'KXMLB%%'
          AND m.status = 'active'
    """)
    rows = cur.fetchall()
    logger.info(f"[Markets] Found {len(rows)} active MLB markets in shared DB.")

    markets_inserted = 0
    prices_inserted = 0
    now = datetime.now(timezone.utc)

    # Switch to non-dict cursor for inserts
    conn2 = get_connection()
    cur2 = conn2.cursor()

    for row in rows:
        ticker = row['ticker']
        parsed = parse_ticker(ticker)

        try:
            cur2.execute("""
                INSERT INTO mlb_kalshi_markets
                    (market_id, ticker, prop_type, game_date, game_time,
                     away_team, home_team, player_team, player_name,
                     jersey_number, line, status, result)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker) DO UPDATE SET
                    status = EXCLUDED.status,
                    updated_at = NOW()
            """, (
                ticker, ticker, parsed['prop_type'],
                parsed['game_date'], parsed['game_time'],
                parsed['away_team'], parsed['home_team'],
                parsed['player_team'], parsed['player_name'],
                parsed['jersey_number'], parsed['line'],
                row['status'], '',
            ))
            markets_inserted += 1
        except Exception as e:
            conn2.rollback()
            continue

        # Price snapshot
        yes_bid = row.get('yes_bid') or 0
        yes_ask = row.get('yes_ask') or 0
        no_bid = 1 - yes_ask if yes_ask else 0
        no_ask = 1 - yes_bid if yes_bid else 0
        spread = round((yes_ask - yes_bid) * 100, 1) if yes_ask > 0 and yes_bid > 0 else 0

        if yes_bid > 0 or yes_ask > 0:
            try:
                cur2.execute("""
                    INSERT INTO mlb_kalshi_prices
                        (ticker, yes_bid, yes_ask, no_bid, no_ask, spread_cents, volume, snapshot_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (ticker, yes_bid, yes_ask, no_bid, no_ask, spread, 0, now))
                prices_inserted += 1
            except Exception as e:
                conn2.rollback()

    conn2.commit()
    put_connection(conn2)
    put_connection(conn)

    logger.info(f"[Markets] Stored {markets_inserted} markets, {prices_inserted} price snapshots.")
    return markets_inserted, prices_inserted


def print_summary():
    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) as cnt FROM mlb_kalshi_markets")
    total = cur.fetchone()['cnt']

    cur.execute("""
        SELECT prop_type, COUNT(*) as cnt
        FROM mlb_kalshi_markets
        GROUP BY prop_type
        ORDER BY cnt DESC
    """)
    props = cur.fetchall()

    cur.execute("SELECT COUNT(*) as cnt FROM mlb_kalshi_prices")
    prices = cur.fetchone()['cnt']

    print()
    print("=" * 60)
    print("  MLB MARKET COLLECTION SUMMARY")
    print("=" * 60)
    print(f"  Total markets:         {total:>8,}")
    print(f"  Total price snapshots: {prices:>8,}")
    print()
    print("  Prop type breakdown:")
    for p in props:
        print(f"    {p['prop_type']:25s} {p['cnt']:>6,}")
    print("=" * 60)

    put_connection(conn)


def main():
    parser = argparse.ArgumentParser(description="MLB Kalshi Market Collector")
    parser.add_argument("--monitor", action="store_true", help="Continuous collection every 15 min")
    parser.add_argument("--interval", type=int, default=900, help="Interval in seconds (default: 900)")
    args = parser.parse_args()

    if args.monitor:
        logger.info(f"[Monitor] Starting continuous collection every {args.interval}s...")
        while True:
            try:
                collect_markets()
            except Exception as e:
                logger.error(f"[Monitor] Collection error: {e}")
            time.sleep(args.interval)
    else:
        collect_markets()
        print_summary()


if __name__ == "__main__":
    main()
