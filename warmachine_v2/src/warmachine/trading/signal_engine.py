"""
Module: signal_engine.py
Purpose: Prediction + market snapshot -> OrderIntent (or None).
         Broker-agnostic. Sizing delegated to KellySizer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from warmachine.edge.kelly_sizer import KellySizer
from warmachine.risk.types import OrderIntent, OrderSide
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


class Prediction(BaseModel):
    model_config = ConfigDict(frozen=True, protected_namespaces=())

    ticker: str
    prop_type: str
    player_id: Optional[str] = None
    game_id: str

    model_prob_yes: float = Field(..., ge=0.0, le=1.0)
    model_version: str

    market_yes_ask_cents: int = Field(..., ge=1, le=99)
    market_no_ask_cents: int = Field(..., ge=1, le=99)

    predicted_at: datetime
    dnp_adjusted_prob_yes: Optional[float] = None


class SignalEngineConfig(BaseModel):
    min_edge_pp: float = 15.0
    yes_edge_premium_pp: float = 5.0
    max_edge_cap_pp: float = 60.0


class SignalEngine:
    def __init__(self, config: SignalEngineConfig, kelly_sizer: KellySizer):
        self.config = config
        self.sizer = kelly_sizer

    def generate(
        self, pred: Prediction, bankroll_dollars: float
    ) -> Optional[OrderIntent]:
        p_yes = (
            pred.dnp_adjusted_prob_yes
            if pred.dnp_adjusted_prob_yes is not None
            else pred.model_prob_yes
        )

        yes_market_prob = pred.market_yes_ask_cents / 100.0
        no_market_prob = pred.market_no_ask_cents / 100.0

        yes_edge_pp = (p_yes - yes_market_prob) * 100 - self.config.yes_edge_premium_pp
        no_edge_pp = ((1 - p_yes) - no_market_prob) * 100

        if (
            yes_edge_pp >= no_edge_pp
            and yes_edge_pp >= self.config.min_edge_pp
        ):
            side = OrderSide.YES
            edge_pp = yes_edge_pp
            price_cents = pred.market_yes_ask_cents
            prob = p_yes
        elif no_edge_pp >= self.config.min_edge_pp:
            side = OrderSide.NO
            edge_pp = no_edge_pp
            price_cents = pred.market_no_ask_cents
            prob = 1 - p_yes
        else:
            return None

        if edge_pp > self.config.max_edge_cap_pp:
            log.warning(
                f"Edge {edge_pp:.1f}pp > cap {self.config.max_edge_cap_pp}pp - skipping"
            )
            return None

        quantity = self.sizer.calc_quantity(
            prob=prob,
            price_cents=price_cents,
            bankroll_dollars=bankroll_dollars,
        )
        if quantity <= 0:
            return None

        return OrderIntent(
            ticker=pred.ticker,
            side=side,
            quantity=quantity,
            limit_price_cents=price_cents,
            signal_generated_at=pred.predicted_at,
            signal_model_prob=prob,
            signal_edge_pp=edge_pp,
            game_id=pred.game_id,
            player_id=pred.player_id,
            prop_type=pred.prop_type,
        )
