#!/usr/bin/env python3
"""
Price Analyzer - Phase 3: Pattern Discovery & Market Intelligence
=================================================================
Prediction Market War Machine

Analyzes recorded price data to discover exploitable patterns:
  1. Spread analysis - when/where spreads are tightest (best entry)
  2. Volatility profiles - which markets move most, when
  3. Price convergence - how prices behave approaching expiration
  4. Overreaction detection - big moves that tend to revert
  5. Volume patterns - activity cycles, unusual volume spikes
  6. Cross-market correlation - related markets moving together/apart
  7. Market efficiency scoring - how quickly prices adjust

Designed to get smarter as more data accumulates from market_recorder.

Usage:
    # Full analysis report
    python scripts/price_analyzer.py

    # Specific analysis
    python scripts/price_analyzer.py --report spreads
    python scripts/price_analyzer.py --report volatility
    python scripts/price_analyzer.py --report convergence
    python scripts/price_analyzer.py --report overreaction
    python scripts/price_analyzer.py --report anomalies

    # Analyze specific category
    python scripts/price_analyzer.py --category sports

    # Export analysis to JSON
    python scripts/price_analyzer.py --export data/analysis.json
"""

import sys
import json
import math
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_connection, put_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class SpreadProfile:
    """Spread statistics for a market or category."""
    ticker: str
    category: str
    avg_spread_cents: float
    min_spread_cents: float
    max_spread_cents: float
    median_spread_cents: float
    observation_count: int
    pct_tight: float           # % of time spread <= 5c
    pct_tradeable: float       # % of time spread <= 10c


@dataclass
class VolatilityProfile:
    """Price movement statistics."""
    ticker: str
    category: str
    price_std: float           # standard deviation of mid-price
    price_range: float         # max - min price observed
    avg_price: float
    max_move_pct: float        # largest single-period move as % of price
    observation_count: int


@dataclass
class Anomaly:
    """Detected price anomaly or potential opportunity."""
    ticker: str
    title: str
    category: str
    anomaly_type: str          # spread_compression, volume_spike, price_dislocation, etc.
    severity: float            # 0-1, higher = more notable
    description: str
    detected_at: str
    data: dict                 # supporting metrics


# ============================================================================
# Analysis Engine
# ============================================================================

