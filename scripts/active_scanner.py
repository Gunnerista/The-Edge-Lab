#!/usr/bin/env python3
"""
Active Scanner - Find Signals NOW Without Waiting
===================================================
Prediction Market War Machine

Three attack vectors that work immediately:

1. HISTORY MINER
   Pull settled markets from Kalshi, analyze settlement patterns,
   closing line value, and how prices behaved before resolution.

2. ORDERBOOK SCANNER
   Hit live orderbooks for real bid/ask depth. Find thin books
   where we can move the market, or stale quotes to pick off.

3. CROSS-MARKET ARBITRAGE
   Find events with mutually exclusive outcomes where YES prices
   don't sum to 100%. Buy underpriced, sell overpriced.

Usage:
    # Run all scanners
    python scripts/active_scanner.py

    # Specific scanner
    python scripts/active_scanner.py --scan history
    python scripts/active_scanner.py --scan orderbook
    python scripts/active_scanner.py --scan arbitrage

    # Export results
    python scripts/active_scanner.py --export data/scan_results.json
"""

import sys
import io
import json
import time
import math
import sqlite3
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

# Fix Windows encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kalshi_client import KalshiConfig, KalshiAPIClient, create_client
from signal_engine import KALSHI_FEE_RATE, net_ev, breakeven_prob

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "market_data.db"


# ============================================================================
# 1. HISTORY MINER - Learn from settled markets
# ============================================================================

