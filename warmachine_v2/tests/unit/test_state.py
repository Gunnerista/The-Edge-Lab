"""Tests for warmachine.core.state and session_state."""

from __future__ import annotations

import json
from unittest import mock

import pytest
from pydantic import ValidationError

from warmachine.core.session_state import SessionState, SessionStateData
from warmachine.core.state import (
    CircuitBreaker,
    CircuitState,
    Position,
    StateSnapshot,
    WarMachineState,
)


# ---------------- fixtures ----------------


def make_balance(cash_cents: int = 28061, port_cents: int = 0) -> dict:
    return {"balance": cash_cents, "portfolio_value": port_cents}


def make_positions(items=None) -> dict:
    return {"market_positions": items or []}


@pytest.fixture
def mock_kalshi():
    m = mock.MagicMock()
    m.get_balance.return_value = make_balance()
    m.get_positions_raw.return_value = make_positions()
    return m


@pytest.fixture
def session(tmp_path):
    return SessionState(tmp_path / "session.json")


@pytest.fixture
def wms(mock_kalshi, session):
    return WarMachineState(kalshi=mock_kalshi, session=session)


@pytest.fixture
def frozen_time():
    """Patch time.time inside the state module; returned list[0] is current t."""
    t = [1000.0]

    def fake():
        return t[0]

    with mock.patch("warmachine.core.state.time.time", side_effect=fake):
        yield t


# =============== State cache (1-4) ===============


def test_fresh_fetch_returns_kalshi_values(wms, mock_kalshi):
    mock_kalshi.get_balance.return_value = make_balance(cash_cents=50000, port_cents=2500)
    snap = wms.refresh()
    assert snap.cash == 500.0
    assert snap.portfolio_value == 25.0
    assert snap.bankroll == 525.0
    assert snap.is_stale is False


def test_cache_hit_within_ttl_no_api_call(wms, mock_kalshi, frozen_time):
    wms.refresh()
    assert mock_kalshi.get_balance.call_count == 1
    frozen_time[0] += 5.0  # still within 10s TTL
    wms.refresh()
    assert mock_kalshi.get_balance.call_count == 1  # no new call


def test_cache_miss_after_ttl_triggers_api(wms, mock_kalshi, frozen_time):
    wms.refresh()
    assert mock_kalshi.get_balance.call_count == 1
    frozen_time[0] += 11.0  # past TTL
    wms.refresh()
    assert mock_kalshi.get_balance.call_count == 2


def test_force_refresh_bypasses_cache(wms, mock_kalshi, frozen_time):
    wms.refresh()
    assert mock_kalshi.get_balance.call_count == 1
    wms.refresh(force=True)
    assert mock_kalshi.get_balance.call_count == 2


# =============== Stale cache on API failure (5-7) ===============


def test_api_failure_returns_stale_cache_with_flag(wms, mock_kalshi, frozen_time):
    wms.refresh()  # populate cache
    frozen_time[0] += 20.0
    mock_kalshi.get_balance.side_effect = RuntimeError("boom")
    snap = wms.refresh()
    assert snap.is_stale is True
    assert snap.bankroll == 280.61


def test_stale_cache_increments_breaker_count(wms, mock_kalshi, frozen_time):
    wms.refresh()
    frozen_time[0] += 20.0
    mock_kalshi.get_balance.side_effect = RuntimeError("boom")
    before = wms._breaker.failure_count
    wms.refresh()
    assert wms._breaker.failure_count == before + 1


def test_api_failure_no_cache_raises(mock_kalshi, session):
    mock_kalshi.get_balance.side_effect = RuntimeError("boom")
    wms = WarMachineState(kalshi=mock_kalshi, session=session)
    with pytest.raises(RuntimeError, match="unreachable"):
        wms.refresh()


# =============== Circuit breaker (8-12) ===============