class PriceAnalyzer:
    """Analyzes price data from market_data.db."""

    def __init__(self):
        self.conn = get_connection(dict_cursor=True)

    def _query(self, sql: str, params: tuple = ()) -> List[dict]:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    # ----------------------------------------------------------------
    # 1. Spread Analysis
    # ----------------------------------------------------------------

    def analyze_spreads(self, category: str = None) -> List[SpreadProfile]:
        """
        Analyze bid-ask spreads across markets.
        Identifies which markets/categories have the tightest spreads.
        """
        where = "WHERE s.yes_bid > 0 AND s.yes_ask > 0 AND s.yes_ask > s.yes_bid"
        params = []
        if category:
            where += " AND m.category = %s"
            params.append(category)

        rows = self._query(f"""
            SELECT s.ticker, m.category,
                   AVG((s.yes_ask - s.yes_bid) * 100) as avg_spread,
                   MIN((s.yes_ask - s.yes_bid) * 100) as min_spread,
                   MAX((s.yes_ask - s.yes_bid) * 100) as max_spread,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN (s.yes_ask - s.yes_bid) * 100 <= 5 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as pct_tight,
                   SUM(CASE WHEN (s.yes_ask - s.yes_bid) * 100 <= 10 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as pct_tradeable
            FROM price_snapshots s
            LEFT JOIN markets m ON s.ticker = m.ticker
            {where}
            GROUP BY s.ticker
            HAVING COUNT(*) >= 1
            ORDER BY avg_spread ASC
        """, tuple(params))

        profiles = []
        for r in rows:
            profiles.append(SpreadProfile(
                ticker=r["ticker"],
                category=r["category"] or "unknown",
                avg_spread_cents=round(r["avg_spread"], 2),
                min_spread_cents=round(r["min_spread"], 2),
                max_spread_cents=round(r["max_spread"], 2),
                median_spread_cents=round(r["avg_spread"], 2),  # approx with avg
                observation_count=r["cnt"],
                pct_tight=round(r["pct_tight"], 3),
                pct_tradeable=round(r["pct_tradeable"], 3),
            ))

        return profiles

    def spread_summary_by_category(self) -> List[dict]:
        """Aggregate spread stats by market category."""
        rows = self._query("""
            SELECT m.category,
                   COUNT(DISTINCT s.ticker) as market_count,
                   AVG((s.yes_ask - s.yes_bid) * 100) as avg_spread,
                   MIN((s.yes_ask - s.yes_bid) * 100) as min_spread,
                   COUNT(*) as total_obs
            FROM price_snapshots s
            LEFT JOIN markets m ON s.ticker = m.ticker
            WHERE s.yes_bid > 0 AND s.yes_ask > 0 AND s.yes_ask > s.yes_bid
            GROUP BY m.category
            ORDER BY avg_spread ASC
        """)
        return rows

    # ----------------------------------------------------------------
    # 2. Volatility Analysis
    # ----------------------------------------------------------------

    def analyze_volatility(self, min_observations: int = 3, category: str = None) -> List[VolatilityProfile]:
        """
        Compute volatility profiles for markets with enough data.
        Requires multiple snapshots to measure price movement.
        """
        where = "WHERE s.yes_price > 0"
        params = []
        if category:
            where += " AND m.category = %s"
            params.append(category)

        rows = self._query(f"""
            SELECT s.ticker, m.category,
                   AVG(s.yes_price) as avg_price,
                   MIN(s.yes_price) as min_price,
                   MAX(s.yes_price) as max_price,
                   COUNT(*) as cnt
            FROM price_snapshots s
            LEFT JOIN markets m ON s.ticker = m.ticker
            {where}
            GROUP BY s.ticker
            HAVING COUNT(*) >= %s
            ORDER BY (MAX(s.yes_price) - MIN(s.yes_price)) DESC
        """, tuple(params) + (min_observations,))

        profiles = []
        for r in rows:
            price_range = r["max_price"] - r["min_price"]
            avg = r["avg_price"]

            # Compute std dev with a separate query for this ticker
            std_rows = self._query(
                "SELECT yes_price FROM price_snapshots WHERE ticker = %s AND yes_price > 0 ORDER BY timestamp",
                (r["ticker"],)
            )
            prices = [sr["yes_price"] for sr in std_rows]
            std = _std_dev(prices) if len(prices) > 1 else 0

            # Max single-period move
            max_move = 0
            for i in range(1, len(prices)):
                move = abs(prices[i] - prices[i - 1])
                if prices[i - 1] > 0:
                    move_pct = move / prices[i - 1]
                    max_move = max(max_move, move_pct)

            profiles.append(VolatilityProfile(
                ticker=r["ticker"],
                category=r["category"] or "unknown",
                price_std=round(std, 4),
                price_range=round(price_range, 4),
                avg_price=round(avg, 4),
                max_move_pct=round(max_move, 4),
                observation_count=r["cnt"],
            ))

        return profiles

    # ----------------------------------------------------------------
    # 3. Price Convergence Analysis
    # ----------------------------------------------------------------

    def analyze_convergence(self) -> List[dict]:
        """
        Analyze how prices converge as markets approach expiration.

        Theory: as event time nears, uncertainty resolves and prices
        should converge toward 0 or 100. Early mispricings get corrected.
        This is where edge exists for early entrants.
        """
        # Find markets with expiration times and multiple price observations
        rows = self._query("""
            SELECT m.ticker, m.title, m.category, m.expiration_time,
                   COUNT(*) as obs_count
            FROM markets m
            JOIN price_snapshots s ON m.ticker = s.ticker
            WHERE m.expiration_time != '' AND s.yes_price > 0
            GROUP BY m.ticker
            HAVING obs_count >= 3
            ORDER BY obs_count DESC
            LIMIT 50
        """)

        results = []
        for r in rows:
            # Get time series for this market
            series = self._query(
                """SELECT timestamp, yes_price, yes_bid, yes_ask
                   FROM price_snapshots
                   WHERE ticker = %s AND yes_price > 0
                   ORDER BY timestamp""",
                (r["ticker"],)
            )

            if len(series) < 3:
                continue

            prices = [s["yes_price"] for s in series]
            first_price = prices[0]
            last_price = prices[-1]

            # Measure convergence: did price move toward extremes (0 or 1)?
            first_dist_to_extreme = min(first_price, 1 - first_price)
            last_dist_to_extreme = min(last_price, 1 - last_price)
            convergence = first_dist_to_extreme - last_dist_to_extreme

            # Price drift
            drift = last_price - first_price

            results.append({
                "ticker": r["ticker"],
                "title": r["title"],
                "category": r["category"],
                "observations": r["obs_count"],
                "first_price": round(first_price, 4),
                "last_price": round(last_price, 4),
                "drift": round(drift, 4),
                "convergence": round(convergence, 4),  # positive = converging to extreme
                "price_range": round(max(prices) - min(prices), 4),
            })

        return sorted(results, key=lambda x: -abs(x["convergence"]))

    # ----------------------------------------------------------------
    # 4. Overreaction Detection
    # ----------------------------------------------------------------

    def detect_overreactions(self, reversion_threshold: float = 0.03) -> List[dict]:
        """
        Find price moves that reverted - signs of market overreaction.

        Pattern: price spikes/drops then returns toward previous level.
        These represent trading opportunities on future overreactions.
        """
        # Get markets with enough data
        tickers = self._query("""
            SELECT ticker FROM price_snapshots
            WHERE yes_price > 0
            GROUP BY ticker HAVING COUNT(*) >= 5
        """)

        overreactions = []

        for row in tickers:
            ticker = row["ticker"]
            series = self._query(
                """SELECT timestamp, yes_price FROM price_snapshots
                   WHERE ticker = %s AND yes_price > 0
                   ORDER BY timestamp""",
                (ticker,)
            )
            prices = [s["yes_price"] for s in series]

            for i in range(1, len(prices) - 1):
                # Detect spike: move from i-1 to i, then reversion from i to i+1
                move_out = prices[i] - prices[i - 1]
                move_back = prices[i + 1] - prices[i]

                # Overreaction = big move followed by opposite direction
                if abs(move_out) >= reversion_threshold and move_out * move_back < 0:
                    reversion_pct = abs(move_back / move_out) if move_out != 0 else 0
                    if reversion_pct >= 0.3:  # at least 30% reversion
                        overreactions.append({
                            "ticker": ticker,
                            "timestamp": series[i]["timestamp"],
                            "price_before": round(prices[i - 1], 4),
                            "spike_price": round(prices[i], 4),
                            "revert_price": round(prices[i + 1], 4),
                            "spike_size": round(move_out, 4),
                            "reversion": round(move_back, 4),
                            "reversion_pct": round(reversion_pct, 3),
                        })

        return sorted(overreactions, key=lambda x: -abs(x["spike_size"]))

    # ----------------------------------------------------------------
    # 5. Current Anomaly Detection
    # ----------------------------------------------------------------

    def detect_anomalies(self) -> List[Anomaly]:
        """
        Scan latest data for current anomalies / potential opportunities.

        Checks:
        - Unusually tight spreads (opportunity to enter)
        - Unusually wide spreads (potential mispricing)
        - Volume spikes vs. recent average
        - Price near 50c (maximum uncertainty = maximum edge potential)
        - Extreme prices (near 0 or 100) with decent volume
        """
        anomalies = []
        now = datetime.now(timezone.utc).isoformat()

        # Get latest snapshot per ticker with market info
        latest = self._query("""
            SELECT DISTINCT ON (s.ticker)
                   s.ticker, s.yes_bid, s.yes_ask, s.yes_price,
                   s.volume, s.open_interest, s.timestamp,
                   m.title, m.category, m.sub_category, m.liquidity_grade
            FROM price_snapshots s
            JOIN markets m ON s.ticker = m.ticker
            WHERE s.yes_bid > 0 AND s.yes_ask > 0
            ORDER BY s.ticker, s.id DESC
        """)

        if not latest:
            return anomalies

        # Compute category-level spread averages for comparison
        cat_spreads = {}
        for row in latest:
            cat = row["category"] or "other"
            spread = (row["yes_ask"] - row["yes_bid"]) * 100
            cat_spreads.setdefault(cat, []).append(spread)

        cat_avg_spread = {
            cat: sum(spreads) / len(spreads)
            for cat, spreads in cat_spreads.items()
        }

        for row in latest:
            ticker = row["ticker"]
            title = row["title"] or ticker
            cat = row["category"] or "other"
            bid = row["yes_bid"]
            ask = row["yes_ask"]
            mid = row["yes_price"]
            spread = (ask - bid) * 100
            vol = row["volume"]
            oi = row["open_interest"]

            # 1. Tight spread anomaly (better than category average by 50%+)
            avg_sp = cat_avg_spread.get(cat, spread)
            if avg_sp > 0 and spread < avg_sp * 0.5 and spread <= 5:
                anomalies.append(Anomaly(
                    ticker=ticker, title=title, category=cat,
                    anomaly_type="tight_spread",
                    severity=min(1.0, (avg_sp - spread) / avg_sp),
                    description=f"Spread {spread:.1f}c vs category avg {avg_sp:.1f}c - good entry window",
                    detected_at=now,
                    data={"spread": spread, "category_avg": avg_sp, "bid": bid, "ask": ask},
                ))

            # 2. High uncertainty (price near 50c) with decent liquidity
            uncertainty = 1 - abs(mid - 0.5) * 2  # 1.0 at 50c, 0.0 at 0c/100c
            if uncertainty > 0.8 and spread <= 15:
                anomalies.append(Anomaly(
                    ticker=ticker, title=title, category=cat,
                    anomaly_type="high_uncertainty",
                    severity=uncertainty * 0.8,
                    description=f"Price at {mid*100:.0f}c (near 50/50) - maximum edge potential",
                    detected_at=now,
                    data={"mid_price": mid, "uncertainty": uncertainty, "spread": spread},
                ))

            # 3. Volume spike (if we have historical volume data)
            if vol > 100 and spread <= 15:
                anomalies.append(Anomaly(
                    ticker=ticker, title=title, category=cat,
                    anomaly_type="volume_notable",
                    severity=min(1.0, vol / 500),
                    description=f"Volume {vol} with {spread:.0f}c spread - active market",
                    detected_at=now,
                    data={"volume": vol, "spread": spread, "open_interest": oi},
                ))

            # 4. Extreme price with volume (likely near-resolution)
            if (mid < 0.10 or mid > 0.90) and vol > 0:
                anomalies.append(Anomaly(
                    ticker=ticker, title=title, category=cat,
                    anomaly_type="near_resolution",
                    severity=0.5,
                    description=f"Price at {mid*100:.0f}c - market nearing resolution",
                    detected_at=now,
                    data={"mid_price": mid, "volume": vol},
                ))

        # Sort by severity
        anomalies.sort(key=lambda a: -a.severity)
        return anomalies

    # ----------------------------------------------------------------
    # 6. Market Efficiency Score
    # ----------------------------------------------------------------

    def score_efficiency(self) -> List[dict]:
        """
        Score how efficiently each market category incorporates information.

        Efficient markets: tight spreads, high volume, fast price adjustment.
        Inefficient markets: wide spreads, low volume, stale prices = OPPORTUNITY.
        """
        rows = self._query("""
            SELECT m.category, m.sub_category,
                   COUNT(DISTINCT s.ticker) as market_count,
                   AVG(CASE WHEN s.yes_bid > 0 AND s.yes_ask > 0
                       THEN (s.yes_ask - s.yes_bid) * 100 ELSE NULL END) as avg_spread,
                   AVG(s.volume) as avg_volume,
                   AVG(s.open_interest) as avg_oi,
                   SUM(CASE WHEN s.yes_bid > 0 THEN 1 ELSE 0 END) * 1.0 /
                       NULLIF(COUNT(*), 0) as pct_with_bids
            FROM price_snapshots s
            JOIN markets m ON s.ticker = m.ticker
            WHERE s.source = 'rest'
            GROUP BY m.category, m.sub_category
            HAVING market_count >= 3
            ORDER BY avg_spread ASC
        """)

        results = []
        for r in rows:
            # Efficiency score: higher = more efficient (harder to exploit)
            # Lower = less efficient (more opportunity)
            spread_score = max(0, 1 - (r["avg_spread"] or 100) / 50)  # 0c = 1.0, 50c = 0
            volume_score = min(1, (r["avg_volume"] or 0) / 200)
            bid_score = r["pct_with_bids"] or 0

            efficiency = (spread_score * 0.4 + volume_score * 0.3 + bid_score * 0.3)

            results.append({
                "category": r["category"] or "unknown",
                "sub_category": r["sub_category"] or "unknown",
                "market_count": r["market_count"],
                "avg_spread_cents": round(r["avg_spread"] or 0, 1),
                "avg_volume": round(r["avg_volume"] or 0, 0),
                "pct_with_bids": round((r["pct_with_bids"] or 0) * 100, 1),
                "efficiency_score": round(efficiency, 3),
                "opportunity_score": round(1 - efficiency, 3),  # inverse
            })

        return sorted(results, key=lambda x: -x["opportunity_score"])

    # ----------------------------------------------------------------
    # 7. Data Health Report
    # ----------------------------------------------------------------

    def data_health(self) -> dict:
        """
        Report on data collection quality and coverage.
        Helps decide when we have enough data for reliable analysis.
        """
        stats = {}

        row = self._query(
            "SELECT COUNT(*), COUNT(DISTINCT ticker), MIN(timestamp), MAX(timestamp) FROM price_snapshots"
        )[0]
        stats["total_snapshots"] = row["COUNT(*)"]
        stats["unique_tickers"] = row["COUNT(DISTINCT ticker)"]
        stats["earliest"] = row["MIN(timestamp)"] or "N/A"
        stats["latest"] = row["MAX(timestamp)"] or "N/A"

        # Time span
        if stats["earliest"] != "N/A" and stats["latest"] != "N/A":
            try:
                t1 = datetime.fromisoformat(stats["earliest"].replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(stats["latest"].replace("Z", "+00:00"))
                stats["data_span_hours"] = round((t2 - t1).total_seconds() / 3600, 1)
            except Exception:
                stats["data_span_hours"] = 0
        else:
            stats["data_span_hours"] = 0

        # Coverage by source
        sources = self._query(
            "SELECT source, COUNT(*) as cnt FROM price_snapshots GROUP BY source"
        )
        stats["by_source"] = {r["source"]: r["cnt"] for r in sources}

        # Tickers with multiple observations (needed for time-series analysis)
        row = self._query(
            "SELECT COUNT(*) as cnt FROM (SELECT ticker FROM price_snapshots GROUP BY ticker HAVING COUNT(*) >= 3)"
        )[0]
        stats["tickers_with_3plus_obs"] = row["cnt"]

        row = self._query(
            "SELECT COUNT(*) as cnt FROM (SELECT ticker FROM price_snapshots GROUP BY ticker HAVING COUNT(*) >= 10)"
        )[0]
        stats["tickers_with_10plus_obs"] = row["cnt"]

        # Markets with prices
        row = self._query(
            "SELECT COUNT(DISTINCT ticker) FROM price_snapshots WHERE yes_bid > 0"
        )[0]
        stats["tickers_with_bids"] = row["COUNT(DISTINCT ticker)"]

        # Recommendations
        recs = []
        if stats["data_span_hours"] < 1:
            recs.append("CRITICAL: Less than 1 hour of data. Run market_recorder for at least 24h.")
        elif stats["data_span_hours"] < 24:
            recs.append("Limited data. Convergence and volatility analysis need 24h+ of recording.")
        if stats["tickers_with_3plus_obs"] < 10:
            recs.append("Few tickers have multiple observations. Run recorder with shorter intervals.")
        if stats["tickers_with_bids"] < 50:
            recs.append("Most markets have no bids. This is normal for illiquid markets.")

        stats["recommendations"] = recs
        return stats

    # ----------------------------------------------------------------
    # Full Report
    # ----------------------------------------------------------------

    def full_report(self, category: str = None) -> dict:
        """Generate a complete analysis report."""
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_health": self.data_health(),
            "efficiency_scores": self.score_efficiency(),
            "spread_by_category": self.spread_summary_by_category(),
            "anomalies": [asdict(a) for a in self.detect_anomalies()[:20]],
        }

        # Only include time-series analyses if we have enough data
        health = report["data_health"]
        if health["tickers_with_3plus_obs"] > 0:
            report["convergence"] = self.analyze_convergence()[:20]
            report["overreactions"] = self.detect_overreactions()[:20]
            report["volatility"] = [
                asdict(v) for v in self.analyze_volatility(min_observations=3, category=category)[:20]
            ]

        return report

    def close(self):
        put_connection(self.conn)
        self.conn = None


