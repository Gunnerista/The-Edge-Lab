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

import re
import sys
import json
import logging
import argparse
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from dataclasses import asdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nba_model import NBAModel
from tz import ET
from db import get_connection, put_connection

try:
    import requests as _requests
except ImportError:
    _requests = None

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "data" / "prediction_log.jsonl"

# Prop types we model
PROP_PREFIXES = ["KXNBAPTS", "KXNBAREB", "KXNBAAST"]
PROP_TYPE_MAP = {"KXNBAPTS": "points", "KXNBAREB": "rebounds", "KXNBAAST": "assists"}
CONFIG_VERSION = "v3_prop_edge"

# Maps The Odds API full team name → Kalshi 3-letter abbreviation
_TEAM_NAME_TO_ABBR = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}


def fetch_game_spreads() -> dict:
    """
    Fetch today's NBA pre-game spreads from The Odds API.
    Returns dict: 6-char matchup code → absolute spread (float).
    Both orderings stored (e.g. "BOSCHA" and "CHABOS" → same value).
    Returns {} on error or missing ODDS_API_KEY.
    """
    import os
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key or _requests is None:
        return {}
    try:
        resp = _requests.get(
            "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/",
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": "spreads",
                "dateFormat": "iso",
                "oddsFormat": "american",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"[Spreads] Odds API {resp.status_code}: {resp.text[:200]}")
            return {}
        games = resp.json()
    except Exception as e:
        logger.warning(f"[Spreads] Odds API fetch failed: {e}")
        return {}

    spreads = {}
    for game in games:
        home_abbr = _TEAM_NAME_TO_ABBR.get(game.get("home_team", ""), "")
        away_abbr = _TEAM_NAME_TO_ABBR.get(game.get("away_team", ""), "")
        if not home_abbr or not away_abbr:
            continue

        # Find the favorite's absolute spread from the first bookmaker available
        spread_val = None
        for bm in game.get("bookmakers", []):
            for mkt in bm.get("markets", []):
                if mkt.get("key") != "spreads":
                    continue
                for outcome in mkt.get("outcomes", []):
                    pt = outcome.get("point")
                    if pt is not None and pt < 0:
                        spread_val = abs(pt)
                        break
                if spread_val is not None:
                    break
            if spread_val is not None:
                break

        if spread_val is None:
            continue

        # Store under both orderings so ticker lookup works regardless of convention
        spreads[away_abbr + home_abbr] = spread_val
        spreads[home_abbr + away_abbr] = spread_val

    logger.info(f"[Spreads] Fetched {len(spreads) // 2} game spreads from Odds API")
    return spreads


def extract_matchup(ticker: str):
    """Extract 6-char matchup code from NBA prop ticker (e.g. 'BOSCHA')."""
    # e.g. KXNBAPTS-26MAR29BOSCHA-CHABMILLER24-10
    m = re.search(r"-\d{2}[A-Z]{3}\d{1,2}([A-Z]{6})-", ticker)
    return m.group(1) if m else None


def date_to_ticker_pattern(d: date) -> str:
    """Convert date to ticker date segment pattern: 26MAR28 for 2026-03-28."""
    yy = str(d.year)[-2:]
    mon = d.strftime("%b").upper()
    dd = str(d.day)
    return f"{yy}{mon}{dd}"


def get_active_props(dates: list[date]) -> list[dict]:
    """Fetch active NBA prop markets for given dates with latest prices."""
    conn = get_connection(dict_cursor=True)

    all_props = []
    try:
        for d in dates:
            pattern = date_to_ticker_pattern(d)
            for prefix in PROP_PREFIXES:
                ticker_like = f"{prefix}-{pattern}%"
                cur = conn.cursor()
                cur.execute("""
                    SELECT m.ticker, m.title, m.status, m.close_time,
                           p.yes_price, p.yes_bid, p.yes_ask, p.timestamp as price_time
                    FROM markets m
                    LEFT JOIN price_snapshots p ON m.ticker = p.ticker
                      AND p.id = (SELECT MAX(id) FROM price_snapshots WHERE ticker = m.ticker)
                    WHERE m.ticker LIKE %s
                      AND m.status = 'active'
                    ORDER BY m.ticker
                """, (ticker_like,))
                rows = cur.fetchall()

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
    finally:
        put_connection(conn)
    return all_props


def run_forward_test(dates: list[date], source: str = "auto-cli"):
    """Run model on all active NBA props for given dates and log predictions.

    Args:
        dates: List of dates to run predictions for.
        source: "auto" = runner.py scheduler (cron/catch-up)
                "auto-cli" = CLI direct execution (python forward_test.py)
                "manual" = hand-entered bets (user placed on Kalshi directly)
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info(f"[ForwardTest] Dates: {[d.isoformat() for d in dates]}")

    # Fetch pre-game spreads (once per run)
    game_spreads = fetch_game_spreads()

    # Get active props
    props = get_active_props(dates)
    logger.info(f"[ForwardTest] Found {len(props)} active NBA prop markets")

    if not props:
        logger.warning("[ForwardTest] No active props found. Run auto_trader to collect markets first.")
        return

    # Init model
    model = NBAModel()
    prediction_time = datetime.now(timezone.utc).isoformat()

    predictions = []
    skipped = 0

    # Price filter: skip illiquid/extreme lines where betting is impossible
    MIN_KALSHI_PRICE = 0.05  # below 5c = no liquidity, extreme longshot
    MAX_KALSHI_PRICE = 0.95  # above 95c = near-certain, no edge available
    skipped_price = 0

    for prop in props:
        ticker = prop["ticker"]
        title = prop["title"]
        kalshi_price = prop["kalshi_price"] or 0.0

        # Skip markets with no price or extreme prices
        if not kalshi_price or kalshi_price < MIN_KALSHI_PRICE:
            skipped_price += 1
            logger.debug(f"[ForwardTest] Skip (price {kalshi_price:.2f} < {MIN_KALSHI_PRICE}): {ticker}")
            continue
        if kalshi_price > MAX_KALSHI_PRICE:
            skipped_price += 1
            logger.debug(f"[ForwardTest] Skip (price {kalshi_price:.2f} > {MAX_KALSHI_PRICE}): {ticker}")
            continue

        # Use the model's ticker parser + prediction
        result = model.process_kalshi_nba_ticker(ticker, title, kalshi_price)

        if not result:
            skipped += 1
            logger.debug(f"[ForwardTest] Skip (no prediction): {ticker}")
            continue

        edge = round(result.model_prob - kalshi_price, 4)

        matchup = extract_matchup(ticker)
        pre_game_spread = game_spreads.get(matchup) if matchup else None

        entry = {
            "ticker": ticker,
            "player": result.player_name,
            "prop_type": result.prop_type,
            "line": result.line,
            "projected_value": result.projected_value,
            "model_prob": result.model_prob,
            "kalshi_price": round(kalshi_price, 4),
            "edge": edge,
            "confidence": result.confidence,
            "game_date": prop["game_date"],
            "matchup": matchup,
            "pre_game_spread": pre_game_spread,
            "prediction_time": prediction_time,
            "source": source,
            "reasoning": result.reasoning,
            "actual_result": None,
            "settled": False,
            "config_version": CONFIG_VERSION,
        }
        predictions.append(entry)

    model.close()

    # Write to JSONL
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        for entry in predictions:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(f"[ForwardTest] Logged {len(predictions)} predictions, skipped {skipped} (no model), skipped {skipped_price} (price filter)")
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
