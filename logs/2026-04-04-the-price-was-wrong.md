> **📝 ERRATA — added 2026-04-09**  
> `v4_edge_optimized` ran for exactly one day. On April 5 it was replaced by `v5_kelly`, which reactivated assists at a tightened `min_edge=0.25` and introduced quarter-Kelly position sizing with a 5% bankroll cap. v5 is the config that went live with real capital. See [`ERRATA.md`](../ERRATA.md) and Chapter 4 for the full transition. The original text below is unedited.

---

# The Price Was Wrong

**Date**: 2026-04-04 (covering 04/02 → 04/04)
**Tags**: `bug-fix` `entry-price` `no-betting` `v3-diagnosis` `v4-config` `edge-threshold`

---

## TL;DR

Found and fixed an entry price bug that had been systematically overstating edge on every single prediction. Activated NO betting — which turned out to be the only profitable side. Ran v3 config to 156 settled predictions and discovered that 79% of bets were losing money. Assists, once the star performer, collapsed to -27.8% ROI. Rebounds emerged as the sole profitable prop type. Rebuilt the entire config from scratch. The model was never wrong — we were just entering at the wrong price.

---

## Where We Left Off

Chapter 2 ended on a high: 999 predictions settled, Brier Score 0.1681, ROI at +6.1%. Assists were the dominant performer at +17.2% ROI. The v3 config had just been deployed with prop-specific MIN_EDGE thresholds, and we were targeting 100 settled predictions to validate it.

Those numbers were a lie. Not the Brier Score — that was real. But the ROI was built on a pricing error we hadn't caught yet.

---

## Bug #1: The Entry Price Was Wrong

### The Discovery

Every prediction in the system records an `entry_price` — the price at which we'd theoretically enter the position. This is the foundation of all P&L calculations. Get this wrong, and every number downstream is fiction.

**The bug**: For YES bets, the system was using the **bid** price instead of the **ask** price. In any market, you buy at the ask (higher) and sell at the bid (lower). The spread is the market maker's cut. By recording the bid as our entry, we were pretending we could buy at the sell price — systematically overstating our edge on every single YES bet.

**The fix**:
```
# Before (wrong)
entry_price = market_data["yes_bid"]

# After (correct)
entry_price = market_data["yes_ask"]     # YES bets: buy at the ask
entry_price = 1 - market_data["yes_bid"] # NO bets: inverse of yes bid
```

This single change meant every historical ROI figure was inflated. The +6.1% overall ROI from Chapter 2? Built on imaginary savings from a spread we'd actually have to pay.

---

## Bug #2: NO Betting Was Dead

### The 2x Spread Rule Problem

The system had a safety filter: don't enter if the spread is more than 2x the edge. Reasonable in theory — it prevents entering illiquid markets where the spread eats your edge.

**The problem**: This rule was applied to both YES and NO bets. But NO bets on Kalshi don't face the same spread dynamics as YES bets. The 2x spread rule was killing every NO signal before it could execute.

**The result**: Out of 999+ predictions, the system had placed exactly **0 NO bets**. Zero. The entire P&L was built on YES-side entries only.

**The fix**: Applied the 2x spread rule to YES bets only. NO bets are now free to execute based on edge alone.

```python
# 2x spread rule → YES only
if sig.side == "yes" and spread > 2 * edge:
    skip()
# NO side: no spread filter
```

---

## The 48-Hour Blackout (04/01–04/02)

Two days of data went completely missing. Not because of the database or scheduler — two code bugs stacked on top of each other.

**Bug A — The Restart Loop**: The runner script checks if the current time is within the betting window. If the window has closed, it exits. The batch script that launches it automatically restarts it. So from 5:54 AM, the runner would start, see "window closed," exit, restart, see "window closed," exit — 879 times in a row over 17 hours.

**Bug B — Zero-Padding**: When the runner finally entered the game window at 5 PM, it searched for tickers using an unpadded date format: `KXNBAPTS-26APR1%` instead of `KXNBAPTS-26APR01%`. The `1%` wildcard matched nothing. Every catch-up attempt returned zero markets. The system logged "No active NBA props, skipping" and went silent.

Both bugs were patched. But ~600–700 predictions worth of data from those two days are gone permanently.

---

## v3 Results: 156 Predictions of Truth

With the entry price fix and NO betting activation, the v3 config ran clean from 04/03. Here's what 156 settled predictions revealed:

### Overall Performance

| Metric | Value |
|--------|-------|
| Brier Score | 0.1732 |
| P&L | -$3.95 |
| ROI | -4.4% |
| Win Rate | 88/156 = 56.4% |

**The model calibration is strong** (Brier 0.1732 vs. coin flip at 0.25). But calibration alone doesn't pay the bills. After Kalshi's fee structure, the margin evaporates.

### Prop Type Breakdown

| Prop | Bets | Win Rate | ROI |
|------|------|----------|-----|
| Rebounds | 63 | 68.3% | **+17.6%** |
| Points | 61 | 50.8% | -13.9% |
| Assists | 32 | 43.8% | -27.8% |

