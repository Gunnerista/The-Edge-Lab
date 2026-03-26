#!/usr/bin/env python3
"""
War Machine Runner - Concurrent observe + active_scanner
=========================================================
Runs two loops simultaneously:
  1. Observer: Records all market data every 60s
  2. Scanner: Runs arbitrage + orderbook scan every 5 minutes

Sends Discord alerts for signals, errors, and daily summary.
Auto-restarts on crash (5s delay).

Usage:
    python scripts/runner.py                    # default: both loops
    python scripts/runner.py --observe-only     # just data collection
    python scripts/runner.py --scan-only        # just scanning
    python scripts/runner.py --no-settlement-filter  # include long-dated markets
"""

import sys
import io
import os
import json
import time
import signal
import logging
import argparse
import threading
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from kalshi_client import KalshiConfig, create_client
from market_recorder import MarketDatabase, RESTRecorder
from market_classifier import MarketClassifier
from active_scanner import ActiveScanner
from signal_engine import SignalEngine
from learning_log import LearningLog

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
def _get_webhook():
    return os.environ.get("DISCORD_WEBHOOK_URL", "")

# ============================================================================
# Discord Alert Helpers
# ============================================================================

def discord_send(embed: dict):
    """Send a Discord embed. Silently fails if no webhook configured."""
    if not _get_webhook():
        return
    try:
        requests.post(_get_webhook(), json={"embeds": [embed]}, timeout=10)
    except Exception as e:
        logger.error(f"Discord send failed: {e}")