class HistoryMiner:
    """
    Pull and analyze settled Kalshi markets.
    Finds patterns in how markets settle: favorites win rate,
    closing line accuracy, vig distribution.
    """

    def __init__(self, client: KalshiAPIClient, db_path: Path = DB_PATH):
        self.client = client
        self.conn = sqlite3.connect(str(db_path))
        self._ensure_table()

    def _ensure_table(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS settled_markets (
                ticker TEXT PRIMARY KEY,
                event_ticker TEXT DEFAULT '',
                title TEXT DEFAULT '',
                category TEXT DEFAULT '',
                result TEXT DEFAULT '',
                last_yes_bid REAL DEFAULT 0,
                last_yes_ask REAL DEFAULT 0,
                last_yes_mid REAL DEFAULT 0,
                volume REAL DEFAULT 0,
                close_time TEXT DEFAULT '',
                fetched_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_settled_result ON settled_markets(result);
            CREATE INDEX IF NOT EXISTS idx_settled_volume ON settled_markets(volume);
        """)
        self.conn.commit()

    def fetch_settled_markets(self, max_pages: int = 20) -> int:
        """Pull settled markets from Kalshi API into local DB."""
        cursor = None
        total = 0

        for page in range(max_pages):
            try:
                resp = self.client.get_markets(
                    limit=200, status="settled", cursor=cursor
                )
            except Exception as e:
                logger.error(f"Settled fetch page {page} failed: {e}")
                break

            markets = resp.get("markets", [])
            if not markets:
                break

            now = datetime.now(timezone.utc).isoformat()
            for m in markets:
                ticker = m.get("ticker", "")
                if not ticker:
                    continue

                yb = float(m.get("yes_bid_dollars", 0) or 0)
                ya = float(m.get("yes_ask_dollars", 0) or 0)
                mid = (yb + ya) / 2 if yb > 0 and ya > 0 else float(m.get("last_price_dollars", 0) or 0)
                vol = float(m.get("volume_fp", 0) or 0)

                self.conn.execute(
                    """INSERT OR REPLACE INTO settled_markets
                       (ticker, event_ticker, title, category, result,
                        last_yes_bid, last_yes_ask, last_yes_mid, volume,
                        close_time, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ticker,
                        m.get("event_ticker", ""),
                        m.get("title", ""),
                        m.get("category", ""),
                        m.get("result", ""),
                        yb, ya, mid, vol,
                        m.get("close_time", ""),
                        now,
                    ),
                )
                total += 1

            cursor = resp.get("cursor")
            if not cursor:
                break
            time.sleep(0.2)

        self.conn.commit()
        logger.info(f"Fetched {total} settled markets")
        return total

    def analyze_settlements(self) -> dict:
        """
        Analyze patterns in settled markets.

        Key questions:
        - Do favorites (high YES price) actually win more?
        - How accurate is the closing line? (calibration)
        - What's the vig distribution?
        - Where are the biggest mispricings?
        """
        cur = self.conn.cursor()

        # Total settled
        cur.execute("SELECT COUNT(*) FROM settled_markets")
        total = cur.fetchone()[0]
        if total == 0:
            return {"error": "No settled markets. Run fetch first."}

        cur.execute("SELECT COUNT(*) FROM settled_markets WHERE volume > 0")
        with_volume = cur.fetchone()[0]

        # Result distribution
        cur.execute("""
            SELECT result, COUNT(*) FROM settled_markets
            WHERE result IN ('yes', 'no')
            GROUP BY result
        """)
        result_dist = dict(cur.fetchall())

        # Calibration: how well does closing price predict outcome?
        # Bin by closing mid-price, check actual YES rate per bin
        calibration = []
        bins = [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
                (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]

        for lo, hi in bins:
            cur.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN result = 'yes' THEN 1 ELSE 0 END) as yes_count
                FROM settled_markets
                WHERE last_yes_mid >= ? AND last_yes_mid < ?
                AND result IN ('yes', 'no') AND volume > 0
            """, (lo, hi))
            row = cur.fetchone()
            if row[0] > 0:
                actual_rate = row[1] / row[0]
                expected = (lo + hi) / 2
                calibration.append({
                    "bin": f"{lo*100:.0f}-{hi*100:.0f}c",
                    "count": row[0],
                    "yes_rate": round(actual_rate, 3),
                    "expected": round(expected, 3),
                    "error": round(actual_rate - expected, 3),
                })

        # Biggest upsets: high-priced YES that settled NO (and vice versa)
        cur.execute("""
            SELECT ticker, title, last_yes_mid, result, volume
            FROM settled_markets
            WHERE last_yes_mid > 0.7 AND result = 'no' AND volume > 10
            ORDER BY last_yes_mid DESC LIMIT 10
        """)
        upsets_favored = [dict(zip(
            ["ticker", "title", "closing_price", "result", "volume"], r
        )) for r in cur.fetchall()]

        cur.execute("""
            SELECT ticker, title, last_yes_mid, result, volume
            FROM settled_markets
            WHERE last_yes_mid < 0.3 AND last_yes_mid > 0 AND result = 'yes' AND volume > 10
            ORDER BY last_yes_mid ASC LIMIT 10
        """)
        upsets_underdog = [dict(zip(
            ["ticker", "title", "closing_price", "result", "volume"], r
        )) for r in cur.fetchall()]

        # Favorite win rate (markets priced > 60c)
        cur.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN result = 'yes' THEN 1 ELSE 0 END) as yes_wins
            FROM settled_markets
            WHERE last_yes_mid > 0.6 AND result IN ('yes', 'no') AND volume > 0
        """)
        row = cur.fetchone()
        fav_total = row[0]
        fav_wins = row[1] or 0

        return {
            "total_settled": total,
            "with_volume": with_volume,
            "result_distribution": result_dist,
            "favorite_win_rate": round(fav_wins / fav_total, 3) if fav_total > 0 else 0,
            "favorite_sample_size": fav_total,
            "calibration": calibration,
            "upsets_favorite_lost": upsets_favored[:5],
            "upsets_underdog_won": upsets_underdog[:5],
        }

    def close(self):
        self.conn.close()


# ============================================================================
# 2. ORDERBOOK SCANNER - Real-time depth analysis
# ============================================================================