def test_breaker_opens_after_5_failures():
    cb = CircuitBreaker()
    for _ in range(5):
        cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_breaker_rejects_fetch_when_open(wms, mock_kalshi):
    # Trip breaker directly
    for _ in range(5):
        wms._breaker.record_failure()
    assert wms._breaker.state == CircuitState.OPEN
    mock_kalshi.get_balance.reset_mock()
    result = wms._fetch_from_kalshi()
    assert result is None
    assert mock_kalshi.get_balance.call_count == 0


def test_breaker_half_open_after_60s():
    cb = CircuitBreaker()
    with mock.patch("warmachine.core.state.time.time") as t:
        t.return_value = 1000.0
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        t.return_value = 1000.0 + 61.0
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN


def test_breaker_closes_on_success_in_half_open(wms, mock_kalshi, frozen_time):
    for _ in range(5):
        wms._breaker.record_failure()
    frozen_time[0] += 61.0
    snap = wms._fetch_from_kalshi()
    assert snap is not None
    assert wms._breaker.state == CircuitState.CLOSED
    assert wms._breaker.failure_count == 0


def test_breaker_reopens_on_failure_in_half_open(wms, mock_kalshi, frozen_time):
    for _ in range(5):
        wms._breaker.record_failure()
    frozen_time[0] += 61.0
    mock_kalshi.get_balance.side_effect = RuntimeError("still broken")
    result = wms._fetch_from_kalshi()
    assert result is None
    assert wms._breaker.state == CircuitState.OPEN


# =============== SessionState (13-17) ===============


def test_session_records_positive_pnl_resets_losses(session):
    session.data.consecutive_losses = 3
    session.record_settlement(pnl=5.0, ticker="X", current_bankroll=300.0)
    assert session.consecutive_losses == 0
    assert session.daily_pnl == 5.0


def test_session_records_negative_pnl_increments_losses(session):
    session.record_settlement(pnl=-3.0, ticker="X", current_bankroll=280.0)
    session.record_settlement(pnl=-1.0, ticker="Y", current_bankroll=279.0)
    assert session.consecutive_losses == 2
    assert session.daily_pnl == pytest.approx(-4.0)


def test_daily_rollover_clears_daily_pnl(tmp_path):
    path = tmp_path / "s.json"
    initial = SessionStateData(daily_pnl=42.0, date="2020-01-01").model_dump()
    path.write_text(json.dumps(initial), encoding="utf-8")
    s = SessionState(path)
    assert s.daily_pnl == 0.0  # rolled over


def test_weekly_rollover_clears_weekly_pnl(tmp_path):
    path = tmp_path / "s.json"
    initial = SessionStateData(
        weekly_pnl=100.0, week_start="2020-01-01", date="2020-01-01"
    ).model_dump()
    path.write_text(json.dumps(initial), encoding="utf-8")
    s = SessionState(path)
    assert s.weekly_pnl == 0.0


def test_hwm_updates_only_on_new_peak(session):
    session.record_settlement(pnl=10.0, ticker="A", current_bankroll=300.0)
    assert session.high_water_mark == 300.0
    session.record_settlement(pnl=-5.0, ticker="B", current_bankroll=295.0)
    assert session.high_water_mark == 300.0  # unchanged on drawdown
    session.record_settlement(pnl=20.0, ticker="C", current_bankroll=320.0)
    assert session.high_water_mark == 320.0


def test_dd_level_boundaries(session):
    session.data.high_water_mark = 100.0
    assert session.dd_level(100.0) == "GREEN"
    assert session.dd_level(94.0) == "GREEN"    # -6% > -7%
    assert session.dd_level(92.0) == "YELLOW"   # -8%
    assert session.dd_level(87.0) == "ORANGE"   # -13%
    assert session.dd_level(80.0) == "RED"      # -20%


# =============== Pydantic validation (18-20) ===============


def test_position_model_rejects_negative_exposure():
    with pytest.raises(ValidationError):
        Position(ticker="X", quantity=1, market_exposure=-0.01, avg_price=0.5)


def test_snapshot_immutable():
    snap = StateSnapshot(
        cash=100.0, portfolio_value=0.0, bankroll=100.0, fetched_at=0.0
    )
    with pytest.raises(ValidationError):
        snap.cash = 999.0
