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

from db import get_connection, put_connection
from kalshi_client import KalshiConfig, create_client
from signal_engine import SignalEngine, Signal, net_ev, KALSHI_FEE_RATE
from trade_engine import TradeEngine, TradeOrder
from active_scanner import ActiveScanner
from safety import (
    SafetyManager, SAFETY_CONFIG,
    CircuitBreakerException, BankruptcyException, DrawdownBreakerException,
)
from bankroll import kelly_bet_size
from kill_switch import KillSwitch
from learning_log import LearningLog
from filters import is_within_settlement_window
from calibration import CalibrationTracker
from risk_decomposition import tag_loss_category
from self_learner import get_params, SelfLearner

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAPER_TRADES_FILE = PROJECT_ROOT / "data" / "paper_trades.jsonl"


def should_place_bet(prop_type: str, side: str, calculated_edge: float) -> bool:
    """v4_edge_optimized: prop type gate + side-aware edge threshold."""
    prop_min = SignalEngine.PROP_MIN_EDGE.get(prop_type, 0.25)
    if prop_min is None:  # prop type disabled
        return False
    if side == "yes":
        prop_min += SignalEngine.YES_EDGE_PREMIUM
    effective_min = max(prop_min, SignalEngine.GLOBAL_MIN_EDGE)
    return calculated_edge >= effective_min


def _log_paper_trade(ticker: str, title: str, side: str, price_cents: int,
                     count: int, signal_type: str, edge: float, model_prob: float,
                     data: dict = None, execution_mode: str = "paper"):
    """Record a paper trade for later settlement verification."""
    # Kelly sizing info
    entry_price = price_cents / 100.0
    odds = (1 - entry_price) / entry_price if 0 < entry_price < 1 else 0
    safety = SafetyManager()
    k_frac = 0.25
    k_bet = kelly_bet_size(edge, odds, safety.bankroll, k_frac) if odds > 0 else 0
    if odds > 0:
        p = edge + (1 / (1 + odds))
        q = 1 - p
        k_full_pct = max(0, (odds * p - q) / odds)
    else:
        k_full_pct = 0

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
        "kelly_fraction": k_frac,
        "kelly_bet_size": round(k_bet, 2),
        "kelly_full_pct": round(k_full_pct, 4),
        "bet_contracts": count,
        "execution_mode": execution_mode,
    }
    PAPER_TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PAPER_TRADES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

# ============================================================================
# SAFETY KILL SWITCH
# ============================================================================
# Set True to force ALL trades to paper mode (emergency override).
FORCE_DRY_RUN = False

# ============================================================================
# LIVE PROP TYPE ROUTING
# ============================================================================
# Only these prop types execute via Kalshi API (real money).
# All other prop types remain paper-traded.
# FORCE_DRY_RUN=True overrides this (everything stays paper).
LIVE_PROP_TYPES = {"rebounds", "points", "assists"}
MAX_OPEN_POSITIONS = 10

# ============================================================================
# Auto-Trading Thresholds
# ============================================================================

