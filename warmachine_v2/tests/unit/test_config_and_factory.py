"""Tests for warmachine.core.config + warmachine.runner.factory."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from pydantic import ValidationError

from warmachine.core.config import Settings, load_settings


# ---------------- helpers ----------------


def _clear_wm_env(monkeypatch):
    """Drop any WM_* env vars to guarantee a clean load."""
    for k in list(os.environ):
        if k.startswith("WM_"):
            monkeypatch.delenv(k, raising=False)


def _write_toml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "test.toml"
    p.write_text(body, encoding="utf-8")
    return p


def _posix_paths_toml(tmp_path: Path, mode: str = "paper", extra: str = "") -> str:
    pp = tmp_path.as_posix()
    return f"""
[runtime]
mode = "{mode}"

[paths]
data_dir = "{pp}/data"
state_file = "{pp}/state.json"
clv_log = "{pp}/clv.jsonl"
paper_state = "{pp}/paper.json"
kill_switch = "{pp}/STOP"
heartbeat = "{pp}/hb.txt"

[database]
host = "localhost"
port = 5432
dbname = "warmachine"
user = "postgres"
password = "test"
{extra}
"""


# =================== Config loading (1-8) ===================


def test_default_settings_loads_without_toml(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    missing_toml = tmp_path / "does_not_exist.toml"
    settings = load_settings(toml_path=missing_toml)
    assert settings.runtime.mode == "paper"
    assert settings.runtime.poll_interval_seconds == 60
    assert settings.trading.global_min_edge_pp == 15.0
    assert settings.paper.initial_balance_cents == 28061
    assert settings.risk.bankruptcy.floor_dollars == 100.0


def test_toml_file_overrides_defaults(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    toml = _write_toml(tmp_path, '[runtime]\nmode = "live"\npoll_interval_seconds = 30\n')
    settings = load_settings(toml_path=toml)
    assert settings.runtime.mode == "live"
    assert settings.runtime.poll_interval_seconds == 30


def test_env_var_overrides_toml(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    toml = _write_toml(tmp_path, '[runtime]\nmode = "paper"\n')
    monkeypatch.setenv("WM_RUNTIME__MODE", "live")
    settings = load_settings(toml_path=toml)
    assert settings.runtime.mode == "live"


def test_nested_exposure_config_accessible(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    settings = load_settings(toml_path=tmp_path / "missing.toml")
    assert settings.risk.exposure.per_player_pct == 0.05
    assert settings.risk.exposure.per_ticker_pct == 0.03


def test_kelly_nested_under_trading(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    settings = load_settings(toml_path=tmp_path / "missing.toml")
    assert settings.trading.kelly.fractional_multiplier == 0.25
    assert settings.trading.kelly.single_trade_cap_pct == 0.03


def test_paper_initial_balance_28061(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    settings = load_settings(toml_path=tmp_path / "missing.toml")
    assert settings.paper.initial_balance_cents == 28061


def test_bankruptcy_floor_100_default(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    settings = load_settings(toml_path=tmp_path / "missing.toml")
    assert settings.risk.bankruptcy.floor_dollars == 100.0
    assert settings.risk.bankruptcy.cooldown_days == 14


def test_invalid_mode_raises(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    monkeypatch.setenv("WM_RUNTIME__MODE", "invalid_mode")
    with pytest.raises((ValidationError, ValueError, Exception)):
        load_settings(toml_path=tmp_path / "missing.toml")


# =================== Factory integration placeholders (9-10) ===================


@pytest.mark.integration
def test_build_runner_paper_creates_paper_adapter():
    """Requires real PostgreSQL + Kalshi credentials. Skipped in CI."""
    pytest.skip("integration — requires live PG + Kalshi")


@pytest.mark.integration
def test_build_runner_live_creates_kalshi_adapter():
    pytest.skip("integration — requires live PG + Kalshi")


# =================== Factory pure-unit tests (11-14) ===================


def _mock_db() -> mock.MagicMock:
    db = mock.MagicMock()
    cursor = mock.MagicMock()
    db.cursor.return_value = cursor
    # PlayerResolver preload expects empty results
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    return db


def _mock_kalshi() -> mock.MagicMock:
    k = mock.MagicMock()
    k.base_url = "https://api.test"
    k.get_balance.return_value = {"balance": 28061, "portfolio_value": 0}
    k.get_positions_raw.return_value = {"market_positions": []}
    return k


def _build_with_mocks(tmp_path, mode=None, extra_toml=""):
    toml = _write_toml(tmp_path, _posix_paths_toml(tmp_path, extra=extra_toml))
    from warmachine.runner import factory

    db_mock = _mock_db()
    kalshi_mock = _mock_kalshi()
    with mock.patch.object(factory, "_build_db", return_value=db_mock), mock.patch.object(
        factory, "create_v1_kalshi_client", return_value=kalshi_mock
    ):
        runner = factory.build_runner(mode=mode, toml_path=toml)
    return runner, db_mock, kalshi_mock


def test_factory_respects_mode_override(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    runner, _, _ = _build_with_mocks(tmp_path, mode="live")
    assert runner.broker.mode == "live"


def test_factory_respects_toml_mode(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    runner, _, _ = _build_with_mocks(tmp_path, mode=None)  # TOML defaults to paper
    assert runner.broker.mode == "paper"


def test_factory_wires_all_modules(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    runner, _, _ = _build_with_mocks(tmp_path, mode="paper")
    # Core runner attrs
    for attr in (
        "lifecycle", "state", "kalshi", "signal_engine", "risk_engine",
        "broker", "router", "bridge", "models", "clv",
    ):
        assert hasattr(runner, attr), f"runner missing {attr}"
    # Factory-added extras
    for attr in ("market_fetcher", "resolver", "dnp_model", "settings", "db_connection"):
        assert hasattr(runner, attr), f"runner missing {attr}"
    # Prop models wiring
    assert set(runner.models.keys()) == {"points", "rebounds", "assists", "threes"}


def test_factory_uses_config_exposure_values(tmp_path, monkeypatch):
    _clear_wm_env(monkeypatch)
    extra = """
[risk.exposure]
per_ticker_pct = 0.01
per_game_pct = 0.02
per_player_pct = 0.03
per_prop_type_daily_pct = 0.04
total_open_pct = 0.05
"""
    runner, _, _ = _build_with_mocks(tmp_path, mode="paper", extra_toml=extra)
    # Risk engine's exposure gate is at index 2 in the gates list
    # (order: kill, bankruptcy, exposure, liquidity, edge_decay)
    exposure_gate = runner.risk_engine.gates[2]
    assert exposure_gate.limits.per_ticker_pct == 0.01
    assert exposure_gate.limits.per_game_pct == 0.02
    assert exposure_gate.limits.per_player_pct == 0.03
    assert exposure_gate.limits.per_prop_type_daily_pct == 0.04
    assert exposure_gate.limits.total_open_pct == 0.05