class OrderbookScanner:
    """
    Fetch live orderbooks and find exploitable spread/depth patterns.

    Targets:
    - Thin books where a small order moves the price significantly
    - Stale quotes (prices that haven't updated while related markets moved)
    - Spread compression opportunities (where spread is temporarily tight)
    - Penny-wide markets with real depth (best for limit orders)
    """

    def __init__(self, client: KalshiAPIClient):
        self.client = client

    def scan_orderbook(self, ticker: str, depth: int = 10) -> Optional[dict]:
        """Fetch and analyze a single orderbook."""
        try:
            ob = self.client.get_market_orderbook(ticker, depth=depth)
        except Exception as e:
            logger.debug(f"Orderbook fetch failed for {ticker}: {e}")
            return None

        fp = ob.get("orderbook_fp", ob.get("orderbook", {}))

        # Parse order levels
        # Kalshi orderbook: arrays sorted LOW to HIGH price
        # Best yes_bid = HIGHEST price in yes_dollars
        # Best no_bid = HIGHEST price in no_dollars
        yes_bids = []
        no_bids = []

        for level in fp.get("yes_dollars", fp.get("yes", [])):
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                yes_bids.append({"price": float(level[0]), "size": float(level[1])})

        for level in fp.get("no_dollars", fp.get("no", [])):
            if isinstance(level, (list, tuple)) and len(level) >= 2:
                no_bids.append({"price": float(level[0]), "size": float(level[1])})

        if not yes_bids and not no_bids:
            return None

        # Best bid = highest price (last element when sorted low-to-high)
        best_yes_bid = yes_bids[-1]["price"] if yes_bids else 0
        best_no_bid = no_bids[-1]["price"] if no_bids else 0
        yes_ask = 1 - best_no_bid if best_no_bid > 0 else 0
        spread = yes_ask - best_yes_bid if best_yes_bid > 0 and yes_ask > 0 else 1

        # Total depth (liquidity available)
        yes_depth = sum(l["size"] for l in yes_bids)
        no_depth = sum(l["size"] for l in no_bids)

        # Cost to eat through top 5c of the book
        move_cost_yes = 0
        # Reverse for descending order (best first)
        for level in reversed(yes_bids):
            move_cost_yes += level["price"] * level["size"]
            if best_yes_bid - level["price"] >= 0.05:
                break

        return {
            "ticker": ticker,
            "best_yes_bid": best_yes_bid,
            "best_yes_ask": yes_ask,
            "best_no_bid": best_no_bid,
            "spread_cents": round(spread * 100, 1),
            "yes_bid_depth": round(yes_depth, 2),
            "no_bid_depth": round(no_depth, 2),
            "yes_levels": len(yes_bids),
            "no_levels": len(no_bids),
            "move_cost_5c": round(move_cost_yes, 2),
            "yes_bids": yes_bids[:5],
            "no_bids": no_bids[:5],
        }

    def discover_liquid_tickers(self, max_pages: int = 15) -> List[str]:
        """Find tickers with actual liquidity via events endpoint (better coverage)."""
        liquid = []
        seen = set()
        cursor = None

        for page in range(max_pages):
            try:
                resp = self.client.get_events(
                    limit=100, cursor=cursor, status="open",
                    with_nested_markets=True,
                )
            except Exception as e:
                break

            events = resp.get("events", [])
            if not events:
                break

            for ev in events:
                for m in ev.get("markets", []):
                    ticker = m.get("ticker", "")
                    if ticker in seen:
                        continue
                    seen.add(ticker)

                    yb = float(m.get("yes_bid_dollars", 0) or 0)
                    ya = float(m.get("yes_ask_dollars", 0) or 0)

                    if yb > 0 and ya > 0 and ya > yb and (ya - yb) < 0.20:
                        liquid.append(ticker)

            cursor = resp.get("cursor")
            if not cursor:
                break
            time.sleep(0.15)

        logger.info(f"Found {len(liquid)} liquid tickers from {len(seen)} scanned")
        return liquid

    def scan_many(self, tickers: List[str], delay: float = 0.15) -> List[dict]:
        """Scan orderbooks for multiple tickers."""
        results = []
        for i, ticker in enumerate(tickers):
            ob = self.scan_orderbook(ticker)
            if ob and ob["spread_cents"] < 50:  # filter out empty books
                results.append(ob)
            if delay > 0:
                time.sleep(delay)
            if (i + 1) % 50 == 0:
                logger.info(f"  Scanned {i+1}/{len(tickers)} orderbooks...")
        return results

    def find_spread_opportunities(self, orderbooks: List[dict]) -> List[dict]:
        """Find the most tradeable orderbooks by spread and depth."""
        opps = []
        for ob in orderbooks:
            if ob["spread_cents"] <= 0 or ob["best_yes_bid"] <= 0:
                continue

            mid = (ob["best_yes_bid"] + ob["best_yes_ask"]) / 2
            score = 0

            # Tight spread bonus
            if ob["spread_cents"] <= 2:
                score += 5
            elif ob["spread_cents"] <= 5:
                score += 3
            elif ob["spread_cents"] <= 10:
                score += 1

            # Depth bonus
            total_depth = ob["yes_bid_depth"] + ob["no_bid_depth"]
            if total_depth >= 100:
                score += 3
            elif total_depth >= 20:
                score += 2
            elif total_depth >= 5:
                score += 1

            # Near 50/50 bonus (max edge potential)
            uncertainty = 1 - abs(mid - 0.5) * 2
            if uncertainty > 0.6:
                score += 2

            if score > 0:
                opps.append({
                    **ob,
                    "mid_price": round(mid, 4),
                    "uncertainty": round(uncertainty, 3),
                    "tradability_score": score,
                })

        return sorted(opps, key=lambda x: -x["tradability_score"])