def alert_signal(signal_data: dict):
    """Send signal detection alert."""
    discord_send({
        "title": f"Signal: {signal_data.get('signal_type', '?')}",
        "color": 0x4CAF50,
        "fields": [
            {"name": "Market", "value": str(signal_data.get("title", "?"))[:100], "inline": False},
            {"name": "Side", "value": signal_data.get("side", "?").upper(), "inline": True},
            {"name": "Price", "value": f"{signal_data.get('price', 0)*100:.0f}c", "inline": True},
            {"name": "Edge", "value": f"{signal_data.get('edge', 0)*100:.1f}%", "inline": True},
            {"name": "EV", "value": f"{signal_data.get('ev_cents', 0):.1f}c", "inline": True},
            {"name": "Confidence", "value": signal_data.get("confidence", "?"), "inline": True},
            {"name": "Settlement", "value": signal_data.get("expiration", "?")[:19], "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def alert_execution(trade_data: dict):
    """Send trade execution alert."""
    discord_send({
        "title": "Trade Executed",
        "color": 0x2196F3,
        "fields": [
            {"name": "Ticker", "value": trade_data.get("ticker", "?"), "inline": False},
            {"name": "Side", "value": trade_data.get("side", "?").upper(), "inline": True},
            {"name": "Price", "value": f"{trade_data.get('price', 0)}c", "inline": True},
            {"name": "Count", "value": str(trade_data.get("count", 0)), "inline": True},
            {"name": "Cost", "value": f"${trade_data.get('cost', 0):.2f}", "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def alert_daily_summary(summary: dict):
    """Send daily P&L summary."""
    discord_send({
        "title": "Daily Summary",
        "color": 0x9C27B0,
        "fields": [
            {"name": "P&L", "value": f"${summary.get('pnl', 0):.2f}", "inline": True},
            {"name": "Balance", "value": f"${summary.get('balance', 0):.2f}", "inline": True},
            {"name": "Open Positions", "value": str(summary.get('positions', 0)), "inline": True},
            {"name": "Signals Today", "value": str(summary.get('signals', 0)), "inline": True},
            {"name": "Markets Recorded", "value": f"{summary.get('markets', 0):,}", "inline": True},
            {"name": "Settling Tomorrow", "value": str(summary.get('settling_tomorrow', 0)), "inline": True},
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    # Send calibration as separate message if available
    cal_text = summary.get("calibration", "")
    if cal_text:
        discord_send({
            "title": "Calibration",
            "color": 0x607D8B,
            "description": cal_text,
        })


def alert_error(error_msg: str):
    """Send error alert."""
    discord_send({
        "title": "ERROR",
        "color": 0xF44336,
        "description": error_msg[:500],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


# ============================================================================
# Observer Loop
# ============================================================================

def observer_loop(interval: int = 60, stop_event: threading.Event = None):
    """Record all market data continuously."""
    config = KalshiConfig.from_env()
    config.use_demo = False
    client = create_client(config)
    db = MarketDatabase()
    recorder = RESTRecorder(client, db)
    classifier = MarketClassifier()

    cycle = 0
    while not (stop_event and stop_event.is_set()):
        cycle += 1
        start = time.time()
        try:
            count = recorder.record_snapshot()
            classifier.classify_all(force=False)
            elapsed = time.time() - start
            logger.info(f"[Observer] Cycle {cycle}: {count} markets ({elapsed:.1f}s)")
        except Exception as e:
            logger.error(f"[Observer] Cycle {cycle} failed: {e}")
            alert_error(f"Observer error: {e}")

        sleep_time = max(0, interval - (time.time() - start))
        if stop_event:
            stop_event.wait(timeout=sleep_time)
        else:
            time.sleep(sleep_time)

    db.commit()
    db.close()
    classifier.close()
    logger.info("[Observer] Stopped")


# ============================================================================
# Scanner Loop
# ============================================================================

def scanner_loop(
    interval: int = 300,
    max_settlement_hours: int = 72,
    stop_event: threading.Event = None,
    auto_trade: bool = True,
    dry_run: bool = False,
):
    """Run active scanner + auto-trader continuously."""
    from auto_trader import AutoTrader

    learn = LearningLog()
    trader = AutoTrader(dry_run=dry_run) if auto_trade else None
    cycle = 0

    while not (stop_event and stop_event.is_set()):
        cycle += 1
        start = time.time()
        try:
            # Auto-trade cycle (includes signal + arb scanning)
            if trader:
                trader.reset_cycle()
                trade_results = trader.run_cycle()
                trader.check_settlements()
                logger.info(
                    f"[AutoTrader] Cycle {cycle}: "
                    f"signals={trade_results['signals_found']} "
                    f"submitted={trade_results['orders_submitted']} "
                    f"filled={trade_results['orders_filled']} "
                    f"arb={trade_results['arb_found']}/{trade_results['arb_executed']}"
                )

            # Also run the broader active scanner for logging
            scanner = ActiveScanner(
                use_prod=True,
                max_settlement_hours=max_settlement_hours,
            )

            # Arbitrage scan
            arb = scanner.run_arbitrage(max_pages=10)
            actionable = [o for o in arb.get("opportunities", []) if o.get("opportunity_type") != "monitor"]

            for opp in actionable[:3]:
                # Signal-only alert removed — auto_trader handles execution alerts
                learn.signal_detected(
                    ticker=opp.get("event_ticker", ""),
                    signal_type=f"arb:{opp['opportunity_type']}",
                    side="yes",
                    price=opp.get("total_ask", 0),
                    edge=opp.get("net_edge", 0),
                    ev_cents=opp.get("net_edge_cents", 0),
                    confidence="high" if opp.get("net_edge_cents", 0) > 20 else "medium",
                    category=opp.get("category", ""),
                )

            # Signal engine scan
            try:
                sig_engine = SignalEngine(max_settlement_hours=max_settlement_hours)
                signals = sig_engine.scan_all()
                high_signals = [s for s in signals if s.confidence in ("high", "medium") and s.net_ev_cents > 2]

                for sig in high_signals[:3]:
                    # Signal-only alert removed — auto_trader handles execution alerts
                    learn.signal_detected(
                        ticker=sig.ticker,
                        signal_type=sig.signal_type,
                        side=sig.side,
                        price=sig.entry_price,
                        edge=sig.edge,
                        ev_cents=sig.net_ev_cents,
                        confidence=sig.confidence,
                        category=sig.category,
                    )
                sig_engine.close()
            except Exception as e:
                logger.error(f"[Scanner] Signal engine error: {e}")

            elapsed = time.time() - start
            logger.info(
                f"[Scanner] Cycle {cycle}: "
                f"{arb.get('actionable', 0)} arb opps, "
                f"{len(high_signals) if 'high_signals' in dir() else 0} signals "
                f"({elapsed:.1f}s)"
            )

        except Exception as e:
            logger.error(f"[Scanner] Cycle {cycle} failed: {e}")
            alert_error(f"Scanner error: {e}")

        sleep_time = max(0, interval - (time.time() - start))
        if stop_event:
            stop_event.wait(timeout=sleep_time)
        else:
            time.sleep(sleep_time)

    logger.info("[Scanner] Stopped")


# ============================================================================
# Daily Summary
# ============================================================================

def send_daily_summary_if_due():
    """Check if it's time for daily summary (10pm local)."""
    now = datetime.now()
    if now.hour == 22 and now.minute < 2:
        try:
            config = KalshiConfig.from_env()
            config.use_demo = False
            client = create_client(config)
            bal = client.get_balance()
            balance = bal.get("balance", 0) / 100  # cents to dollars

            from trade_engine import TradeLogger, PositionManager
            tl = TradeLogger()
            pm = PositionManager()
            pnl_data = tl.get_pnl_summary()

            learn = LearningLog()
            today_signals = len([
                e for e in learn.get_recent(1000, "signal_detected")
                if e.get("logged_at", "")[:10] == now.strftime("%Y-%m-%d")
            ])

            # Calibration summary
            from calibration import CalibrationTracker
            cal = CalibrationTracker()
            cal_summary = cal.format_discord_summary()

            alert_daily_summary({
                "balance": balance,
                "pnl": pnl_data.get("realized_pnl", 0),
                "positions": len(pm.get_positions()),
                "signals": today_signals,
                "markets": 0,
                "settling_tomorrow": 0,
                "calibration": cal_summary,
            })
            # Weekly loss decomposition (Sunday only)
            if now.weekday() == 6:  # Sunday
                from risk_decomposition import format_discord_weekly, generate_loss_report
                loss_report = generate_loss_report()
                if loss_report["total_losses"] > 0:
                    discord_send({
                        "title": "Weekly Loss Decomposition",
                        "color": 0xFF5722,
                        "description": format_discord_weekly(loss_report),
                    })

            # Self-learner: auto-tune parameters
            from self_learner import SelfLearner
            learner = SelfLearner()
            changes = learner.run_tuning_cycle()
            if changes:
                discord_send({
                    "title": "\U0001f9e0 Parameter Auto-Tune",
                    "color": 0x00BCD4,
                    "description": "\n".join(
                        f"**{k}**: {v['old']} \u2192 {v['new']}"
                        for k, v in changes.items()
                    ),
                })

        except Exception as e:
            logger.error(f"Daily summary failed: {e}")


# ============================================================================
# Main Runner
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="War Machine Runner")
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Auto-trade in simulation mode")
    parser.add_argument("--no-auto-trade", action="store_true", help="Disable auto-trading (signal-only)")
    parser.add_argument("--observe-interval", type=int, default=60)
    parser.add_argument("--scan-interval", type=int, default=120)
    parser.add_argument("--no-settlement-filter", action="store_true")
    parser.add_argument("--max-settlement-hours", type=int, default=72)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(
                str(PROJECT_ROOT / "data" / "runner.log"),
                encoding="utf-8",
            ),
        ],
    )

    settlement_hours = 0 if args.no_settlement_filter else args.max_settlement_hours
    auto_trade = not args.no_auto_trade and not args.observe_only

    # === PAPER TRADING MODE — all auto-trades forced to dry-run ===
    PAPER_TRADING = True
    if PAPER_TRADING:
        args.dry_run = True

    trade_mode = "PAPER" if PAPER_TRADING else ("DRY RUN" if args.dry_run else ("LIVE" if auto_trade else "OFF"))

    print()
    print("  ====================================================")
    print("  PREDICTION MARKET WAR MACHINE - Runner")
    print("  ====================================================")
    print(f"  Observer:    {'ON' if not args.scan_only else 'OFF'} ({args.observe_interval}s)")
    print(f"  Scanner:     {'ON' if not args.observe_only else 'OFF'} ({args.scan_interval}s)")
    print(f"  Auto-Trade:  {trade_mode}")
    print(f"  Settlement:  {settlement_hours}h filter" if settlement_hours else "  Settlement:  No filter")
    print(f"  Discord:     {'Configured' if _get_webhook() else 'Not set (DISCORD_WEBHOOK_URL)'}")
    print("  ====================================================")
    print()

    if PAPER_TRADING:
        discord_send({
            "title": "\U0001f52c PAPER TRADING MODE",
            "description": "\uc2e4\ub9e4\ub9e4 \uc911\ub2e8, \uc804\ub7b5 \uac80\uc99d \uc911. \uc2dc\uadf8\ub110 \uac10\uc9c0 + \uac00\uc0c1 \ub9e4\ub9e4 \uae30\ub85d\ub9cc \ud568.",
            "color": 0x9C27B0,
        })

    stop_event = threading.Event()

    def handle_shutdown(sig, frame):
        logger.info("Shutdown signal received")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    threads = []

    if not args.scan_only:
        t = threading.Thread(
            target=observer_loop,
            args=(args.observe_interval, stop_event),
            daemon=True,
            name="observer",
        )
        threads.append(t)

    if not args.observe_only:
        t = threading.Thread(
            target=scanner_loop,
            args=(args.scan_interval, settlement_hours, stop_event, auto_trade, args.dry_run),
            daemon=True,
            name="scanner",
        )
        threads.append(t)

    for t in threads:
        t.start()
        logger.info(f"Started thread: {t.name}")

    # Keep main thread alive, check for daily summary
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=60)
            send_daily_summary_if_due()
    except KeyboardInterrupt:
        stop_event.set()

    for t in threads:
        t.join(timeout=10)

    logger.info("Runner stopped.")


if __name__ == "__main__":
    main()
