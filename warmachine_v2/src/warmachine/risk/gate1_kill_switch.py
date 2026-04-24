"""
Module: gate1_kill_switch.py
Purpose: Gate 1 - Kill Switch (file-drop pattern, CME standard).
         Check: STOP file exists -> reject all new orders. ~0.1ms cost.
"""

from __future__ import annotations

from pathlib import Path

from warmachine.risk.types import GateResult, OrderIntent


class KillSwitchGate:
    def __init__(self, stop_file: Path):
        self.stop_file = Path(stop_file)

    def check(self, order: OrderIntent) -> GateResult:
        if self.stop_file.exists():
            reason = (
                self.stop_file.read_text(encoding="utf-8").strip()
                or "no reason given"
            )
            return GateResult(
                gate_name="kill_switch",
                passed=False,
                reason=f"STOP file active: {reason}",
                severity="CRITICAL",
            )
        return GateResult(gate_name="kill_switch", passed=True)