# ============================================================================
# 3. CROSS-MARKET ARBITRAGE DETECTOR
# ============================================================================

class ArbitrageDetector:
    """
    Find events where mutually exclusive outcomes are mispriced.

    Supports settlement window filtering (default: 72h).

    If outcomes A, B, C are exhaustive and exclusive:
      P(A) + P(B) + P(C) should = 1.0

    Overpriced total (>1.0): sell overpriced outcomes
    Underpriced total (<1.0): buy all YES = guaranteed profit

    Also checks for YES/NO inconsistency within a single market:
      yes_price + no_price should = ~1.0 (minus vig)
    """

    def __init__(self, client: KalshiAPIClient, max_settlement_hours: int = 0):
        self.client = client
        self.max_settlement_hours = max_settlement_hours  # 0 = no filter

    def scan_events(self, max_pages: int = 10) -> List[dict]:
        """
        Scan all open events for cross-market arbitrage.
        Returns list of arbitrage opportunities.
        """
        from filters import is_within_settlement_window

        opportunities = []
        cursor = None

        for page in range(max_pages):
            try:
                resp = self.client.get_events(
                    limit=100, cursor=cursor, status="open",
                    with_nested_markets=True,
                )
            except Exception as e:
                logger.error(f"Event scan page {page} failed: {e}")
                break

            events = resp.get("events", [])
            if not events:
                break

            for ev in events:
                markets = ev.get("markets", [])
                if len(markets) < 2:
                    continue

                # Settlement filter: skip long-dated events
                if self.max_settlement_hours > 0:
                    exp = ev.get("expected_expiration_time", "")
                    close = ev.get("close_date", "")
                    # Check event-level first, then any market
                    event_in_window = is_within_settlement_window(
                        exp, close, self.max_settlement_hours
                    )
                    if not event_in_window:
                        # Check if any market in the event is within window
                        any_in_window = any(
                            is_within_settlement_window(
                                m.get("expected_expiration_time", m.get("expiration_time", "")),
                                m.get("close_time", ""),
                                self.max_settlement_hours,
                            )
                            for m in markets
                        )
                        if not any_in_window:
                            continue

                opp = self._analyze_event(ev, markets)
                if opp:
                    opportunities.append(opp)

            cursor = resp.get("cursor")
            if not cursor:
                break
            time.sleep(0.2)

        # Sort by profitability
        opportunities.sort(key=lambda x: -x.get("net_edge", 0))
        return opportunities

    def _is_mutually_exclusive(self, event: dict, markets: List[dict]) -> bool:
        """
        Heuristic: determine if event markets are mutually exclusive.

        Mutually exclusive patterns:
        - "Who will win X?" (match, election) - exactly one winner
        - "Will X or Y happen first?" - exactly one first
        - "Which X will Y?" - exactly one answer
        - Match events (2 markets: player A wins, player B wins)

        Non-exclusive patterns:
        - "Who will run/attend/be pardoned" - multiple can happen
        - "Which teams will qualify" - multiple teams qualify
        - Over/under thresholds (10+, 15+, 20+ all overlap)
        - "Will X happen in 2026?" (different categories, independent)
        """
        title = (event.get("title", "") or "").lower()
        n = len(markets)

        # Strong exclusive signals
        exclusive_phrases = [
            "who will win", "who will be the next", "which will win",
            "will win the", "who will become", "winner of",
            "or y ipo first", "ipo first", "leave first",
            "will leave next", "leave office next",
            "which g7 leader", "who will the next",
            "rate after", "fed funds rate",  # rate bands (but check structure)
        ]

        # Strong non-exclusive signals
        non_exclusive_phrases = [
            "who will run", "who will attend", "who will pardon",
            "who will be pardoned", "which teams will", "qualif",
            "nominations for", "will receive a",
            "will be one of", "primetime game",
            "spending increase", "when will",
            "with 5+", "with 10+", "with 3+",
            # Cumulative threshold markets (rate >= X, overlapping)
            "fed funds rate", "rate after",
            "upper bound", "lower bound",
            # Multiple-winner events
            "win a pga", "win a wta", "win a tennis grand slam",
            "win a grand slam", "win a major",
            "win the fields medal",  # 4 winners per cycle
            "government spending",
        ]

        for phrase in non_exclusive_phrases:
            if phrase in title:
                return False

        for phrase in exclusive_phrases:
            if phrase in title:
                return True

        # Match pattern: exactly 2 markets, titles suggest opponents
        if n == 2:
            t0 = (markets[0].get("title", "") or "").lower()
            t1 = (markets[1].get("title", "") or "").lower()
            if "will" in t0 and "win" in t0 and "will" in t1 and "win" in t1:
                return True

        # If market count is small (2-3) and sum is near 100%, likely exclusive
        # Sum check done externally

        return False  # default: assume non-exclusive (safer)

    def _analyze_event(self, event: dict, markets: List[dict]) -> Optional[dict]:
        """Analyze a single event for arbitrage."""
        event_ticker = event.get("event_ticker", "")
        title = event.get("title", "")
        category = event.get("category", "")

        # Extract prices for each market
        market_prices = []
        for m in markets:
            yb = float(m.get("yes_bid_dollars", 0) or 0)
            ya = float(m.get("yes_ask_dollars", 0) or 0)
            nb = float(m.get("no_bid_dollars", 0) or 0)
            na = float(m.get("no_ask_dollars", 0) or 0)
            mid = (yb + ya) / 2 if yb > 0 and ya > 0 else 0

            market_prices.append({
                "ticker": m.get("ticker", ""),
                "title": m.get("title", ""),
                "yes_bid": yb,
                "yes_ask": ya,
                "no_bid": nb,
                "no_ask": na,
                "mid": mid,
                "has_liquidity": yb > 0 or ya > 0,
            })

        # Filter to markets with liquidity
        liquid = [mp for mp in market_prices if mp["has_liquidity"]]
        if len(liquid) < 2:
            return None

        # Check mutual exclusivity
        is_exclusive = self._is_mutually_exclusive(event, markets)

        # Sum of mid-prices
        total_mid = sum(mp["mid"] for mp in liquid)
        total_ask = sum(mp["yes_ask"] for mp in liquid if mp["yes_ask"] > 0)
        total_bid = sum(mp["yes_bid"] for mp in liquid if mp["yes_bid"] > 0)

        # For non-exclusive events, no arbitrage possible
        if not is_exclusive:
            return None

        # For exclusive events: deviation from 1.0 = opportunity
        deviation = abs(total_mid - 1.0)
        if deviation < 0.05:
            return None

        # Determine opportunity type
        if total_ask > 0 and total_ask < 0.93:
            gross_profit = 1.0 - total_ask
            fee = gross_profit * KALSHI_FEE_RATE
            net_profit = gross_profit - fee
            opp_type = "buy_all_yes"
            net_edge = net_profit
        elif total_bid > 1.07:
            gross_profit = total_bid - 1.0
            fee = gross_profit * KALSHI_FEE_RATE
            net_profit = gross_profit - fee
            opp_type = "sell_overpriced"
            net_edge = net_profit
        else:
            opp_type = "monitor"
            net_edge = 0

        return {
            "event_ticker": event_ticker,
            "title": title,
            "category": category,
            "is_exclusive": is_exclusive,
            "market_count": len(liquid),
            "total_liquid_markets": len(liquid),
            "total_mid": round(total_mid, 4),
            "total_ask": round(total_ask, 4),
            "total_bid": round(total_bid, 4),
            "deviation_from_100": round(deviation * 100, 1),
            "opportunity_type": opp_type,
            "net_edge": round(net_edge, 4),
            "net_edge_cents": round(net_edge * 100, 1),
            "markets": [
                {
                    "ticker": mp["ticker"],
                    "title": mp["title"][:50],
                    "yes_bid": mp["yes_bid"],
                    "yes_ask": mp["yes_ask"],
                    "mid": mp["mid"],
                }
                for mp in sorted(liquid, key=lambda x: -x["mid"])
            ],
        }


