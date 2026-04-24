"""
Module: gate2_exposure.py
Purpose: Gate 2 - Exposure caps (per-ticker, per-game, per-player, per-prop-type, total).
         Research: Wizard of Odds (variance & bankroll), OddsJam unit sizing.
         Designed to block Schroder-style 3-stack scenarios.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from warmachine.core.state import WarMachineState
from warmachine.risk.types import GateResult, OrderIntent


class ExposureLimits(BaseModel):
    per_ticker_pct: float = 0.03
    per_game_pct: float = 0.08
    per_player_pct: float = 0.05
    per_prop_type_daily_pct: float = 0.15
    total_open_pct: float = 0.30


_PROP_MARKER = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "threes": "3PT",
}


class ExposureGate:
    def __init__(
        self, state: WarMachineState, limits: Optional[ExposureLimits] = None
    ):
        self.state = state
        self.limits = limits or ExposureLimits()

    def check(self, order: OrderIntent) -> GateResult:
        bankroll = self.state.bankroll
        if bankroll <= 0:
            return GateResult(
                gate_name="exposure",
                passed=False,
                reason="bankroll non-positive",
                severity="CRITICAL",
            )

        new_cost = order.notional_dollars
        positions = self.state.positions

        same_ticker_exposure = sum(
            p.market_exposure for p in positions if p.ticker == order.ticker
        )
        new_ticker_exposure = same_ticker_exposure + new_cost
        if new_ticker_exposure / bankroll > self.limits.per_ticker_pct:
            return GateResult(
                gate_name="exposure",
                passed=False,
                reason=(
                    f"per-ticker {new_ticker_exposure/bankroll:.1%} > "
                    f"{self.limits.per_ticker_pct:.1%}"
                ),
                severity="WARN",
            )

        same_game = self._filter_by_game(positions, order.game_id)
        game_exposure = sum(p.market_exposure for p in same_game) + new_cost
        if game_exposure / bankroll > self.limits.per_game_pct:
            return GateResult(
                gate_name="exposure",
                passed=False,
                reason=(
                    f"per-game {game_exposure/bankroll:.1%} > "
                    f"{self.limits.per_game_pct:.1%}"
                ),
                severity="WARN",
            )

        if order.player_id:
            same_player = self._filter_by_player(positions, order.player_id)
            player_exposure = sum(p.market_exposure for p in same_player) + new_cost
            if player_exposure / bankroll > self.limits.per_player_pct:
                return GateResult(
                    gate_name="exposure",
                    passed=False,
                    reason=(
                        f"per-player {player_exposure/bankroll:.1%} > "
                        f"{self.limits.per_player_pct:.1%}"
                    ),
                    severity="WARN",
                )

        same_type_open = self._filter_by_prop_type(positions, order.prop_type)
        type_exposure = sum(p.market_exposure for p in same_type_open) + new_cost
        if type_exposure / bankroll > self.limits.per_prop_type_daily_pct:
            return GateResult(
                gate_name="exposure",
                passed=False,
                reason=(
                    f"per-prop-type {type_exposure/bankroll:.1%} > "
                    f"{self.limits.per_prop_type_daily_pct:.1%}"
                ),
                severity="WARN",
            )

        total_open = sum(p.market_exposure for p in positions) + new_cost
        if total_open / bankroll > self.limits.total_open_pct:
            return GateResult(
                gate_name="exposure",
                passed=False,
                reason=(
                    f"total-open {total_open/bankroll:.1%} > "
                    f"{self.limits.total_open_pct:.1%}"
                ),
                severity="WARN",
            )

        return GateResult(gate_name="exposure", passed=True)

    @staticmethod
    def _filter_by_game(positions, game_id: str):
        return [p for p in positions if game_id in p.ticker]

    @staticmethod
    def _filter_by_player(positions, player_id: str):
        return [p for p in positions if player_id in p.ticker]

    @staticmethod
    def _filter_by_prop_type(positions, prop_type: str):
        marker = _PROP_MARKER.get(prop_type.lower())
        if not marker:
            return []
        return [p for p in positions if marker in p.ticker]
