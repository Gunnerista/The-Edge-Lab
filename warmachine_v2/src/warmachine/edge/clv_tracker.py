"""
Module: clv_tracker.py
Purpose: CLV tracker - closing line value record and analysis.
         Research refs: Pinnacle, Unabated, Pikkit, OddsJam.
         Kalshi-specific: no vig, cent price = implied probability directly.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from warmachine.utils.logger import get_logger

log = get_logger(__name__)


class CLVStatus(str, Enum):
    PENDING_CLOSE = "pending_close"
    CLOSING_CAPTURED = "closing_captured"
    SETTLED = "settled"
    MISSED_CLOSE = "missed_close"


class CLVEntry(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ticker: str
    entry_time_utc: str
    entry_price_cents: int = Field(..., ge=1, le=99)
    entry_price_dollars: float = Field(..., ge=0.01, le=0.99)
    side: Literal["YES", "NO"]
    quantity: int = Field(..., gt=0)
    model_prob: float = Field(..., ge=0.0, le=1.0)
    edge_at_entry_pp: float
    prop_type: str
    player: Optional[str] = None
    line: Optional[float] = None
    game_id: str
    game_tip_off_utc: str

    closing_snapshot_utc: Optional[str] = None
    closing_price_cents: Optional[int] = None
    closing_price_dollars: Optional[float] = None

    clv_pp: Optional[float] = None
    clv_pct: Optional[float] = None
    beat_close: Optional[bool] = None

    outcome: Optional[Literal["WIN", "LOSS"]] = None
    realized_pnl: Optional[float] = None
    settlement_time_utc: Optional[str] = None

    status: CLVStatus = CLVStatus.PENDING_CLOSE


class CLVTracker:
    CLOSING_WINDOW_MINUTES_BEFORE = 5
    CLOSING_WINDOW_MINUTES_AFTER = 15

    MILESTONE_PRELIM = 50
    MILESTONE_MEANINGFUL = 200
    MILESTONE_CONFIDENT = 500

    BEAT_RATE_STRONG = 0.60
    BEAT_RATE_NEUTRAL = 0.50
    BEAT_RATE_WARNING = 0.45

    AVG_CLV_PP_ELITE = 5.0
    AVG_CLV_PP_PROFITABLE = 2.0

    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists():
            self.log_path.touch()

    # ============ WRITE ============

    def record_entry(self, entry: CLVEntry) -> None:
        line = entry.model_dump_json() + "\n"
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(line)
        log.info(
            f"CLV entry recorded: {entry.ticker} @ {entry.entry_price_cents}c, "
            f"edge={entry.edge_at_entry_pp:.1f}pp"
        )

    def record_closing(
        self, entry_id: str, closing_price_cents: int, capture_time_utc: str
    ) -> None:
        entries = self._load_all()
        updated = False
        found = None
        for e in entries:
            if e.entry_id == entry_id and e.status == CLVStatus.PENDING_CLOSE:
                e.closing_snapshot_utc = capture_time_utc
                e.closing_price_cents = closing_price_cents
                e.closing_price_dollars = closing_price_cents / 100.0
                if e.side == "YES":
                    e.clv_pp = float(closing_price_cents - e.entry_price_cents)
                else:
                    e.clv_pp = float(e.entry_price_cents - closing_price_cents)
                e.clv_pct = (e.clv_pp / e.entry_price_cents) * 100.0
                e.beat_close = e.clv_pp > 0
                e.status = CLVStatus.CLOSING_CAPTURED
                updated = True
                found = e
                break
        if updated:
            self._rewrite_all(entries)
            log.info(
                f"CLV closing captured: {entry_id} clv_pp={found.clv_pp:+.1f}"
            )

    def record_miss(self, entry_id: str, reason: str) -> None:
        entries = self._load_all()
        for e in entries:
            if e.entry_id == entry_id and e.status == CLVStatus.PENDING_CLOSE:
                e.status = CLVStatus.MISSED_CLOSE
                break
        self._rewrite_all(entries)
        log.warning(f"CLV closing MISSED: {entry_id} reason={reason}")

    def record_settlement(
        self, ticker: str, outcome: Literal["WIN", "LOSS"], pnl: float
    ) -> None:
        entries = self._load_all()
        for e in entries:
            if e.ticker == ticker and e.status in (
                CLVStatus.CLOSING_CAPTURED,
                CLVStatus.MISSED_CLOSE,
            ):
                e.outcome = outcome
                e.realized_pnl = pnl
                e.settlement_time_utc = datetime.now(timezone.utc).isoformat()
                e.status = CLVStatus.SETTLED
                break
        self._rewrite_all(entries)

    # ============ CLOSING CAPTURE LOGIC ============

    def get_entries_needing_close(self, now_utc: datetime) -> list[CLVEntry]:
        entries = self._load_all()
        result: list[CLVEntry] = []
        for e in entries:
            if e.status != CLVStatus.PENDING_CLOSE:
                continue
            tip_off = datetime.fromisoformat(
                e.game_tip_off_utc.replace("Z", "+00:00")
            )
            window_start = tip_off - timedelta(
                minutes=self.CLOSING_WINDOW_MINUTES_BEFORE
            )
            window_end = tip_off + timedelta(
                minutes=self.CLOSING_WINDOW_MINUTES_AFTER
            )
            if window_start <= now_utc <= window_end:
                result.append(e)
            elif now_utc > window_end:
                self.record_miss(e.entry_id, "runner not active during closing window")
        return result

    # ============ ANALYSIS ============

    def stats(self, window_days: Optional[int] = None) -> dict:
        entries = self._load_all()
        if window_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
            entries = [
                e
                for e in entries
                if datetime.fromisoformat(e.entry_time_utc.replace("Z", "+00:00"))
                >= cutoff
            ]

        scored = [e for e in entries if e.clv_pp is not None]
        n = len(scored)
        if n == 0:
            return {"n": 0, "status": "no_data"}

        avg_clv_pp = sum(e.clv_pp for e in scored) / n
        avg_clv_pct = sum(e.clv_pct for e in scored) / n
        beat_count = sum(1 for e in scored if e.beat_close)
        beat_rate = beat_count / n

        if n >= self.MILESTONE_CONFIDENT:
            tier = "STATISTICAL_CONFIDENCE"
        elif n >= self.MILESTONE_MEANINGFUL:
            tier = "MEANINGFUL"
        elif n >= self.MILESTONE_PRELIM:
            tier = "PRELIMINARY"
        else:
            tier = "INSUFFICIENT"

        if (
            beat_rate >= self.BEAT_RATE_STRONG
            and avg_clv_pp >= self.AVG_CLV_PP_PROFITABLE
        ):
            perf = "STRONG_EDGE"
        elif beat_rate < self.BEAT_RATE_WARNING:
            perf = "WARNING"
        elif beat_rate >= self.BEAT_RATE_NEUTRAL:
            perf = "MARGINAL"
        else:
            perf = "SUBPAR"

        return {
            "n": n,
            "avg_clv_pp": round(avg_clv_pp, 2),
            "avg_clv_pct": round(avg_clv_pct, 2),
            "beat_rate": round(beat_rate, 3),
            "beat_count": beat_count,
            "sample_tier": tier,
            "performance": perf,
        }

    def breakdown_by_prop_type(self) -> dict[str, dict]:
        entries = [e for e in self._load_all() if e.clv_pp is not None]
        result: dict[str, dict] = {}
        for prop in set(e.prop_type for e in entries):
            subset = [e for e in entries if e.prop_type == prop]
            n = len(subset)
            if n == 0:
                continue
            result[prop] = {
                "n": n,
                "avg_clv_pp": round(sum(e.clv_pp for e in subset) / n, 2),
                "beat_rate": round(sum(1 for e in subset if e.beat_close) / n, 3),
            }
        return result

    def breakdown_by_edge_bucket(self) -> dict[str, dict]:
        entries = [e for e in self._load_all() if e.clv_pp is not None]
        buckets = [
            ("15-20pp", 15, 20),
            ("20-25pp", 20, 25),
            ("25-30pp", 25, 30),
            ("30pp+", 30, 100),
        ]
        result: dict[str, dict] = {}
        for label, lo, hi in buckets:
            subset = [e for e in entries if lo <= e.edge_at_entry_pp < hi]
            n = len(subset)
            if n == 0:
                continue
            result[label] = {
                "n": n,
                "avg_clv_pp": round(sum(e.clv_pp for e in subset) / n, 2),
                "beat_rate": round(sum(1 for e in subset if e.beat_close) / n, 3),
            }
        return result

    # ============ IO ============

    def _load_all(self) -> list[CLVEntry]:
        if not self.log_path.exists():
            return []
        entries: list[CLVEntry] = []
        with self.log_path.open("r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                entries.append(CLVEntry(**json.loads(raw)))
        return entries

    def _rewrite_all(self, entries: list[CLVEntry]) -> None:
        tmp = self.log_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(e.model_dump_json() + "\n")
        tmp.replace(self.log_path)
