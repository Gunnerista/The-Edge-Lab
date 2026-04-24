"""
Module: kelly_sizer.py
Purpose: Fractional-Kelly sizer (0.25x default).
         f* = (bp - q) / b, b = (100 - price) / price for Kalshi binary.
"""

from __future__ import annotations

from pydantic import BaseModel


class KellyConfig(BaseModel):
    fractional_multiplier: float = 0.25
    single_trade_cap_pct: float = 0.03
    min_contracts: int = 1


class KellySizer:
    def __init__(self, config: KellyConfig):
        self.config = config

    def calc_quantity(
        self, prob: float, price_cents: int, bankroll_dollars: float
    ) -> int:
        if bankroll_dollars <= 0 or price_cents <= 0 or price_cents >= 100:
            return 0
        if prob <= price_cents / 100.0:
            return 0

        p = prob
        q = 1.0 - prob
        b = (100 - price_cents) / price_cents
        f_star = (b * p - q) / b
        if f_star <= 0:
            return 0

        f_adjusted = min(
            f_star * self.config.fractional_multiplier,
            self.config.single_trade_cap_pct,
        )
        dollars = bankroll_dollars * f_adjusted
        if dollars <= 0:
            return 0

        contracts = int(dollars * 100 / price_cents)
        return max(self.config.min_contracts, contracts)
