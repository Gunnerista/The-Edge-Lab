#!/usr/bin/env python3
"""
MLB Strikeout Forward Test
============================
War Machine MLB

Scans today's Kalshi MLB strikeout markets, runs model predictions,
and records predictions with edge to mlb_predictions table.

Usage:
    python mlb/scripts/mlb_forward_test.py
    python mlb/scripts/mlb_forward_test.py --date 2026-04-05
"""

import sys
import os
import io
import re
import logging
import argparse
from datetime import datetime, date, timezone

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

from db import get_connection, put_connection
from tz import ET
from mlb_features import build_live_features
from mlb_model import MLBStrikeoutModel
from mlb_schedule import get_today_starters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Config (from mlb_config.yaml values)
MIN_EDGE = 0.20
YES_PREMIUM = 0.05
MAX_SPREAD_CENTS = 20
MIN_PRICE = 0.05
MAX_PRICE = 0.95
CONFIG_VERSION = "mlb_v1_strikeouts"


def _match_player(cur, team, jersey_number):
    """Match ticker player to DB player via team + jersey."""
    if not team or not jersey_number:
        return None
    cur.execute(
        "SELECT mlb_api_id, name FROM mlb_players WHERE team = %s AND jersey_number = %s LIMIT 1",
        (team, jersey_number)
    )
    row = cur.fetchone()
    return row if row else None


def _is_confirmed_starter(player_name, team, starters):
    """
    Check if player is confirmed starter for their team today.

    Tries exact match first, then last-name match.
    Returns (is_starter, actual_starter_name).
    """
    actual = starters.get(team)
    if not actual:
        return False, 'TBD/no game'

    # Exact match
    if player_name.lower() == actual.lower():
        return True, actual

    # Last name match
    player_last = player_name.split()[-1].lower() if player_name else ''
    actual_last = actual.split()[-1].lower() if actual else ''
    if player_last and actual_last and player_last == actual_last:
        return True, actual

    return False, actual


def _date_to_ticker_pattern(d):
    """Convert date to Kalshi ticker date pattern: 26APR05."""
    from tz import ticker_date_pattern
    return ticker_date_pattern(d)


