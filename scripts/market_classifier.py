#!/usr/bin/env python3
"""
Market Classifier - Phase 2: Auto-Classification & Liquidity Assessment
========================================================================
Prediction Market War Machine

Classifies all recorded markets by:
  1. Category: sports, politics, economics, crypto, weather, entertainment, other
  2. Sub-category: nba, mlb, nfl, president, fed_rate, etc.
  3. Market type: single, parlay, prop, game_winner, over_under, etc.
  4. Liquidity grade: A (very liquid) through F (dead)
  5. Tradability score: composite score for whether we should watch this market

Reads from / writes to: data/market_data.db (same DB as market_recorder)

Usage:
    # Classify all unclassified markets
    python scripts/market_classifier.py

    # Re-classify everything (force)
    python scripts/market_classifier.py --force

    # Show classification summary
    python scripts/market_classifier.py --summary

    # Show tradeable markets only
    python scripts/market_classifier.py --tradeable
"""

import sys
import re
import sqlite3
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "market_data.db"


# ============================================================================
# Classification Rules
# ============================================================================

@dataclass
class MarketClassification:
    """Classification result for a single market."""
    category: str          # sports, politics, economics, crypto, weather, entertainment, other
    sub_category: str      # nba, mlb, president, fed_rate, etc.
    market_type: str       # single, parlay, prop, game_winner, over_under, etc.
    liquidity_grade: str   # A, B, C, D, F
    tradeable: bool        # Should we actively watch this market?
    confidence: float      # How confident are we in the classification (0-1)


