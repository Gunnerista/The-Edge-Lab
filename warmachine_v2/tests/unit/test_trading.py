"""Tests for warmachine.trading + warmachine.edge.kelly_sizer."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pytest
from pydantic import ValidationError  # noqa: F401

from warmachine.edge.kelly_sizer import KellyConfig, KellySizer
from warmachine.risk.types import GateResult, OrderIntent, OrderSide, RiskDecision
from warmachine.trading.broker_adapter import (
    FillResult,
    KalshiAdapter,
    PaperAdapter,
    kalshi_fee_cents,
)
from warmachine.trading.order_router import OrderRouter
from warmachine.trading.signal_engine import (
    Prediction,
    SignalEngine,
    SignalEngineConfig,
)


# ---------------- helpers ----------------


def make_intent(
    ticker: str = "T1",
    side: str = "YES",
    quantity: int = 10,
    limit_price_cents: int = 30,
    model_prob: float = 0.50,
) -> OrderIntent:
    return OrderIntent(
        ticker=ticker,
        side=OrderSide(side),
        quantity=quantity,
        limit_price_cents=limit_price_cents,
        signal_generated_at=datetime.now(timezone.utc),
        signal_model_prob=model_prob,
        signal_edge_pp=20.0,
        game_id="G1",
        player_id="P1",
        prop_type="points",
    )


def paper(tmp_path, initial_cents=28061, price_fn=None):
    return PaperAdapter(
        state_path=tmp_path / "paper_state.json",
        initial_balance_cents=initial_cents,
        kalshi_price_source=price_fn or (lambda t, s: 30),
    )


# =================== kalshi_fee_cents (1-4) ===================


def test_fee_at_50c_1_contract_is_1_or_2c():
    f = kalshi_fee_cents(quantity=1, price_cents=50)
    assert 1 <= f <= 2


def test_fee_at_extreme_prices_minimal():
    assert kalshi_fee_cents(1, 1) == 1
    assert kalshi_fee_cents(1, 99) == 1


def test_fee_scales_with_quantity():
    f1 = kalshi_fee_cents(100, 50)
    f2 = kalshi_fee_cents(1000, 50)
    assert f2 >= f1 * 9  # ~10x qty should yield ~10x fee


def test_fee_minimum_1_cent():
    # Tiny order far from 50c would round to 0 without floor
    assert kalshi_fee_cents(1, 2) == 1


# =================== PaperAdapter (5-14) ===================


def test_initial_balance_correct(tmp_path):
    p = paper(tmp_path, initial_cents=28061)
    assert p.get_cash_cents() == 28061
    assert p.get_balance_cents() == 28061


def test_buy_reduces_cash_by_cost_plus_fee(tmp_path):
    p = paper(tmp_path, initial_cents=10000, price_fn=lambda t, s: 30)
    intent = make_intent(quantity=20, limit_price_cents=30)
    fill = p.place_order(intent)
    assert fill.status == "filled"
    expected_fee = kalshi_fee_cents(20, fill.fill_price_cents)
    expected_cost = 20 * fill.fill_price_cents + expected_fee
    assert fill.total_cost_cents == expected_cost
    assert p.get_cash_cents() == 10000 - expected_cost


def test_position_tracked_after_buy(tmp_path):
    p = paper(tmp_path, initial_cents=10000, price_fn=lambda t, s: 30)
    p.place_order(make_intent(quantity=20, limit_price_cents=30))
    positions = p.get_positions()
    assert len(positions) == 1
    assert positions[0]["ticker"] == "T1"
    assert positions[0]["quantity"] == 20


def test_avg_price_updated_correctly_on_multiple_buys(tmp_path):
    prices = [30, 40]
    idx = [0]

    def price_fn(t, s):
        return prices[idx[0]]

    p = paper(tmp_path, initial_cents=10000, price_fn=price_fn)
    p.place_order(make_intent(quantity=10, limit_price_cents=35))  # fills at 31
    idx[0] = 1
    p.place_order(make_intent(quantity=10, limit_price_cents=45))  # fills at 41
    positions = p.get_positions()
    # avg fill price = (10*31 + 10*41)/20 = 36
    assert positions[0]["quantity"] == 20
    assert positions[0]["avg_price_cents"] == 36


def test_market_price_above_limit_rejects(tmp_path):
    p = paper(tmp_path, initial_cents=10000, price_fn=lambda t, s: 50)
    fill = p.place_order(make_intent(quantity=10, limit_price_cents=30))
    assert fill.status == "rejected"
    assert "limit" in fill.reason


def test_insufficient_cash_rejects(tmp_path):
    p = paper(tmp_path, initial_cents=100, price_fn=lambda t, s: 30)
    # qty 100 * 30c = 3000c needed, have 100c
    fill = p.place_order(make_intent(quantity=100, limit_price_cents=30))
    assert fill.status == "rejected"
    assert "insufficient cash" in fill.reason


def test_settle_win_credits_payout(tmp_path):
    p = paper(tmp_path, initial_cents=10000, price_fn=lambda t, s: 30)
    p.place_order(make_intent(quantity=10, limit_price_cents=30))
    cash_after_buy = p.get_cash_cents()
    pnl = p.settle_position("T1", outcome_yes_wins=True)
    # Payout = 10 * 100c = 1000c; pnl = payout - total_cost
    assert p.get_cash_cents() == cash_after_buy + 10 * 100
    assert pnl > 0


def test_settle_loss_credits_zero(tmp_path):
    p = paper(tmp_path, initial_cents=10000, price_fn=lambda t, s: 30)
    p.place_order(make_intent(quantity=10, limit_price_cents=30))
    cash_after_buy = p.get_cash_cents()
    pnl = p.settle_position("T1", outcome_yes_wins=False)
    assert p.get_cash_cents() == cash_after_buy  # no payout
    assert pnl < 0


def test_mode_returns_paper(tmp_path):
    assert paper(tmp_path).mode == "paper"


def test_state_persists_across_instances(tmp_path):
    state_path = tmp_path / "paper_state.json"
    p1 = PaperAdapter(state_path, 10000, lambda t, s: 30)
    p1.place_order(make_intent(quantity=10, limit_price_cents=30))
    cash1 = p1.get_cash_cents()
    # Second instance, same path
    p2 = PaperAdapter(state_path, 99999, lambda t, s: 30)  # initial ignored
    assert p2.get_cash_cents() == cash1
    assert len(p2.get_positions()) == 1


# =================== KellySizer (15-20) ===================


def test_no_edge_returns_zero():
    s = KellySizer(KellyConfig())
    # prob equals price -> no edge
    assert s.calc_quantity(prob=0.30, price_cents=30, bankroll_dollars=300) == 0


def test_positive_edge_returns_positive_quantity():
    s = KellySizer(KellyConfig())
    q = s.calc_quantity(prob=0.50, price_cents=30, bankroll_dollars=300)
    assert q > 0


def test_fractional_multiplier_applied():
    high = KellySizer(KellyConfig(fractional_multiplier=1.0, single_trade_cap_pct=1.0))
    low = KellySizer(KellyConfig(fractional_multiplier=0.25, single_trade_cap_pct=1.0))
    q_hi = high.calc_quantity(prob=0.50, price_cents=30, bankroll_dollars=300)
    q_lo = low.calc_quantity(prob=0.50, price_cents=30, bankroll_dollars=300)
    assert q_hi > q_lo
    assert q_lo == pytest.approx(q_hi * 0.25, rel=0.05)


def test_single_trade_cap_enforced():
    capped = KellySizer(KellyConfig(fractional_multiplier=1.0, single_trade_cap_pct=0.03))
    uncapped = KellySizer(KellyConfig(fractional_multiplier=1.0, single_trade_cap_pct=1.0))
    q_cap = capped.calc_quantity(prob=0.90, price_cents=30, bankroll_dollars=1000)
    q_free = uncapped.calc_quantity(prob=0.90, price_cents=30, bankroll_dollars=1000)
    # q_cap spends at most 3% of bankroll = $30
    assert q_cap * 30 <= 30 * 100
    assert q_free > q_cap


def test_zero_bankroll_returns_zero():
    s = KellySizer(KellyConfig())
    assert s.calc_quantity(prob=0.50, price_cents=30, bankroll_dollars=0) == 0


def test_min_contracts_floor():
    s = KellySizer(KellyConfig(fractional_multiplier=0.01, min_contracts=1))
    # Tiny bankroll but edge exists -> computed contracts would round to 0
    q = s.calc_quantity(prob=0.50, price_cents=30, bankroll_dollars=1.0)
    assert q >= 1  # min floor


# =================== SignalEngine (21-26) ===================


def _engine(fractional=0.25, cap=0.03):
    return SignalEngine(
        config=SignalEngineConfig(),
        kelly_sizer=KellySizer(
            KellyConfig(fractional_multiplier=fractional, single_trade_cap_pct=cap)
        ),
    )


def _pred(**overrides) -> Prediction:
    defaults = dict(
        ticker="T1",
        prop_type="points",
        player_id="P1",
        game_id="G1",
        model_prob_yes=0.50,
        model_version="v1",
        market_yes_ask_cents=30,
        market_no_ask_cents=70,
        predicted_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return Prediction(**defaults)


def test_edge_below_min_returns_none():
    # yes_raw_edge = (0.30 - 0.20)*100 = 10pp, after -5 premium = 5pp < 15 min
    # no_edge = (0.70 - 0.80)*100 = -10pp
    pred = _pred(model_prob_yes=0.30, market_yes_ask_cents=20, market_no_ask_cents=80)
    assert _engine().generate(pred, bankroll_dollars=300) is None


def test_yes_edge_accounts_for_premium_5pp():
    # Raw 20pp -> after premium 15pp, passes
    pred_ok = _pred(model_prob_yes=0.50, market_yes_ask_cents=30, market_no_ask_cents=80)
    intent = _engine().generate(pred_ok, bankroll_dollars=300)
    assert intent is not None
    assert intent.side == OrderSide.YES

    # Raw 19pp -> after premium 14pp, rejected
    pred_fail = _pred(
        model_prob_yes=0.50, market_yes_ask_cents=31, market_no_ask_cents=80
    )
    assert _engine().generate(pred_fail, bankroll_dollars=300) is None


def test_picks_no_side_when_better():
    # p_yes=0.20 -> NO prob=0.80; yes_market=50, no_market=30
    # yes_edge = (0.20-0.50)*100 - 5 = -35pp
    # no_edge = (0.80-0.30)*100 = 50pp
    pred = _pred(model_prob_yes=0.20, market_yes_ask_cents=50, market_no_ask_cents=30)
    intent = _engine().generate(pred, bankroll_dollars=300)
    assert intent is not None
    assert intent.side == OrderSide.NO
    assert intent.limit_price_cents == 30
    assert intent.signal_model_prob == pytest.approx(0.80)


def test_max_edge_cap_filters_suspicious():
    # Extreme mispricing: p_yes=0.95, yes_market=0.05 -> raw 90pp -> after premium 85pp > 60 cap
    pred = _pred(
        model_prob_yes=0.95, market_yes_ask_cents=5, market_no_ask_cents=99
    )
    assert _engine().generate(pred, bankroll_dollars=300) is None


def test_dnp_adjustment_applied_when_provided():
    # Raw prob 0.60 would give huge edge. DNP-adjusted 0.40 barely makes edge.
    pred = _pred(
        model_prob_yes=0.60,
        dnp_adjusted_prob_yes=0.40,
        market_yes_ask_cents=20,
        market_no_ask_cents=80,
    )
    intent = _engine().generate(pred, bankroll_dollars=300)
    assert intent is not None
    # Stored prob should be 0.40, not 0.60
    assert intent.signal_model_prob == pytest.approx(0.40)


def test_kelly_zero_returns_none_intent():
    # Zero bankroll -> Kelly returns 0 -> SignalEngine returns None
    pred = _pred(model_prob_yes=0.50, market_yes_ask_cents=30, market_no_ask_cents=80)
    assert _engine().generate(pred, bankroll_dollars=0) is None


# =================== OrderRouter (27-30) ===================


def _risk_pass():
    eng = mock.MagicMock()
    eng.evaluate.return_value = RiskDecision(
        allowed=True,
        gate_results=[GateResult(gate_name="all", passed=True)],
    )
    return eng


def _risk_block(reason="blocked"):
    eng = mock.MagicMock()
    eng.evaluate.return_value = RiskDecision(
        allowed=False,
        gate_results=[
            GateResult(gate_name="kill_switch", passed=False, reason=reason)
        ],
        first_failed_gate="kill_switch",
    )
    return eng


def _broker_fill(status="filled"):
    b = mock.MagicMock()
    b.mode = "paper"
    b.place_order.return_value = FillResult(
        order_id="x",
        ticker="T1",
        side=OrderSide.YES,
        filled_quantity=10,
        fill_price_cents=30,
        fee_cents=2,
        total_cost_cents=302,
        filled_at=datetime.now(timezone.utc),
        status=status,
        reason="sim" if status == "rejected" else None,
    )
    return b


def test_risk_blocked_does_not_route():
    broker = _broker_fill()
    router = OrderRouter(risk_engine=_risk_block("STOP"), broker=broker)
    result = router.route(make_intent())
    assert result.routed is False
    assert "risk" in result.reason
    assert broker.place_order.call_count == 0


def test_risk_allowed_routes_to_broker():
    broker = _broker_fill()
    router = OrderRouter(risk_engine=_risk_pass(), broker=broker)
    result = router.route(make_intent())
    assert result.routed is True
    assert broker.place_order.call_count == 1


def test_broker_rejection_captured_in_result():
    broker = _broker_fill(status="rejected")
    router = OrderRouter(risk_engine=_risk_pass(), broker=broker)
    result = router.route(make_intent())
    assert result.routed is False
    assert result.fill is not None
    assert result.fill.status == "rejected"
    assert "broker rejected" in result.reason


def test_successful_route_returns_fill():
    broker = _broker_fill()
    router = OrderRouter(risk_engine=_risk_pass(), broker=broker)
    result = router.route(make_intent())
    assert result.routed is True
    assert result.fill.filled_quantity == 10
    assert result.fill.fill_price_cents == 30


# =================== KalshiAdapter live path (31-43) ===================


def _mock_kalshi_client(create_resp=None, fills_list=None, market_resp=None):
    client = mock.MagicMock()
    client.create_order.return_value = (
        create_resp
        if create_resp is not None
        else {"order": {"order_id": "kx_abc123"}}
    )
    client.get_fills.return_value = {"fills": list(fills_list or [])}
    if market_resp is not None:
        client.get_market.return_value = market_resp
        # block the fast path so code falls through to client.get_market
        del client.session
        client.base_url = "https://api.elections.kalshi.com"
    return client


def _fast_adapter(client):
    a = KalshiAdapter(client)
    a.POLL_INTERVAL_SECONDS = 0.0  # no real sleep in tests
    return a


def test_kalshi_place_order_calls_create_order_with_correct_kwargs_yes():
    client = _mock_kalshi_client(
        fills_list=[{"count_fp": 10, "yes_price": 30, "fee_cost": 12}]
    )
    adapter = _fast_adapter(client)
    intent = make_intent(side="YES", quantity=10, limit_price_cents=30)
    adapter.place_order(intent)
    kwargs = client.create_order.call_args.kwargs
    assert kwargs["side"] == "yes"
    assert kwargs["action"] == "buy"
    assert kwargs["type"] == "limit"
    assert kwargs["yes_price"] == 30
    assert "no_price" not in kwargs


def test_kalshi_place_order_calls_create_order_with_correct_kwargs_no():
    client = _mock_kalshi_client(
        fills_list=[{"count_fp": 10, "no_price": 25, "fee_cost": 10}]
    )
    adapter = _fast_adapter(client)
    intent = make_intent(side="NO", quantity=10, limit_price_cents=25)
    adapter.place_order(intent)
    kwargs = client.create_order.call_args.kwargs
    assert kwargs["side"] == "no"
    assert kwargs["no_price"] == 25
    assert "yes_price" not in kwargs


def test_kalshi_place_order_uses_count_not_quantity():
    client = _mock_kalshi_client(
        fills_list=[{"count_fp": 10, "yes_price": 30, "fee_cost": 12}]
    )
    adapter = _fast_adapter(client)
    adapter.place_order(make_intent(quantity=10))
    kwargs = client.create_order.call_args.kwargs
    assert kwargs["count"] == 10
    assert "quantity" not in kwargs


def test_kalshi_place_order_handles_missing_order_id():
    client = _mock_kalshi_client(create_resp={"order": {}})
    adapter = _fast_adapter(client)
    fill = adapter.place_order(make_intent())
    assert fill.status == "rejected"
    assert "no order_id" in fill.reason
    # Should not have polled fills when submission had no id
    assert client.get_fills.call_count == 0


def test_kalshi_place_order_hydrates_from_fills():
    client = _mock_kalshi_client(
        fills_list=[{"count_fp": 50, "yes_price": 30, "fee_cost": 74}]
    )
    adapter = _fast_adapter(client)
    fill = adapter.place_order(make_intent(quantity=50, limit_price_cents=30))
    assert fill.status == "filled"
    assert fill.filled_quantity == 50
    assert fill.fill_price_cents == 30
    assert fill.fee_cents == 74
    assert fill.total_cost_cents == 50 * 30 + 74


def test_kalshi_place_order_aggregates_partial_fills():
    client = _mock_kalshi_client(
        fills_list=[
            {"count_fp": 30, "yes_price": 30, "fee_cost": 44},
            {"count_fp": 20, "yes_price": 31, "fee_cost": 30},
        ]
    )
    adapter = _fast_adapter(client)
    fill = adapter.place_order(make_intent(quantity=50, limit_price_cents=35))
    # weighted avg: (30*30 + 20*31) / 50 = 1520 / 50 = 30 (integer division)
    assert fill.filled_quantity == 50
    assert fill.fill_price_cents == 30  # floor
    assert fill.fee_cents == 74
    assert fill.status == "filled"


def test_kalshi_place_order_no_fills_after_retries():
    client = _mock_kalshi_client(fills_list=[])
    adapter = _fast_adapter(client)
    fill = adapter.place_order(make_intent())
    assert fill.status == "rejected"
    assert "no fills after 3 polls" in fill.reason
    assert client.get_fills.call_count == 3


def test_kalshi_extract_price_cents_from_int():
    adapter = KalshiAdapter(mock.MagicMock())
    assert adapter._extract_price_cents({"yes_price": 30}, "YES") == 30
    assert adapter._extract_price_cents({"no_price": 72}, "NO") == 72


def test_kalshi_extract_price_cents_from_dollar_string():
    adapter = KalshiAdapter(mock.MagicMock())
    assert adapter._extract_price_cents({"yes_price_dollars": "0.30"}, "YES") == 30
    assert adapter._extract_price_cents({"no_price_dollars": "0.97"}, "NO") == 97


def test_kalshi_extract_price_cents_handles_missing():
    adapter = KalshiAdapter(mock.MagicMock())
    assert adapter._extract_price_cents({}, "YES") == 0


def test_get_current_price_cents_handles_dollars_string():
    client = _mock_kalshi_client(
        market_resp={"market": {"yes_ask_dollars": "0.97", "no_ask_dollars": "0.04"}}
    )
    adapter = KalshiAdapter(client)
    assert adapter.get_current_price_cents("T1", "YES") == 97
    assert adapter.get_current_price_cents("T1", "NO") == 4


def test_get_current_price_cents_fallback_to_int():
    client = _mock_kalshi_client(market_resp={"market": {"yes_ask": 65, "no_ask": 35}})
    adapter = KalshiAdapter(client)
    assert adapter.get_current_price_cents("T1", "YES") == 65
    assert adapter.get_current_price_cents("T1", "NO") == 35


def test_get_current_price_cents_defaults_50_on_missing():
    client = _mock_kalshi_client(market_resp={"market": {}})
    adapter = KalshiAdapter(client)
    assert adapter.get_current_price_cents("T1", "YES") == 50
    assert adapter.get_current_price_cents("T1", "NO") == 50