def run_mlb_forward_test(target_date=None):
    """Run forward test on today's MLB strikeout markets."""
    if target_date is None:
        target_date = datetime.now(ET).date()

    date_pattern = _date_to_ticker_pattern(target_date)
    logger.info(f"[MLB Forward] Date: {target_date} (pattern: {date_pattern})")

    # Load model
    model = MLBStrikeoutModel()
    try:
        model.load()
    except Exception as e:
        logger.error(f"[MLB Forward] Cannot load model: {e}")
        return

    # Fetch confirmed starters
    try:
        starters = get_today_starters(target_date.strftime('%m/%d/%Y'))
        logger.info(f"[MLB Forward] Confirmed starters: {len(starters)} teams")
    except Exception as e:
        logger.error(f"[MLB Forward] Cannot fetch starters: {e}")
        starters = {}

    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()

    # Get today's strikeout markets with latest prices from shared tables
    cur.execute("""
        SELECT m.ticker, m.title,
               p.yes_bid, p.yes_ask, p.yes_price
        FROM markets m
        JOIN price_snapshots p ON m.ticker = p.ticker
            AND p.id = (SELECT MAX(id) FROM price_snapshots WHERE ticker = m.ticker)
        WHERE m.ticker LIKE %s
          AND m.status = 'active'
          AND p.yes_bid > 0 AND p.yes_ask > 0
    """, (f"KXMLBKS-{date_pattern}%",))
    markets = cur.fetchall()
    logger.info(f"[MLB Forward] Found {len(markets)} strikeout markets with prices")

    # Also get parsed market data from mlb_kalshi_markets
    cur.execute("""
        SELECT ticker, player_name, player_team, jersey_number, line,
               away_team, home_team
        FROM mlb_kalshi_markets
        WHERE prop_type = 'strikeouts' AND game_date = %s
    """, (target_date,))
    parsed_markets = {r['ticker']: r for r in cur.fetchall()}

    # Get already-predicted tickers to avoid duplicates on re-run
    cur.execute(
        "SELECT ticker FROM mlb_predictions WHERE game_date = %s AND result IS DISTINCT FROM 'INVALID'",
        (target_date,)
    )
    already_predicted = {r['ticker'] for r in cur.fetchall()}

    predictions = []
    skipped_spread = 0
    skipped_price = 0
    skipped_player = 0
    skipped_starter = 0
    skipped_features = 0
    skipped_dup = 0

    for mkt in markets:
        ticker = mkt['ticker']

        # Duplicate check
        if ticker in already_predicted:
            skipped_dup += 1
            continue

        yes_bid = mkt['yes_bid']
        yes_ask = mkt['yes_ask']
        spread_cents = round((yes_ask - yes_bid) * 100, 1)

        # Spread filter
        if spread_cents > MAX_SPREAD_CENTS:
            skipped_spread += 1
            continue

        # Price filter
        mid_price = (yes_bid + yes_ask) / 2
        if mid_price < MIN_PRICE or mid_price > MAX_PRICE:
            skipped_price += 1
            continue

        # Get parsed data
        parsed = parsed_markets.get(ticker)
        if not parsed or not parsed['line']:
            continue

        line = parsed['line']
        player_team = parsed['player_team']
        jersey = parsed['jersey_number']

        # Match player in DB
        player = _match_player(cur, player_team, jersey)
        if not player:
            skipped_player += 1
            continue

        bbref_id = str(player['mlb_api_id'])
        player_name = player['name']

        # Starter verification gate
        if starters:
            is_starter, actual = _is_confirmed_starter(player_name, player_team, starters)
            if not is_starter:
                logger.info(
                    f"[MLB Forward] SKIPPED: {player_name} not confirmed starter for "
                    f"{player_team} (actual: {actual})"
                )
                skipped_starter += 1
                continue

        # Build features
        features = build_live_features(bbref_id, target_date, '', line)
        if features is None:
            skipped_features += 1
            continue

        # Predict
        cal_prob = model.predict_proba(features)

        # Determine side and edge
        # YES = over the line
        yes_entry = yes_ask
        no_entry = 1 - yes_bid
        yes_edge = cal_prob - yes_entry
        no_edge = (1 - cal_prob) - no_entry

        # Apply YES premium
        yes_edge_adj = yes_edge - YES_PREMIUM

        # Pick better side
        if yes_edge_adj >= no_edge:
            side = 'YES'
            edge = yes_edge
            edge_adj = yes_edge_adj
            entry_price = yes_entry
        else:
            side = 'NO'
            edge = no_edge
            edge_adj = no_edge  # no premium on NO side
            entry_price = no_entry

        # Edge threshold
        if edge_adj < MIN_EDGE:
            continue

        # Record prediction
        conn2 = get_connection()
        cur2 = conn2.cursor()
        try:
            cur2.execute("""
                INSERT INTO mlb_predictions
                    (ticker, prop_type, player_name, game_date, line,
                     model_prob, calibrated_prob, market_price, edge, side,
                     bet_placed, result)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, NULL)
            """, (
                ticker, 'strikeouts', player_name, target_date, line,
                round(cal_prob, 4), round(cal_prob, 4),
                round(entry_price, 4), round(edge, 4), side,
            ))
            conn2.commit()
            predictions.append({
                'ticker': ticker,
                'player': player_name,
                'line': line,
                'model_prob': cal_prob,
                'market_price': entry_price,
                'edge': edge,
                'edge_adj': edge_adj,
                'side': side,
                'spread': spread_cents,
            })
        except Exception as e:
            conn2.rollback()
            logger.debug(f"[MLB Forward] Insert error for {ticker}: {e}")
        finally:
            put_connection(conn2)

    put_connection(conn)

    # Summary
    print()
    print("=" * 70)
    print(f"  MLB STRIKEOUT FORWARD TEST - {target_date}")
    print("=" * 70)
    print(f"  Markets scanned:       {len(markets)}")
    print(f"  Skipped (spread>20c):  {skipped_spread}")
    print(f"  Skipped (price):       {skipped_price}")
    print(f"  Skipped (no player):   {skipped_player}")
    print(f"  Skipped (not starter): {skipped_starter}")
    print(f"  Skipped (no features): {skipped_features}")
    print(f"  Predictions recorded:  {len(predictions)}")

    if predictions:
        edges = [p['edge'] for p in predictions]
        yes_count = sum(1 for p in predictions if p['side'] == 'YES')
        no_count = len(predictions) - yes_count
        print()
        print(f"  Edge distribution:")
        print(f"    Min:  {min(edges)*100:.1f}%")
        print(f"    Avg:  {sum(edges)/len(edges)*100:.1f}%")
        print(f"    Max:  {max(edges)*100:.1f}%")
        print(f"  Side: YES={yes_count}, NO={no_count}")
        print()
        print(f"  {'Player':20s} {'Line':>5s} {'Side':>4s} {'Model':>6s} {'Price':>6s} {'Edge':>6s} {'Spread':>6s}")
        print(f"  {'-'*20} {'-'*5} {'-'*4} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
        for p in sorted(predictions, key=lambda x: -x['edge']):
            print(f"  {p['player']:20s} {p['line']:5.1f} {p['side']:>4s} {p['model_prob']:5.1%} {p['market_price']:5.1%} {p['edge']:+5.1%} {p['spread']:5.1f}c")

    print("=" * 70)
    return predictions


def main():
    parser = argparse.ArgumentParser(description="MLB Strikeout Forward Test")
    parser.add_argument("--date", type=str, help="Date YYYY-MM-DD")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date) if args.date else None
    run_mlb_forward_test(target_date)


if __name__ == "__main__":
    main()