# Ticker prefix -> (category, sub_category, market_type)
# Order matters: longer prefixes checked first
TICKER_RULES: List[Tuple[str, str, str, str]] = [
    # ---- Sports: Parlays / Multi-leg ----
    ("KXMVESPORTSMULTIGAME",    "sports",       "parlay",       "parlay"),
    ("KXMVENBASINGLEGAME",      "sports",       "nba",          "parlay"),
    ("KXMVECBCHAMPIONSHIP",     "sports",       "college",      "parlay"),
    ("KXMVESPORTS",             "sports",       "multi",        "parlay"),

    # ---- Sports: NBA ----
    ("KXNBAGAME",               "sports",       "nba",          "game_winner"),
    ("KXNBAPTS",                "sports",       "nba",          "prop_points"),
    ("KXNBAREB",                "sports",       "nba",          "prop_rebounds"),
    ("KXNBAAST",                "sports",       "nba",          "prop_assists"),
    ("KXNBASTL",                "sports",       "nba",          "prop_steals"),
    ("KXNBABLK",                "sports",       "nba",          "prop_blocks"),
    ("KXNBA3PT",                "sports",       "nba",          "prop_threes"),
    ("KXNBATOV",                "sports",       "nba",          "prop_turnovers"),
    ("KXNBAOU",                 "sports",       "nba",          "over_under"),
    ("KXNBASPRD",               "sports",       "nba",          "spread"),
    ("KXNBA",                   "sports",       "nba",          "other"),

    # ---- Sports: MLB ----
    ("KXMLBGAME",               "sports",       "mlb",          "game_winner"),
    ("KXMLBOU",                 "sports",       "mlb",          "over_under"),
    ("KXMLBHITS",               "sports",       "mlb",          "prop_hits"),
    ("KXMLBHR",                 "sports",       "mlb",          "prop_hr"),
    ("KXMLBRBI",                "sports",       "mlb",          "prop_rbi"),
    ("KXMLBSO",                 "sports",       "mlb",          "prop_strikeouts"),
    ("KXMLB",                   "sports",       "mlb",          "other"),

    # ---- Sports: NFL ----
    ("KXNFLGAME",               "sports",       "nfl",          "game_winner"),
    ("KXNFLOU",                 "sports",       "nfl",          "over_under"),
    ("KXNFL",                   "sports",       "nfl",          "other"),

    # ---- Sports: NHL ----
    ("KXNHLGAME",               "sports",       "nhl",          "game_winner"),
    ("KXNHL",                   "sports",       "nhl",          "other"),

    # ---- Sports: Soccer ----
    ("KXSOC",                   "sports",       "soccer",       "other"),

    # ---- Sports: MMA / Tennis / Golf ----
    ("KXMMA",                   "sports",       "mma",          "other"),
    ("KXTEN",                   "sports",       "tennis",       "other"),
    ("KXGOL",                   "sports",       "golf",         "other"),

    # ---- Sports: College ----
    ("KXCBB",                   "sports",       "college_bb",   "other"),
    ("KXCFB",                   "sports",       "college_fb",   "other"),

    # ---- Cross-category (parlays mixing sports + other) ----
    ("KXMVECROSSCATEGORY",      "parlay_cross", "mixed",        "parlay"),

    # ---- Politics ----
    ("KXPRESWIN",               "politics",     "president",    "winner"),
    ("KXPRES",                  "politics",     "president",    "other"),
    ("KXSENATE",                "politics",     "senate",       "other"),
    ("KXSEN",                   "politics",     "senate",       "other"),
    ("KXHOUSE",                 "politics",     "house",        "other"),
    ("KXHOU",                   "politics",     "house",        "other"),
    ("KXGOV",                   "politics",     "governor",     "other"),
    ("KXELEC",                  "politics",     "election",     "other"),
    ("KXPOPVOTE",               "politics",     "popular_vote", "other"),
    ("KXTRUMP",                 "politics",     "trump",        "other"),
    ("KXBIDEN",                 "politics",     "biden",        "other"),
    ("KXCABINET",               "politics",     "cabinet",      "other"),
    ("KXSCOTUS",                "politics",     "scotus",       "other"),
    ("KXIMPEACH",               "politics",     "impeach",      "other"),

    # ---- Economics ----
    ("KXFEDRATE",               "economics",    "fed_rate",     "rate_decision"),
    ("KXFED",                   "economics",    "fed",          "other"),
    ("KXCPI",                   "economics",    "cpi",          "indicator"),
    ("KXGDP",                   "economics",    "gdp",          "indicator"),
    ("KXJOBS",                  "economics",    "jobs",         "indicator"),
    ("KXJOB",                   "economics",    "jobs",         "indicator"),
    ("KXUNEMPLOY",              "economics",    "unemployment", "indicator"),
    ("KXRATE",                  "economics",    "rates",        "other"),
    ("KXSP5",                   "economics",    "sp500",        "index"),
    ("KXNAS",                   "economics",    "nasdaq",       "index"),
    ("KXDOW",                   "economics",    "dow",          "index"),
    ("KXRUS",                   "economics",    "russell",      "index"),
    ("KXTARIFF",                "economics",    "tariff",       "policy"),
    ("KXRECESSION",             "economics",    "recession",    "other"),
    ("KXDEBT",                  "economics",    "debt",         "other"),
    ("KXOIL",                   "economics",    "oil",          "commodity"),
    ("KXGOLD",                  "economics",    "gold",         "commodity"),
    ("KXGAS",                   "economics",    "gas",          "commodity"),

    # ---- Crypto ----
    ("KXBTC",                   "crypto",       "bitcoin",      "price"),
    ("KXETH",                   "crypto",       "ethereum",     "price"),
    ("KXSOL",                   "crypto",       "solana",       "price"),
    ("KXCRYPTO",                "crypto",       "general",      "other"),

    # ---- Weather ----
    ("KXHURRICANE",             "weather",      "hurricane",    "other"),
    ("KXHUR",                   "weather",      "hurricane",    "other"),
    ("KXTEMP",                  "weather",      "temperature",  "other"),
    ("KXWEA",                   "weather",      "general",      "other"),
    ("KXSNOW",                  "weather",      "snow",         "other"),
    ("KXRAIN",                  "weather",      "rain",         "other"),

    # ---- Entertainment / Culture ----
    ("KXOSCAR",                 "entertainment", "oscars",      "other"),
    ("KXEMMY",                  "entertainment", "emmys",       "other"),
    ("KXGRAM",                  "entertainment", "grammys",     "other"),
    ("KXTV",                    "entertainment", "tv",          "other"),
    ("KXMOVIE",                 "entertainment", "movies",      "other"),

    # ---- Science / Tech ----
    ("KXAI",                    "tech",         "ai",           "other"),
    ("KXSPACE",                 "tech",         "space",        "other"),
    ("KXTSLA",                  "tech",         "tesla",        "stock"),
]


# ============================================================================
# Liquidity Grading
# ============================================================================