# ============================================================================
# Master Scanner
# ============================================================================

class ActiveScanner:
    """Orchestrates all active scanning modules."""

    def __init__(self, use_prod: bool = True, max_settlement_hours: int = 72):
        self.config = KalshiConfig.from_env()
        if use_prod:
            self.config.use_demo = False
        self.client = create_client(self.config)
        self.max_settlement_hours = max_settlement_hours

    def run_history(self, max_pages: int = 15) -> dict:
        """Run history miner."""
        logger.info("=== HISTORY MINER ===")
        miner = HistoryMiner(self.client)

        # Fetch settled markets
        count = miner.fetch_settled_markets(max_pages=max_pages)
        logger.info(f"Fetched {count} settled markets")

        # Analyze patterns
        analysis = miner.analyze_settlements()
        miner.close()
        return analysis

    def run_orderbook(self, tickers: List[str] = None) -> dict:
        """Run orderbook scanner."""
        logger.info("=== ORDERBOOK SCANNER ===")
        scanner = OrderbookScanner(self.client)

        if not tickers:
            # Discover liquid tickers directly from Kalshi API
            tickers = scanner.discover_liquid_tickers(max_pages=5)
            # Cap at 200 to keep scan time reasonable (~30 seconds)
            tickers = tickers[:200]

        if not tickers:
            return {"error": "No liquid tickers found on Kalshi."}

        logger.info(f"Scanning {len(tickers)} orderbooks...")
        orderbooks = scanner.scan_many(tickers)
        opportunities = scanner.find_spread_opportunities(orderbooks)

        return {
            "scanned": len(tickers),
            "with_orderbook": len(orderbooks),
            "opportunities": len(opportunities),
            "top_opportunities": opportunities[:20],
        }

    def run_arbitrage(self, max_pages: int = 10) -> dict:
        """Run cross-market arbitrage scanner."""
        logger.info("=== ARBITRAGE SCANNER ===")
        detector = ArbitrageDetector(self.client, max_settlement_hours=self.max_settlement_hours)
        opportunities = detector.scan_events(max_pages=max_pages)

        actionable = [o for o in opportunities if o["opportunity_type"] != "monitor"]

        return {
            "events_with_deviation": len(opportunities),
            "actionable": len(actionable),
            "opportunities": opportunities[:30],
        }

    def run_all(self) -> dict:
        """Run all scanners."""
        results = {}

        results["history"] = self.run_history()
        results["arbitrage"] = self.run_arbitrage()
        results["orderbook"] = self.run_orderbook()

        return results


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Active Scanner - Find Signals NOW"
    )
    parser.add_argument(
        "--scan",
        choices=["all", "history", "orderbook", "arbitrage"],
        default="all",
    )
    parser.add_argument("--export", type=str, help="Export results to JSON")
    parser.add_argument("--history-pages", type=int, default=15)
    parser.add_argument("--arb-pages", type=int, default=10)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    scanner = ActiveScanner(use_prod=True)

    print("=" * 60)
    print("ACTIVE SCANNER - Finding Signals NOW")
    print("=" * 60)

    if args.scan == "history" or args.scan == "all":
        analysis = scanner.run_history(max_pages=args.history_pages)

        print("\n--- SETTLEMENT PATTERNS ---")
        if "error" in analysis:
            print(f"  {analysis['error']}")
        else:
            print(f"  Settled markets: {analysis['total_settled']} (with volume: {analysis['with_volume']})")
            print(f"  Results: {analysis['result_distribution']}")
            print(f"  Favorite (>60c) win rate: {analysis['favorite_win_rate']*100:.1f}% (n={analysis['favorite_sample_size']})")

            if analysis.get("calibration"):
                print(f"\n  Price Calibration (closing price vs actual outcome):")
                print(f"  {'Bin':>12} {'Count':>6} {'Expected':>9} {'Actual':>8} {'Error':>7}")
                for c in analysis["calibration"]:
                    err_flag = " ***" if abs(c["error"]) > 0.10 else ""
                    print(f"  {c['bin']:>12} {c['count']:>6} {c['expected']*100:>8.1f}% {c['yes_rate']*100:>7.1f}% {c['error']*100:>+6.1f}%{err_flag}")

            if analysis.get("upsets_underdog_won"):
                print(f"\n  Biggest Upsets (underdog won, was priced <30c):")
                for u in analysis["upsets_underdog_won"][:5]:
                    t = u["title"][:50] if u.get("title") else u["ticker"][:50]
                    print(f"    {u['closing_price']*100:.0f}c -> YES | vol={u['volume']:.0f} | {t}")

    if args.scan == "arbitrage" or args.scan == "all":
        arb = scanner.run_arbitrage(max_pages=args.arb_pages)

        print(f"\n--- CROSS-MARKET ARBITRAGE ---")
        print(f"  Events with deviation: {arb['events_with_deviation']}")
        print(f"  Actionable: {arb['actionable']}")

        for opp in arb["opportunities"][:15]:
            t = opp["title"][:45]
            if opp["opportunity_type"] != "monitor":
                flag = " <<< ACTIONABLE"
            else:
                flag = ""

            print(f"\n  [{opp['category']:15s}] {t}")
            print(f"    Markets: {opp['market_count']} | Sum mid={opp['total_mid']*100:.0f}c | "
                  f"Sum ask={opp['total_ask']*100:.0f}c | Sum bid={opp['total_bid']*100:.0f}c")
            print(f"    Deviation: {opp['deviation_from_100']:.1f}c | "
                  f"Type: {opp['opportunity_type']} | Net edge: {opp['net_edge_cents']:.1f}c{flag}")

            # Show individual markets for actionable opportunities
            if opp["opportunity_type"] != "monitor":
                for m in opp["markets"][:5]:
                    mt = m["title"][:40]
                    print(f"      bid={m['yes_bid']*100:.0f}c ask={m['yes_ask']*100:.0f}c mid={m['mid']*100:.0f}c | {mt}")

    if args.scan == "orderbook" or args.scan == "all":
        ob = scanner.run_orderbook()

        print(f"\n--- ORDERBOOK ANALYSIS ---")
        if "error" in ob:
            print(f"  {ob['error']}")
        else:
            print(f"  Scanned: {ob['scanned']} | With book: {ob['with_orderbook']} | Opportunities: {ob['opportunities']}")
            for opp in ob.get("top_opportunities", [])[:15]:
                print(
                    f"  [score={opp['tradability_score']}] {opp['ticker'][:45]:45s} | "
                    f"bid={opp['best_yes_bid']*100:.0f}c ask={opp['best_yes_ask']*100:.0f}c "
                    f"spread={opp['spread_cents']:.0f}c | "
                    f"depth: Y={opp['yes_bid_depth']:.0f} N={opp['no_bid_depth']:.0f}"
                )

    # Export
    if args.export:
        all_results = {}
        if args.scan == "all":
            all_results = scanner.run_all()
        with open(args.export, "w") as f:
            json.dump(all_results, f, indent=2, default=str)
        print(f"\nExported to {args.export}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
