#!/usr/bin/env python3
"""
Learning Log - Track signals, decisions, and outcomes for continuous improvement.
=================================================================================
Every signal detected, every decision made, every outcome observed.
This is how the War Machine gets smarter over time.

Log types:
  signal_detected  - A signal was found
  signal_skipped   - Signal found but not acted on (with reason)
  trade_entered    - Position opened
  trade_exited     - Position closed (with P&L)
  trade_wrong      - Post-mortem on a losing trade
  market_settled   - A watched market settled (outcome recorded)

Usage:
    from learning_log import LearningLog
    log = LearningLog()
    log.signal_detected(ticker, signal_type, ...)
    log.signal_skipped(ticker, reason, ...)
    log.trade_wrong(ticker, entry_price, exit_price, reason, ...)
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "data" / "learning_log.jsonl"


class LearningLog:
    """Append-only learning log for the War Machine."""

    def __init__(self, log_file: Path = LOG_FILE, source: str = "auto"):
        self.file = log_file
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self._source = source

    def _write(self, entry: dict):
        entry["logged_at"] = datetime.now(timezone.utc).isoformat()
        entry["source"] = self._source
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def signal_detected(
        self,
        ticker: str,
        signal_type: str,
        side: str,
        price: float,
        edge: float,
        ev_cents: float,
        confidence: str,
        category: str = "",
        expiration: str = "",
        extra: dict = None,
    ):
        """Log a detected signal."""
        self._write({
            "type": "signal_detected",
            "ticker": ticker,
            "signal_type": signal_type,
            "side": side,
            "price": price,
            "edge": edge,
            "ev_cents": ev_cents,
            "confidence": confidence,
            "category": category,
            "expiration": expiration,
            **(extra or {}),
        })

    def signal_skipped(
        self,
        ticker: str,
        signal_type: str,
        reason: str,
        price: float = 0,
        edge: float = 0,
        extra: dict = None,
    ):
        """Log a signal that was detected but not acted on."""
        self._write({
            "type": "signal_skipped",
            "ticker": ticker,
            "signal_type": signal_type,
            "reason": reason,
            "price": price,
            "edge": edge,
            **(extra or {}),
        })

    def trade_entered(
        self,
        ticker: str,
        side: str,
        price: float,
        count: int,
        signal_type: str,
        edge: float = 0,
        reasoning: str = "",
    ):
        """Log a trade entry."""
        self._write({
            "type": "trade_entered",
            "ticker": ticker,
            "side": side,
            "price": price,
            "count": count,
            "signal_type": signal_type,
            "edge": edge,
            "reasoning": reasoning,
        })

    def trade_exited(
        self,
        ticker: str,
        side: str,
        entry_price: float,
        exit_price: float,
        count: int,
        pnl: float,
        reason: str = "",
    ):
        """Log a trade exit with P&L."""
        self._write({
            "type": "trade_exited",
            "ticker": ticker,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "count": count,
            "pnl": pnl,
            "reason": reason,
        })

    def trade_wrong(
        self,
        ticker: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        what_went_wrong: str,
        lesson: str,
    ):
        """Post-mortem on a losing trade."""
        self._write({
            "type": "trade_wrong",
            "ticker": ticker,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "what_went_wrong": what_went_wrong,
            "lesson": lesson,
        })

    def market_settled(
        self,
        ticker: str,
        result: str,
        our_signal_side: str = "",
        our_signal_price: float = 0,
        did_we_trade: bool = False,
        pnl: float = 0,
    ):
        """Record a market settlement outcome."""
        self._write({
            "type": "market_settled",
            "ticker": ticker,
            "result": result,
            "our_signal_side": our_signal_side,
            "our_signal_price": our_signal_price,
            "did_we_trade": did_we_trade,
            "pnl": pnl,
        })

    def get_recent(self, limit: int = 50, log_type: str = None) -> list:
        """Read recent log entries."""
        if not self.file.exists():
            return []
        entries = []
        with open(self.file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if log_type and entry.get("type") != log_type:
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
        return entries[-limit:]

    def get_stats(self) -> dict:
        """Summary statistics from the learning log."""
        entries = self.get_recent(limit=10000)
        if not entries:
            return {"total_entries": 0}

        by_type = {}
        for e in entries:
            t = e.get("type", "unknown")
            by_type[t] = by_type.get(t, 0) + 1

        # Win/loss from trade_exited
        exits = [e for e in entries if e.get("type") == "trade_exited"]
        wins = sum(1 for e in exits if e.get("pnl", 0) > 0)
        losses = sum(1 for e in exits if e.get("pnl", 0) < 0)
        total_pnl = sum(e.get("pnl", 0) for e in exits)

        # Skip analysis
        skips = [e for e in entries if e.get("type") == "signal_skipped"]
        skip_reasons = {}
        for s in skips:
            r = s.get("reason", "unknown")
            skip_reasons[r] = skip_reasons.get(r, 0) + 1

        return {
            "total_entries": len(entries),
            "by_type": by_type,
            "trades_won": wins,
            "trades_lost": losses,
            "total_pnl": round(total_pnl, 2),
            "top_skip_reasons": dict(sorted(skip_reasons.items(), key=lambda x: -x[1])[:5]),
        }