def grade_liquidity(
    yes_bid: float,
    yes_ask: float,
    volume: int,
    open_interest: int,
) -> str:
    """
    Grade market liquidity from A (excellent) to F (dead).

    A: Tight spread, high volume - immediately tradeable
    B: Reasonable spread, decent volume - tradeable with patience
    C: Wide spread or low volume - tradeable in small size
    D: Very wide spread, minimal activity - observation only
    F: No bids, no activity - dead market
    """
    spread = (yes_ask - yes_bid) if yes_bid > 0 and yes_ask > 0 else 1.0
    spread_cents = spread * 100

    score = 0

    # Spread scoring (0-4 points)
    if spread_cents <= 3:
        score += 4
    elif spread_cents <= 6:
        score += 3
    elif spread_cents <= 10:
        score += 2
    elif spread_cents <= 20:
        score += 1

    # Volume scoring (0-3 points)
    if volume >= 500:
        score += 3
    elif volume >= 100:
        score += 2
    elif volume >= 10:
        score += 1

    # Bid presence (0-2 points)
    if yes_bid > 0 and yes_ask > 0:
        score += 2
    elif yes_bid > 0 or yes_ask > 0:
        score += 1

    # Open interest (0-1 point)
    if open_interest >= 50:
        score += 1

    # Grade mapping
    if score >= 8:
        return "A"
    elif score >= 6:
        return "B"
    elif score >= 4:
        return "C"
    elif score >= 2:
        return "D"
    else:
        return "F"


def is_tradeable(
    liquidity_grade: str,
    market_type: str,
    yes_bid: float,
    yes_ask: float,
    volume: int,
) -> bool:
    """
    Determine if a market is worth actively monitoring for trading.

    Criteria:
    - Must have at least D liquidity grade
    - Must have a bid and ask present
    - Parlays are generally less tradeable (lower priority)
    """
    if liquidity_grade == "F":
        return False
    if yes_bid <= 0 or yes_ask <= 0:
        return False
    # Parlays need higher liquidity bar
    if market_type == "parlay" and liquidity_grade not in ("A", "B"):
        return False
    return True


# ============================================================================
# Classifier Engine
# ============================================================================

