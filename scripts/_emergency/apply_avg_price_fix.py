"""avg_entry_price patch (Patch 5+6) - use total_traded_dollars / count - 2026-04-15

User-intent script only COMPUTED avg_entry_cents but didn't WIRE it into the positions
dict or the 'added from Kalshi' branch. This script does both.

Field choice: total_traded_dollars over market_exposure_dollars because total_traded
reflects actual entry cost (settled cost at fill time), while market_exposure is a
mark-to-market value that drifts. Both are equal for an unchanged open position, but
total_traded is the semantically correct "entry price" source.
"""
from pathlib import Path

path = Path("scripts/trade_engine.py")
code = path.read_text(encoding="utf-8")

report = []

# ---------------------------------------------------------------------------
# Patch 5: compute avg_entry_cents in the parsing loop
# Insertion point: right after pos_count line from Patch 1
# ---------------------------------------------------------------------------
old5 = '                pos_count = int(round(abs(pos_count_f)))\n                pos_side'
new5 = (
    '                pos_count = int(round(abs(pos_count_f)))\n'
    '                # avg entry ≈ total_traded_dollars / count (actual cost at fill)\n'
    '                try:\n'
    '                    total_traded = float(p.get("total_traded_dollars", "0") or 0)\n'
    '                except (TypeError, ValueError):\n'
    '                    total_traded = 0.0\n'
    '                avg_entry_price = (total_traded / pos_count) if pos_count > 0 else 0.0\n'
    '                pos_side'
)
if 'avg_entry_price = (total_traded' in code:
    report.append("[PATCH 5] SKIPPED: already applied")
elif old5 in code:
    code = code.replace(old5, new5)
    report.append("[PATCH 5] APPLIED: avg_entry_price computed from total_traded_dollars")
else:
    report.append("[PATCH 5] FAILED: insertion point not found")

# ---------------------------------------------------------------------------
# Patch 6: propagate avg_entry_price into api_map entry
# ---------------------------------------------------------------------------
old6 = (
    '                api_map[key] = {\n'
    '                    "ticker": ticker,\n'
    '                    "side": side,\n'
    '                    "count": count,\n'
)
new6 = (
    '                api_map[key] = {\n'
    '                    "ticker": ticker,\n'
    '                    "side": side,\n'
    '                    "count": count,\n'
    '                    "avg_entry_price": avg_entry_price,\n'
)
if '"avg_entry_price": avg_entry_price' in code:
    report.append("[PATCH 6] SKIPPED: already applied")
elif old6 in code:
    code = code.replace(old6, new6)
    report.append("[PATCH 6] APPLIED: avg_entry_price added to api_map entry")
else:
    report.append("[PATCH 6] FAILED: api_map insertion point not found")

# ---------------------------------------------------------------------------
# Patch 7: use api_pos['avg_entry_price'] instead of hardcoded 0 in
# "Position exists on Kalshi but not locally" branch
# ---------------------------------------------------------------------------
old7 = '"avg_entry_price": 0,  # unknown from API'
new7 = '"avg_entry_price": api_pos.get("avg_entry_price", 0),'
if 'api_pos.get("avg_entry_price"' in code:
    report.append("[PATCH 7] SKIPPED: already applied")
elif old7 in code:
    code = code.replace(old7, new7)
    report.append("[PATCH 7] APPLIED: new-from-Kalshi positions now carry avg_entry_price")
else:
    report.append("[PATCH 7] FAILED: target line not found")

path.write_text(code, encoding="utf-8")
print("\n".join(report))
print(f"\n[DONE] {path}")
