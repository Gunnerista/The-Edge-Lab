"""
Module: engine.py
Purpose: 5-Gate Risk Engine orchestrator.
         First-fail-returns. Order: Kill -> Bankruptcy -> Exposure -> Liquidity -> EdgeDecay.
"""

from __future__ import annotations

from warmachine.risk.gate1_kill_switch import KillSwitchGate
from warmachine.risk.gate2_exposure import ExposureGate
from warmachine.risk.gate3_liquidity import LiquidityGate
from warmachine.risk.gate4_edge_decay import EdgeDecayGate
from warmachine.risk.gate5_bankruptcy import BankruptcyGate
from warmachine.risk.types import GateResult, OrderIntent, RiskDecision
from warmachine.utils.logger import get_logger

log = get_logger(__name__)


class RiskEngine:
    """Runs the 5 gates cheapest-first, short-circuits on first failure."""

    def __init__(
        self,
        kill: KillSwitchGate,
        bankruptcy: BankruptcyGate,
        exposure: ExposureGate,
        liquidity: LiquidityGate,
        edge_decay: EdgeDecayGate,
    ):
        self.gates = [kill, bankruptcy, exposure, liquidity, edge_decay]

    def evaluate(self, order: OrderIntent) -> RiskDecision:
        results: list[GateResult] = []
        for gate in self.gates:
            result = gate.check(order)
            results.append(result)
            if not result.passed:
                log.info(f"Order rejected at {result.gate_name}: {result.reason}")
                return RiskDecision(
                    allowed=False,
                    gate_results=results,
                    first_failed_gate=result.gate_name,
                )
        return RiskDecision(allowed=True, gate_results=results)
