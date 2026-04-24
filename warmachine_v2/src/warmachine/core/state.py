"""
Module: state.py
Purpose: WarMachineState - Kalshi API is the source of truth for bankroll/positions.
         TTL cache 10s (Kalshi Basic tier 20 reads/sec).
         Circuit breaker for API outage (5 failures -> open -> 60s recovery).
         JSON stores ONLY session counters (daily_pnl, HWM, DD, consecutive_losses).
         JSON NEVER stores money amounts.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from warmachine.core.kalshi_client import KalshiClient
from warmachine.core.session_state import SessionState
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Position(BaseModel):
    model_config = ConfigDict(frozen=True)
    ticker: str
    quantity: int
    market_exposure: float = Field(..., ge=0)
    avg_price: float = Field(..., ge=0)


class StateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)
    cash: float = Field(..., ge=0)
    portfolio_value: float = Field(..., ge=0)
    bankroll: float = Field(..., ge=0)
    positions: tuple[Position, ...] = Field(default_factory=tuple)
    fetched_at: float
    is_stale: bool = False


class CircuitBreaker:
    FAILURE_THRESHOLD = 5
    RECOVERY_TIMEOUT_SEC = 60.0

    def __init__(self) -> None:
        self.state: CircuitState = CircuitState.CLOSED
        self.failure_count: int = 0
        self.last_failure_time: float = 0.0

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.RECOVERY_TIMEOUT_SEC:
                self.state = CircuitState.HALF_OPEN
                log.info("Circuit breaker HALF_OPEN (recovery attempt)")
                return True
            return False
        return True

    def record_success(self) -> None:
        if self.state == CircuitState.HALF_OPEN:
            log.info("Circuit breaker recovered -> CLOSED")
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.FAILURE_THRESHOLD:
            self.state = CircuitState.OPEN
            log.critical(
                f"Circuit breaker OPEN after {self.failure_count} failures. "
                f"Fresh fetches rejected for {self.RECOVERY_TIMEOUT_SEC}s."
            )


class WarMachineState:
    CACHE_TTL_SECONDS = 10.0

    def __init__(self, kalshi: KalshiClient, session: SessionState):
        self._kalshi = kalshi
        self._session = session
        self._cache: Optional[StateSnapshot] = None
        self._breaker = CircuitBreaker()

    def _fetch_from_kalshi(self) -> Optional[StateSnapshot]:
        if not self._breaker.allow_request():
            log.warning("Circuit breaker OPEN - fetch rejected")
            return None
        try:
            bal = self._kalshi.get_balance()
            cash = bal["balance"] / 100.0
            port = bal["portfolio_value"] / 100.0

            pos_resp = self._kalshi.get_positions_raw()
            positions = tuple(
                Position(
                    ticker=p["ticker"],
                    quantity=p.get("position", 0),
                    market_exposure=p.get("market_exposure", 0) / 100.0,
                    avg_price=p.get("average_cost", 0) / 100.0,
                )
                for p in pos_resp.get("market_positions", [])
                if p.get("position", 0) != 0
            )

            snap = StateSnapshot(
                cash=cash,
                portfolio_value=port,
                bankroll=cash + port,
                positions=positions,
                fetched_at=time.time(),
                is_stale=False,
            )
            self._breaker.record_success()
            return snap
        except Exception as e:
            self._breaker.record_failure()
            log.error(f"Kalshi API fetch failed: {e}")
            return None

    def refresh(self, force: bool = False) -> StateSnapshot:
        now = time.time()

        if (
            not force
            and self._cache
            and (now - self._cache.fetched_at) < self.CACHE_TTL_SECONDS
        ):
            return self._cache

        fresh = self._fetch_from_kalshi()
        if fresh is not None:
            self._cache = fresh
            return fresh

        if self._cache:
            age = now - self._cache.fetched_at
            stale = self._cache.model_copy(update={"is_stale": True})
            log.warning(
                f"Using stale cache (age {age:.0f}s, breaker={self._breaker.state.value})"
            )
            return stale

        raise RuntimeError("Kalshi API unreachable and no cache available")

    @property
    def bankroll(self) -> float:
        return self.refresh().bankroll

    @property
    def cash(self) -> float:
        return self.refresh().cash

    @property
    def portfolio_value(self) -> float:
        return self.refresh().portfolio_value

    @property
    def positions(self) -> tuple[Position, ...]:
        return self.refresh().positions

    @property
    def is_stale(self) -> bool:
        return self.refresh().is_stale

    @property
    def circuit_state(self) -> CircuitState:
        return self._breaker.state

    @property
    def daily_pnl(self) -> float:
        return self._session.daily_pnl

    @property
    def weekly_pnl(self) -> float:
        return self._session.weekly_pnl

    @property
    def consecutive_losses(self) -> int:
        return self._session.consecutive_losses

    @property
    def dd_level(self) -> Literal["GREEN", "YELLOW", "ORANGE", "RED"]:
        return self._session.dd_level(self.bankroll)

    def record_settlement(self, pnl: float, ticker: str) -> None:
        self._session.record_settlement(
            pnl=pnl, ticker=ticker, current_bankroll=self.bankroll
        )
