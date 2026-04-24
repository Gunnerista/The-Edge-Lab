"""Tests for GaussianBaseline + V1DataBridge + LifecycleManager + WarMachineRunner."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from unittest import mock

import pytest

from warmachine.data.v1_bridge import V1DataBridge
from warmachine.models.base import ModelInput
from warmachine.models.gaussian_baseline import (
    PLATT_A,
    PLATT_B,
    GaussianBaseline,
    GaussianConfig,
    _logit,
    _sigmoid,
)
from warmachine.runner.lifecycle import LifecycleManager
from warmachine.runner.main import WarMachineRunner


# ---------------- helpers ----------------


def base_features(**overrides) -> dict:
    f = {
        "season_avg": 25.0,
        "recent_10_avg": 22.0,
        "recent_5_avg": 18.0,
        "home_away_avg": 20.0,
        "recent_std": 5.0,
        "opp_allowed": 20.0,     # == league_avg -> matchup 0
        "league_avg": 20.0,
        "opp_pace": 100.0,       # == league_pace -> pace 0
        "league_pace": 100.0,
        "is_b2b": 0,
        "games_played": 70,
        "spread": 0.0,
        "line": 15.5,
    }
    f.update(overrides)
    return f


def make_model(prop_type: str = "points", use_platt: bool = True, use_shrink: bool = True):
    return GaussianBaseline(
        GaussianConfig(
            prop_type=prop_type,
            use_platt=use_platt,
            use_edge_shrinkage=use_shrink,
        )
    )


def model_input(features: dict, player: str = "P1", game: str = "G1") -> ModelInput:
    return ModelInput(player_id=player, game_id=game, features=features)


# =================== GaussianBaseline (1-14) ===================


def test_sigmoid_boundaries():
    assert _sigmoid(0.0) == pytest.approx(0.5)
    assert _sigmoid(100) == pytest.approx(1.0, abs=1e-10)
    assert _sigmoid(-100) == pytest.approx(0.0, abs=1e-10)


def test_logit_boundaries():
    assert _logit(0.5) == pytest.approx(0.0)
    # clipped at eps: huge negative but finite
    assert _logit(0.0) < -10
    assert _logit(1.0) > 10


def test_weight_sum_equals_one_exactly():
    from warmachine.models.gaussian_baseline import (
        WEIGHT_HOME_AWAY, WEIGHT_RECENT_5, WEIGHT_RECENT_10, WEIGHT_SEASON,
    )
    total = WEIGHT_SEASON + WEIGHT_RECENT_10 + WEIGHT_RECENT_5 + WEIGHT_HOME_AWAY
    assert total == pytest.approx(1.0, abs=1e-12)


def test_projection_matches_known_example():
    m = make_model()
    f = base_features(season_avg=25, recent_10_avg=22, recent_5_avg=18, home_away_avg=20)
    assert m._compute_base_projection(f) == pytest.approx(20.9)


def test_b2b_penalty_applied():
    m = make_model()
    adj_no = m._compute_adjustments(base_features(is_b2b=0))
    adj_yes = m._compute_adjustments(base_features(is_b2b=1))
    assert adj_no["b2b"] == 0.0
    assert adj_yes["b2b"] == pytest.approx(-0.08)


def test_blowout_spread_14_gives_minus_9pct():
    m = make_model()
    adj = m._compute_adjustments(base_features(spread=14.0))
    # -0.07 + (14-10)*-0.005 = -0.09
    assert adj["blowout"] == pytest.approx(-0.09)


def test_blowout_max_cap_minus_15pct():
    m = make_model()
    adj = m._compute_adjustments(base_features(spread=50.0))
    assert adj["blowout"] == pytest.approx(-0.15)


def test_matchup_clipped_to_20pct():
    m = make_model()
    adj = m._compute_adjustments(base_features(opp_allowed=200.0, league_avg=10.0))
    assert adj["matchup"] == pytest.approx(0.20)


def test_std_floor_applied_per_prop():
    m_pts = make_model("points")
    m_reb = make_model("rebounds")
    m_ast = make_model("assists")
    f = base_features(recent_std=0.5, is_b2b=0, games_played=70)
    assert m_pts._compute_std(f) == 4.0
    assert m_reb._compute_std(f) == 2.0
    assert m_ast._compute_std(f) == 1.5


def test_std_b2b_multiplier_1_15():
    m = make_model()
    f = base_features(recent_std=10.0, is_b2b=1, games_played=70)
    assert m._compute_std(f) == pytest.approx(10.0 * 1.15)


def test_std_gp_under_20_multiplier_1_20():
    m = make_model()
    f = base_features(recent_std=10.0, is_b2b=0, games_played=15)
    assert m._compute_std(f) == pytest.approx(10.0 * 1.20)


def test_platt_v2_coefficients_applied():
    m = make_model(use_platt=True, use_shrink=False)
    out = m.predict(model_input(base_features()))
    expected_cal = _sigmoid(PLATT_A * _logit(out.raw_probability) + PLATT_B)
    assert out.calibrated_probability == pytest.approx(expected_cal, abs=1e-9)


def test_edge_shrinkage_at_threshold():
    m = make_model(use_shrink=True)
    # Craft features that produce a clear over-model
    # Use extreme line so raw is very high
    f = base_features(line=1.0)  # line well below projection -> P(X>line) near 1
    out = m.predict(model_input(f), market_prob_yes=0.30)
    # calibrated is high, market is 0.30, edge > 0.20 -> shrink
    # After shrink: final = cal - 0.05*(cal - 0.30) = 0.95*cal + 0.015
    m_no_shrink = make_model(use_shrink=False)
    out_raw = m_no_shrink.predict(model_input(f), market_prob_yes=0.30)
    expected = out_raw.calibrated_probability - 0.05 * (
        out_raw.calibrated_probability - 0.30
    )
    assert out.calibrated_probability == pytest.approx(expected, abs=1e-9)


def test_edge_shrinkage_skipped_below_threshold():
    m_shrink = make_model(use_shrink=True)
    m_no = make_model(use_shrink=False)
    f = base_features()
    out_no = m_no.predict(model_input(f), market_prob_yes=0.50)
    # Use market close to calibrated so edge < 0.20
    close_market = out_no.calibrated_probability + 0.05
    out_shrink = m_shrink.predict(model_input(f), market_prob_yes=close_market)
    assert out_shrink.calibrated_probability == out_no.calibrated_probability


# =================== V1DataBridge (15-18) ===================


def _mock_db(fetchone_results: list):
    db = mock.MagicMock()
    cur = mock.MagicMock()
    cur.fetchone.side_effect = list(fetchone_results)
    db.cursor.return_value = cur
    return db, cur


def test_bridge_returns_none_for_missing_player():
    db, cur = _mock_db([None])
    bridge = V1DataBridge(db)
    result = bridge.get_player_features(
        player_id="ABSENT", prop_type="points", is_home=True,
        opp_team_id="X", is_b2b=False, spread=0, line=10,
    )
    assert result is None


def test_bridge_maps_points_columns_correctly():
    # player row: season=25, r10=22, r5=18, home=26, away=24, std=5, gp=70
    # opp row: allow=21, pace=101; avg_row: 20, 100
    db, cur = _mock_db([
        (25.0, 22.0, 18.0, 26.0, 24.0, 5.0, 70),
        (21.0, 101.0),
        (20.0, 100.0),
    ])
    bridge = V1DataBridge(db)
    f = bridge.get_player_features(
        player_id="P1", prop_type="points", is_home=True,
        opp_team_id="OPP", is_b2b=True, spread=5.0, line=15.5,
    )
    assert f["season_avg"] == 25.0
    assert f["recent_10_avg"] == 22.0
    assert f["recent_5_avg"] == 18.0
    assert f["home_away_avg"] == 26.0   # is_home=True
    assert f["recent_std"] == 5.0
    assert f["games_played"] == 70
    assert f["is_b2b"] == 1
    assert f["spread"] == 5.0
    assert f["line"] == 15.5
    # Verify SQL contained points column
    sqls = [call.args[0] for call in cur.execute.call_args_list if call.args]
    assert any("points_pg" in s for s in sqls)


def test_bridge_fallback_to_season_if_recent_null():
    # r10 and r5 are None -> fall back to season_avg
    db, cur = _mock_db([
        (20.0, None, None, None, None, 4.0, 50),
        (20.0, 100.0),
        (20.0, 100.0),
    ])
    bridge = V1DataBridge(db)
    f = bridge.get_player_features(
        player_id="P1", prop_type="points", is_home=True,
        opp_team_id="OPP", is_b2b=False, spread=0, line=10,
    )
    assert f["recent_10_avg"] == 20.0
    assert f["recent_5_avg"] == 20.0
    assert f["home_away_avg"] == 20.0


def test_opp_stats_default_on_missing():
    # Player exists; opp row None triggers defaults
    db, cur = _mock_db([
        (20.0, 22.0, 18.0, 21.0, 19.0, 5.0, 70),
        None,                  # opp_row
        (20.0, 100.0),         # avg_row (unused since opp missing)
    ])
    bridge = V1DataBridge(db)
    f = bridge.get_player_features(
        player_id="P1", prop_type="points", is_home=True,
        opp_team_id="ABSENT", is_b2b=False, spread=0, line=10,
    )
    assert f["opp_allowed"] == V1DataBridge.DEFAULT_OPP_ALLOWED
    assert f["league_avg"] == V1DataBridge.DEFAULT_LEAGUE_AVG
    assert f["opp_pace"] == V1DataBridge.DEFAULT_OPP_PACE
    assert f["league_pace"] == V1DataBridge.DEFAULT_LEAGUE_PACE


# =================== LifecycleManager (19-21) ===================


def test_should_stop_false_initially():
    lc = LifecycleManager()
    assert lc.should_stop() is False


def test_request_stop_sets_event():
    lc = LifecycleManager()
    lc.request_stop()
    assert lc.should_stop() is True


def test_wait_or_stop_returns_early_on_signal():
    lc = LifecycleManager()

    def stopper():
        time.sleep(0.05)
        lc.request_stop()

    threading.Thread(target=stopper, daemon=True).start()
    t0 = time.time()
    result = lc.wait_or_stop(5.0)
    elapsed = time.time() - t0
    assert result is True
    assert elapsed < 1.0  # should return within ~0.1s, not 5s


# =================== WarMachineRunner (22-24) ===================


def _make_runner(lifecycle=None):
    lc = lifecycle or LifecycleManager()
    state = mock.MagicMock()
    state.is_stale = False
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
    runner.POLL_INTERVAL_SECONDS = 0.01
    return runner, lc


def test_runner_exception_isolation_continues_next_cycle():
    runner, lc = _make_runner()
    calls = [0]

    def cycle_impl():
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("boom")
        lc.request_stop()  # stop after 2nd success

    runner._run_one_cycle = cycle_impl

    t = threading.Thread(target=runner.run, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "runner did not exit"
    assert calls[0] >= 2, "runner did not continue after exception"


def test_runner_respects_max_markets_per_cycle():
    runner, lc = _make_runner()
    runner.MAX_MARKETS_PER_CYCLE = 50

    # 100 markets, each evaluate-mock records the call
    markets = [{"ticker": f"KXNBAPTS-M{i}"} for i in range(100)]
    runner._scan_nba_prop_markets = lambda: markets

    eval_calls = [0]

    def eval_impl(market):
        eval_calls[0] += 1
        return None

    runner._evaluate_market = eval_impl

    # Stop after one cycle by requesting stop before second wait
    def stop_after_one(*a, **k):
        lc.request_stop()
        return True

    runner.lifecycle.wait_or_stop = stop_after_one
    runner.run()

    assert eval_calls[0] == 50


def test_runner_stops_on_lifecycle_signal():
    runner, lc = _make_runner()
    lc.request_stop()  # stop BEFORE run
    runner.run()
    assert runner.cycle_count == 0
