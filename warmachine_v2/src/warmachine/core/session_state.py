"""
Module: session_state.py
Purpose: Session counters only. NO money amounts are stored here.
Storage: daily/weekly PnL, consecutive losses, HWM, DD level, halts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from warmachine.utils.logger import get_logger

log = get_logger(__name__)


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class SessionStateData(BaseModel):
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    consecutive_losses: int = 0
    high_water_mark: float = 0.0
    date: str = Field(default_factory=_today_utc)
    week_start: str = Field(default_factory=_today_utc)
    daily_halted: bool = False
    weekly_halted: bool = False
    cooldown_until: Optional[str] = None
    bankruptcy_triggered_at: Optional[str] = None
    paper_only_until: Optional[str] = None


class SessionState:
    DD_YELLOW = -0.07
    DD_ORANGE = -0.12
    DD_RED = -0.15
    BANKRUPTCY_FLOOR = 100.0
    BANKRUPTCY_COOLDOWN_DAYS = 14

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: SessionStateData = self._load()

    def _load(self) -> SessionStateData:
        if not self.path.exists():
            data = SessionStateData()
            self._save(data)
            return data
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        data = SessionStateData(
            **{k: v for k, v in raw.items() if k in SessionStateData.model_fields}
        )
        self._rollover(data)
        return data

    def _save(self, data: SessionStateData) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(data.model_dump_json(indent=2), encoding="utf-8")

    def _rollover(self, data: SessionStateData) -> None:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        changed = False
        if data.date != today:
            data.daily_pnl = 0.0
            data.daily_halted = False
            data.date = today
            changed = True
        week_start_dt = datetime.strptime(data.week_start, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        if (now - week_start_dt).days >= 7:
            data.weekly_pnl = 0.0
            data.weekly_halted = False
            data.week_start = today
            changed = True
        if changed:
            self._save(data)

    def record_settlement(
        self, pnl: float, ticker: str, current_bankroll: float
    ) -> None:
        self._rollover(self.data)
        self.data.daily_pnl += pnl
        self.data.weekly_pnl += pnl
        if pnl < 0:
            self.data.consecutive_losses += 1
        else:
            self.data.consecutive_losses = 0
        if current_bankroll > self.data.high_water_mark:
            old = self.data.high_water_mark
            self.data.high_water_mark = current_bankroll
            log.info(f"HWM: ${old:.2f} -> ${current_bankroll:.2f}")
        if (
            current_bankroll < self.BANKRUPTCY_FLOOR
            and self.data.bankruptcy_triggered_at is None
        ):
            now = datetime.now(timezone.utc)
            self.data.bankruptcy_triggered_at = now.isoformat()
            self.data.paper_only_until = (
                now + timedelta(days=self.BANKRUPTCY_COOLDOWN_DAYS)
            ).isoformat()
            log.critical(
                f"BANKRUPTCY: ${current_bankroll:.2f} < ${self.BANKRUPTCY_FLOOR}. "
                f"Paper mode {self.BANKRUPTCY_COOLDOWN_DAYS}d."
            )
        self._save(self.data)

    def dd_level(
        self, current_bankroll: float
    ) -> Literal["GREEN", "YELLOW", "ORANGE", "RED"]:
        if self.data.high_water_mark <= 0:
            return "GREEN"
        dd = (
            current_bankroll - self.data.high_water_mark
        ) / self.data.high_water_mark
        if dd < self.DD_RED:
            return "RED"
        if dd < self.DD_ORANGE:
            return "ORANGE"
        if dd < self.DD_YELLOW:
            return "YELLOW"
        return "GREEN"

    @property
    def daily_pnl(self) -> float:
        return self.data.daily_pnl

    @property
    def weekly_pnl(self) -> float:
        return self.data.weekly_pnl

    @property
    def consecutive_losses(self) -> int:
        return self.data.consecutive_losses

    @property
    def high_water_mark(self) -> float:
        return self.data.high_water_mark
