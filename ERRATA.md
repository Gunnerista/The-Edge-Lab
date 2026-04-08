# ERRATA

A running record of factual corrections to The Edge Lab's published chapters. Chapters themselves are never silently edited — when something turns out to be wrong, an errata box goes on top of the original, and the full reasoning lives here.

This file exists because the chapters are written in real time, with whatever I knew at the time, and some of what I knew at the time was wrong. Pretending otherwise would defeat the point of writing in public.

---

## 2026-04-09 — Errata batch following Chapter 4 audit

### E1 · Chapter 1 (2026-03-28) — `legacy` config metrics

**Claim in original:** Brier score improved from 0.21 to 0.1852 after the logistic → XGBoost migration. Treated as the system's headline performance metric.

**Correction:** These numbers were measured on the `legacy` config (no `config_version` tag) over a 999-prediction window from March 28 to March 31. They are accurate for that window and that config. They are not the system's current performance — both the model and the config have been rebuilt twice since then. They are preserved as a historical snapshot of where the system stood on Day 1.

**Where to find the current numbers:** Mission Log table in [`README.md`](./README.md).

---

### E2 · Chapter 2 (2026-04-02) — Inflated ROI from entry-price bug

**Claim in original:** 999 settled predictions, +6.1% overall ROI, assists +17.2% ROI. Used as the basis for "Phase 1 validated."

**Correction:** The forward-test bookkeeping logged YES-side entries at the bid price rather than the ask. Because YES bets in the relevant edge band were systematically being filled higher than the recorded entry, every realized P&L was inflated. When the bug was identified in Chapter 3 and the same window was re-evaluated under the corrected v3 config (`v3_prop_edge`, 156 settled predictions over April 3–4):

| Prop type | Original (buggy) ROI | Corrected ROI |
|---|---|---|
| Overall | +6.1% | −4.4% |
| Rebounds | (not isolated) | +17.6% |
| Assists | +17.2% | −27.8% |
| Points | (not isolated) | −13.9% |

The corrected numbers are the basis for the v4 and v5 configs and for the live deployment.

---

### E3 · Chapter 3 (2026-04-04) — v4 was a one-day config

**Claim in original:** "v4_edge_optimized deployed" framed as the new operating config going forward.

**Correction:** v4 ran for exactly one day. On April 5 the active config became `v5_kelly`, which:

- Reactivated assists at a tightened `min_edge=0.25` (v4 had disabled them)
- Kept the rebounds threshold at 20% and the global threshold at 15%
- Kept the YES premium at +5¢
- Introduced quarter-Kelly position sizing with a 5% bankroll cap and a min/max bet floor

Chapter 4 covers the v5 transition, the live deployment, and the bugs that surfaced in the first week of live capital.

---

### E4 · README — Live status

**Claim in original README (pre-2026-04-09):** "Live transition ($300) — Pending v3 validation."

**Correction:** The live transition happened on April 5, 2026, not at $300 of bankroll, and not under v3. Actual live deployment: bankroll $66.19, config `v5_kelly`, rebounds-only on real money, points and assists on paper. The README has been rewritten to reflect this.

---

## Policy

- Chapters are append-only. The original prose is never edited. Errata boxes are added on top.
- Every correction is dated and lives both at the top of the affected chapter and as an entry in this file.
- If a correction itself turns out to be wrong, that gets its own dated entry. No silent overwrites.