class MarketClassifier:
    """Classifies markets from the market_data.db database."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self._ensure_columns()

    def _ensure_columns(self):
        """Add classification columns to markets table if they don't exist."""
        cur = self.conn.cursor()
        existing = {row[1] for row in cur.execute("PRAGMA table_info(markets)").fetchall()}

        new_cols = {
            "sub_category": "TEXT DEFAULT ''",
            "market_type": "TEXT DEFAULT ''",
            "liquidity_grade": "TEXT DEFAULT ''",
            "tradeable": "INTEGER DEFAULT 0",
            "classified_at": "TEXT DEFAULT ''",
        }

        for col, typedef in new_cols.items():
            if col not in existing:
                cur.execute(f"ALTER TABLE markets ADD COLUMN {col} {typedef}")
                logger.info(f"Added column: {col}")

        self.conn.commit()

    def classify_market(self, ticker: str, title: str, event_ticker: str) -> Tuple[str, str, str, float]:
        """
        Classify a single market by ticker and title.
        Returns (category, sub_category, market_type, confidence).
        """
        ticker_upper = ticker.upper()

        # Try ticker prefix rules (most reliable)
        for prefix, category, sub_cat, mtype in TICKER_RULES:
            if ticker_upper.startswith(prefix):
                return category, sub_cat, mtype, 0.95

        # Fallback: title keyword matching
        title_lower = (title or "").lower()
        event_lower = (event_ticker or "").lower()
        combined = f"{title_lower} {event_lower}"

        # Title-based classification (lower confidence)
        title_rules = [
            (["nba", "basketball", "celtics", "lakers", "warriors"], "sports", "nba", "other"),
            (["mlb", "baseball", "yankees", "dodgers", "home run"], "sports", "mlb", "other"),
            (["nfl", "football", "touchdown", "quarterback"], "sports", "nfl", "other"),
            (["nhl", "hockey", "goals", "assists"], "sports", "nhl", "other"),
            (["soccer", "epl", "premier league", "la liga", "champions league"], "sports", "soccer", "other"),
            (["president", "election", "democrat", "republican", "congress"], "politics", "general", "other"),
            (["fed", "interest rate", "fomc", "monetary"], "economics", "fed", "other"),
            (["cpi", "inflation", "consumer price"], "economics", "cpi", "indicator"),
            (["gdp", "gross domestic"], "economics", "gdp", "indicator"),
            (["jobs", "employment", "nonfarm", "payroll"], "economics", "jobs", "indicator"),
            (["bitcoin", "btc", "ethereum", "eth", "crypto"], "crypto", "general", "other"),
            (["hurricane", "tornado", "temperature", "weather"], "weather", "general", "other"),
            (["oscar", "emmy", "grammy", "box office"], "entertainment", "general", "other"),
            (["s&p", "nasdaq", "dow jones", "stock market"], "economics", "index", "other"),
        ]

        for keywords, category, sub_cat, mtype in title_rules:
            if any(kw in combined for kw in keywords):
                return category, sub_cat, mtype, 0.6

        return "other", "unknown", "unknown", 0.3

    def classify_all(self, force: bool = False) -> Dict[str, int]:
        """
        Classify all markets in the database.

        Args:
            force: If True, re-classify already classified markets

        Returns:
            Dict with classification counts by category.
        """
        cur = self.conn.cursor()

        if force:
            cur.execute("SELECT ticker, title, event_ticker FROM markets")
        else:
            cur.execute(
                "SELECT ticker, title, event_ticker FROM markets WHERE classified_at = ''"
            )

        rows = cur.fetchall()
        if not rows:
            logger.info("No markets to classify")
            return {}

        logger.info(f"Classifying {len(rows)} markets...")

        # Get latest price data for liquidity grading
        price_cache = {}
        cur.execute("""
            SELECT ticker,
                   MAX(yes_bid) as yes_bid,
                   MAX(yes_ask) as yes_ask,
                   MAX(volume) as volume,
                   MAX(open_interest) as oi
            FROM price_snapshots
            WHERE source = 'rest'
            GROUP BY ticker
        """)
        for row in cur.fetchall():
            price_cache[row[0]] = {
                "yes_bid": row[1] or 0,
                "yes_ask": row[2] or 0,
                "volume": row[3] or 0,
                "open_interest": row[4] or 0,
            }

        now = datetime.now(timezone.utc).isoformat()
        counts: Dict[str, int] = {}
        updates = []

        for ticker, title, event_ticker in rows:
            category, sub_cat, mtype, conf = self.classify_market(ticker, title, event_ticker)

            # Liquidity grading
            prices = price_cache.get(ticker, {})
            yb = prices.get("yes_bid", 0)
            ya = prices.get("yes_ask", 0)
            vol = prices.get("volume", 0)
            oi = prices.get("open_interest", 0)

            liq_grade = grade_liquidity(yb, ya, vol, oi)
            trd = is_tradeable(liq_grade, mtype, yb, ya, vol)

            updates.append((category, sub_cat, mtype, liq_grade, int(trd), now, ticker))
            counts[category] = counts.get(category, 0) + 1

        # Batch update
        cur.executemany(
            """UPDATE markets SET
                category = ?, sub_category = ?, market_type = ?,
                liquidity_grade = ?, tradeable = ?, classified_at = ?
               WHERE ticker = ?""",
            updates,
        )
        self.conn.commit()

        logger.info(f"Classified {len(updates)} markets: {counts}")
        return counts

    def get_summary(self) -> dict:
        """Get classification summary statistics."""
        cur = self.conn.cursor()

        summary = {}

        # By category
        cur.execute("""
            SELECT category, COUNT(*), SUM(tradeable)
            FROM markets WHERE classified_at != ''
            GROUP BY category ORDER BY COUNT(*) DESC
        """)
        summary["by_category"] = [
            {"category": r[0] or "unclassified", "count": r[1], "tradeable": r[2]}
            for r in cur.fetchall()
        ]

        # By sub_category (top 20)
        cur.execute("""
            SELECT category, sub_category, COUNT(*), SUM(tradeable)
            FROM markets WHERE classified_at != ''
            GROUP BY category, sub_category
            ORDER BY COUNT(*) DESC LIMIT 20
        """)
        summary["by_sub_category"] = [
            {"category": r[0], "sub_category": r[1], "count": r[2], "tradeable": r[3]}
            for r in cur.fetchall()
        ]

        # By liquidity grade
        cur.execute("""
            SELECT liquidity_grade, COUNT(*)
            FROM markets WHERE classified_at != ''
            GROUP BY liquidity_grade ORDER BY liquidity_grade
        """)
        summary["by_liquidity"] = [
            {"grade": r[0] or "?", "count": r[1]}
            for r in cur.fetchall()
        ]

        # Tradeable markets
        cur.execute("SELECT COUNT(*) FROM markets WHERE tradeable = 1")
        summary["total_tradeable"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM markets WHERE classified_at != ''")
        summary["total_classified"] = cur.fetchone()[0]

        return summary

    def get_tradeable_markets(self, category: str = None, limit: int = 50) -> List[dict]:
        """Get markets flagged as tradeable, optionally filtered by category."""
        cur = self.conn.cursor()

        query = """
            SELECT m.ticker, m.title, m.category, m.sub_category, m.market_type,
                   m.liquidity_grade, m.close_time, m.expiration_time,
                   s.yes_bid, s.yes_ask, s.volume, s.open_interest
            FROM markets m
            LEFT JOIN (
                SELECT ticker, yes_bid, yes_ask, volume, open_interest,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY timestamp DESC) as rn
                FROM price_snapshots WHERE source = 'rest'
            ) s ON m.ticker = s.ticker AND s.rn = 1
            WHERE m.tradeable = 1
        """
        params = []
        if category:
            query += " AND m.category = ?"
            params.append(category)

        query += " ORDER BY s.volume DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        return [
            {
                "ticker": r[0],
                "title": r[1],
                "category": r[2],
                "sub_category": r[3],
                "market_type": r[4],
                "liquidity_grade": r[5],
                "close_time": r[6],
                "expiration_time": r[7],
                "yes_bid": r[8] or 0,
                "yes_ask": r[9] or 0,
                "volume": r[10] or 0,
                "open_interest": r[11] or 0,
            }
            for r in cur.fetchall()
        ]

    def close(self):
        self.conn.close()


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Market Classifier - Categorize & Grade Kalshi Markets"
    )
    parser.add_argument("--force", action="store_true", help="Re-classify all markets")
    parser.add_argument("--summary", action="store_true", help="Show classification summary")
    parser.add_argument("--tradeable", action="store_true", help="List tradeable markets")
    parser.add_argument("--category", type=str, help="Filter by category (with --tradeable)")
    parser.add_argument("--limit", type=int, default=50, help="Max results for --tradeable")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    classifier = MarketClassifier()

    if args.summary:
        summary = classifier.get_summary()
        print("=" * 60)
        print("Market Classification Summary")
        print("=" * 60)
        print(f"\nTotal classified: {summary['total_classified']}")
        print(f"Total tradeable:  {summary['total_tradeable']}")

        print("\n--- By Category ---")
        for item in summary["by_category"]:
            print(f"  {item['category']:20s} {item['count']:>6}  (tradeable: {item['tradeable']})")

        print("\n--- By Sub-Category (top 20) ---")
        for item in summary["by_sub_category"]:
            print(f"  {item['category']:15s} / {item['sub_category']:15s} {item['count']:>6}  (tradeable: {item['tradeable']})")

        print("\n--- By Liquidity Grade ---")
        for item in summary["by_liquidity"]:
            print(f"  Grade {item['grade']}: {item['count']}")

    elif args.tradeable:
        markets = classifier.get_tradeable_markets(
            category=args.category, limit=args.limit
        )
        print("=" * 60)
        print(f"Tradeable Markets ({len(markets)} found)")
        print("=" * 60)
        for m in markets:
            spread = (m["yes_ask"] - m["yes_bid"]) * 100 if m["yes_bid"] > 0 else 0
            print(
                f"  [{m['liquidity_grade']}] {m['category']:12s} | "
                f"Bid={m['yes_bid']*100:.0f}c Ask={m['yes_ask']*100:.0f}c "
                f"Spread={spread:.0f}c Vol={m['volume']} | "
                f"{m['title'][:50]}"
            )

    else:
        # Classify
        counts = classifier.classify_all(force=args.force)
        if counts:
            print("\nClassification complete:")
            for cat, cnt in sorted(counts.items(), key=lambda x: -x[1]):
                print(f"  {cat}: {cnt}")

        # Auto-show summary
        summary = classifier.get_summary()
        print(f"\nTotal tradeable: {summary['total_tradeable']}")
        print(f"Total classified: {summary['total_classified']}")

    classifier.close()


if __name__ == "__main__":
    main()
