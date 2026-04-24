"""
Module: gate3_liquidity.py
Purpose: Gate 3 - Liquidity filter (spread/depth/slippage/stale quote).
         Research: Kalshi avg spread 3-8c, EventEdge slippage standards.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from warmachine.risk.types import GateResult, OrderIntent


class MarketDataProvider(Protocol):
    def get_market_snapshot(self, ticker: str) -> dict: ...


class LiquidityGate:
    MAX_SPREAD_CENTS = 10
    MIN_DEPTH_MULTIPLIER = 5
    MAX_SLIPPAGE_PCT = 0.03
    STALE_QUOTE_MINUTES = 10

    def __init__(self, market_data: MarketDataProvider):
        self.md = market_data

    def check(self, order: OrderIntent) -> GateResult:
        try:
            snap = self.md.get_market_snapshot(order.ticker)
        except Exception as e:
            return GateResult(
                gate_name="liquidity",
                passed=False,
                reason=f"market snapshot failed: {e}",
                severity="WARN",
            )

        yes_bid = snap.get("yes_bid", 0)
        yes_ask = snap.get("yes_ask", 0)
        no_bid = snap.get("no_bid", 0)
        no_ask = snap.get("no_ask", 0)
        last_trade_time = snap.get("last_trade_time")

        if order.side.value == "YES":
            bid, ask = yes_bid, yes_ask
            depth = snap.get("yes_ask_depth", 0)
        else:
            bid, ask = no_bid, no_ask
            depth = snap.get("no_ask_depth", 0)

        spread = ask - bid if (bid and ask) else 100
        if spread > self.MAX_SPREAD_CENTS:
            return GateResult(
                gate_name="liquidity",
                passed=False,
                reason=f"spread {spread}c > {self.MAX_SPREAD_CENTS}c",
                severity="WARN",
            )

        required_depth = order.quantity * self.MIN_DEPTH_MULTIPLIER
        if depth < required_depth:
            return GateResult(
                gate_name="liquidity",
                passed=False,
                reason=f"depth {depth} < required {required_depth} (5x qty)",
                severity="WARN",
            )

        if last_trade_time:
            try:
                last_dt = datetime.fromisoformat(
                    last_trade_time.replace("Z", "+00:00")
                )
                age_min = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
                if age_min > self.STALE_QUOTE_MINUTES:
                    return GateResult(
                        gate_name="liquidity",
                        passed=False,
                        reason=(
                            f"stale quote {age_min:.0f}min > "
                            f"{self.STALE_QUOTE_MINUTES}min"
                        ),
                        severity="WARN",
                    )
            except Exception:
                pass

        mid = (bid + ask) / 2 if (bid and ask) else 50
        expected_slippage = spread / mid if mid > 0 else 1.0
        if expected_slippage > self.MAX_SLIPPAGE_PCT:
            return GateResult(
                gate_name="liquidity",
                passed=False,
                reason=(
                    f"expected slippage {expected_slippage:.1%} > "
                    f"{self.MAX_SLIPPAGE_PCT:.1%}"
                ),
                severity="WARN",
            )

        return GateResult(gate_name="liquidity", passed=True)
