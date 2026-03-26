#!/usr/bin/env python3
"""
Auto Trader - Automated Signal-to-Execution Pipeline
======================================================
Prediction Market War Machine

Bridges signal_engine + active_scanner -> trade_engine automatically.

Rules:
  - Edge >= 5% to enter (MIN_EDGE_AUTO)
  - Spread <= 3c (MAX_SPREAD_AUTO)
  - Limit orders ONLY (never market)
  - Unfilled orders auto-cancel after 60 seconds
  - 72h settlement filter
  - safety.py limits CANNOT be bypassed
  - Arbitrage (confirmed profit): immediate, up to $50/trade
  - All actions -> Discord alert

Usage:
    # Live auto-trading
    python scripts/auto_trader.py

    # Dry run (simulates but doesn't execute)
    python scripts/auto_trader.py --dry-run

    # Single scan + execute cycle
    python scripts/auto_trader.py --once
"""

import sys
import io
import os
import json
import time
import logging
import argparse
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import asdict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests
from kalshi_client import KalshiConfig, create_client
from signal_engine import SignalEngine, Signal, net_ev, KALSHI_FEE_RATE
from trade_engine import TradeEngine, TradeOrder
from active_scanner import ActiveScanner
from safety import SafetyManager, SAFETY_CONFIG
from learning_log import LearningLog
from filters import is_within_settlement_window
from calibration import CalibrationTracker
from risk_decomposition import tag_loss_category, format_discord_weekly, generate_loss_report
from self_learner import get_params, SelfLearner

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPER_TRADES_FILE = PROJECT_ROOT / "data" / "paper_trades.jsonl"

def _get_webhook():
    return os.environ.get("DISCORD_WEBHOOK_URL", "")

def _log_paper_trade(ticker: str, title: str, side: str, price_cents: int,
                     count: int, signal_type: str, edge: float, model_prob: float,
                     data: dict = None):
    """Record a paper trade for later settlement verification."""
    entry = {
        "ticker": ticker,
        "title": title,
        "side": side,
        "price_cents": price_cents,
        "count": count,
        "signal_type": signal_type,
        "edge": round(edge, 4),
        "model_prob": round(model_prob, 4),
        "vpin": (data or {}).get("vpin", None),
        "hours_to_settlement": (data or {}).get("hours_to_settlement", None),
        "ev_per_hour": (data or {}).get("ev_per_hour", None),
        "entered_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "source": "auto",
    }
    PAPER_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PAPER_TRADES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

# ============================================================================
# Auto-Trading Thresholds
# ============================================================================

MIN_EDGE_AUTO = 0.05           # 5% minimum edge for auto-entry
MIN_EDGE_ARB = 0.01            # 1% for arbitrage (confirmed profit)
MIN_EDGE_MARKET_ORDER = 0.25   # 25% edge -> use market order (buy at ask for instant fill)
MAX_SPREAD_CENTS_AUTO = 10     # 10c max spread for auto-entry
MAX_SPREAD_CENTS_ARB = 10      # 10c for arbitrage (wider OK since profit is locked)
ORDER_TIMEOUT_SEC = 60         # cancel unfilled orders after 60s
MAX_BET_REGULAR = SAFETY_CONFIG["max_single_bet"]      # $35
MAX_BET_ARB = SAFETY_CONFIG["max_single_bet_arb"]      # $50
MAX_SETTLEMENT_HOURS = SAFETY_CONFIG["max_settlement_hours"]  # 72h


# ============================================================================
# Discord Alerts (Korean emoji format)
# ============================================================================

def _discord(content: str):
    if not _get_webhook():
        logger.info(f"[Discord] {content}")
        return
    try:
        requests.post(_get_webhook(), json={"content": content}, timeout=10)
    except Exception:
        pass


def alert_order_submitted(ticker: str, side: str, price_cents: int, count: int,
                          edge: float, title: str = ""):
    t = (title or ticker)[:40]
    _discord(
        f"\U0001f535 **Order**: {t} | {side.upper()} {price_cents}c x{count} | "
        f"edge {edge*100:.1f}%"
    )


