"""End-to-end pipeline tests for WarMachineRunner._evaluate_market + _run_one_cycle."""

from __future__ import annotations

import types
from datetime import datetime, timezone
from unittest import mock

import pytest

from warmachine.data.kalshi_market_parser import PropMarket
from warmachine.data.player_resolver import PlayerResolution
from warmachine.models.base import ModelOutput
from warmachine.risk.types import GateResult, OrderIntent, OrderSide, RiskDecision
from warmachine.runner.lifecycle import LifecycleManager
from warmachine.runner.main import WarMachineRunner
from warmachine.trading.broker_adapter import FillResult
from warmachine.trading.order_router import RoutingResult


# ---------------- fixtures ----------------


def make_prop_market(
    ticker: str = "KXNBAPTS-26APR26CLETOR-TORSBARNES4-10",
    player_name: str = "Scottie Barnes",
) -> PropMarket:
    return PropMarket(
        ticker=ticker,
        event_ticker="KXNBAPTS-26APR26CLETOR",
        prop_type="points",
        player_uuid="fb9a7f9e-8a0c-4e90-94e8-051dea709bef",
        team_uuid="96c447dd-2cdd-4c58-a151-ef0df87de981",
        player_name=player_name,
        line=9.5,
        tip_off_utc=datetime(2026, 4, 26, 20, 0, 0, tzinfo=timezone.utc),
        market_close_utc=datetime(2026, 5, 10, 17, 0, 0, tzinfo=timezone.utc),
        yes_bid_cents=94,
        yes_ask_cents=30,   # low so edge is possible
        no_bid_cents=3,
        no_ask_cents=70,
        yes_ask_depth=5,
        no_ask_depth=5,
        title="Scottie Barnes: 10+ points",
    )


def make_resolution() -> PlayerResolution:
    return PlayerResolution(
        kalshi_uuid="fb9a7f9e-8a0c-4e90-94e8-051dea709bef",
        nba_player_id=1630567,
        player_name="Scottie Barnes",
        method="exact",
        confidence=1.0,
    )


def make_features() -> dict:
    return {
        "season_avg": 19.5, "recent_10_avg": 20.1, "recent_5_avg": 22.0,
        "home_away_avg": 20.8, "recent_std": 5.5,
        "opp_allowed": 21.0, "league_avg": 20.0,
        "opp_pace": 99.0, "league_pace": 100.0,
        "is_b2b": 0, "games_played": 65, "spread": 0.0, "line": 9.5,
    }


def make_model_output(prob: float = 0.65) -> ModelOutput:
    return ModelOutput(
        raw_probability=prob,
        calibrated_probability=prob,
        model_version="gaussian_v1_platt_v2_points",
        features_used=make_features(),
    )


def make_intent() -> OrderIntent:
    return OrderIntent(
        ticker="KXNBAPTS-26APR26CLETOR-TORSBARNES4-10",
        side=OrderSide.YES,
        quantity=20,
        limit_price_cents=30,
        signal_generated_at=datetime.now(timezone.utc),
        signal_model_prob=0.65,
        signal_edge_pp=30.0,
        game_id="KXNBAPTS-26APR26CLETOR",
        player_id="1630567",
        prop_type="points",
    )


def make_fill() -> FillResult:
    return FillResult(
        order_id="live_abc",
        ticker="KXNBAPTS-26APR26CLETOR-TORSBARNES4-10",
        side=OrderSide.YES,
        filled_quantity=20,
        fill_price_cents=30,
        fee_cents=30,
        total_cost_cents=630,
        filled_at=datetime.now(timezone.utc),
        status="filled",
    )


def make_routing(allowed: bool = True, filled: bool = True) -> RoutingResult:
    intent = make_intent()
    decision = RiskDecision(
        allowed=allowed,
        gate_results=[GateResult(gate_name="all", passed=allowed, reason=None if allowed else "blocked")],
        first_failed_gate=None if allowed else "kill_switch",
    )
    return RoutingResult(
        intent=intent,
        risk_decision=decision,
        fill=make_fill() if filled else None,
        routed=allowed and filled,
        reason=None if (allowed and filled) else "blocked",
    )


