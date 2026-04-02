#!/usr/bin/env python3
"""
Signal Engine - Phase 4: Real-Time +EV Opportunity Detection
=============================================================
Prediction Market War Machine

Scans live Kalshi markets for exploitable signals:
  1. Spread compression - sudden tightening = informed flow incoming
  2. Price dislocation  - price moves faster than fundamentals justify
  3. Cross-market arb   - related markets with inconsistent pricing
  4. Overreaction fade  - big price moves likely to revert
  5. Convergence entry  - early position before price converges to resolution
  6. Liquidity vacuum   - thin markets with stale prices = mispricing

All signals account for Kalshi fees before declaring +EV.

Usage:
    # Scan for current signals (one-shot)
    python scripts/signal_engine.py

    # Continuous monitoring
    python scripts/signal_engine.py --monitor --interval 60

    # Filter by category
    python scripts/signal_engine.py --category sports

    # JSON output for piping
    python scripts/signal_engine.py --json
"""

import sys
import json
import math
import logging
import argparse
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

# NBA model integration
try:
    from nba_model import NBAModel, PropPrediction
    NBA_MODEL_AVAILABLE = True
except ImportError:
    NBA_MODEL_AVAILABLE = False

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from db import get_connection, put_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ============================================================================
# Kalshi Fee Structure
# ============================================================================

# Kalshi charges fees on winning trades.
# Fee schedule (as of 2024-2025):
#   - Standard: 7% of profit (capped at certain levels)
#   - Simplified: assume 7% of profit for EV calculations
KALSHI_FEE_RATE = 0.07

# For contracts that settle at $1.00:
#   Buy YES at 40c, settles YES => profit = 60c, fee = 0.07 * 60c = 4.2c, net = 55.8c
#   Buy YES at 40c, settles NO  => loss = 40c (no fee on loss)
#
# Net EV formula:
#   EV = prob_win * (payout - fee) - prob_lose * cost
#   EV = p * ((1 - price) * (1 - fee_rate)) - (1 - p) * price


def net_ev(model_prob: float, price: float, side: str = "yes") -> float:
    """
    Calculate net Expected Value after Kalshi fees.

    Args:
        model_prob: Our estimated probability of YES outcome (0-1)
        price: Price we'd pay in dollars (0-1)
        side: "yes" or "no"

    Returns:
        Net EV per contract in dollars. Positive = +EV.
    """
    if side == "no":
        # Buying NO at price means we think YES prob < (1 - price)
        model_prob = 1 - model_prob
        price = 1 - price

    if price <= 0 or price >= 1:
        return 0

    profit_if_win = (1 - price) * (1 - KALSHI_FEE_RATE)
    loss_if_lose = price

    return model_prob * profit_if_win - (1 - model_prob) * loss_if_lose


def breakeven_prob(price: float) -> float:
    """
    What probability do we need to break even at this price after fees?

    At price p with fee rate f:
      p_breakeven = price / (price + (1 - price) * (1 - fee))
    """
    if price <= 0 or price >= 1:
        return 0.5
    net_win = (1 - price) * (1 - KALSHI_FEE_RATE)
    return price / (price + net_win)


# ============================================================================
# Signal Data Structures
# ============================================================================

@dataclass
class Signal:
    """A detected trading signal."""
    ticker: str
    title: str
    category: str
    sub_category: str
    signal_type: str           # spread_compression, overreaction_fade, etc.
    side: str                  # "yes" or "no"
    entry_price: float         # recommended entry price (dollars, 0-1)
    model_prob: float          # our estimated probability
    market_prob: float         # implied probability from mid-price
    edge: float                # model_prob - breakeven_prob (after fees)
    net_ev_cents: float        # net EV per contract in cents
    confidence: str            # "high", "medium", "low"
    urgency: str               # "immediate", "watch", "patient"
    reasoning: str             # human-readable explanation
    detected_at: str
    data: dict                 # supporting metrics


# ============================================================================
# Signal Detectors
# ============================================================================