def alert_order_filled(ticker: str, side: str, price_cents: int, count: int,
                       title: str = ""):
    t = (title or ticker)[:40]
    _discord(f"\U0001f7e2 **Filled**: {t} | {side.upper()} {price_cents}c x{count}")


def alert_order_cancelled(ticker: str, title: str = ""):
    t = (title or ticker)[:40]
    _discord(f"\u26aa **Unfilled cancel**: {t}")


def alert_settlement_win(ticker: str, amount: float, title: str = ""):
    t = (title or ticker)[:40]
    _discord(f"\U0001f4b0 **Win**: {t} +${amount:.2f}")


def alert_settlement_loss(ticker: str, amount: float, title: str = ""):
    t = (title or ticker)[:40]
    _discord(f"\U0001f534 **Loss**: {t} -${abs(amount):.2f}")


def alert_daily_limit():
    _discord("\U0001f6d1 **Daily limit reached. Halted until tomorrow.**")


def alert_error(msg: str):
    _discord(f"\u26a0\ufe0f **Error**: {msg[:200]}")


# ============================================================================
# Auto Trader
# ============================================================================

class AutoTrader:
    """
    Automated trading pipeline.

    Scan -> Filter -> Size -> Execute -> Monitor -> Cancel/Fill
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.config = KalshiConfig.from_env()
        self.config.use_demo = False

        self.client = create_client(self.config)
        self.trade_engine = TradeEngine(use_demo=False)
        self.safety = SafetyManager()
        self.learn = LearningLog()
        self.calibration = CalibrationTracker()

        # Track pending orders for timeout cancellation
        self._pending_orders: List[dict] = []
        # Track tickers we've already traded (persists across cycles)
        self._traded_tickers: set = set()

        # Fix 5: Load existing position tickers to prevent duplicate orders on restart
        self._load_existing_positions()

    # ----------------------------------------------------------------
    # Core: Scan + Execute cycle
    # ----------------------------------------------------------------

    def run_cycle(self) -> dict:
        """
        One full scan-and-trade cycle.
        Returns summary of actions taken.
        """
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signals_found": 0,
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_cancelled": 0,
            "arb_found": 0,
            "arb_executed": 0,
        }

        # Check if we can trade at all
        can_result = self.safety.can_bet()
        can_bet = can_result[0] if isinstance(can_result, tuple) else can_result
        if not can_bet:
            reason = can_result[1] if isinstance(can_result, tuple) else ""
            logger.info(f"[AutoTrader] Cannot trade: {reason}")
            if "daily" in str(reason).lower() or "weekly" in str(reason).lower():
                alert_daily_limit()
            return results

        # 1. Signal Engine scan (72h filter applied internally)
        try:
            self._process_signals(results)
        except Exception as e:
            logger.error(f"[AutoTrader] Signal scan error: {e}")
            alert_error(f"Signal scan: {e}")

        # 2. Arbitrage scan
        try:
            self._process_arbitrage(results)
        except Exception as e:
            logger.error(f"[AutoTrader] Arb scan error: {e}")
            alert_error(f"Arb scan: {e}")

        # 3. Cancel timed-out orders
        try:
            self._cancel_stale_orders(results)
        except Exception as e:
            logger.error(f"[AutoTrader] Cancel error: {e}")

        # 4. Sync positions with Kalshi (every cycle)
        try:
            self.sync_positions()
        except Exception as e:
            logger.error(f"[AutoTrader] Position sync error: {e}")

        # 5. Check paper trade settlements
        try:
            self.check_paper_settlements()
        except Exception as e:
            logger.error(f"[AutoTrader] Paper settlement check error: {e}")

        return results

    # ----------------------------------------------------------------
    # Signal Processing
    # ----------------------------------------------------------------

    def _process_signals(self, results: dict):
        """Scan signal_engine and auto-execute qualifying signals."""
        engine = SignalEngine(max_settlement_hours=MAX_SETTLEMENT_HOURS)
        signals = engine.scan_all()
        engine.close()

        results["signals_found"] = len(signals)

        # Sort by ev_per_hour descending: capital-efficient trades first
        signals.sort(
            key=lambda s: s.data.get("ev_per_hour", 0),
            reverse=True,
        )

        for sig in signals:
            if sig.ticker in self._traded_tickers:
                continue

            # HARD BLOCK: parlay / cross-category / multi-game / multi-leg markets
            ticker_upper = sig.ticker.upper()
            title_lower = (sig.title or "").lower()

            # Block by ticker keywords
            if any(kw in ticker_upper for kw in (
                "CROSSCATEGORY", "MULTIGAME", "PARLAY", "COMBO",
                "ACCUMULATOR", "MULTI", "MVE",
            )):
                continue

            # Block by title: if title has 2+ commas, it's a multi-leg market
            if (sig.title or "").count(",") >= 2:
                continue

            # Block: title starting with "yes " or "no " = multi-outcome combo
            if title_lower.startswith("yes ") or title_lower.startswith("no "):
                continue

            # Block: any "yes [team]" or "no [team]" patterns with commas = parlay
            if ",yes" in title_lower or ",no " in title_lower:
                continue

            # Filter: edge threshold (uses tuned param)
            tuned_min_edge = get_params().get("min_edge")
            if sig.edge < tuned_min_edge:
                self.learn.signal_skipped(
                    sig.ticker, sig.signal_type,
                    f"edge {sig.edge*100:.1f}% < {tuned_min_edge*100:.0f}% threshold",
                    price=sig.entry_price, edge=sig.edge,
                )
                continue

            # Filter: spread (uses tuned param)
            spread_cents = sig.data.get("spread_cents", 99)
            tuned_max_spread = get_params().get("max_spread_cents")
            if spread_cents > tuned_max_spread:
                self.learn.signal_skipped(
                    sig.ticker, sig.signal_type,
                    f"spread {spread_cents:.0f}c > {tuned_max_spread}c limit",
                    price=sig.entry_price, edge=sig.edge,
                )
                continue

            # Filter: edge must cover spread cost (edge >= spread * 2)
            # Wider spreads eat into profit, so edge needs to be proportionally larger
            spread_ratio = spread_cents / 100
            if sig.edge < spread_ratio * 2:
                self.learn.signal_skipped(
                    sig.ticker, sig.signal_type,
                    f"edge {sig.edge*100:.1f}% < spread cost {spread_ratio*200:.1f}% (2x spread rule)",
                    price=sig.entry_price, edge=sig.edge,
                )
                continue

            # Filter: confidence
            if sig.confidence == "low":
                self.learn.signal_skipped(
                    sig.ticker, sig.signal_type,
                    "low confidence",
                    price=sig.entry_price, edge=sig.edge,
                )
                continue

            # Determine entry price: market order (ask) for high edge, limit order for normal edge
            yes_ask = sig.data.get("yes_ask", 0)
            tuned_mkt_edge = get_params().get("min_edge_market_order")
            if sig.edge >= tuned_mkt_edge and yes_ask > 0:
                # High edge: buy at ask price for instant fill
                if sig.side == "yes":
                    price_cents = int(yes_ask * 100)
                else:
                    # For NO side, ask = 1 - yes_bid
                    yes_bid = sig.data.get("yes_bid", 0)
                    price_cents = int((1 - yes_bid) * 100) if yes_bid > 0 else int(sig.entry_price * 100)
                logger.info(
                    f"[AutoTrader] MARKET ORDER: {sig.ticker} edge={sig.edge*100:.1f}% -> "
                    f"buying at ask {price_cents}c (instant fill)"
                )
            else:
                # Normal edge: use limit order at model entry price
                price_cents = int(sig.entry_price * 100)

            if price_cents < 1 or price_cents > 99:
                continue

            count = self._calculate_size(
                sig.model_prob, sig.entry_price, sig.side, MAX_BET_REGULAR,
                confidence=sig.confidence,
                data_points=sig.data.get("data_points", sig.data.get("history_len", 10)),
            )
            if count <= 0:
                continue

            # Execute
            self._execute_signal(sig, price_cents, count, results)

    def _process_arbitrage(self, results: dict):
        """Scan for arbitrage and auto-execute."""
        scanner = ActiveScanner(
            use_prod=True,
            max_settlement_hours=MAX_SETTLEMENT_HOURS,
        )
        arb_results = scanner.run_arbitrage(max_pages=5)
        opps = arb_results.get("opportunities", [])
        actionable = [o for o in opps if o.get("opportunity_type") != "monitor"]
        results["arb_found"] = len(actionable)

        for opp in actionable:
            if opp.get("net_edge_cents", 0) < 1:
                continue

            # For buy_all_yes arbitrage: buy each market's YES
            if opp["opportunity_type"] == "buy_all_yes":
                self._execute_arb_buy_all(opp, results)

    def _execute_arb_buy_all(self, opp: dict, results: dict):
        """Execute a buy-all-YES arbitrage across multiple markets."""
        markets = opp.get("markets", [])
        total_ask = opp.get("total_ask", 1)

        # Budget: max $50 for arb, split across markets
        budget = min(MAX_BET_ARB, total_ask * 100)  # don't spend more than total ask
        if budget < 1:
            return

        for mkt in markets:
            ticker = mkt.get("ticker", "")
            ask = mkt.get("yes_ask", 0)
            if not ticker or ask <= 0 or ticker in self._traded_tickers:
                continue

            price_cents = int(ask * 100)
            if price_cents < 1 or price_cents > 99:
                continue

            # How many contracts? budget / total_ask gives scale factor
            count = max(1, int(budget / (total_ask * 100)))
            count = min(count, int(MAX_BET_ARB / ask))

            edge = opp.get("net_edge", 0)

            order = self.trade_engine.prepare_order(
                ticker=ticker,
                side="yes",
                price_cents=price_cents,
                count=count,
                signal_type=f"arb:{opp['opportunity_type']}",
                reasoning=f"Arb buy-all-YES: event {opp.get('event_ticker', '?')}, net edge {opp.get('net_edge_cents', 0):.1f}c",
                model_prob=1.0,  # arb = guaranteed
                edge=edge,
            )

            self._submit_order(order, opp.get("title", ticker), results)
            results["arb_executed"] = results.get("arb_executed", 0) + 1

    # ----------------------------------------------------------------
    # Execution
    # ----------------------------------------------------------------

    def _execute_signal(self, sig: Signal, price_cents: int, count: int, results: dict):
        """Execute a single signal trade."""
        order = self.trade_engine.prepare_order(
            ticker=sig.ticker,
            side=sig.side,
            price_cents=price_cents,
            count=count,
            signal_type=sig.signal_type,
            reasoning=sig.reasoning[:200],
            model_prob=sig.model_prob,
            edge=sig.edge,
        )
        self._submit_order(order, sig.title, results, sig_data=sig.data)

    def _submit_order(self, order: TradeOrder, title: str, results: dict, sig_data: dict = None):
        """Submit order and track for timeout."""
        # Validate through safety
        valid, reason = self.trade_engine.validate_trade(order)
        if not valid:
            logger.info(f"[AutoTrader] Rejected {order.ticker}: {reason}")
            self.learn.signal_skipped(
                order.ticker, order.signal_type, f"validation: {reason}",
                price=order.price_cents / 100, edge=order.edge,
            )
            return

        if self.dry_run:
            result = self.trade_engine.execute_order(order, dry_run=True)
            logger.info(
                f"[AutoTrader] PAPER: {order.side.upper()} {order.ticker} "
                f"@ {order.price_cents}c x{order.count} edge={order.edge*100:.1f}%"
            )
            # Record paper trade for settlement tracking
            _log_paper_trade(
                order.ticker, title, order.side, order.price_cents,
                order.count, order.signal_type, order.edge, order.model_prob,
                data=sig_data,
            )
            results["orders_submitted"] = results.get("orders_submitted", 0) + 1
            results["orders_filled"] = results.get("orders_filled", 0) + 1
        else:
            result = self.trade_engine.execute_order(order, dry_run=False)

            if result.get("status") == "submitted":
                order_id = result.get("order_id", "")
                alert_order_submitted(
                    order.ticker, order.side, order.price_cents,
                    order.count, order.edge, title,
                )
                results["orders_submitted"] = results.get("orders_submitted", 0) + 1

                # Track for timeout cancellation
                self._pending_orders.append({
                    "order_id": order_id,
                    "ticker": order.ticker,
                    "title": title,
                    "submitted_at": time.time(),
                    "side": order.side,
                    "price_cents": order.price_cents,
                    "count": order.count,
                })

                self.learn.trade_entered(
                    ticker=order.ticker, side=order.side,
                    price=order.price_cents / 100, count=order.count,
                    signal_type=order.signal_type, edge=order.edge,
                    reasoning=order.reasoning[:200],
                )
            elif result.get("status") == "rejected":
                logger.warning(f"[AutoTrader] Rejected: {result.get('reason')}")
            elif result.get("status") == "error":
                alert_error(f"Order error for {order.ticker}: {result.get('error')}")

        self._traded_tickers.add(order.ticker)

    # ----------------------------------------------------------------
    # Order Management
    # ----------------------------------------------------------------

    def _cancel_stale_orders(self, results: dict):
        """Cancel orders that haven't filled within ORDER_TIMEOUT_SEC."""
        if self.dry_run:
            return

        now = time.time()
        still_pending = []

        for pending in self._pending_orders:
            age = now - pending["submitted_at"]
            order_id = pending["order_id"]

            if age >= ORDER_TIMEOUT_SEC:
                # Check if filled
                try:
                    order_status = self.client.get_orders(
                        ticker=pending["ticker"], status="resting"
                    )
                    resting = [
                        o for o in order_status.get("orders", [])
                        if o.get("order_id") == order_id
                    ]

                    if resting:
                        # Still resting -> cancel
                        self.client.cancel_order(order_id)
                        alert_order_cancelled(pending["ticker"], pending["title"])
                        results["orders_cancelled"] = results.get("orders_cancelled", 0) + 1
                        logger.info(f"[AutoTrader] Cancelled stale order: {pending['ticker']}")

                        # Remove position tracking for unfilled order
                        self.trade_engine.positions.reduce_position(
                            pending["ticker"], pending["side"], pending["count"]
                        )
                    else:
                        # Not resting = filled or already cancelled
                        alert_order_filled(
                            pending["ticker"], pending["side"],
                            pending["price_cents"], pending["count"],
                            pending["title"],
                        )
                        results["orders_filled"] = results.get("orders_filled", 0) + 1

                except Exception as e:
                    logger.error(f"[AutoTrader] Cancel check error: {e}")
                    still_pending.append(pending)  # retry next cycle
            else:
                still_pending.append(pending)

        self._pending_orders = still_pending

    def check_settlements(self):
        """Check if any positions have settled and log results."""
        positions = self.trade_engine.positions.get_positions()
        for pos in positions:
            ticker = pos.get("ticker", "")
            try:
                market_data = self.client.get_market(ticker)
                market = market_data.get("market", market_data)
                result = market.get("result", "")

                if result in ("yes", "no"):
                    entry = pos["avg_entry_price"]
                    side = pos["side"]
                    count = pos["count"]

                    if (side == "yes" and result == "yes") or (side == "no" and result == "no"):
                        # Win
                        gross = (1 - entry) * count
                        fee = gross * KALSHI_FEE_RATE
                        pnl = gross - fee
                        alert_settlement_win(ticker, pnl, pos.get("title", ""))
                        self.learn.trade_exited(
                            ticker, side, entry, 1.0, count, pnl, "settled:win"
                        )
                    else:
                        # Loss
                        pnl = -entry * count
                        alert_settlement_loss(ticker, pnl, pos.get("title", ""))
                        self.learn.trade_exited(
                            ticker, side, entry, 0.0, count, pnl, "settled:loss"
                        )
                        self.learn.trade_wrong(
                            ticker, side, entry, 0.0, pnl,
                            f"Market settled {result}, we held {side}",
                            "Review signal quality for this market type",
                        )

                    # Brier Score + Calibration (auto trades only)
                    sig_type = pos.get("signal_type", "")
                    if sig_type and sig_type != "manual" and sig_type != "synced_from_kalshi":
                        model_prob_val = pos.get("avg_entry_price", 0.5)
                        outcome = 1.0 if pnl > 0 else 0.0
                        brier = (model_prob_val - outcome) ** 2
                        self.learn._write({
                            "type": "brier_score",
                            "ticker": ticker,
                            "model_prob": round(model_prob_val, 4),
                            "outcome": outcome,
                            "score": round(brier, 4),
                            "pnl": round(pnl, 2),
                            "source": "live",
                        })
                        self.calibration.record(model_prob_val, int(outcome), ticker=ticker)

                    # Record safety result
                    self.safety.record_result(pnl > 0, pnl)

                    # Remove position
                    self.trade_engine.positions.reduce_position(ticker, side, count)

            except Exception as e:
                logger.debug(f"Settlement check error for {ticker}: {e}")

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _calculate_size(self, model_prob: float, price: float,
                        side: str, max_bet: float,
                        confidence: str = "medium",
                        data_points: int = 10) -> int:
        """
        Fractional Kelly position sizing.

        Full Kelly is theoretically optimal but assumes perfect edge estimation.
        When edge estimates are noisy, full Kelly leads to ruin.

        Fractions:
          high confidence   -> 0.50 Kelly (half)
          medium confidence -> 0.25 Kelly (quarter)
          low confidence    -> 0 (don't trade)

        Additional: if data_points < 5, halve the fraction again (data uncertainty).
        """
        entry = price if side == "yes" else (1 - price)
        if entry <= 0 or entry >= 1:
            return 0

        # Kelly fraction based on confidence (uses tuned params)
        tp = get_params()
        if confidence == "high":
            kelly_frac = tp.get("kelly_high")
        elif confidence == "medium":
            kelly_frac = tp.get("kelly_medium")
        else:
            return 0  # low confidence = no trade

        # Data scarcity penalty: halve if < 5 data points
        if data_points < 5:
            kelly_frac *= 0.5

        # Full Kelly count from trade engine
        full_kelly_count = self.trade_engine.size_order(model_prob, price, side)

        # Apply fractional Kelly
        count = int(full_kelly_count * kelly_frac)

        # Cap by max bet
        max_count = int(max_bet / entry)
        count = min(count, max_count)

        # Minimum 1 contract
        return max(1, count) if count > 0 else 0

    def check_paper_settlements(self):
        """Check if any paper trades have settled and record results."""
        if not PAPER_TRADES_FILE.exists():
            return

        lines = []
        with open(PAPER_TRADES_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        updated = []
        wins = 0
        losses = 0
        for line in lines:
            entry = json.loads(line)
            # Skip non-auto entries entirely
            if entry.get("source") != "auto":
                continue
            if entry.get("status") != "open":
                updated.append(line)
                continue

            ticker = entry["ticker"]
            try:
                market_data = self.client.get_market(ticker)
                market = market_data.get("market", market_data)
                result = market.get("result", "")

                if result in ("yes", "no"):
                    side = entry["side"]
                    price = entry["price_cents"] / 100
                    count = entry["count"]
                    won = (side == "yes" and result == "yes") or (side == "no" and result == "no")

                    if won:
                        pnl = (1 - price) * count
                        entry["status"] = "win"
                        wins += 1
                    else:
                        pnl = -price * count
                        entry["status"] = "loss"
                        losses += 1

                    entry["settled_at"] = datetime.now(timezone.utc).isoformat()
                    entry["market_result"] = result
                    entry["paper_pnl"] = round(pnl, 2)

                    # Brier Score + Calibration
                    mp = entry.get("model_prob", 0.5)
                    outcome = 1.0 if won else 0.0
                    brier = (mp - outcome) ** 2
                    entry["brier_score"] = round(brier, 4)

                    # Record to learning log
                    self.learn._write({
                        "type": "brier_score",
                        "ticker": ticker,
                        "model_prob": round(mp, 4),
                        "outcome": outcome,
                        "score": round(brier, 4),
                        "paper_pnl": round(pnl, 2),
                        "source": "paper",
                    })

                    # Record to calibration tracker
                    self.calibration.record(mp, int(outcome), ticker=ticker)

                    # Tag loss category for risk decomposition
                    tag_loss_category(entry)

                    updated.append(json.dumps(entry))
                else:
                    updated.append(line)  # still open
            except Exception:
                updated.append(line)

        # Rewrite file
        with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(updated) + "\n" if updated else "")

        if wins or losses:
            total = wins + losses
            logger.info(f"[Paper] Settled {total}: {wins}W {losses}L ({wins/total*100:.0f}%)")

            # Calibration report
            try:
                brier = self.calibration.get_brier_score()
                bias = self.calibration.get_bias()
                n = self.calibration.state["total_predictions"]
                logger.info(
                    f"[Calibration] Brier={brier:.4f} | "
                    f"bias={bias['direction']} ({bias['magnitude']*100:.1f}pp) | "
                    f"n={n}"
                )
                if brier >= 0.25 and n >= 10:
                    _discord(
                        f"\u26a0\ufe0f **Model accuracy deteriorating** \u2014 "
                        f"Brier={brier:.3f} (>0.25 = worse than coin flip). "
                        f"Bias: {bias['direction']}. n={n}"
                    )
            except Exception as e:
                logger.error(f"[Calibration] Error: {e}")

        # === 25-trade milestone report (fires once) ===
        self._check_milestone_report()

    def _check_milestone_report(self):
        """Send a one-time Discord report when 25 settled paper trades are reached."""
        marker = PROJECT_ROOT / "data" / ".milestone_25_sent"
        if marker.exists():
            return

        # Count settled auto trades
        if not PAPER_TRADES_FILE.exists():
            return
        settled = []
        with open(PAPER_TRADES_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("source") == "auto" and d.get("status") in ("win", "loss"):
                    settled.append(d)

        if len(settled) < 25:
            return

        # Reached 25! Build report
        logger.info(f"[Milestone] 25 settled paper trades reached! Sending report.")

        wins = sum(1 for s in settled if s["status"] == "win")
        losses = len(settled) - wins
        win_rate = wins / len(settled) * 100

        paper_pnl = sum(s.get("paper_pnl", 0) for s in settled)

        brier = self.calibration.get_brier_score()
        bias = self.calibration.get_bias()

        loss_report = generate_loss_report()
        loss_cats = loss_report.get("categories", {})

        from self_learner import SelfLearner
        learner = SelfLearner()
        can_tune = learner.should_tune()

        # Format
        lines = [
            f"**Settled**: {len(settled)} trades",
            f"**Win Rate**: {wins}W / {losses}L ({win_rate:.1f}%)",
            f"**Paper P&L**: ${paper_pnl:+.2f}",
            "",
            f"**Brier Score**: {brier:.4f} {'(good)' if brier < 0.20 else '(needs work)' if brier < 0.25 else '(poor)'}",
            f"**Bias**: {bias['direction']} ({bias['magnitude']*100:.1f}pp)",
            "",
            "**Loss Breakdown**:",
        ]
        for cat, data in sorted(loss_cats.items(), key=lambda x: x[1].get("amount", 0), reverse=True):
            lines.append(f"  {cat.replace('_',' ').title()}: {data['count']} trades ({data['pct_amount']:.0f}%)")

        lines.append("")
        n_preds = self.calibration.state.get("total_predictions", 0)
        sl_status = "Ready to tune" if can_tune else f"Waiting ({n_preds}/50 predictions)"
        lines.append(f"**Self-Learner**: {sl_status}")

        _discord(
            f"\U0001f4ca **25-Trade Milestone Report**\n"
            + "\n".join(lines)
        )

        # Mark as sent
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now(timezone.utc).isoformat())

    def _load_existing_positions(self):
        """Fix 5: Load existing position tickers on startup to prevent duplicates."""
        existing_tickers = self.trade_engine.positions.get_tickers()
        self._traded_tickers.update(existing_tickers)
        if existing_tickers:
            logger.info(
                f"[AutoTrader] Loaded {len(existing_tickers)} existing positions: "
                f"{', '.join(t[:30] for t in list(existing_tickers)[:5])}"
                f"{'...' if len(existing_tickers) > 5 else ''}"
            )

    def sync_positions(self):
        """Fix 1+4: Sync local positions with Kalshi API and warn on mismatch."""
        if self.dry_run:
            return

        changes = self.trade_engine.positions.sync_with_kalshi(self.client)

        # Update traded_tickers with any new positions from Kalshi
        current_tickers = self.trade_engine.positions.get_tickers()
        self._traded_tickers.update(current_tickers)

        # Fix 4: Discord warning on mismatch
        if changes.get("mismatches"):
            for m in changes["mismatches"]:
                _discord(
                    f"\u26a0\ufe0f **Position mismatch**: {m['ticker'][:40]} "
                    f"local={m['local_count']} vs Kalshi={m['kalshi_count']} "
                    f"(synced to Kalshi)"
                )

        if changes.get("removed"):
            for t in changes["removed"]:
                logger.info(f"[Sync] Removed local position (not on Kalshi): {t[:50]}")

        if changes.get("added"):
            for t in changes["added"]:
                logger.info(f"[Sync] Added position from Kalshi: {t[:50]}")
                _discord(f"\u26a0\ufe0f **Position found on Kalshi not in local**: {t[:40]}")

    def reset_cycle(self):
        """Reset per-cycle state. Keep _traded_tickers across cycles to prevent duplicates."""
        # DO NOT clear _traded_tickers — prevents duplicate orders on same market
        pass


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Auto Trader - Automated Execution")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without executing")
    parser.add_argument("--once", action="store_true", help="Single cycle then exit")
    parser.add_argument("--interval", type=int, default=120, help="Seconds between cycles")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    mode = "DRY RUN" if args.dry_run else "*** LIVE ***"
    print()
    print("  ====================================================")
    print(f"  AUTO TRADER - {mode}")
    print("  ====================================================")
    print(f"  Min Edge:       {MIN_EDGE_AUTO*100:.0f}%")
    print(f"  Max Spread:     {MAX_SPREAD_CENTS_AUTO}c")
    print(f"  Order Timeout:  {ORDER_TIMEOUT_SEC}s")
    print(f"  Max Bet:        ${MAX_BET_REGULAR} (arb: ${MAX_BET_ARB})")
    print(f"  Settlement:     {MAX_SETTLEMENT_HOURS}h")
    print(f"  Interval:       {args.interval}s")
    print("  ====================================================")
    print()

    if not args.dry_run:
        _discord(
            "\U0001f680 **War Machine AUTO TRADER started** | "
            f"edge>={MIN_EDGE_AUTO*100:.0f}% | spread<={MAX_SPREAD_CENTS_AUTO}c | "
            f"timeout={ORDER_TIMEOUT_SEC}s | max=${MAX_BET_REGULAR}"
        )

    trader = AutoTrader(dry_run=args.dry_run)
    cycle = 0

    try:
        while True:
            cycle += 1
            trader.reset_cycle()

            logger.info(f"--- Auto-Trade Cycle {cycle} ---")
            results = trader.run_cycle()

            # Also check settlements
            trader.check_settlements()

            logger.info(
                f"Cycle {cycle}: "
                f"signals={results['signals_found']} "
                f"submitted={results['orders_submitted']} "
                f"filled={results['orders_filled']} "
                f"cancelled={results['orders_cancelled']} "
                f"arb={results['arb_found']}/{results['arb_executed']}"
            )

            if args.once:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        if not args.dry_run:
            _discord("\u23f9\ufe0f **War Machine AUTO TRADER stopped**")


if __name__ == "__main__":
    main()
