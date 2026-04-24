"""
Module: types.py
Purpose: Shared types for the 5-gate risk engine.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class OrderSide(str, Enum):
    YES = "YES"
    NO = "NO"


class OrderIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    ticker: str
    side: OrderSide
    quantity: int = Field(..., gt=0)
    limit_price_cents: int = Field(..., ge=1, le=99)

    signal_generated_at: datetime
    signal_model_prob: float = Field(..., ge=0, le=1)
    signal_edge_pp: float

    game_id: str
    player_id: Optional[str] = None
    prop_type: str

    @property
    def notional_dollars(self) -> float:
        return (self.quantity * self.limit_price_cents) / 100.0


class GateResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    gate_name: str
    passed: bool
    reason: Optional[str] = None
    severity: str = "INFO"


class RiskDecision(BaseModel):
    allowed: bool
    gate_results: list[GateResult]
    first_failed_gate: Optional[str] = None

    @property
    def rejection_reason(self) -> Optional[str]:
        if self.allowed:
            return None
        for r in self.gate_results:
            if not r.passed:
                return f"{r.gate_name}: {r.reason}"
        return None