def _make_runner(tmp_path):
    lc = LifecycleManager()
    state = mock.MagicMock()
    state.is_stale = False
    state.bankroll = 280.0
    runner = WarMachineRunner(
        lifecycle=lc,
        state=state,
        kalshi_client=mock.MagicMock(),
        signal_engine=mock.MagicMock(),
        risk_engine=mock.MagicMock(),
        broker=mock.MagicMock(mode="paper"),
        order_router=mock.MagicMock(),
        v1_bridge=mock.MagicMock(),
        prop_models={"points": mock.MagicMock()},
        clv_tracker=mock.MagicMock(),
    )
    runner.market_fetcher = mock.MagicMock()
    runner.resolver = mock.MagicMock()
    runner.dnp_model = mock.MagicMock()
    runner.settings = types.SimpleNamespace(
        paths=types.SimpleNamespace(heartbeat=str(tmp_path / "hb.txt"))
    )
    runner.db_connection = mock.MagicMock()
    runner.POLL_INTERVAL_SECONDS = 0.01
    return runner


def _wire_happy_path(runner, prob: float = 0.65, routed: bool = True):
    """Configure all mocks for full success path."""
    runner.resolver.resolve_player.return_value = make_resolution()
    runner.bridge.get_player_features.return_value = make_features()
    runner.models["points"].predict.return_value = make_model_output(prob)
    runner.models["points"].model_version = "gaussian_v1_platt_v2_points"
    runner.dnp_model.adjust_prop_probability.return_value = prob
    runner.signal_engine.generate.return_value = make_intent()
    runner.router.route.return_value = make_routing(allowed=True, filled=routed)


# =================== _evaluate_market (1-11) ===================


def test_evaluate_market_unresolved_player_returns_none(tmp_path):
    r = _make_runner(tmp_path)
    r.resolver.resolve_player.return_value = None
    result = r._evaluate_market(make_prop_market())
    assert result is None
    r.bridge.get_player_features.assert_not_called()


def test_evaluate_market_no_features_returns_none(tmp_path):
    r = _make_runner(tmp_path)
    r.resolver.resolve_player.return_value = make_resolution()
    r.bridge.get_player_features.return_value = None
    result = r._evaluate_market(make_prop_market())
    assert result is None
    r.models["points"].predict.assert_not_called()


