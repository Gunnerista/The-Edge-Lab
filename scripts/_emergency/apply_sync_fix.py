"""Sync function field-name fix patch - 2026-04-15

Kalshi API schema uses string fields with _fp / _dollars suffixes,
not the legacy plain-integer fields. trade_engine.sync_with_kalshi()
was reading non-existent fields, skipping every position, and
wiping local positions.json every cycle.
"""
import re
from pathlib import Path

path = Path("scripts/trade_engine.py")
code = path.read_text(encoding="utf-8")

report = []

# ---------------------------------------------------------------------------
# Patch 1: "position" (int) -> "position_fp" (string, float)
# ---------------------------------------------------------------------------
old1 = '                pos_count = p.get("position", 0)'
new1 = (
    '                pos_count_str = p.get("position_fp", p.get("position", "0"))\n'
    '                try:\n'
    '                    pos_count_f = float(pos_count_str)\n'
    '                except (TypeError, ValueError):\n'
    '                    pos_count_f = 0.0\n'
    '                pos_count = int(round(abs(pos_count_f)))\n'
    '                pos_side = "yes" if pos_count_f > 0 else ("no" if pos_count_f < 0 else None)'
)
if old1 in code:
    code = code.replace(old1, new1)
    report.append("[PATCH 1] APPLIED: position -> position_fp (float parsing)")
else:
    report.append("[PATCH 1] SKIPPED: original line not found")

# ---------------------------------------------------------------------------
# Patch 2: "market_exposure" -> "market_exposure_dollars" (string -> float)
# ---------------------------------------------------------------------------
old2 = '"market_exposure": p.get("market_exposure", 0),'
new2 = '"market_exposure": float(p.get("market_exposure_dollars", p.get("market_exposure", "0")) or 0),'
if old2 in code:
    code = code.replace(old2, new2)
    report.append("[PATCH 2] APPLIED: market_exposure -> market_exposure_dollars")
else:
    report.append("[PATCH 2] SKIPPED: original line not found")

# ---------------------------------------------------------------------------
# Patch 3: side derivation must use signed pos_count_f, not abs pos_count
# (Without this, every synced position is tagged side="yes" — wrong for NO-side holdings.)
# ---------------------------------------------------------------------------
old3 = 'side = "yes" if pos_count > 0 else "no"'
new3 = 'side = pos_side if pos_side is not None else ("yes" if pos_count_f > 0 else "no")'
if old3 in code:
    code = code.replace(old3, new3)
    report.append("[PATCH 3] APPLIED: side now uses signed pos_count_f (prevents NO-side mis-tag)")
else:
    report.append("[PATCH 3] SKIPPED: original line not found")

# ---------------------------------------------------------------------------
# Patch 4: count should come from pos_count (already abs), not abs(pos_count)
# This is a minor cleanup; harmless but clearer.
# ---------------------------------------------------------------------------
old4 = 'count = abs(pos_count)'
new4 = 'count = pos_count  # already abs after patch 1'
if old4 in code:
    code = code.replace(old4, new4)
    report.append("[PATCH 4] APPLIED: count cleanup")
else:
    report.append("[PATCH 4] SKIPPED")

# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
path.write_text(code, encoding="utf-8")
print("\n".join(report))
print(f"\n[DONE] {path} updated.")