class SignalEngine:
    """
    Scans market data for trading signals.
    Each detector method returns a list of Signal objects.
    """

    # Minimum thresholds
    MIN_EDGE = 0.20             # default (used by non-NBA detectors)
    PROP_MIN_EDGE = {           # per-prop-type edge thresholds for NBA prop detector
        "assists": 0.15,
        "points":  0.25,
        "rebounds": 0.25,
        "threes":  0.25,
        "steals":  0.25,
        "blocks":  0.25,
    }
    MIN_NET_EV_CENTS = 1.0      # 1 cent minimum net EV
    MAX_SPREAD_CENTS = 15       # won't trade wider than 15c
    MIN_VOLUME = 0              # 0 for now (many markets are new)

    def __init__(self, max_settlement_hours: int = 72):
        self.conn = get_connection(dict_cursor=True)
        self.max_settlement_hours = max_settlement_hours

    def _query(self, sql: str, params: tuple = ()) -> List[dict]:
        try:
            cur = self.conn.cursor()
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        except Exception:
            self.conn.rollback()
            raise

    def _apply_settlement_filter(self, rows: List[dict]) -> List[dict]:
        """Filter to markets settling within max_settlement_hours."""
        if self.max_settlement_hours <= 0:
            return rows
        from filters import is_within_settlement_window
        return [
            r for r in rows
            if is_within_settlement_window(
                r.get("expiration_time", ""),
                r.get("close_time", ""),
                self.max_settlement_hours,
            )
        ]

    def _get_latest_prices(self) -> List[dict]:
        """Get the most recent price snapshot for each ticker (settlement-filtered)."""
        # Step 1: get max timestamp
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(timestamp) AS max_ts FROM price_snapshots")
        row = cur.fetchone()
        if not row or not row["max_ts"]:
            return []
        max_ts = row["max_ts"]

        # Step 2: get latest snapshot per ticker from recent batch (plain cursor for speed)
        import psycopg2
        plain_cur = self.conn.cursor(cursor_factory=psycopg2.extensions.cursor)
        plain_cur.execute("""
            SELECT DISTINCT ON (ticker)
                   ticker, yes_bid, yes_ask, yes_price,
                   no_bid, no_ask, volume, open_interest, timestamp
            FROM price_snapshots
            WHERE timestamp >= %s - INTERVAL '10 minutes'
              AND yes_bid > 0 AND yes_ask > 0
            ORDER BY ticker, id DESC
        """, (max_ts,))
        snapshots = {r[0]: r for r in plain_cur.fetchall()}
        if not snapshots:
            return []

        # Step 3: join with markets for active tickers
        plain_cur.execute("""
            SELECT ticker, title, category, sub_category, market_type,
                   liquidity_grade, expiration_time, close_time
            FROM markets
            WHERE ticker = ANY(%s) AND status = 'active'
        """, (list(snapshots.keys()),))

        rows = []
        for m in plain_cur.fetchall():
            t = m[0]
            s = snapshots[t]
            rows.append({
                "ticker": t, "yes_bid": s[1], "yes_ask": s[2], "yes_price": s[3],
                "no_bid": s[4], "no_ask": s[5], "volume": s[6],
                "open_interest": s[7], "timestamp": s[8],
                "title": m[1], "category": m[2], "sub_category": m[3],
                "market_type": m[4], "liquidity_grade": m[5],
                "expiration_time": m[6], "close_time": m[7],
            })
        return self._apply_settlement_filter(rows)

    def _get_price_history(self, ticker: str, limit: int = 50) -> List[dict]:
        """Get recent price history for a ticker."""
        return self._query(
            """SELECT timestamp, yes_bid, yes_ask, yes_price, volume, open_interest
               FROM price_snapshots
               WHERE ticker = %s AND yes_price > 0
               ORDER BY timestamp DESC LIMIT %s""",
            (ticker, limit),
        )

    # ----------------------------------------------------------------
    # Detector 1: Spread-Based Signals
    # ----------------------------------------------------------------

    def detect_spread_signals(self) -> List[Signal]:
        """
        Find markets where spreads indicate trading opportunity.

        Tight spread = liquid, efficient. But if we have an edge, tight spread
        means we can enter/exit cheaply.

        Wide spread = inefficient. If mid-price is wrong, the correct price
        might be outside the spread => guaranteed edge.
        """
        signals = []
        latest = self._get_latest_prices()

        for row in latest:
            bid = row["yes_bid"]
            ask = row["yes_ask"]
            spread_cents = (ask - bid) * 100

            if spread_cents > self.MAX_SPREAD_CENTS:
                continue

            mid = (bid + ask) / 2
            if mid <= 0.02 or mid >= 0.98:
                continue  # too extreme

            # Check if the market is near 50/50 (maximum uncertainty)
            # This is where our models can add the most value
            uncertainty = 1 - abs(mid - 0.5) * 2

            if uncertainty > 0.6 and spread_cents <= 10:
                # Near 50/50 with tight spread: good candidate for edge
                be_prob = breakeven_prob(bid)
                ev = net_ev(mid + 0.02, bid, "yes")  # assume tiny 2% edge

                if ev > 0:
                    signals.append(Signal(
                        ticker=row["ticker"],
                        title=row["title"] or row["ticker"],
                        category=row["category"] or "unknown",
                        sub_category=row["sub_category"] or "",
                        signal_type="uncertain_tight_spread",
                        side="yes" if mid > 0.5 else "no",
                        entry_price=bid if mid > 0.5 else (1 - ask),
                        model_prob=mid,
                        market_prob=mid,
                        edge=0.02,
                        net_ev_cents=round(ev * 100, 2),
                        confidence="low",
                        urgency="watch",
                        reasoning=(
                            f"Near 50/50 ({mid*100:.0f}c) with {spread_cents:.0f}c spread. "
                            f"High uncertainty = potential for model edge. "
                            f"Need event-specific analysis to confirm."
                        ),
                        detected_at=datetime.now(timezone.utc).isoformat(),
                        data={
                            "bid": bid, "ask": ask, "spread_cents": spread_cents,
                            "uncertainty": round(uncertainty, 3),
                            "volume": row["volume"],
                            "breakeven_prob": round(be_prob, 4),
                        },
                    ))

        return signals

    # ----------------------------------------------------------------
    # Detector 2: Overreaction Fade
    # ----------------------------------------------------------------

    @staticmethod
    def _bayesian_estimate(prices: list, volumes: list,
                           hours_to_settle: float = 48.0,
                           calibration_bias: float = 0.0) -> tuple:
        """
        Bayesian probability estimation with contextual prior weighting.

        Layers:
          1. Time-to-settlement:  far out → trust prior, close → trust market
          2. Liquidity:           low volume → trust prior (noise), high → trust market
          3. Calibration bias:    if model is overconfident by X%, shift posterior down

        Args:
            prices: chronological price history
            volumes: matching volume history
            hours_to_settle: hours until market settles (affects prior weight)
            calibration_bias: signed bias (positive = overconfident, reduce posterior)

        Returns (posterior_prob, confidence_str, data_quality).
        """
        n = len(prices)
        if n < 3:
            return prices[-1], "low", n

        # =============================================================
        # Prior: exponentially weighted average of full history
        # =============================================================
        decay = 0.85
        weights = [decay ** (n - 1 - i) for i in range(n)]
        total_w = sum(weights)
        prior = sum(p * w for p, w in zip(prices, weights)) / total_w

        # =============================================================
        # Likelihood: volume-informed recent price
        # =============================================================
        recent_prices = prices[-3:]
        recent_vols = volumes[-3:] if len(volumes) >= 3 else [0] * 3
        max_vol = max(max(volumes), 1)

        vol_weights = [0.2 + 0.8 * (v / max_vol) for v in recent_vols]
        total_vw = sum(vol_weights)
        likelihood = sum(p * w for p, w in zip(recent_prices, vol_weights)) / total_vw

        # =============================================================
        # Alpha: prior-vs-likelihood blend ratio
        # Three factors combine to determine how much we trust prior
        # =============================================================

        # Factor 1: Volume ratio (existing logic)
        recent_vol_sum = sum(recent_vols)
        older_vol_avg = sum(volumes[:-3]) / max(len(volumes) - 3, 1) if len(volumes) > 3 else 0
        vol_ratio = recent_vol_sum / max(older_vol_avg * 3, 1) if older_vol_avg > 0 else 1.0

        if vol_ratio < 0.5:
            alpha_vol = 0.75   # low volume: strongly trust prior
        elif vol_ratio > 2.0:
            alpha_vol = 0.25   # high volume: trust market
        else:
            alpha_vol = 0.50

        # Factor 2: Time to settlement
        #   72h+ : market hasn't digested all info yet, prior is strong
        #   24h  : balanced
        #   <4h  : market is very close to truth, prior nearly irrelevant
        #   <1h  : prior almost ignored
        if hours_to_settle > 72:
            alpha_time = 0.70
        elif hours_to_settle > 24:
            alpha_time = 0.50
        elif hours_to_settle > 4:
            alpha_time = 0.35
        elif hours_to_settle > 1:
            alpha_time = 0.20
        else:
            alpha_time = 0.10  # almost pure market price

        # Factor 3: Overall market liquidity
        #   Low total volume = illiquid = noisy prices = trust prior more
        #   High total volume = liquid = efficient prices = trust market more
        total_vol = sum(volumes)
        if total_vol < 100:
            alpha_liq = 0.70   # very illiquid
        elif total_vol < 1000:
            alpha_liq = 0.50   # moderate
        else:
            alpha_liq = 0.30   # liquid

        # Combine: weighted average of three alpha factors
        # Volume ratio is most important (direct signal about this move)
        alpha = 0.50 * alpha_vol + 0.30 * alpha_time + 0.20 * alpha_liq

        # =============================================================
        # Posterior: blend prior and likelihood
        # =============================================================
        posterior = alpha * prior + (1 - alpha) * likelihood

        # =============================================================
        # Calibration bias correction
        # If the model has been systematically overconfident by X%,
        # nudge the posterior toward 0.5 by that amount
        # =============================================================
        if abs(calibration_bias) > 0.01:
            # Positive bias = overconfident = predictions too far from 0.5
            # Correction: move posterior toward 0.5
            posterior = posterior - calibration_bias * (posterior - 0.5)

        posterior = max(0.01, min(0.99, round(posterior, 4)))

        # =============================================================
        # Confidence
        # =============================================================
        data_points = n
        if data_points >= 10 and vol_ratio < 0.5 and hours_to_settle > 4:
            conf = "high"
        elif data_points >= 5 and alpha > 0.4:
            conf = "medium"
        else:
            conf = "low"

        return posterior, conf, data_points

    @staticmethod
    def _calculate_vpin(prices: list, volumes: list) -> float:
        """
        Volume-Synchronized Probability of Informed Trading.

        Measures whether price moves are driven by informed traders or noise.
        - High VPIN (>0.7): one-sided informed flow -> price move is justified
        - Low VPIN (<0.3): balanced flow -> price move is likely noise/overreaction

        Returns VPIN in range [0, 1].
        """
        if len(prices) < 3 or len(volumes) < 3:
            return 0.5  # neutral when insufficient data

        buy_volume = 0.0
        sell_volume = 0.0
        n = min(len(prices), len(volumes))

        for i in range(1, n):
            vol = volumes[i] or 0
            if vol <= 0:
                continue
            if prices[i] > prices[i - 1]:
                buy_volume += vol
            elif prices[i] < prices[i - 1]:
                sell_volume += vol
            else:
                # No price change: split evenly
                buy_volume += vol / 2
                sell_volume += vol / 2

        total = buy_volume + sell_volume
        if total <= 0:
            return 0.5

        return round(abs(buy_volume - sell_volume) / total, 4)

    @staticmethod
    def _hours_to_settlement(ticker: str, conn) -> float:
        """Calculate hours until settlement for a market."""
        cur = conn.cursor()
        cur.execute(
            "SELECT expiration_time, close_time FROM markets WHERE ticker = %s",
            (ticker,)
        )
        row = cur.fetchone()
        if not row:
            return 999.0

        exp = row["expiration_time"] or row["close_time"] or ""
        if not exp:
            return 999.0

        try:
            from dateutil.parser import parse as dtparse
            exp_dt = dtparse(exp)
            now = datetime.now(timezone.utc)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            hours = (exp_dt - now).total_seconds() / 3600
            return max(0.1, hours)  # floor at 0.1h to avoid div-by-zero
        except Exception:
            return 999.0

    def detect_overreaction_signals(self) -> List[Signal]:
        """
        Find markets where price recently spiked and may revert.

        Uses:
        - Bayesian estimation (volume-weighted prior + likelihood)
        - VPIN filter (skip if informed trading detected)
        - Time-adjusted EV (ev_per_hour for capital efficiency)
        """
        signals = []

        tickers_with_history = self._query("""
            SELECT ticker, COUNT(*) as cnt
            FROM price_snapshots
            WHERE yes_price > 0
              AND timestamp >= (SELECT MAX(timestamp) FROM price_snapshots) - INTERVAL '24 hours'
            GROUP BY ticker HAVING COUNT(*) >= 5
        """)

        for row in tickers_with_history:
            ticker = row["ticker"]
            history = self._get_price_history(ticker, limit=30)

            if len(history) < 5:
                continue

            history = list(reversed(history))  # chronological
            prices = [h["yes_price"] for h in history]
            volumes = [h.get("volume", 0) or 0 for h in history]

            if len(prices) < 5:
                continue

            # Skip extreme prices
            if prices[-1] <= 0.15 or prices[-1] >= 0.85:
                continue

            # Tuned params
            try:
                from self_learner import get_params
                tp = get_params()
                overreaction_thresh = tp.get("overreaction_threshold")
                overreaction_rel = tp.get("overreaction_relative")
                vpin_cutoff = tp.get("vpin_cutoff")
            except Exception:
                overreaction_thresh = 0.08
                overreaction_rel = 0.15
                vpin_cutoff = 0.70

            # Calibration bias for posterior correction
            cal_bias = 0.0
            try:
                from calibration import CalibrationTracker
                _cal = CalibrationTracker()
                if _cal.state.get("total_predictions", 0) >= 10:
                    _b = _cal.get_bias()
                    cal_bias = _b.get("avg_predicted", 0) - _b.get("avg_actual", 0)
            except Exception:
                pass

            # Hours to settlement (needed for both Bayesian context and EV/hour)
            hours_to_settle = self._hours_to_settlement(ticker, self.conn)

            # Bayesian posterior with contextual priors
            posterior, bayes_conf, data_points = self._bayesian_estimate(
                prices, volumes,
                hours_to_settle=hours_to_settle,
                calibration_bias=cal_bias,
            )

            # Deviation = current price vs posterior
            deviation = prices[-1] - posterior

            if abs(deviation) < overreaction_thresh:
                continue
            if posterior > 0 and abs(deviation) / posterior < overreaction_rel:
                continue

            # --- VPIN filter (tuned) ---
            vpin = self._calculate_vpin(prices, volumes)
            if vpin > vpin_cutoff:
                continue  # high VPIN = informed trading, price move is justified

            # Volume ratio check (kept from Layer 1)
            recent_vol = sum(volumes[-3:])
            older_vol_avg = sum(volumes[:-3]) / max(len(volumes) - 3, 1) if len(volumes) > 3 else 0
            vol_ratio = recent_vol / max(older_vol_avg * 3, 1) if older_vol_avg > 0 else 1.0

            if vol_ratio > 3.0:
                continue

            # Get market info
            market = self._query(
                "SELECT title, category, sub_category FROM markets WHERE ticker = %s",
                (ticker,)
            )
            if not market:
                continue
            mkt = market[0]

            # Skip parlay / cross-category
            ticker_upper = ticker.upper()
            title_lower = (mkt["title"] or "").lower()
            if ("CROSSCATEGORY" in ticker_upper
                or "PARLAY" in ticker_upper
                or "parlay" in title_lower
                or ",yes " in title_lower
                or ",no " in title_lower):
                continue

            # Spread
            latest = history[-1]
            yes_bid = latest.get("yes_bid", 0)
            yes_ask = latest.get("yes_ask", 0)
            spread_cents = (yes_ask - yes_bid) * 100 if yes_bid > 0 and yes_ask > 0 else 99

            # --- Time-adjusted EV (hours_to_settle already computed above) ---

            # Fade direction
            if deviation > 0:
                side = "no"
                entry = 1 - prices[-1]
                model_prob = 1 - posterior
            else:
                side = "yes"
                entry = prices[-1]
                model_prob = posterior

            ev = net_ev(model_prob, entry, side)
            edge = model_prob - breakeven_prob(entry)
            ev_per_hour = (ev * 100) / hours_to_settle  # cents per hour

            if ev > self.MIN_NET_EV_CENTS / 100 and abs(edge) > self.MIN_EDGE:
                # Confidence: Bayesian + deviation + VPIN
                if bayes_conf == "high" and abs(deviation) > 0.15 and vpin < 0.3:
                    confidence = "high"
                elif bayes_conf in ("high", "medium") and abs(deviation) > 0.10 and vpin < 0.5:
                    confidence = "medium"
                else:
                    confidence = "low"

                signals.append(Signal(
                    ticker=ticker,
                    title=mkt["title"] or ticker,
                    category=mkt["category"] or "unknown",
                    sub_category=mkt["sub_category"] or "",
                    signal_type="overreaction_fade",
                    side=side,
                    entry_price=entry,
                    model_prob=round(model_prob, 4),
                    market_prob=round(prices[-1], 4),
                    edge=round(abs(edge), 4),
                    net_ev_cents=round(ev * 100, 2),
                    confidence=confidence,
                    urgency="immediate" if abs(deviation) > 0.15 else "watch",
                    reasoning=(
                        f"Bayes={posterior*100:.0f}c vs mkt={prices[-1]*100:.0f}c "
                        f"(dev={deviation*100:+.0f}c). "
                        f"VPIN={vpin:.2f} ({'noise' if vpin < 0.3 else 'mixed'}). "
                        f"Settle={hours_to_settle:.0f}h, EV/h={ev_per_hour:.2f}c. "
                        f"Fade {side.upper()} at {entry*100:.0f}c."
                    ),
                    detected_at=datetime.now(timezone.utc).isoformat(),
                    data={
                        "current_price": round(prices[-1], 4),
                        "bayesian_posterior": posterior,
                        "deviation": round(deviation, 4),
                        "vpin": vpin,
                        "vol_ratio": round(vol_ratio, 2),
                        "hours_to_settlement": round(hours_to_settle, 1),
                        "ev_per_hour": round(ev_per_hour, 3),
                        "data_points": data_points,
                        "yes_bid": round(yes_bid, 4),
                        "yes_ask": round(yes_ask, 4),
                        "spread_cents": round(spread_cents, 1),
                    },
                ))

        return signals

    # ----------------------------------------------------------------
    # Detector 2b: NBA Player Props (Real Model)
    # ----------------------------------------------------------------

    def detect_nba_prop_signals(self) -> List[Signal]:
        """
        Detect +EV NBA player prop opportunities using REAL basketball data.

        Unlike detect_overreaction_signals() which uses self-referential
        Kalshi price history, this uses:
          - Player season stats + recent form
          - Opponent defensive matchup
          - Home/away + B2B adjustments
          - Normal distribution P(X > line)

        Only fires for markets classified as sports/nba player props.
        """
        if not NBA_MODEL_AVAILABLE:
            logger.warning("[SignalEngine] NBA model not available, skipping NBA prop scan")
            return []

        signals = []

        # Query NBA prop markets directly by ticker date pattern.
        # Kalshi expiration_time for NBA props is ~14 days out; settlement filter kills all.
        # Use same approach as forward_test.get_active_props(): ticker LIKE date pattern.
        from datetime import date as _date
        try:
            from tz import ET
            today = datetime.now(ET).date()
        except Exception:
            today = datetime.now(timezone.utc).date()

        NBA_PREFIXES = ["KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT", "KXNBASTL", "KXNBABLK"]
        dates = [today, today + timedelta(days=1)]

        def _date_to_pattern(d: _date) -> str:
            return f"{str(d.year)[-2:]}{d.strftime('%b').upper()}{d.day}"

        nba_markets_dict = {}
        cur = self.conn.cursor()
        for d in dates:
            dp = _date_to_pattern(d)
            for pfx in NBA_PREFIXES:
                cur.execute("""
                    SELECT m.ticker, m.title, m.category, m.sub_category, m.market_type,
                           m.liquidity_grade, m.expiration_time, m.close_time,
                           p.yes_bid, p.yes_ask, p.yes_price, p.no_bid, p.no_ask,
                           p.volume, p.open_interest, p.timestamp
                    FROM markets m
                    LEFT JOIN price_snapshots p ON m.ticker = p.ticker
                      AND p.id = (SELECT MAX(id) FROM price_snapshots WHERE ticker = m.ticker)
                    WHERE m.ticker LIKE %s
                      AND m.status = 'active'
                      AND p.yes_bid > 0 AND p.yes_ask > 0
                """, (f"{pfx}-{dp}%",))
                for row in cur.fetchall():
                    nba_markets_dict[row["ticker"]] = dict(row)

        nba_markets = list(nba_markets_dict.values())

        if not nba_markets:
            logger.debug("[SignalEngine] No NBA prop markets found")
            return []

        try:
            model = NBAModel()
        except Exception as e:
            logger.error(f"[SignalEngine] Failed to initialize NBA model: {e}")
            return []

        for mkt in nba_markets:
            ticker = mkt["ticker"]
            title = mkt.get("title", "")
            yes_bid = mkt["yes_bid"]
            yes_ask = mkt["yes_ask"]
            mid_price = mkt["yes_price"] or (yes_bid + yes_ask) / 2
            spread_cents = (yes_ask - yes_bid) * 100

            # Skip wide spreads
            if spread_cents > self.MAX_SPREAD_CENTS:
                continue

            # Skip extreme prices
            if mid_price <= 0.10 or mid_price >= 0.90:
                continue

            try:
                prediction = model.process_kalshi_nba_ticker(ticker, title, mid_price)
            except Exception as e:
                logger.debug(f"[SignalEngine] NBA model error for {ticker}: {e}")
                continue

            if not prediction:
                continue

            # Determine side: if model says higher prob than market, buy YES
            # If model says lower prob, buy NO
            model_prob_yes = prediction.model_prob
            market_prob_yes = mid_price

            if model_prob_yes > market_prob_yes:
                side = "yes"
                entry = yes_bid  # buy YES at bid
                model_prob = model_prob_yes
            else:
                side = "no"
                entry = 1 - yes_ask  # buy NO
                model_prob = 1 - model_prob_yes

            if entry <= 0 or entry >= 1:
                continue

            ev = net_ev(model_prob, entry, side)
            edge = model_prob - breakeven_prob(entry)

            # Only signal if edge meets per-prop threshold
            prop_min_edge = self.PROP_MIN_EDGE.get(prediction.prop_type, 0.25)
            if ev <= self.MIN_NET_EV_CENTS / 100 or edge < prop_min_edge:
                continue

            hours_to_settle = self._hours_to_settlement(ticker, self.conn)
            ev_per_hour = (ev * 100) / max(hours_to_settle, 0.1)

            # Confidence from model
            confidence = prediction.confidence

            # Skip low confidence unless edge is massive
            if confidence == "low" and edge < 0.10:
                continue

            signals.append(Signal(
                ticker=ticker,
                title=title or ticker,
                category=mkt.get("category", "sports"),
                sub_category=mkt.get("sub_category", "nba"),
                signal_type="nba_prop_model",
                side=side,
                entry_price=entry,
                model_prob=round(model_prob, 4),
                market_prob=round(mid_price, 4),
                edge=round(abs(edge), 4),
                net_ev_cents=round(ev * 100, 2),
                confidence=confidence,
                urgency="immediate" if edge > 0.10 else "watch",
                reasoning=(
                    f"NBA Model: {prediction.reasoning} | "
                    f"Market={mid_price*100:.0f}c vs Model={model_prob_yes*100:.0f}c | "
                    f"Edge={edge*100:.1f}% | EV/h={ev_per_hour:.2f}c"
                ),
                detected_at=datetime.now(timezone.utc).isoformat(),
                data={
                    "player": prediction.player_name,
                    "prop_type": prediction.prop_type,
                    "line": prediction.line,
                    "projected": prediction.projected_value,
                    "std_dev": prediction.std_dev,
                    "matchup_adj": prediction.matchup_adjustment,
                    "b2b_adj": prediction.b2b_adjustment,
                    "pace_adj": prediction.pace_adjustment,
                    "injury": prediction.injury_status,
                    "model_source": "nba_model_v1",
                    "yes_bid": round(yes_bid, 4),
                    "yes_ask": round(yes_ask, 4),
                    "spread_cents": round(spread_cents, 1),
                    "hours_to_settlement": round(hours_to_settle, 1),
                    "ev_per_hour": round(ev_per_hour, 3),
                    "data_quality": prediction.data_quality,
                },
            ))

        model.close()
        logger.info(f"[SignalEngine] NBA prop model: {len(signals)} signals")
        return signals

    # ----------------------------------------------------------------
    # Detector 3: Cross-Market Correlation
    # ----------------------------------------------------------------

    def detect_cross_market_signals(self) -> List[Signal]:
        """
        Find inconsistencies between related markets.

        Examples:
        - Team A win + Team A player props should be correlated
        - Economic indicators: CPI up + Fed rate hike should be correlated
        - Same event, different contract structures
        """
        signals = []

        # Reuse already-fetched latest prices (optimized 3-step query)
        latest = self._get_latest_prices()
        if not latest:
            return signals

        # Group by event_ticker in Python
        from collections import defaultdict
        by_event: dict = defaultdict(list)
        for row in latest:
            evt = row.get("event_ticker", "")
            if evt and row.get("yes_price", 0) > 0:
                by_event[evt].append(row)

        for evt, markets in by_event.items():
            if len(markets) < 2:
                continue

            if len(markets) < 2:
                continue

            # Check: sum of YES probabilities for mutually exclusive outcomes
            # should equal ~1.0 (minus fees/vig)
            total_yes = sum(m["yes_price"] for m in markets)

            if len(markets) >= 2 and total_yes > 0:
                # If outcomes are mutually exclusive and exhaustive,
                # total should be close to 1.0 (with some vig)
                # Significant deviation = mispricing
                if total_yes < 0.85 or total_yes > 1.20:
                    # Compute tightest spread among sub-markets
                    spreads = []
                    for m in markets:
                        if m.get("yes_bid", 0) > 0 and m.get("yes_ask", 0) > 0:
                            spreads.append((m["yes_ask"] - m["yes_bid"]) * 100)
                    best_spread = min(spreads) if spreads else 99

                    # Potential arbitrage or mispricing
                    signals.append(Signal(
                        ticker=markets[0]["ticker"],
                        title=f"Event: {evt}",
                        category=markets[0]["category"] or "unknown",
                        sub_category=markets[0]["sub_category"] or "",
                        signal_type="cross_market_inconsistency",
                        side="yes",
                        entry_price=0,
                        model_prob=0,
                        market_prob=0,
                        edge=abs(total_yes - 1.0),
                        net_ev_cents=0,
                        confidence="low",
                        urgency="watch",
                        reasoning=(
                            f"Event {evt}: {len(markets)} markets sum to {total_yes*100:.0f}c "
                            f"(expected ~100c for exhaustive outcomes). "
                            f"Delta = {abs(total_yes - 1.0)*100:.0f}c. Needs manual review."
                        ),
                        detected_at=datetime.now(timezone.utc).isoformat(),
                        data={
                            "event_ticker": evt,
                            "market_count": len(markets),
                            "total_yes_prob": round(total_yes, 4),
                            "spread_cents": best_spread,
                            "markets": [
                                {"ticker": m["ticker"], "yes_price": m["yes_price"], "title": m["title"][:40]}
                                for m in markets[:5]
                            ],
                        },
                    ))

        return signals

    # ----------------------------------------------------------------
    # Detector 4: Volume Spike
    # ----------------------------------------------------------------

    def detect_volume_signals(self) -> List[Signal]:
        """
        Detect unusual volume activity that may indicate informed trading.
        """
        signals = []

        # Reuse already-fetched latest prices (optimized 3-step query)
        latest = self._get_latest_prices()
        rows = sorted(
            [r for r in latest if r.get("volume", 0) > 100 and r.get("yes_bid", 0) > 0 and r.get("yes_ask", 0) > 0],
            key=lambda r: r.get("volume", 0),
            reverse=True,
        )[:20]

        for row in rows:
            spread_cents = (row["yes_ask"] - row["yes_bid"]) * 100
            if spread_cents > self.MAX_SPREAD_CENTS:
                continue

            mid = row["yes_price"]
            signals.append(Signal(
                ticker=row["ticker"],
                title=row["title"] or row["ticker"],
                category=row["category"] or "unknown",
                sub_category=row["sub_category"] or "",
                signal_type="high_volume",
                side="yes" if mid > 0.5 else "no",
                entry_price=row["yes_bid"] if mid > 0.5 else (1 - row["yes_ask"]),
                model_prob=mid,
                market_prob=mid,
                edge=0,  # no edge estimate without model
                net_ev_cents=0,
                confidence="low",
                urgency="watch",
                reasoning=(
                    f"Volume {row['volume']} with {spread_cents:.0f}c spread. "
                    f"Active market worth monitoring for edge opportunities."
                ),
                detected_at=datetime.now(timezone.utc).isoformat(),
                data={
                    "volume": row["volume"],
                    "open_interest": row["open_interest"],
                    "spread_cents": spread_cents,
                    "mid_price": mid,
                },
            ))

        return signals

    # ----------------------------------------------------------------
    # Master Scanner
    # ----------------------------------------------------------------

    def scan_all(self, category: str = None) -> List[Signal]:
        """
        Run all detectors and return combined, deduplicated signal list.
        """
        all_signals = []

        logger.info("Running signal detectors...")

        # Run each detector
        detectors = [
            ("nba_props", self.detect_nba_prop_signals),
            ("spread", self.detect_spread_signals),
            ("overreaction", self.detect_overreaction_signals),
            ("cross_market", self.detect_cross_market_signals),
            ("volume", self.detect_volume_signals),
        ]

        for name, detector in detectors:
            try:
                signals = detector()
                logger.info(f"  {name}: {len(signals)} signals")
                all_signals.extend(signals)
            except Exception as e:
                logger.error(f"  {name} detector failed: {e}")

        # Filter by category if specified
        if category:
            all_signals = [s for s in all_signals if s.category == category]

        # Sort by: confidence (high first), then net EV, then edge
        conf_order = {"high": 0, "medium": 1, "low": 2}
        all_signals.sort(key=lambda s: (
            conf_order.get(s.confidence, 3),
            -s.net_ev_cents,
            -s.edge,
        ))

        # Deduplicate by ticker (keep highest priority signal per market)
        seen_tickers = set()
        deduped = []
        for s in all_signals:
            if s.ticker not in seen_tickers:
                deduped.append(s)
                seen_tickers.add(s.ticker)

        logger.info(f"Total unique signals: {len(deduped)}")
        return deduped

    def close(self):
        put_connection(self.conn)
        self.conn = None


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Signal Engine - Real-Time +EV Opportunity Detection"
    )
    parser.add_argument("--category", type=str, help="Filter by market category")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--interval", type=int, default=60, help="Monitor interval (seconds)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--min-edge", type=float, default=0.03, help="Minimum edge threshold")
    parser.add_argument("--max-signals", type=int, default=20, help="Max signals to show")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    engine = SignalEngine()
    engine.MIN_EDGE = args.min_edge

    def run_scan():
        signals = engine.scan_all(category=args.category)
        signals = signals[:args.max_signals]

        if args.json:
            print(json.dumps([asdict(s) for s in signals], indent=2, default=str))
        else:
            if not signals:
                print("  No signals detected.")
                return signals

            for i, s in enumerate(signals, 1):
                print(
                    f"\n  Signal #{i}: {s.signal_type}"
                )
                print(f"    Market:     {s.title[:60]}")
                print(f"    Category:   {s.category}/{s.sub_category}")
                print(f"    Side:       {s.side.upper()} at {s.entry_price*100:.0f}c")
                print(f"    Edge:       {s.edge*100:.1f}%")
                print(f"    Net EV:     {s.net_ev_cents:.1f}c/contract")
                print(f"    Confidence: {s.confidence} | Urgency: {s.urgency}")
                print(f"    Reasoning:  {s.reasoning}")

        return signals

    if args.monitor:
        print("=" * 60)
        print("Signal Engine - Continuous Monitoring")
        print(f"  Interval: {args.interval}s | Category: {args.category or 'all'}")
        print("=" * 60)

        cycle = 0
        while True:
            cycle += 1
            print(f"\n--- Scan #{cycle} ({datetime.now().strftime('%H:%M:%S')}) ---")
            try:
                signals = run_scan()
                print(f"  [{len(signals)} signals detected]")
            except Exception as e:
                logger.error(f"Scan failed: {e}")

            time.sleep(args.interval)
    else:
        print("=" * 60)
        print("Signal Engine - Market Scan")
        print("=" * 60)
        run_scan()

    engine.close()


if __name__ == "__main__":
    main()