**Rebounds is the only profitable prop type.** At 68.3% win rate and +17.6% ROI, it's carrying the entire system.

**Assists collapsed.** The +17.2% ROI from Chapter 2 was an illusion — inflated by the entry price bug and a single blowout game. With corrected pricing over 32 clean bets, assists are deep negative. The MIN_EDGE of 0.15 was too low; the system was entering positions where no real edge existed.

**Points are a coin flip.** 50.8% win rate means the model has almost no predictive power on points markets. Kalshi's pricing on points appears efficient.

### Side Analysis

| Side | Bets | Win Rate | ROI |
|------|------|----------|-----|
| YES | 63 | 41.3% | -20.3% |
| NO | 93 | 66.7% | +4.4% |

**NO betting works. YES betting doesn't** — at least not at current thresholds. The YES side has a structural disadvantage: the ask price premium on Kalshi is larger for YES positions, and the market appears to be more efficiently priced on the YES side.

### Edge Threshold Analysis

| Edge Range | Bets | ROI |
|------------|------|-----|
| 0–5% | 50 | -11.8% |
| 5–10% | 46 | -11.3% |
| 10–15% | 27 | -5.7% |
| 15–20% | 15 | +20.4% |
| 30%+ | 9 | +76.2% |

**Every bet below 15% edge lost money.** This is the clearest signal in the entire dataset. The fee structure demands high-conviction entries. Low-edge bets are structurally -EV on Kalshi regardless of model accuracy.

### Concentration Risk

| Matchup | Bets | P&L Share |
|---------|------|-----------|
| MIN @ PHI | 79 | 107.9% of total loss |
| ORL @ DAL | 77 | +7.9% |

79 out of 156 bets came from a single matchup (MIN @ PHI), which accounted for more than 100% of the total loss. The system has no diversification controls — if one game generates signals, it floods that game.

---

## The v4 Rebuild

The v3 data told us exactly where the money was leaking. The fix isn't a better model — it's a better filter.

### Config Changes

| Parameter | v3 | v4 | Rationale |
|-----------|----|----|-----------|
| Assists | MIN_EDGE 0.15 | **Disabled** | 43.8% win rate, -27.8% ROI |
| Rebounds | MIN_EDGE 0.25 | **MIN_EDGE 0.20** | Only profitable prop, loosened to capture more |
| Points | MIN_EDGE 0.25 | MIN_EDGE 0.25 | Maintained, borderline |
| YES Premium | None | **+0.05** | YES side structurally disadvantaged |
| Global Floor | None | **0.15** | Everything below 15% edge lost money |

### Effective Entry Thresholds (v4)

| | YES | NO |
|--|-----|-----|
| **Points** | ≥ 30% edge | ≥ 25% edge |
| **Rebounds** | ≥ 25% edge | ≥ 20% edge |
| **Assists** | Blocked | Blocked |

### What Didn't Change
- Platt scaling parameters (A=0.8976, B=-0.4242)
- runner.py scheduling logic
- NO side spread rule exemption
- equity_curve.json tracking

### Files Modified
- `signal_engine.py` — Added `PROP_MIN_EDGE`, `YES_EDGE_PREMIUM`, `GLOBAL_MIN_EDGE` class variables
- `forward_test.py` — Removed assists collection, added `CONFIG_VERSION = "v4_edge_optimized"`
- `auto_trader.py` — New `should_place_bet()` function replacing legacy filter

---

## What We Learned

1. **Entry price is everything.** A single field using bid instead of ask made every ROI metric in the system unreliable. The model was fine — the measurement was broken. Always verify the most basic assumptions first.

2. **Edge threshold selection matters more than model accuracy.** Brier Score 0.1732 is genuinely good calibration. But good calibration at low-edge entries is a guaranteed way to lose money after fees. The fee structure, not the model, determines the minimum viable edge.

3. **Prop types are not created equal.** Rebounds had structural inefficiency. Points markets were efficiently priced. Assists were a trap — high signal volume, negative edge. Treating all props the same was leaving money on the table and throwing money away simultaneously.

4. **YES and NO sides have different economics.** The ask premium, spread dynamics, and market efficiency differ by side. A flat MIN_EDGE applied to both sides ignores this asymmetry.

5. **Silent failures are more dangerous than crashes.** The system ran for two days processing zero signals without raising a single alert. No error, no warning — just quiet emptiness in the prediction log. If it crashes, you notice. If it silently produces nothing, you might not check for days.

6. **One game is not a strategy.** 79 out of 156 bets in a single matchup. 107.9% of P&L concentrated in one game. Whether that game wins or loses, the system is exposed to variance it can't survive long-term.

---

## What's Next

v4 is live. The target: 100 settled predictions under the new config with ROI above +10%. If it hits, we deploy real capital. If it doesn't, we diagnose again.

The model works. The calibration is real. Now we need the config to let it make money.

---

*Previous: [Phase 1 Validation — The First 1,000 Predictions](2026-04-02-phase1-validation.md)*
*Config: `v4_edge_optimized` | Deployed: 2026-04-04*