MIN_EDGE_AUTO = 0.10           # 10% minimum edge for auto-entry (was 5%, fee-adjusted backtest confirmed <10% is negative ROI)
MIN_EDGE_ARB = 0.01            # 1% for arbitrage (confirmed profit)
MIN_EDGE_MARKET_ORDER = 0.25   # 25% edge -> use market order (buy at ask for instant fill)
MAX_SPREAD_CENTS_AUTO = 10     # 10c max spread for auto-entry
MAX_SPREAD_CENTS_ARB = 10      # 10c for arbitrage (wider OK since profit is locked)
ORDER_TIMEOUT_SEC = 60         # cancel unfilled orders after 60s
MAX_BET_REGULAR = SAFETY_CONFIG["max_single_bet"]      # $35
MAX_BET_ARB = SAFETY_CONFIG["max_single_bet_arb"]      # $50
MAX_SETTLEMENT_HOURS = SAFETY_CONFIG["max_settlement_hours"]  # 72h


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
        self.kill_switch = KillSwitch()

        # Track pending orders for timeout cancellation
        self._pending_orders: List[dict] = []
        # Track tickers we've already traded (persists across cycles)
        self._traded_tickers: set = set()

        # Fix 5: Load existing position tickers to prevent duplicate orders on restart
        self.settlement_ledger_path = Path(__file__).resolve().parent.parent / "data" / "settlement_ledger.jsonl"
        self._ledger_tickers_cache = None  # lazy-loaded set of processed tickers
        self._load_existing_positions()

    # ----------------------------------------------------------------
    # Helpers: emergency kill switch engagement
    # ----------------------------------------------------------------

    def _engage_kill_switch(self, reason: str) -> None:
        """Write data/kill_switch.json so subsequent runs also block trades.

        Used when a CircuitBreakerException is raised during run_cycle:
        the exception is serialized as the kill_switch reason, so even if
        the process restarts it cannot resume trading until the file is
        removed manually.
        """
        from pathlib import Path as _Path
        import json as _json
        repo_root = _Path(__file__).resolve().parent.parent
        target = repo_root / "data" / "kill_switch.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "enabled": True,
            "reason": reason,
            "set_by": "circuit_breaker",
            "set_at": datetime.now(timezone.utc).isoformat(),
        }
        target.write_text(_json.dumps(payload, indent=2))
        logger.critical(f"[AutoTrader] kill_switch.json engaged: {reason}")

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

        # Kill switch early check (cycle-level gate). Skip before any expensive
        # scans so an engaged switch stops the cycle at the top.
        ks_status = self.kill_switch.check()
        if ks_status.active:
            logger.warning(
                f"[AutoTrader] Kill switch engaged ({ks_status.reason!r}) - "
                f"skipping entire cycle"
            )
            return results

        # Check if we can trade at all
        can_result = self.safety.can_bet()
        can_bet = can_result[0] if isinstance(can_result, tuple) else can_result
        if not can_bet:
            reason = can_result[1] if isinstance(can_result, tuple) else ""
            logger.info(f"[AutoTrader] Cannot trade: {reason}")
            return results

        # Wrap the full cycle body so a CircuitBreakerException raised deep
        # inside record_result() triggers auto-engagement of kill_switch.
        try:
            # Idempotent settlement check every cycle — catches Kalshi-settled
            # markets that were missed during runner downtime or sleep.
            try:
                self.check_settlements()
            except Exception as _e:
                logger.warning(f"[Cycle] check_settlements failed: {_e}")

            return self._run_cycle_body(results)
        except BankruptcyException as e:
            logger.critical(f"[CIRCUIT BREAKER / BANKRUPTCY] {e}")
            self._engage_kill_switch(reason=f"bankruptcy: {e}")
            raise
        except DrawdownBreakerException as e:
            logger.critical(f"[CIRCUIT BREAKER / DRAWDOWN RED] {e}")
            self._engage_kill_switch(reason=f"drawdown_red: {e}")
            raise
        except CircuitBreakerException as e:
            logger.critical(f"[CIRCUIT BREAKER] {e}")
            self._engage_kill_switch(reason=f"circuit_breaker: {e}")
            raise

    def _run_cycle_body(self, results: dict) -> dict:
        """Inner cycle steps. CircuitBreakerException propagates up to run_cycle."""
        # 1. Signal Engine scan (72h filter applied internally)
        try:
            self._process_signals(results)
        except Exception as e:
            logger.error(f"[AutoTrader] Signal scan error: {e}")

        # 2. Arbitrage scan
        try:
            self._process_arbitrage(results)
        except Exception as e:
            logger.error(f"[AutoTrader] Arb scan error: {e}")

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

        # 5. Exit monitor: check open positions for mid-trade exits
        try:
            exit_results = self.exit_monitor()
            if exit_results.get("exits_triggered", 0) > 0:
                logger.info(f"[AutoTrader] Exit monitor: {exit_results}")
        except Exception as e:
            logger.error(f"[AutoTrader] Exit monitor error: {e}")

        # 6. Check paper trade settlements
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

        # Max open positions check
        open_positions = len(self.trade_engine.positions.get_positions())

        # Sort by ev_per_hour descending: capital-efficient trades first
        signals.sort(
            key=lambda s: s.data.get("ev_per_hour", 0),
            reverse=True,
        )

        for sig in signals:
            if open_positions >= MAX_OPEN_POSITIONS:
                logger.info(f"[AutoTrader] Max open positions ({MAX_OPEN_POSITIONS}) reached, skipping remaining signals")
                break

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

            # === Phase 1: NBA-only auto-entry ===
            # Non-NBA markets: self-referential model has no real edge.
            # Only allow nba_prop_model signals (from real basketball data).
            # Other signal types (overreaction_fade, spread, volume) from non-NBA
            # markets are blocked until those markets get real models too.
            if sig.signal_type != "nba_prop_model":
                self.learn.signal_skipped(
                    sig.ticker, sig.signal_type,
                    f"Phase 1: only NBA prop model signals allowed (got {sig.signal_type})",
                    price=sig.entry_price, edge=sig.edge,
                )
                continue

            # Filter: v4 prop/side-aware edge gate
            prop_type = sig.data.get("prop_type", "")
            if not should_place_bet(prop_type, sig.side, sig.edge):
                self.learn.signal_skipped(
                    sig.ticker, sig.signal_type,
                    f"should_place_bet=False prop={prop_type} side={sig.side} edge={sig.edge*100:.1f}%",
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

            # Filter: edge must cover spread cost (YES only)
            # NO bets: PROP_MIN_EDGE in signal_engine already ensures sufficient edge
            spread_ratio = spread_cents / 100
            if sig.side == "yes" and sig.edge < spread_ratio * 2:
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
            open_positions += 1

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

            self._submit_order(order, opp.get("title", ticker), results, prop_type="arb")
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
        prop_type = sig.data.get("prop_type", "")
        self._submit_order(order, sig.title, results, sig_data=sig.data, prop_type=prop_type)

    def _submit_order(self, order: TradeOrder, title: str, results: dict,
                      sig_data: dict = None, prop_type: str = ""):
        """Submit order and track for timeout. Routes live vs paper per prop_type."""
        # Validate through safety
        valid, reason = self.trade_engine.validate_trade(order)
        if not valid:
            logger.info(f"[AutoTrader] Rejected {order.ticker}: {reason}")
            self.learn.signal_skipped(
                order.ticker, order.signal_type, f"validation: {reason}",
                price=order.price_cents / 100, edge=order.edge,
            )
            return

        # Per-signal live/paper routing
        signal_is_live = (
            not self.dry_run
            and not FORCE_DRY_RUN
            and prop_type in LIVE_PROP_TYPES
        )
        execution_mode = "live" if signal_is_live else "paper"

        if not signal_is_live:
            result = self.trade_engine.execute_order(order, dry_run=True)
            logger.info(
                f"[AutoTrader] PAPER: {order.side.upper()} {order.ticker} "
                f"@ {order.price_cents}c x{order.count} edge={order.edge*100:.1f}% "
                f"[prop={prop_type}]"
            )
            _log_paper_trade(
                order.ticker, title, order.side, order.price_cents,
                order.count, order.signal_type, order.edge, order.model_prob,
                data=sig_data, execution_mode=execution_mode,
            )
            results["orders_submitted"] = results.get("orders_submitted", 0) + 1
            results["orders_filled"] = results.get("orders_filled", 0) + 1
        else:
            logger.info(
                f"[AutoTrader] *** LIVE ***: {order.side.upper()} {order.ticker} "
                f"@ {order.price_cents}c x{order.count} edge={order.edge*100:.1f}% "
                f"[prop={prop_type}]"
            )
            result = self.trade_engine.execute_order(order, dry_run=False)

            if result.get("status") == "submitted":
                order_id = result.get("order_id", "")
                results["orders_submitted"] = results.get("orders_submitted", 0) + 1

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
                logger.error(f"[AutoTrader] Order error for {order.ticker}: {result.get('error')}")

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
                        results["orders_cancelled"] = results.get("orders_cancelled", 0) + 1
                        logger.info(f"[AutoTrader] Cancelled stale order: {pending['ticker']}")

                        # Remove position tracking for unfilled order
                        self.trade_engine.positions.reduce_position(
                            pending["ticker"], pending["side"], pending["count"]
                        )
                    else:
                        # Not resting = filled or already cancelled
                        results["orders_filled"] = results.get("orders_filled", 0) + 1

                except Exception as e:
                    logger.error(f"[AutoTrader] Cancel check error: {e}")
                    still_pending.append(pending)  # retry next cycle
            else:
                still_pending.append(pending)

        self._pending_orders = still_pending

    def _load_ledger_tickers(self):
        """Lazy-load processed ticker set from ledger file."""
        if self._ledger_tickers_cache is not None:
            return self._ledger_tickers_cache
        processed = set()
        if self.settlement_ledger_path.exists():
            try:
                with open(self.settlement_ledger_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            t = entry.get("ticker")
                            if t:
                                processed.add(t)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"[Ledger] Failed to load {self.settlement_ledger_path}: {e}")
        self._ledger_tickers_cache = processed
        logger.info(f"[Ledger] Loaded {len(processed)} prior settlements from ledger")
        return processed

    def _append_ledger(self, entry: dict):
        """Append one settlement record to ledger and update cache."""
        try:
            self.settlement_ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settlement_ledger_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            if self._ledger_tickers_cache is not None:
                self._ledger_tickers_cache.add(entry["ticker"])
        except Exception as e:
            logger.error(f"[Ledger] APPEND FAILED for {entry.get('ticker')}: {e}")

    def check_settlements(self):
        """
        Idempotent settlement check. Kalshi API is ground truth.
        Skips tickers already recorded in settlement_ledger.jsonl.
        Called every cycle from run_cycle.
        """
        from datetime import datetime, timezone, timedelta

        processed = self._load_ledger_tickers()

        # Query Kalshi for recent settlements (last 48h window, idempotent-safe)
        now_ts = int(datetime.now(timezone.utc).timestamp())
        min_ts = now_ts - 48 * 3600
        try:
            settlements = self.client.paginate(
                "get_settlements", min_ts=min_ts, max_ts=now_ts, limit=200
            )
        except Exception as e:
            logger.warning(f"[Settlements] Kalshi API query failed: {e}")
            return

        new_count = 0
        for s in settlements:
            ticker = s.get("ticker")
            if not ticker or ticker in processed:
                continue

            try:
                result = s.get("market_result", "")  # "yes" | "no"
                revenue_cents = s.get("revenue", 0) or 0
                revenue = revenue_cents / 100.0
                settled_time = s.get("settled_time", "")

                # Match against local position for entry/side/count context
                local_pos = None
                for p in self.trade_engine.positions.get_positions():
                    if p.get("ticker") == ticker:
                        local_pos = p
                        break

                if local_pos:
                    entry = local_pos.get("avg_entry_price", 0.5)
                    side = local_pos.get("side", "yes")
                    count = local_pos.get("count", 0)
                    sig_type = local_pos.get("signal_type", "")
                else:
                    # No local position (maybe cleared already) — derive from Kalshi
                    entry = 0.5  # unknown
                    side = "yes"  # default assumption
                    count = 0
                    sig_type = "kalshi_only"

                # PnL: use existing formula for Brier continuity
                if (side == "yes" and result == "yes") or (side == "no" and result == "no"):
                    gross = (1 - entry) * count if count else revenue
                    fee = gross * KALSHI_FEE_RATE
                    pnl = gross - fee
                    exit_reason = "settled:win"
                    self.learn.trade_exited(ticker, side, entry, 1.0, count, pnl, exit_reason)
                else:
                    pnl = -entry * count if count else -revenue  # edge case
                    exit_reason = "settled:loss"
                    self.learn.trade_exited(ticker, side, entry, 0.0, count, pnl, exit_reason)
                    self.learn.trade_wrong(
                        ticker, side, entry, 0.0, pnl,
                        f"Market settled {result}, we held {side}",
                        "Review signal quality for this market type",
                    )

                # Brier + Calibration (auto trades only, has local context)
                if local_pos and sig_type and sig_type not in ("manual", "synced_from_kalshi", "kalshi_only"):
                    outcome = 1.0 if pnl > 0 else 0.0
                    brier = (entry - outcome) ** 2
                    self.learn._write({
                        "type": "brier_score",
                        "ticker": ticker,
                        "model_prob": round(entry, 4),
                        "outcome": outcome,
                        "score": round(brier, 4),
                        "pnl": round(pnl, 2),
                        "source": "live",
                    })
                    self.calibration.record(entry, int(outcome), ticker=ticker)

                # Bankroll update via safety
                self.safety.record_result(pnl > 0, pnl)

                # Local position cleanup
                if local_pos and count > 0:
                    try:
                        self.trade_engine.positions.reduce_position(ticker, side, count)
                    except Exception as e:
                        logger.warning(f"[Settlements] reduce_position failed for {ticker}: {e}")

                # Ledger append (idempotency guarantee)
                self._append_ledger({
                    "ticker": ticker,
                    "settled_time": settled_time,
                    "market_result": result,
                    "revenue_dollars": round(revenue, 4),
                    "entry_price": round(entry, 4),
                    "count": count,
                    "side": side,
                    "pnl": round(pnl, 2),
                    "sig_type": sig_type,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "source": "check_settlements_cycle",
                })
                new_count += 1
                logger.info(
                    f"[Settlements] {ticker} settled {result} | "
                    f"pnl=${pnl:+.2f} | revenue=${revenue:.2f}"
                )

            except Exception as e:
                logger.warning(f"[Settlements] Processing error for {ticker}: {e}")

        if new_count > 0:
            logger.info(f"[Settlements] Processed {new_count} new settlement(s) this cycle")

    # ----------------------------------------------------------------
    # Exit Monitor: Mid-Trade Exit Strategy
    # ----------------------------------------------------------------

    EXIT_PROFIT_THRESHOLD = 0.50   # 50% profit → sell
    EXIT_LOSS_THRESHOLD = -0.30    # 30% loss → cut
    EXIT_TIME_THRESHOLD_HOURS = 2  # < 2h to settlement → consider exit

    def exit_monitor(self) -> dict:
        """
        Monitor open positions and trigger exits when thresholds are hit.

        Exit conditions:
          1. Profit target: unrealized P&L >= 50% of entry cost → sell
          2. Stop loss: unrealized P&L <= -30% of entry cost → sell
          3. Time exit: < 2h to settlement + losing position → sell

        Returns summary of exit actions taken.
        """
        results = {"exits_triggered": 0, "exits_submitted": 0}

        if not PAPER_TRADES_FILE.exists():
            return results

        lines = []
        with open(PAPER_TRADES_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        updated = []
        exits = 0

        for line in lines:
            entry = json.loads(line)
            if entry.get("source") != "auto" or entry.get("status") != "open":
                updated.append(line)
                continue

            ticker = entry["ticker"]
            side = entry["side"]
            entry_price = entry["price_cents"] / 100
            count = entry["count"]
            cost = entry_price * count

            try:
                # Get current market price from Kalshi
                market_data = self.client.get_market(ticker)
                market = market_data.get("market", market_data)
                result = market.get("result", "")

                # If already settled, skip (will be handled by check_paper_settlements)
                if result in ("yes", "no"):
                    updated.append(line)
                    continue

                # Current YES price
                yes_price = market.get("yes_price", 0)
                if not yes_price:
                    yes_bid = market.get("yes_bid", 0)
                    yes_ask = market.get("yes_ask", 0)
                    yes_price = (yes_bid + yes_ask) / 2 if yes_bid and yes_ask else 0

                if yes_price <= 0:
                    updated.append(line)
                    continue

                # Calculate unrealized P&L
                if side == "yes":
                    current_value = yes_price * count
                    sell_price = yes_price
                else:
                    current_value = (1 - yes_price) * count
                    sell_price = 1 - yes_price

                # Gross unrealized (before fees on exit)
                unrealized_gross = current_value - cost
                unrealized_pct = unrealized_gross / cost if cost > 0 else 0

                # Hours to settlement
                hours = self._hours_to_settlement_for_ticker(ticker)

                # Exit decision
                exit_reason = None

                if unrealized_pct >= self.EXIT_PROFIT_THRESHOLD:
                    exit_reason = f"profit_target ({unrealized_pct:.0%} >= {self.EXIT_PROFIT_THRESHOLD:.0%})"
                elif unrealized_pct <= self.EXIT_LOSS_THRESHOLD:
                    exit_reason = f"stop_loss ({unrealized_pct:.0%} <= {self.EXIT_LOSS_THRESHOLD:.0%})"
                elif hours < self.EXIT_TIME_THRESHOLD_HOURS and unrealized_pct < 0:
                    exit_reason = f"time_exit ({hours:.1f}h left, losing {unrealized_pct:.0%})"

                if exit_reason:
                    results["exits_triggered"] += 1

                    # Calculate net P&L after fees
                    if unrealized_gross > 0:
                        fee = unrealized_gross * KALSHI_FEE_RATE
                        net_pnl = unrealized_gross - fee
                    else:
                        fee = 0
                        net_pnl = unrealized_gross

                    # Mark as exited
                    entry["status"] = "exited"
                    entry["exit_reason"] = exit_reason
                    entry["exit_price"] = round(sell_price * 100)
                    entry["exit_at"] = datetime.now(timezone.utc).isoformat()
                    entry["paper_pnl"] = round(net_pnl, 4)
                    entry["fee"] = round(fee, 4)

                    # In paper mode, just log. In live mode, would submit sell order.
                    if self.dry_run:
                        logger.info(
                            f"[Exit] PAPER: {ticker} {side} | {exit_reason} | "
                            f"P&L=${net_pnl:.2f} ({unrealized_pct:.0%})"
                        )
                    else:
                        # Live exit: submit sell order
                        try:
                            sell_side = "no" if side == "yes" else "yes"
                            order = self.trade_engine.prepare_order(
                                ticker=ticker,
                                side=sell_side,
                                price_cents=int(sell_price * 100),
                                count=count,
                                signal_type="exit_monitor",
                                reasoning=exit_reason,
                                model_prob=0.5,
                                edge=0,
                            )
                            self.trade_engine.execute_order(order, dry_run=False)
                            results["exits_submitted"] += 1
                        except Exception as e:
                            logger.error(f"[Exit] Error submitting exit for {ticker}: {e}")

                    self.learn._write({
                        "type": "exit_triggered",
                        "ticker": ticker,
                        "side": side,
                        "reason": exit_reason,
                        "pnl": round(net_pnl, 4),
                        "unrealized_pct": round(unrealized_pct, 4),
                        "hours_to_settlement": round(hours, 1),
                    })

                    exits += 1
                    updated.append(json.dumps(entry))
                else:
                    updated.append(line)

            except Exception as e:
                logger.debug(f"[Exit] Monitor error for {ticker}: {e}")
                updated.append(line)

        # Rewrite file
        if exits > 0:
            with open(PAPER_TRADES_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(updated) + "\n" if updated else "")
            logger.info(f"[Exit] Triggered {exits} exits")

        return results

    def _hours_to_settlement_for_ticker(self, ticker: str) -> float:
        """Get hours to settlement for a ticker from DB."""
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT expiration_time, close_time FROM markets WHERE ticker = %s",
                (ticker,)
            )
            row = cur.fetchone()
            put_connection(conn)
            if not row:
                return 999.0
            exp = row[0] or row[1] or ""
            if not exp:
                return 999.0
            from dateutil.parser import parse as dtparse
            exp_dt = dtparse(exp)
            now = datetime.now(timezone.utc)
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            return max(0.1, (exp_dt - now).total_seconds() / 3600)
        except Exception:
            return 999.0

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _calculate_size(self, model_prob: float, price: float,
                        side: str, max_bet: float,
                        confidence: str = "medium",
                        data_points: int = 10) -> int:
        """
        Kelly Criterion position sizing via bankroll.kelly_bet_size().

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

        # Calculate edge and odds for kelly_bet_size
        edge = abs(model_prob - price)
        odds = (1 - entry) / entry if entry > 0 else 0
        if odds <= 0:
            return 0

        bankroll = self.safety.bankroll
        bet_dollars = kelly_bet_size(edge, odds, bankroll, kelly_frac)

        # Convert dollars to contracts
        count = int(bet_dollars / entry) if entry > 0 else 0

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
                        gross_profit = (1 - price) * count
                        fee = gross_profit * KALSHI_FEE_RATE  # 7% fee on winnings
                        pnl = gross_profit - fee
                        entry["status"] = "win"
                        entry["fee"] = round(fee, 4)
                        wins += 1
                    else:
                        pnl = -price * count
                        entry["status"] = "loss"
                        entry["fee"] = 0.0
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
            except Exception as e:
                logger.error(f"[Calibration] Error: {e}")

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

        if changes.get("mismatches"):
            for m in changes["mismatches"]:
                logger.warning(
                    f"[Sync] Position mismatch: {m['ticker'][:40]} "
                    f"local={m['local_count']} vs Kalshi={m['kalshi_count']} (synced)"
                )

        if changes.get("removed"):
            for t in changes["removed"]:
                logger.info(f"[Sync] Removed local position (not on Kalshi): {t[:50]}")

        if changes.get("added"):
            for t in changes["added"]:
                logger.info(f"[Sync] Added position from Kalshi: {t[:50]}")

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

    # SAFETY: FORCE_DRY_RUN overrides CLI flag
    if FORCE_DRY_RUN and not args.dry_run:
        print("  [SAFETY] FORCE_DRY_RUN=True -> overriding to dry-run mode")
        print("  [SAFETY] To go live, set FORCE_DRY_RUN=False in auto_trader.py")
        args.dry_run = True

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


if __name__ == "__main__":
    main()
