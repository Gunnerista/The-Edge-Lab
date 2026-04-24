"""
Module: gate4_edge_decay.py
Purpose: Gate 4 - Edge decay re-validation.
         If signal->order gap > 30s: re-fetch current price, reject if drift > 3c
         or revalidated edge < 10pp.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from warmachine.risk.types import GateResult, OrderIntent


class PriceProvider(Protocol):
    def get_current_price_cents(self, ticker: str, side: str) -> int: ...


class EdgeDecayGate:
    MAX_SIGNAL_AGE_SECONDS = 30
    MAX_PRICE_DRIFT_CENTS = 3
    MIN_REVALIDATED_EDGE_PP = 10.0

    def __init__(self, price_provider: PriceProvider):
        self.pp = price_provider

    def check(self, order: OrderIntent) -> GateResult:
        now = datetime.now(timezone.utc)
        sig_at = order.signal_generated_at
        if sig_at.tzinfo is None:
            sig_at = sig_at.replace(tzinfo=timezone.utc)
        age_seconds = (now - sig_at).total_seconds()

        if age_seconds <= self.MAX_SIGNAL_AGE_SECONDS:
            return GateResult(gate_name="edge_decay", passed=True)

        try:
            current_price = self.pp.get_current_price_cents(
                order.ticker, order.side.value
            )
        except Exception as e:
            return GateResult(
                gate_name="edge_decay",
                passed=False,
                reason=f"re-validation price fetch failed: {e}",
                severity="WARN",
            )

        price_drift = abs(current_price - order.limit_price_cents)
        if price_drift > self.MAX_PRICE_DRIFT_CENTS:
            return GateResult(
                gate_name="edge_decay",
                passed=False,
                reason=(
                    f"price drift {price_drift}c > "
                    f"{self.MAX_PRICE_DRIFT_CENTS}c (age {age_seconds:.0f}s)"
                ),
                severity="WARN",
            )

        current_implied_prob = current_price / 100.0
        revalidated_edge_pp = (order.signal_model_prob - current_implied_prob) * 100
        if revalidated_edge_pp < self.MIN_REVALIDATED_EDGE_PP:
            return GateResult(
                gate_name="edge_decay",
                passed=False,
                reason=(
                    f"revalidated edge {revalidated_edge_pp:.1f}pp < "
                    f"{self.MIN_REVALIDATED_EDGE_PP}pp"
                ),
                severity="WARN",
            )

        return GateResult(gate_name="edge_decay", passed=True)
