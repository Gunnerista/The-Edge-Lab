"""
Module: gate5_bankruptcy.py
Purpose: Gate 5 - Bankruptcy floor + paper-only lockout.
         bankroll < $100 -> reject; paper_only_until in future -> reject.
"""

from __future__ import annotations

from datetime import datetime, timezone

from warmachine.core.state import WarMachineState
from warmachine.risk.types import GateResult, OrderIntent


class BankruptcyGate:
    FLOOR_DOLLARS = 100.0

    def __init__(self, state: WarMachineState):
        self.state = state

    def check(self, order: OrderIntent) -> GateResult:
        if self.state.bankroll < self.FLOOR_DOLLARS:
            return GateResult(
                gate_name="bankruptcy",
                passed=False,
                reason=(
                    f"bankroll ${self.state.bankroll:.2f} < "
                    f"floor ${self.FLOOR_DOLLARS}"
                ),
                severity="CRITICAL",
            )

        paper_until = self.state._session.data.paper_only_until
        if paper_until:
            try:
                paper_dt = datetime.fromisoformat(paper_until.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < paper_dt:
                    return GateResult(
                        gate_name="bankruptcy",
                        passed=False,
                        reason=f"paper mode until {paper_until}",
                        severity="CRITICAL",
                    )
            except Exception:
                pass

        return GateResult(gate_name="bankruptcy", passed=True)
