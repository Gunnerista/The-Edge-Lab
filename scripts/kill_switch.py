"""
War Machine — Kill Switch Draft
================================

SCOPE: This is a DRAFT for review. Do NOT import this from production yet.
       Target integration in Sprint 1 Day 1.

DESIGN DECISIONS (see docs/DIAGNOSIS_REPORT_20260414_PHASE2.md §B):
  - File-based trigger: data/kill_switch.json
  - Insertion point: trade_engine.py:474 (AFTER validate_trade, BEFORE dry_run branch)
  - Also hook into: trade_engine.py:exit_position() (EXIT PATH, per A.5 finding)
  - Fail-safe: JSON parse error / IO error → BLOCK trades (pessimistic default)
  - File caching: re-check file mtime every check — negligible overhead at <1 trade/sec

USAGE (after integration):
  # To enable kill switch:
  $ echo '{"enabled": true, "reason": "daily loss limit hit", "set_by": "ikjun"}' > data/kill_switch.json

  # To disable:
  $ rm data/kill_switch.json
  # OR
  $ echo '{"enabled": false}' > data/kill_switch.json
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Resolve relative to repo root. scripts/kill_switch.py -> repo root is parent.parent.
DEFAULT_KILL_SWITCH_PATH = Path(__file__).resolve().parent.parent / "data" / "kill_switch.json"


@dataclass
class KillSwitchStatus:
    active: bool
    reason: str = ""
    set_by: str = ""
    set_at: str = ""  # ISO timestamp when file was written

    def block_message(self, path: str, action: str) -> str:
        parts = [f"[KILL_SWITCH] {action} BLOCKED"]
        if self.reason:
            parts.append(f"reason={self.reason!r}")
        if self.set_by:
            parts.append(f"set_by={self.set_by!r}")
        parts.append(f"file={path}")
        return " ".join(parts)


class KillSwitch:
    """
    File-based emergency shutoff. Checks a JSON file on every invocation.

    Fail-safe: any error reading the file → treated as ACTIVE (block).
    This prevents "bad JSON" → "kill switch silently fails" from allowing
    trades when the operator thought they were blocked.

    Thread-safe: internal lock guards the read.
    """

    _lock = threading.Lock()

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_KILL_SWITCH_PATH

    def check(self) -> KillSwitchStatus:
        """
        Returns KillSwitchStatus. `status.active == True` means all trading
        must be blocked.
        """
        with self._lock:
            if not self.path.exists():
                return KillSwitchStatus(active=False)

            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError as e:
                logger.error(f"[KillSwitch] Failed to read {self.path}: {e} -> FAIL-SAFE: blocking")
                return KillSwitchStatus(active=True, reason=f"file read error: {e}")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error(f"[KillSwitch] Malformed JSON in {self.path}: {e} -> FAIL-SAFE: blocking")
                return KillSwitchStatus(active=True, reason=f"malformed kill_switch.json: {e}")

            if not isinstance(data, dict):
                logger.error(f"[KillSwitch] {self.path} not an object -> FAIL-SAFE: blocking")
                return KillSwitchStatus(active=True, reason="kill_switch.json not a JSON object")

            enabled = bool(data.get("enabled", False))
            if not enabled:
                return KillSwitchStatus(active=False)

            return KillSwitchStatus(
                active=True,
                reason=str(data.get("reason", "unspecified")),
                set_by=str(data.get("set_by", "")),
                set_at=str(data.get("set_at", "")),
            )

    def must_be_clear(self, action_label: str) -> None:
        """
        Raise KillSwitchActive if the switch is engaged. Use at ingress of
        every real-money code path.
        """
        status = self.check()
        if status.active:
            msg = status.block_message(str(self.path), action_label)
            logger.warning(msg)
            raise KillSwitchActive(msg)


class KillSwitchActive(Exception):
    """Raised when an attempted trade is blocked by the kill switch."""


# ============================================================================
# INTEGRATION PATCHES (for Sprint 1 Day 1 — DO NOT APPLY YET)
# ============================================================================
#
# Patch 1 — trade_engine.py:
# ------------------------------------------------------------------
# Insert at top of file after existing imports:
#     from kill_switch import KillSwitch, KillSwitchActive
#
# In TradeEngine.__init__():
#     self.kill_switch = KillSwitch()
#
# Modify TradeEngine.execute_order(order, dry_run=True) at line 474
# (after validate_trade, before dry_run branch):
#
#     # ── Emergency kill switch ───────────────────────────────────
#     try:
#         self.kill_switch.must_be_clear(action_label=f"execute_order({order.ticker} {order.side})")
#     except KillSwitchActive as e:
#         return {"status": "blocked", "reason": str(e)}
#
# Modify TradeEngine.exit_position() at entry (line ~560):
#
#     try:
#         self.kill_switch.must_be_clear(action_label=f"exit_position({ticker} {side})")
#     except KillSwitchActive as e:
#         return {"status": "blocked", "reason": str(e)}
#
#
# Patch 2 — auto_trader.py (optional defensive layer):
# ------------------------------------------------------------------
# In AutoTrader.run_cycle() at the very top:
#
#     if self.kill_switch.check().active:
#         logger.warning("[AutoTrader] Kill switch engaged, skipping cycle")
#         return
#
# ============================================================================


# ============================================================================
# SELF-TEST (not run in prod — unit-test harness only)
# ============================================================================

def _self_test():
    """Sanity test: create/remove a test file and verify behavior."""
    import tempfile
    tmpdir = Path(tempfile.mkdtemp())
    test_path = tmpdir / "kill_switch.json"

    ks = KillSwitch(test_path)

    # Case 1: no file → not active
    assert ks.check().active is False, "missing file should NOT be active"

    # Case 2: enabled=false → not active
    test_path.write_text('{"enabled": false}')
    assert ks.check().active is False

    # Case 3: enabled=true → active
    test_path.write_text('{"enabled": true, "reason": "test", "set_by": "claude"}')
    s = ks.check()
    assert s.active is True
    assert s.reason == "test"
    assert s.set_by == "claude"

    # Case 4: malformed JSON → FAIL-SAFE active
    test_path.write_text('{not valid json')
    s = ks.check()
    assert s.active is True
    assert "malformed" in s.reason

    # Case 5: JSON but not an object → FAIL-SAFE active
    test_path.write_text('[1, 2, 3]')
    s = ks.check()
    assert s.active is True

    # Case 6: must_be_clear raises
    test_path.write_text('{"enabled": true, "reason": "halt"}')
    try:
        ks.must_be_clear("test_action")
        raise AssertionError("should have raised")
    except KillSwitchActive as e:
        assert "BLOCKED" in str(e)

    # Cleanup
    test_path.unlink()
    tmpdir.rmdir()

    print("[KillSwitch] all self-tests passed.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _self_test()
