"""
Module: order_router.py
Purpose: OrderRouter - RiskEngine + BrokerAdapter pipeline.
         Flow: OrderIntent -> RiskEngine.evaluate -> BrokerAdapter.place_order.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from warmachine.risk.engine import RiskEngine
from warmachine.risk.types import OrderIntent, RiskDecision
from warmachine.trading.broker_adapter import BrokerAdapter, FillResult
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


class RoutingResult(BaseModel):
    intent: OrderIntent
    risk_decision: RiskDecision
    fill: Optional[FillResult] = None
    routed: bool
    reason: Optional[str] = None


class OrderRouter:
    def __init__(self, risk_engine: RiskEngine, broker: BrokerAdapter):
        self.risk = risk_engine
        self.broker = broker

    def route(self, intent: OrderIntent) -> RoutingResult:
        decision = self.risk.evaluate(intent)
        if not decision.allowed:
            log.info(f"Order blocked by risk: {decision.rejection_reason}")
            return RoutingResult(
                intent=intent,
                risk_decision=decision,
                routed=False,
                reason=f"risk: {decision.rejection_reason}",
            )

        fill = self.broker.place_order(intent)
        if fill.status == "rejected":
            return RoutingResult(
                intent=intent,
                risk_decision=decision,
                fill=fill,
                routed=False,
                reason=f"broker rejected: {fill.reason}",
            )

        log.info(
            f"[{self.broker.mode.upper()}] routed {intent.ticker} "
            f"{intent.side.value} {fill.filled_quantity}@{fill.fill_price_cents}c"
        )
        return RoutingResult(
            intent=intent,
            risk_decision=decision,
            fill=fill,
            routed=True,
        )
