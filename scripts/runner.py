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
from zoneinfo import ZoneInfo

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


def send_player_report(today_str: str):
    """Send daily player performance report (v2 config only) to Discord."""
    KALSHI_FEE = 0.07
    STARTING_BANKROLL = 300.0
    CONFIG_V2 = "v3_prop_edge"

    pred_log_path = PROJECT_ROOT / "data" / "prediction_log.jsonl"
    if not pred_log_path.exists():
        discord_send({
            "title": "📊 데일리 플레이어 리포트",
            "color": 0x607D8B,
            "description": "오늘 정산 없음",
        })
        return

    all_v2_settled = []
    today_v2_settled = []
    with open(pred_log_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line:
                continue
            try:
                p = json.loads(_line)
            except Exception:
                continue
            if (p.get("config_version") == CONFIG_V2
                    and p.get("settled")
                    and p.get("actual_result") in ("yes", "no")):
                all_v2_settled.append(p)
                if p.get("game_date") == today_str:
                    today_v2_settled.append(p)

    if not today_v2_settled:
        discord_send({
            "title": "📊 데일리 플레이어 리포트",
            "color": 0x607D8B,
            "description": "오늘 정산 없음",
        })
        return

    def _pred_pnl(p):
        buy_price = p.get("kalshi_price") or 0.5
        model_p = p.get("model_prob", 0.5)
        bet_yes = model_p > buy_price
        entry = buy_price if bet_yes else (1 - buy_price)
        won = (p["actual_result"] == "yes") if bet_yes else (p["actual_result"] == "no")
        return (1 - entry) * (1 - KALSHI_FEE) if won else -entry

    def _pred_side(p):
        return "YES" if (p.get("model_prob", 0.5) > (p.get("kalshi_price") or 0.5)) else "NO"

    def _fmt_pnl(v):
        return f"+${v:.2f}" if v >= 0 else f"-${abs(v):.2f}"

    # Today stats
    today_pnls = [(p, _pred_pnl(p)) for p in today_v2_settled]
    today_wins = sum(1 for _, pnl in today_pnls if pnl > 0)
    today_losses = len(today_pnls) - today_wins
    winrate = round(today_wins / len(today_pnls) * 100, 1)
    today_pnl = sum(pnl for _, pnl in today_pnls)

    # Cumulative v2 stats
    all_pnl = sum(_pred_pnl(p) for p in all_v2_settled)
    n_settled = len(all_v2_settled)
    total_cost = sum(
        (p.get("kalshi_price") or 0.5) if (p.get("model_prob", 0.5) > (p.get("kalshi_price") or 0.5))
        else (1 - (p.get("kalshi_price") or 0.5))
        for p in all_v2_settled
    )
    roi = round(all_pnl / total_cost * 100, 1) if total_cost > 0 else 0.0
    virtual_bankroll = STARTING_BANKROLL + all_pnl

    # Best and worst today
    today_pnls_sorted = sorted(today_pnls, key=lambda x: x[1])
    worst_p, worst_pnl = today_pnls_sorted[0]
    best_p, best_pnl = today_pnls_sorted[-1]

    def _fmt_player(p):
        return f"{p.get('player', '?')} {p.get('prop_type', '?')} {p.get('line', '?')} {_pred_side(p)}"

    color = 0x4CAF50 if today_pnl >= 0 else 0xF44336
    description = "\n".join([
        "📊 **오늘 결과**",
        f"├ 승패: {today_wins}승 {today_losses}패 (승률 {winrate}%)",
        f"├ 오늘 P&L: {_fmt_pnl(today_pnl)}",
        f"└ 잔고: ${virtual_bankroll:.2f} (시작 $300)",
        "",
        "📈 **누적 (new config v2 only)**",
        f"├ 총 settled: {n_settled}건",
        f"├ 누적 ROI: {roi}% (fee 반영)",
        f"└ 누적 P&L: {_fmt_pnl(all_pnl)}",
        "",
        f"🏆 **오늘 베스트**: {_fmt_player(best_p)} → {_fmt_pnl(best_pnl)}",
        f"💀 **오늘 워스트**: {_fmt_player(worst_p)} → {_fmt_pnl(worst_pnl)}",
    ])

    discord_send({
        "title": "📊 데일리 플레이어 리포트",
        "color": color,
        "description": description,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def save_equity_curve_point(today_str: str):
    """
    Append today's equity snapshot to data/equity_curve.json.
    Computes cumulative stats from prediction_log.jsonl (all settled predictions).
    Skips if an entry for today_str already exists.
    """
    KALSHI_FEE = 0.07
    STARTING_BANKROLL = 300.0
    EQUITY_FILE = PROJECT_ROOT / "data" / "equity_curve.json"

    # Load existing curve
    curve = []
    if EQUITY_FILE.exists():
        try:
            with open(EQUITY_FILE, "r", encoding="utf-8") as _f:
                curve = json.load(_f)
        except Exception:
            curve = []

    # Skip if already recorded today
    if any(e.get("date") == today_str for e in curve):
        logger.info(f"[Equity] Entry for {today_str} already exists, skipping.")
        return

    # Load predictions
    pred_log_path = PROJECT_ROOT / "data" / "prediction_log.jsonl"
    if not pred_log_path.exists():
        logger.warning("[Equity] prediction_log.jsonl not found, skipping equity curve save.")
        return

    all_settled = []
    today_settled = []
    with open(pred_log_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line:
                continue
            try:
                p = json.loads(_line)
            except Exception:
                continue
            if p.get("settled") and p.get("actual_result") in ("yes", "no"):
                all_settled.append(p)
                if p.get("game_date") == today_str:
                    today_settled.append(p)

    def _pnl(p):
        bp = p.get("kalshi_price") or 0.5
        mp = p.get("model_prob", 0.5)
        bet_yes = mp > bp
        entry = bp if bet_yes else (1 - bp)
        won = (p["actual_result"] == "yes") if bet_yes else (p["actual_result"] == "no")
        return (1 - entry) * (1 - KALSHI_FEE) if won else -entry

    cum_pnl = sum(_pnl(p) for p in all_settled)
    cum_wins = sum(1 for p in all_settled if _pnl(p) > 0)
    cum_cost = sum(
        (p.get("kalshi_price") or 0.5) if (p.get("model_prob", 0.5) > (p.get("kalshi_price") or 0.5))
        else (1 - (p.get("kalshi_price") or 0.5))
        for p in all_settled
    )
    win_rate = round(cum_wins / len(all_settled) * 100, 1) if all_settled else 0.0
    roi = round(cum_pnl / cum_cost * 100, 1) if cum_cost > 0 else 0.0

    # Determine config_version: use v2 if any today's predictions are tagged, else legacy
    has_v2 = any(p.get("config_version") == "v3_prop_edge" for p in all_settled)
    config_version = "v3_prop_edge" if has_v2 else "legacy"

    entry = {
        "date": today_str,
        "config_version": config_version,
        "cumulative_pnl": round(cum_pnl, 2),
        "balance": round(STARTING_BANKROLL + cum_pnl, 2),
        "settled_today": len(today_settled),
        "settled_cumulative": len(all_settled),
        "win_rate": win_rate,
        "roi": roi,
    }
    curve.append(entry)
    curve.sort(key=lambda x: x.get("date", ""))

    EQUITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EQUITY_FILE, "w", encoding="utf-8") as _f:
        json.dump(curve, _f, indent=2, ensure_ascii=False)

    logger.info(
        f"[Equity] Saved {today_str}: cum_pnl={cum_pnl:+.2f}, "
        f"balance=${STARTING_BANKROLL + cum_pnl:.2f}, "
        f"settled_today={len(today_settled)}, n={len(all_settled)}"
    )


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

    global _total_markets_recorded
    cycle = 0
    while not (stop_event and stop_event.is_set()):
        cycle += 1
        start = time.time()
        try:
            count = recorder.record_snapshot()
            _total_markets_recorded += count
            # classifier disabled: causes DB lock every 50 cycles
            # classifier.classify_all(force=False)
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
# Timezone
# ============================================================================

ET = ZoneInfo("US/Eastern")

def _now_et() -> datetime:
    """Current time in US/Eastern."""
    return datetime.now(ET)


def _get_today_nba_schedule() -> list:
    """
    Returns sorted list of game tipoff times (UTC-aware datetime) for today (ET calendar date).
    Returns [] on no games or API error.
    """
    try:
        from nba_api.live.nba.endpoints import scoreboard
        from dateutil.parser import parse as dtparse
        data = scoreboard.ScoreBoard().get_dict()
        games = data.get("scoreboard", {}).get("games", [])
        times = []
        for g in games:
            t = g.get("gameTimeUTC")
            if t:
                dt = dtparse(t)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                times.append(dt)
        return sorted(times)
    except Exception as e:
        logger.warning(f"[Schedule] NBA schedule check failed: {e}")
        return []


# ============================================================================
# Scheduled Tasks: Forward Test + Settle
# ============================================================================

_forward_test_done_today = False
_settle_done_today = False
_daily_summary_done_today = False
_total_markets_recorded = 0  # cumulative market snapshots recorded today

def run_scheduled_tasks():
    """
    Check and run time-based tasks (all times in ET):
      - 12:00 PM ET: Forward test (predict today's NBA games)
      - 11:59 PM ET: Settle predictions (check box scores)
    """
    global _forward_test_done_today, _settle_done_today, _daily_summary_done_today, _total_markets_recorded

    now = _now_et()
    today_str = now.strftime("%Y-%m-%d")

    # Reset flags at midnight
    if now.hour == 0 and now.minute < 2:
        _forward_test_done_today = False
        _settle_done_today = False
        _daily_summary_done_today = False
        _total_markets_recorded = 0

    # 12:00 PM ET — Forward test
    if now.hour == 12 and now.minute < 2 and not _forward_test_done_today:
        _forward_test_done_today = True
        logger.info(f"[Scheduler] Running forward test for {today_str} (12:00 PM ET)")
        try:
            from forward_test import run_forward_test
            from datetime import date as date_cls
            today_date = now.date()
            tomorrow = today_date + timedelta(days=1)
            run_forward_test([today_date, tomorrow], source="auto")
            logger.info("[Scheduler] Forward test complete")
        except Exception as e:
            logger.error(f"[Scheduler] Forward test failed: {e}")
            alert_error(f"Forward test failed: {e}")

    # 11:59 PM ET — Settle predictions (retry up to 3x on DB lock)
    if now.hour == 23 and now.minute >= 55 and not _settle_done_today:
        _settle_done_today = True
        logger.info(f"[Scheduler] Settling predictions for {today_str} (11:59 PM ET)")
        from settle_predictions import settle
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                settle(target_date=today_str)
                logger.info("[Scheduler] Settle complete")
                break
            except Exception as e:
                if "locked" in str(e).lower() and attempt < max_retries:
                    logger.warning(f"[Scheduler] Settle attempt {attempt}/{max_retries} failed (DB locked), retrying in 60s...")
                    time.sleep(60)
                else:
                    logger.error(f"[Scheduler] Settle failed (attempt {attempt}/{max_retries}): {e}")
                    alert_error(f"Settle predictions failed: {e}")
                    break


# ============================================================================
# Daily Summary
# ============================================================================

def send_daily_summary_if_due():
    """Check if it's time for daily summary (10pm ET). Runs once per day."""
    global _daily_summary_done_today
    now = _now_et()
    if now.hour == 22 and now.minute < 2 and not _daily_summary_done_today:
        _daily_summary_done_today = True
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

            # Brier Score: prediction_log settled predictions + new Platt
            import math as _math
            import json as _json
            PLATT_A, PLATT_B = 0.8976, -0.4242
            def _sigmoid(z): return 1/(1+_math.exp(-z)) if z >= 0 else _math.exp(z)/(1+_math.exp(z))
            def _apply_platt(p):
                p = max(0.001, min(0.999, p))
                return _sigmoid(PLATT_A * _math.log(p/(1-p)) + PLATT_B)
            pred_log_path = PROJECT_ROOT / "data" / "prediction_log.jsonl"
            brier_score = None
            if pred_log_path.exists():
                brier_preds = []
                with open(pred_log_path, "r", encoding="utf-8") as _f:
                    for _line in _f:
                        _line = _line.strip()
                        if not _line: continue
                        try:
                            _p = _json.loads(_line)
                        except Exception:
                            continue
                        if _p.get("settled") and _p.get("actual_result") in ("yes", "no"):
                            brier_preds.append(_p)
                if brier_preds:
                    brier_score = sum(
                        (_apply_platt(_p.get("model_prob", 0.5)) - (1 if _p["actual_result"] == "yes" else 0)) ** 2
                        for _p in brier_preds
                    ) / len(brier_preds)

            # Build calibration summary with Brier override
            from calibration import CalibrationTracker
            cal = CalibrationTracker()
            cal_summary = cal.format_discord_summary()
            if brier_score is not None:
                cal_summary = f"Brier={brier_score:.4f} (platt, n={len(brier_preds)}) | " + cal_summary

            alert_daily_summary({
                "balance": pnl_data.get("virtual_bankroll", 300.0),
                "pnl": pnl_data.get("realized_pnl", 0),
                "positions": len(pm.get_positions()),
                "signals": today_signals,
                "markets": _total_markets_recorded,
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
# Startup Catch-Up
# ============================================================================

def _startup_catchup_forward_test():
    """
    Run forward test immediately if all conditions are met:
      1. Current time is past 12:00 PM ET
      2. Today has NBA games (active props exist in market_data.db)
      3. No source="auto" predictions exist for today in prediction_log.jsonl

    This handles the case where runner.py starts (or restarts) after 12pm.
    Sets _forward_test_done_today to prevent the 12pm cron from double-firing.
    """
    global _forward_test_done_today

    now = _now_et()
    if now.hour < 12:
        return  # before noon, regular cron will handle it

    if _forward_test_done_today:
        return

    today_str = now.strftime("%Y-%m-%d")

    # Check if auto predictions already exist for today
    try:
        log_file = PROJECT_ROOT / "data" / "prediction_log.jsonl"
        if log_file.exists():
            import json
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if (entry.get("source") == "auto"
                            and entry.get("game_date") == today_str):
                        logger.info(f"[Catchup] Auto predictions already exist for {today_str}, skipping")
                        _forward_test_done_today = True
                        return
    except Exception as e:
        logger.warning(f"[Catchup] Error reading prediction log: {e}")

    # Check if today has NBA games (active props in market_data.db)
    try:
        conn = get_connection()
        # Build ticker date pattern (e.g., 26MAR28 for 2026-03-28)
        yy = str(now.year)[-2:]
        mon = now.strftime("%b").upper()
        dd = str(now.day)
        pattern = f"KXNBAPTS-{yy}{mon}{dd}%"
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM markets WHERE ticker LIKE %s AND status = 'active'",
            (pattern,)
        )
        row = cur.fetchone()
        put_connection(conn)

        prop_count = row[0] if row else 0
        if prop_count == 0:
            logger.info(f"[Catchup] No active NBA props for {today_str}, skipping")
            return
    except Exception as e:
        logger.warning(f"[Catchup] Error checking market_data.db: {e}")
        # Proceed anyway — forward_test will gracefully handle no-data case
        prop_count = -1

    # All conditions met — run catch-up
    _forward_test_done_today = True
    logger.info(
        f"[Catchup] 12pm passed, {prop_count} active props, 0 auto predictions "
        f"-- running forward test now ({now.strftime('%I:%M %p ET')})"
    )
    try:
        from forward_test import run_forward_test
        today_date = now.date()
        tomorrow = today_date + timedelta(days=1)
        run_forward_test([today_date, tomorrow], source="auto")
        logger.info("[Catchup] Forward test catch-up complete")
    except Exception as e:
        logger.error(f"[Catchup] Forward test catch-up failed: {e}")
        alert_error(f"Catchup forward test failed: {e}")


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

    # ── NBA Schedule Gate ──────────────────────────────────────────────────
    logger.info("[Schedule] Checking today's NBA schedule...")
    game_times_utc = _get_today_nba_schedule()

    if not game_times_utc:
        logger.info("[Schedule] No NBA games today — skipping")
        sys.exit(0)

    first_tipoff = game_times_utc[0].astimezone(ET)
    last_tipoff  = game_times_utc[-1].astimezone(ET)
    start_at     = first_tipoff - timedelta(hours=2)
    # NBA game avg ~2.5h; +1h settle buffer = last_tipoff + 3.5h
    shutdown_at  = last_tipoff + timedelta(hours=3, minutes=30)
    # Floor: 12:30 AM ET next day — ensures settle (11:59 PM) runs before player report
    today_1230am_et = first_tipoff.replace(hour=0, minute=30, second=0, microsecond=0) + timedelta(days=1)
    shutdown_at = max(shutdown_at, today_1230am_et)

    now_et = _now_et()
    logger.info(
        f"[Schedule] {len(game_times_utc)} NBA game(s) today | "
        f"First tip: {first_tipoff.strftime('%I:%M %p ET')} | "
        f"Window: {start_at.strftime('%I:%M %p')} – {shutdown_at.strftime('%I:%M %p')} ET"
    )

    if now_et >= shutdown_at:
        # Window already closed — sleep until next 4pm ET to block bat restart loop
        next_start = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        if now_et >= next_start:
            next_start += timedelta(days=1)
        wait_secs = (next_start - now_et).total_seconds()
        logger.info(
            f"[Schedule] Today's window already closed ({shutdown_at.strftime('%I:%M %p ET')}). "
            f"Sleeping until {next_start.strftime('%Y-%m-%d %I:%M %p ET')} ({wait_secs/3600:.1f}h)."
        )
        try:
            while _now_et() < next_start:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
        sys.exit(0)

    if now_et < start_at:
        wait_mins = (start_at - now_et).total_seconds() / 60
        logger.info(f"[Schedule] {wait_mins:.0f}m until scan window opens. Waiting...")
        try:
            while _now_et() < start_at:
                time.sleep(30)
        except KeyboardInterrupt:
            logger.info("[Schedule] Interrupted during wait. Exiting.")
            sys.exit(0)
        logger.info("[Schedule] Scan window opened. Starting.")
    # ───────────────────────────────────────────────────────────────────────

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
    print(f"  Timezone:    US/Eastern ({_now_et().strftime('%I:%M %p ET')})")
    print(f"  Observer:    {'ON' if not args.scan_only else 'OFF'} ({args.observe_interval}s)")
    print(f"  Scanner:     {'ON' if not args.observe_only else 'OFF'} ({args.scan_interval}s)")
    print(f"  Auto-Trade:  {trade_mode}")
    print(f"  Scheduled:   Forward test 12:00pm ET, Settle 11:59pm ET")
    print(f"  Settlement:  {settlement_hours}h filter" if settlement_hours else "  Settlement:  No filter")
    print(f"  Discord:     {'Configured' if _get_webhook() else 'Not set (DISCORD_WEBHOOK_URL)'}")
    print("  ====================================================")
    print()

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

    if PAPER_TRADING:
        discord_send({
            "title": "\U0001f52c PAPER TRADING MODE",
            "description": "\uc2e4\ub9e4\ub9e4 \uc911\ub2e8, \uc804\ub7b5 \uac80\uc99d \uc911. \uc2dc\uadf8\ub110 \uac10\uc9c0 + \uac00\uc0c1 \ub9e4\ub9e4 \uae30\ub85d\ub9cc \ud568.",
            "color": 0x9C27B0,
        })

    # Startup catch-up: if 12pm ET already passed today and no auto predictions exist,
    # run forward test immediately (e.g., runner started at 6pm)
    _startup_catchup_forward_test()

    # Keep main thread alive — scheduled tasks + daily summary (all ET)
    player_report_sent = False
    try:
        while not stop_event.is_set():
            stop_event.wait(timeout=60)
            run_scheduled_tasks()        # 12pm forward test, 11:59pm settle
            send_daily_summary_if_due()  # 10pm daily summary
            if _now_et() >= shutdown_at:
                if not player_report_sent:
                    player_report_sent = True
                    today_str_report = _now_et().strftime("%Y-%m-%d")
                    logger.info("[Schedule] Sending daily player report...")
                    try:
                        send_player_report(today_str_report)
                    except Exception as _rpt_err:
                        logger.error(f"[Schedule] Player report failed: {_rpt_err}")
                    logger.info("[Schedule] Saving equity curve point...")
                    try:
                        save_equity_curve_point(today_str_report)
                    except Exception as _eq_err:
                        logger.error(f"[Schedule] Equity curve save failed: {_eq_err}")
                logger.info(
                    f"[Schedule] Operating window closed "
                    f"({shutdown_at.strftime('%I:%M %p ET')}). Shutting down."
                )
                stop_event.set()
    except KeyboardInterrupt:
        stop_event.set()

    for t in threads:
        t.join(timeout=10)

    logger.info("Runner stopped.")


if __name__ == "__main__":
    main()