def test_evaluate_market_no_signal_returns_signal_false(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    r.signal_engine.generate.return_value = None  # low edge
    result = r._evaluate_market(make_prop_market())
    assert result == {"ticker": "KXNBAPTS-26APR26CLETOR-TORSBARNES4-10",
                      "signal": False, "routed": False}
    r.router.route.assert_not_called()


def test_evaluate_market_signal_but_gate_blocks(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    r.router.route.return_value = make_routing(allowed=False, filled=False)
    result = r._evaluate_market(make_prop_market())
    assert result["signal"] is True
    assert result["routed"] is False
    assert result["gate_reject"] is True
    r.clv.record_entry.assert_not_called()


def test_evaluate_market_full_success_path(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    result = r._evaluate_market(make_prop_market())
    assert result["signal"] is True
    assert result["routed"] is True
    assert result["gate_reject"] is False
    assert result["fill"] is not None
    assert result["fill"]["filled_quantity"] == 20
    r.clv.record_entry.assert_called_once()


def test_evaluate_market_calls_resolver_with_uuid_and_name(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    mkt = make_prop_market()
    r._evaluate_market(mkt)
    r.resolver.resolve_player.assert_called_once_with(mkt.player_uuid, mkt.player_name)


def test_evaluate_market_builds_correct_prediction(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    mkt = make_prop_market()
    r._evaluate_market(mkt)
    call = r.signal_engine.generate.call_args
    pred = call.args[0]
    assert pred.ticker == mkt.ticker
    assert pred.prop_type == "points"
    assert pred.market_yes_ask_cents == 30
    assert pred.market_no_ask_cents == 70
    assert pred.game_id == mkt.event_ticker
    assert pred.player_id == "1630567"


def test_evaluate_market_dnp_adjustment_applied(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r, prob=0.70)
    r.dnp_model.adjust_prop_probability.return_value = 0.55  # adjusted
    r._evaluate_market(make_prop_market())
    r.dnp_model.adjust_prop_probability.assert_called_once()
    pred = r.signal_engine.generate.call_args.args[0]
    assert pred.dnp_adjusted_prob_yes == 0.55


def test_evaluate_market_dnp_failure_falls_back_to_raw(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r, prob=0.70)
    r.dnp_model.adjust_prop_probability.side_effect = RuntimeError("dnp broken")
    result = r._evaluate_market(make_prop_market())
    assert result is not None
    assert result["signal"] is True
    pred = r.signal_engine.generate.call_args.args[0]
    assert pred.dnp_adjusted_prob_yes == 0.70  # fell back to raw


def test_evaluate_market_clv_recorded_on_successful_route(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    r._evaluate_market(make_prop_market())
    r.clv.record_entry.assert_called_once()
    clv_entry = r.clv.record_entry.call_args.args[0]
    assert clv_entry.ticker == "KXNBAPTS-26APR26CLETOR-TORSBARNES4-10"
    assert clv_entry.entry_price_cents == 30
    assert clv_entry.quantity == 20
    assert clv_entry.player == "Scottie Barnes"


def test_evaluate_market_clv_not_recorded_on_reject(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    r.router.route.return_value = make_routing(allowed=False, filled=False)
    r._evaluate_market(make_prop_market())
    r.clv.record_entry.assert_not_called()


# =================== _run_one_cycle (12-15) ===================


def test_run_one_cycle_writes_heartbeat(tmp_path):
    r = _make_runner(tmp_path)
    r.market_fetcher.fetch_all_open_nba_prop_markets.return_value = []
    r._run_one_cycle()
    hb = tmp_path / "hb.txt"
    assert hb.exists()
    assert "T" in hb.read_text()  # ISO format contains 'T'


def test_run_one_cycle_handles_market_exception(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    # 3 markets; middle one triggers exception in evaluate
    m1 = make_prop_market(ticker="KXNBAPTS-A-P1-10")
    m2 = make_prop_market(ticker="KXNBAPTS-B-P2-10")
    m3 = make_prop_market(ticker="KXNBAPTS-C-P3-10")
    r.market_fetcher.fetch_all_open_nba_prop_markets.return_value = [m1, m2, m3]

    def flaky_resolve(uuid, name):
        if "B" in r.resolver.resolve_player.call_args.args[0] or True:
            # Fail on the 2nd call
            if r.resolver.resolve_player.call_count == 2:
                raise RuntimeError("boom on 2nd market")
        return make_resolution()

    r.resolver.resolve_player.side_effect = flaky_resolve

    r._run_one_cycle()
    # Despite exception on market 2, all 3 attempted
    assert r.resolver.resolve_player.call_count == 3


def test_run_one_cycle_aggregates_stats(tmp_path, caplog):
    import logging
    r = _make_runner(tmp_path)

    # 3 markets with different outcomes
    m1 = make_prop_market(ticker="KXNBAPTS-A-P1-10")
    m2 = make_prop_market(ticker="KXNBAPTS-B-P2-10")
    m3 = make_prop_market(ticker="KXNBAPTS-C-P3-10")
    r.market_fetcher.fetch_all_open_nba_prop_markets.return_value = [m1, m2, m3]

    # Wire happy path defaults
    r.resolver.resolve_player.return_value = make_resolution()
    r.bridge.get_player_features.return_value = make_features()
    r.models["points"].predict.return_value = make_model_output(0.65)
    r.models["points"].model_version = "gaussian_v1_platt_v2_points"
    r.dnp_model.adjust_prop_probability.return_value = 0.65

    # Market 1: signal + route success
    # Market 2: signal but router rejects
    # Market 3: no signal
    r.signal_engine.generate.side_effect = [make_intent(), make_intent(), None]
    r.router.route.side_effect = [
        make_routing(allowed=True, filled=True),
        make_routing(allowed=False, filled=False),
    ]

    with caplog.at_level(logging.INFO, logger="warmachine.runner.main"):
        r._run_one_cycle()

    msg = " ".join(rec.message for rec in caplog.records)
    assert "scanned=3" in msg
    assert "signals=2" in msg
    assert "routed=1" in msg


def test_run_one_cycle_respects_max_markets_cap(tmp_path):
    r = _make_runner(tmp_path)
    _wire_happy_path(r)
    r.MAX_MARKETS_PER_CYCLE = 50
    markets = [make_prop_market(ticker=f"KXNBAPTS-M{i}-P-10") for i in range(100)]
    r.market_fetcher.fetch_all_open_nba_prop_markets.return_value = markets
    r._run_one_cycle()
    assert r.resolver.resolve_player.call_count == 50
