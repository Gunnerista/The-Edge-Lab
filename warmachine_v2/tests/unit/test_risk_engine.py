"""Tests for warmachine.risk 5-gate engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from warmachine.core.state import Position
from warmachine.risk.engine import RiskEngine
from warmachine.risk.gate1_kill_switch import KillSwitchGate
from warmachine.risk.gate2_exposure import ExposureGate, ExposureLimits
from warmachine.risk.gate3_liquidity import LiquidityGate
from warmachine.risk.gate4_edge_decay import EdgeDecayGate
from warmachine.risk.gate5_bankruptcy import BankruptcyGate
from warmachine.risk.types import GateResult, OrderIntent, OrderSide


# ---------------- helpers ----------------


def make_order(
    ticker: str = "KXNBAPTS-26APR24MINUTA-MINTOWNS32-20",
    side: str = "YES",
    quantity: int = 10,
    limit_price_cents: int = 30,
    signal_age_seconds: float = 5.0,
    signal_model_prob: float = 0.50,
    signal_edge_pp: float = 20.0,
    game_id: str = "26APR24MINUTA",
    player_id: str = "MINTOWNS32",
    prop_type: str = "points",
) -> OrderIntent:
    return OrderIntent(
        ticker=ticker,
        side=OrderSide(side),
        quantity=quantity,
        limit_price_cents=limit_price_cents,
        signal_generated_at=datetime.now(timezone.utc)
        - timedelta(seconds=signal_age_seconds),
        signal_model_prob=signal_model_prob,
        signal_edge_pp=signal_edge_pp,
        game_id=game_id,
        player_id=player_id,
        prop_type=prop_type,
    )


def mock_state(bankroll: float = 300.0, positions=None, paper_only_until=None):
    m = mock.MagicMock()
    m.bankroll = bankroll
    m.positions = tuple(positions or [])
    m._session.data.paper_only_until = paper_only_until
    return m


def mk_pos(ticker: str, exposure_dollars: float, qty: int = 10) -> Position:
    return Position(
        ticker=ticker,
        quantity=qty,
        market_exposure=exposure_dollars,
        avg_price=0.30,
    )


# =================== Gate 1: Kill Switch (1-3) ===================


def test_no_stop_file_passes(tmp_path):
    gate = KillSwitchGate(tmp_path / "STOP")
    assert gate.check(make_order()).passed is True


def test_stop_file_exists_rejects(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("manual halt by Ikjun", encoding="utf-8")
    gate = KillSwitchGate(stop)
    result = gate.check(make_order())
    assert result.passed is False
    assert "manual halt by Ikjun" in result.reason
    assert result.severity == "CRITICAL"


def test_stop_file_empty_still_rejects(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("", encoding="utf-8")
    gate = KillSwitchGate(stop)
    result = gate.check(make_order())
    assert result.passed is False
    assert "no reason given" in result.reason


# =================== Gate 2: Exposure (4-11) ===================


def test_per_ticker_cap_3pct():
    # 3% of $300 = $9. Order qty=5 * 30c = $1.50. Already 8 on same ticker -> 9.50 > 9 -> reject.
    state = mock_state(
        bankroll=300.0,
        positions=[mk_pos("KXNBAPTS-26APR24MINUTA-MINTOWNS32-20", 8.00)],
    )
    gate = ExposureGate(state)
    order = make_order(quantity=5, limit_price_cents=30)  # $1.50
    result = gate.check(order)
    assert result.passed is False
    assert "per-ticker" in result.reason


def test_per_game_cap_8pct():
    # 8% of $300 = $24. Per-ticker cap $9.
    # Same game existing $22 + order $3 = $25 > $24 (order $3 is under per-ticker).
    state = mock_state(
        bankroll=300.0,
        positions=[
            mk_pos("KXNBAPTS-26APR24MINUTA-MINEDWARDS5-25", 12.00),
            mk_pos("KXNBAREB-26APR24MINUTA-UTAJOHN10-8", 10.00),
        ],
    )
    gate = ExposureGate(state)
    order = make_order(quantity=15, limit_price_cents=20)  # $3
    result = gate.check(order)
    assert result.passed is False
    assert "per-game" in result.reason


def test_per_player_cap_5pct_schroder_scenario():
    """Schroder 3-stack: CLEDSCHRODER8-2 (AST) + CLEDSCHRODER8-4 (AST) + new PTS order."""
    state = mock_state(
        bankroll=300.0,
        positions=[
            mk_pos("KXNBAAST-26APR20TORCLE-CLEDSCHRODER8-2", 8.00),
            mk_pos("KXNBAAST-26APR20TORCLE-CLEDSCHRODER8-4", 5.00),
        ],
    )
    # 5% of $300 = $15. Existing 13 + new $3 = $16 > $15 -> reject.
    gate = ExposureGate(state)
    order = make_order(
        ticker="KXNBAPTS-26APR20TORCLE-CLEDSCHRODER8-10",
        quantity=10,
        limit_price_cents=30,  # $3.00
        game_id="26APR20TORCLE",
        player_id="CLEDSCHRODER8",
        prop_type="points",
    )
    result = gate.check(order)
    assert result.passed is False
    assert "per-player" in result.reason


def test_per_prop_type_cap_15pct():
    # 15% of $300 = $45. Existing PTS $40 + order $6 = $46 > $45.
    # Order $6 stays below per-ticker cap ($9).
    state = mock_state(
        bankroll=300.0,
        positions=[
            mk_pos("KXNBAPTS-26APR24AAABBB-PLAYER1-10", 20.00),
            mk_pos("KXNBAPTS-26APR24CCCDDD-PLAYER2-15", 20.00),
        ],
    )
    gate = ExposureGate(state)
    order = make_order(
        ticker="KXNBAPTS-26APR24EEEFFF-PLAYER3-25",
        quantity=10,
        limit_price_cents=60,  # $6
        game_id="26APR24EEEFFF",
        player_id="PLAYER3",
        prop_type="points",
    )
    result = gate.check(order)
    assert result.passed is False
    assert "per-prop-type" in result.reason


def test_total_open_cap_30pct():
    # 30% of $300 = $90. Build up exposure under each sub-cap but exceed total.
    # Mix prop types to dodge per-prop-type cap.
    state = mock_state(
        bankroll=300.0,
        positions=[
            mk_pos(f"KXNBAPTS-26APR24G{i}-P{i}-10", 8.0) for i in range(5)
        ]
        + [mk_pos(f"KXNBAREB-26APR24H{i}-Q{i}-8", 8.0) for i in range(5)]
        + [mk_pos(f"KXNBAAST-26APR24I{i}-R{i}-4", 2.0) for i in range(5)],
    )
    # existing total = 5*8 + 5*8 + 5*2 = 90
    gate = ExposureGate(state)
    order = make_order(
        ticker="KXNBA3PT-26APR24ZZZ-PX-3",
        quantity=10,
        limit_price_cents=10,  # $1.00
        game_id="26APR24ZZZ",
        player_id="PX",
        prop_type="threes",
    )
    result = gate.check(order)
    assert result.passed is False
    assert "total-open" in result.reason


def test_non_positive_bankroll_rejects():
    state = mock_state(bankroll=0.0)
    gate = ExposureGate(state)
    result = gate.check(make_order())
    assert result.passed is False
    assert "non-positive" in result.reason


def test_first_order_no_positions_passes():
    state = mock_state(bankroll=300.0, positions=[])
    gate = ExposureGate(state)
    result = gate.check(make_order(quantity=5, limit_price_cents=30))  # $1.50 < $9
    assert result.passed is True


def test_exact_cap_boundary_passes_then_fails():
    # 3% of $300 = $9.00 per-ticker. $9.00 passes (not > 3%). $9.01 fails.
    state = mock_state(bankroll=300.0, positions=[])
    gate = ExposureGate(state)
    # $9.00 exactly = 30 * 30c
    pass_order = make_order(quantity=30, limit_price_cents=30)
    assert gate.check(pass_order).passed is True
    # $9.30 = 31 * 30c
    fail_order = make_order(quantity=31, limit_price_cents=30)
    assert gate.check(fail_order).passed is False


# =================== Gate 3: Liquidity (12-16) ===================


def _md(**snap) -> mock.MagicMock:
    m = mock.MagicMock()
    m.get_market_snapshot.return_value = snap
    return m


def test_spread_within_10c_passes():
    # Tight 1c spread at mid=48.5 -> slippage ~2% (< 3%).
    md = _md(
        yes_bid=48, yes_ask=49, yes_ask_depth=200,
        last_trade_time=datetime.now(timezone.utc).isoformat(),
    )
    gate = LiquidityGate(md)
    assert gate.check(make_order(quantity=10, side="YES")).passed is True


def test_spread_over_10c_rejects():
    md = _md(yes_bid=20, yes_ask=40, yes_ask_depth=200)
    gate = LiquidityGate(md)
    result = gate.check(make_order(side="YES"))
    assert result.passed is False
    assert "spread 20c" in result.reason


def test_depth_insufficient_rejects():
    # qty=10 -> need 50 depth
    md = _md(yes_bid=45, yes_ask=50, yes_ask_depth=20)
    gate = LiquidityGate(md)
    result = gate.check(make_order(quantity=10, side="YES"))
    assert result.passed is False
    assert "depth" in result.reason


def test_stale_quote_over_10min_rejects():
    stale = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    md = _md(
        yes_bid=45, yes_ask=50, yes_ask_depth=200, last_trade_time=stale,
    )
    gate = LiquidityGate(md)
    result = gate.check(make_order(quantity=10, side="YES"))
    assert result.passed is False
    assert "stale quote" in result.reason


def test_slippage_over_3pct_rejects():
    # Low-price market: bid=5, ask=7, mid=6, spread=2, slippage = 2/6 ~33%.
    md = _md(
        yes_bid=5, yes_ask=7, yes_ask_depth=200,
        last_trade_time=datetime.now(timezone.utc).isoformat(),
    )
    gate = LiquidityGate(md)
    result = gate.check(make_order(quantity=10, side="YES", limit_price_cents=7))
    assert result.passed is False
    assert "slippage" in result.reason


# =================== Gate 4: Edge Decay (17-20) ===================


def _pp(current_price: int) -> mock.MagicMock:
    m = mock.MagicMock()
    m.get_current_price_cents.return_value = current_price
    return m


def test_fresh_signal_passes_no_revalidation():
    pp = _pp(current_price=99)  # would be bad if checked
    gate = EdgeDecayGate(pp)
    result = gate.check(make_order(signal_age_seconds=5.0))
    assert result.passed is True
    assert pp.get_current_price_cents.call_count == 0


def test_stale_signal_price_drift_over_3c_rejects():
    # age > 30s, current=40, limit=30 -> drift=10 > 3
    pp = _pp(current_price=40)
    gate = EdgeDecayGate(pp)
    result = gate.check(
        make_order(signal_age_seconds=60.0, limit_price_cents=30)
    )
    assert result.passed is False
    assert "price drift" in result.reason


def test_stale_signal_revalidated_edge_below_10pp_rejects():
    # age > 30s, current=31 (drift=1 OK), model_prob=0.35 -> edge=(0.35-0.31)*100=4pp < 10
    pp = _pp(current_price=31)
    gate = EdgeDecayGate(pp)
    result = gate.check(
        make_order(
            signal_age_seconds=60.0,
            limit_price_cents=30,
            signal_model_prob=0.35,
        )
    )
    assert result.passed is False
    assert "revalidated edge" in result.reason


def test_stale_signal_still_valid_passes():
    # age > 30s, current=31, model_prob=0.50 -> edge=19pp, drift=1 -> OK
    pp = _pp(current_price=31)
    gate = EdgeDecayGate(pp)
    result = gate.check(
        make_order(
            signal_age_seconds=60.0,
            limit_price_cents=30,
            signal_model_prob=0.50,
        )
    )
    assert result.passed is True


# =================== Gate 5: Bankruptcy (21-23) ===================


def test_bankroll_above_floor_passes():
    gate = BankruptcyGate(mock_state(bankroll=150.0))
    assert gate.check(make_order()).passed is True


def test_bankroll_below_floor_rejects():
    gate = BankruptcyGate(mock_state(bankroll=50.0))
    result = gate.check(make_order())
    assert result.passed is False
    assert "floor" in result.reason
    assert result.severity == "CRITICAL"


def test_paper_mode_active_rejects():
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    gate = BankruptcyGate(
        mock_state(bankroll=300.0, paper_only_until=future)
    )
    result = gate.check(make_order())
    assert result.passed is False
    assert "paper mode" in result.reason


# =================== RiskEngine integration (24-28) ===================


def _mock_gate(name: str, passed: bool, reason: str = None) -> mock.MagicMock:
    g = mock.MagicMock()
    g.check.return_value = GateResult(
        gate_name=name, passed=passed, reason=reason
    )
    return g


def test_all_pass_allows():
    kill = _mock_gate("kill_switch", True)
    bank = _mock_gate("bankruptcy", True)
    expo = _mock_gate("exposure", True)
    liq = _mock_gate("liquidity", True)
    ed = _mock_gate("edge_decay", True)
    engine = RiskEngine(kill, bank, expo, liq, ed)
    decision = engine.evaluate(make_order())
    assert decision.allowed is True
    assert len(decision.gate_results) == 5
    assert decision.first_failed_gate is None


def test_first_gate_fail_returns_early():
    kill = _mock_gate("kill_switch", False, "STOP file")
    bank = _mock_gate("bankruptcy", True)
    expo = _mock_gate("exposure", True)
    liq = _mock_gate("liquidity", True)
    ed = _mock_gate("edge_decay", True)
    engine = RiskEngine(kill, bank, expo, liq, ed)
    decision = engine.evaluate(make_order())
    assert decision.allowed is False
    assert decision.first_failed_gate == "kill_switch"
    assert bank.check.call_count == 0
    assert expo.check.call_count == 0
    assert liq.check.call_count == 0
    assert ed.check.call_count == 0


def test_gate_order_kill_before_exposure():
    # Both would fail, but kill is evaluated first.
    kill = _mock_gate("kill_switch", False, "STOP")
    bank = _mock_gate("bankruptcy", True)
    expo = _mock_gate("exposure", False, "over cap")
    liq = _mock_gate("liquidity", True)
    ed = _mock_gate("edge_decay", True)
    engine = RiskEngine(kill, bank, expo, liq, ed)
    decision = engine.evaluate(make_order())
    assert decision.first_failed_gate == "kill_switch"
    assert expo.check.call_count == 0


def test_rejection_reason_includes_gate_name():
    kill = _mock_gate("kill_switch", True)
    bank = _mock_gate("bankruptcy", False, "bankroll $50 < floor $100")
    expo = _mock_gate("exposure", True)
    liq = _mock_gate("liquidity", True)
    ed = _mock_gate("edge_decay", True)
    engine = RiskEngine(kill, bank, expo, liq, ed)
    decision = engine.evaluate(make_order())
    assert decision.rejection_reason.startswith("bankruptcy:")
    assert "floor" in decision.rejection_reason


def test_decision_contains_all_evaluated_results():
    # Exposure (3rd gate) fails: 3 gate_results, not 5.
    kill = _mock_gate("kill_switch", True)
    bank = _mock_gate("bankruptcy", True)
    expo = _mock_gate("exposure", False, "over cap")
    liq = _mock_gate("liquidity", True)
    ed = _mock_gate("edge_decay", True)
    engine = RiskEngine(kill, bank, expo, liq, ed)
    decision = engine.evaluate(make_order())
    names = [r.gate_name for r in decision.gate_results]
    assert names == ["kill_switch", "bankruptcy", "exposure"]
    assert liq.check.call_count == 0
    assert ed.check.call_count == 0