# ============================================================================
# Utilities
# ============================================================================

def _std_dev(values: List[float]) -> float:
    """Standard deviation."""
    if len(values) < 2:
        return 0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Price Analyzer - Pattern Discovery for Prediction Markets"
    )
    parser.add_argument(
        "--report",
        choices=["full", "spreads", "volatility", "convergence", "overreaction", "anomalies", "efficiency", "health"],
        default="full",
        help="Analysis type"
    )
    parser.add_argument("--category", type=str, help="Filter by market category")
    parser.add_argument("--export", type=str, help="Export to JSON file")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    analyzer = PriceAnalyzer()

    if args.report == "health":
        health = analyzer.data_health()
        print("=" * 60)
        print("Data Health Report")
        print("=" * 60)
        for key, val in health.items():
            if key == "recommendations":
                print(f"\n  Recommendations:")
                for r in val:
                    print(f"    - {r}")
            elif key == "by_source":
                print(f"  Sources:")
                for src, cnt in val.items():
                    print(f"    {src}: {cnt:,}")
            else:
                print(f"  {key}: {val}")

    elif args.report == "spreads":
        print("=" * 60)
        print("Spread Analysis by Category")
        print("=" * 60)
        for row in analyzer.spread_summary_by_category():
            print(
                f"  {(row['category'] or 'unknown'):15s} | "
                f"markets={row['market_count']:>4} | "
                f"avg={row['avg_spread']:.1f}c | "
                f"min={row['min_spread']:.1f}c | "
                f"obs={row['total_obs']}"
            )

        print("\n--- Tightest Individual Spreads ---")
        profiles = analyzer.analyze_spreads(category=args.category)
        for p in profiles[:20]:
            print(
                f"  {p.ticker[:50]:50s} | "
                f"avg={p.avg_spread_cents:.1f}c | "
                f"tight={p.pct_tight*100:.0f}% | "
                f"obs={p.observation_count}"
            )

    elif args.report == "volatility":
        print("=" * 60)
        print("Volatility Analysis")
        print("=" * 60)
        profiles = analyzer.analyze_volatility(min_observations=3, category=args.category)
        if not profiles:
            print("  Not enough data. Need 3+ observations per ticker.")
            print("  Run market_recorder for longer to accumulate time-series data.")
        else:
            for p in profiles[:20]:
                print(
                    f"  {p.ticker[:45]:45s} | "
                    f"std={p.price_std*100:.1f}c | "
                    f"range={p.price_range*100:.1f}c | "
                    f"max_move={p.max_move_pct*100:.1f}% | "
                    f"obs={p.observation_count}"
                )

    elif args.report == "convergence":
        print("=" * 60)
        print("Price Convergence Analysis")
        print("=" * 60)
        results = analyzer.analyze_convergence()
        if not results:
            print("  Not enough data. Need markets with 3+ observations and expiration times.")
        else:
            for r in results[:20]:
                direction = "CONVERGING" if r["convergence"] > 0 else "diverging"
                print(
                    f"  {r['ticker'][:40]:40s} | "
                    f"{r['first_price']*100:.0f}c -> {r['last_price']*100:.0f}c | "
                    f"drift={r['drift']*100:+.1f}c | "
                    f"{direction} | obs={r['observations']}"
                )

    elif args.report == "overreaction":
        print("=" * 60)
        print("Overreaction Detection")
        print("=" * 60)
        results = analyzer.detect_overreactions()
        if not results:
            print("  Not enough data. Need 5+ observations per ticker with price movements.")
        else:
            for r in results[:20]:
                print(
                    f"  {r['ticker'][:40]:40s} | "
                    f"{r['price_before']*100:.0f}c -> {r['spike_price']*100:.0f}c -> {r['revert_price']*100:.0f}c | "
                    f"spike={r['spike_size']*100:+.1f}c revert={r['reversion_pct']*100:.0f}%"
                )

    elif args.report == "anomalies":
        print("=" * 60)
        print("Current Anomalies / Opportunities")
        print("=" * 60)
        anomalies = analyzer.detect_anomalies()
        if not anomalies:
            print("  No anomalies detected in current data.")
        else:
            for a in anomalies[:30]:
                print(
                    f"  [{a.severity:.2f}] {a.anomaly_type:20s} | "
                    f"{a.category:12s} | {a.description}"
                )

    elif args.report == "efficiency":
        print("=" * 60)
        print("Market Efficiency Scores (lower efficiency = more opportunity)")
        print("=" * 60)
        results = analyzer.score_efficiency()
        for r in results:
            bar = "#" * int(r["opportunity_score"] * 20)
            print(
                f"  {r['category']:12s}/{r['sub_category']:12s} | "
                f"opp={r['opportunity_score']:.2f} {bar:20s} | "
                f"spread={r['avg_spread_cents']:.0f}c vol={r['avg_volume']:.0f} "
                f"bids={r['pct_with_bids']:.0f}% ({r['market_count']} mkts)"
            )

    else:  # full
        report = analyzer.full_report(category=args.category)

        print("=" * 60)
        print("Full Analysis Report")
        print("=" * 60)

        # Health
        h = report["data_health"]
        print(f"\n--- Data Health ---")
        print(f"  Snapshots: {h['total_snapshots']:,} | Tickers: {h['unique_tickers']}")
        print(f"  Span: {h['data_span_hours']}h | With bids: {h['tickers_with_bids']}")
        for rec in h.get("recommendations", []):
            print(f"  >> {rec}")

        # Efficiency
        print(f"\n--- Efficiency Scores ---")
        for r in report["efficiency_scores"][:10]:
            print(
                f"  {r['category']:12s}/{r['sub_category']:12s} | "
                f"opportunity={r['opportunity_score']:.2f} | "
                f"spread={r['avg_spread_cents']:.0f}c"
            )

        # Anomalies
        print(f"\n--- Anomalies ({len(report['anomalies'])}) ---")
        for a in report["anomalies"][:10]:
            print(f"  [{a['severity']:.2f}] {a['anomaly_type']:20s} | {a['description'][:60]}")

        if args.export:
            # Convert for JSON serialization
            with open(args.export, "w") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"\nExported to {args.export}")

    analyzer.close()


if __name__ == "__main__":
    main()
