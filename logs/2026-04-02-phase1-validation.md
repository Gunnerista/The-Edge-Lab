# Phase 1 Validation — The First 1,000 Predictions

**Date**: 2026-04-02 (covering 03/28 → 04/02)
**Tags**: `validation` `calibration` `infrastructure` `milestone`

---

## TL;DR

Ran 1,167 predictions. Settled 999. Found real signal in assists markets, discovered the model was bleeding money on low-edge bets, killed three critical bugs, migrated the entire database, and rebuilt the calibration system. The model works — but only when you point it at the right targets.

## Where We Left Off

Day 1 ended with a freshly deployed XGBoost model, a forward test on autopilot, and zero settled predictions. The plan was simple: collect 100+ predictions, validate, then decide on live deployment.

Reality had other plans.

---

## The Infrastructure Crisis (Days 1–2)

### SQLite Had to Die

The system runs three concurrent threads — Observer (market data collection), Scanner (signal detection), and Main (orchestration). SQLite's single-writer lock made this a ticking time bomb.

```
"database is locked" — SQLite, constantly
```

**Migrated the entire stack to PostgreSQL** (`warmachine` DB, `psycopg2` with `ThreadedConnectionPool`). Transferred ~18.5M rows:

| Table | Rows |
|-------|------|
| `markets` | ~3.96M |
| `price_snapshots` | ~14.5M |
| `nba_players` | 445 |
| `nba_teams` | 30 |
| `nba_games` | 23 |

No rollback. Migration is permanent. Multi-threaded writes work flawlessly now.

### The Kalshi Expiration Bug

This one was subtle and nearly killed the project.

**Problem**: `detect_nba_prop_signals()` was finding zero signals. Not low signals — *zero*. The pipeline appeared healthy, the model was running, but nothing came out the other end.

**Root cause**: Kalshi sets `expiration_time` on NBA player props to the **end of the season** (~April 14), not the game date. Our settlement-window filter was checking `expiration_time < now + 72 hours`, which meant every single NBA prop failed the filter.

**Fix**: Rewrote signal detection to parse the game date from the ticker symbol pattern (matching `forward_test.py`'s approach).

**Result**: Signals went from **0 → 203** in one cycle. Paper trades from **0 → 57**.

> *Lesson: Silent failures are the deadliest bugs. The system looked fine. It was doing absolutely nothing.*

---

## The Numbers — 999 Settled Predictions

After 4 days of continuous operation:

### Model Accuracy

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| **Overall Brier Score** | 0.1656 | < 0.20 | ✅ Pass |
| Points Brier | 0.1701 | < 0.20 | ✅ Pass |
| Rebounds Brier | 0.1836 | < 0.20 | ✅ Pass |
| Assists Brier | 0.1336 | < 0.20 | ✅ Pass |

The model's probability estimates are genuinely good. Assists are exceptionally sharp.

### But the Money Told a Different Story

| Segment | ROI (fee-adjusted) | Win Rate | n |
|---------|-------------------|----------|---|
| **Overall (edge>0)** | -0.2% | 48.4% | 308 |
| Edge 10–15% | -7.5% | 43.8% | 48 |
| Edge 15–20% | +9.1% | 48.1% | 27 |
| **Edge 20%+** | **+13.0%** | **60.6%** | **33** |

A model that predicts well but bets on everything is a model that loses to fees. The Kalshi fee structure (~7%) means low-edge bets are structurally unprofitable.

### The Prop Type Gap

| Prop | ROI | Win Rate | P&L | n |
|------|-----|----------|-----|---|
| **Assists** | **+17.2%** | **66%** | **+$24.68** | 259 |
| Points | +2.0% | 60% | +$4.26 | 378 |
| Rebounds | +2.3% | 53% | +$4.24 | 362 |

Assists aren't just better — they're in a different league. Points and rebounds are essentially breakeven after fees.

---

## Calibration Deep Dive

The raw Brier score looked great, but the calibration curve revealed a systematic bias:

| Predicted Probability | Actual YES Rate | Deviation |
|----------------------|-----------------|-----------|
| 0.3–0.4 | 23.1% (expected ~35%) | ⚠️ 11.7pp |
| **0.5–0.6** | **33.3% (expected ~55%)** | **⚠️ 22.2pp** |
| 0.7–0.8 | 62.0% (expected ~75%) | ⚠️ 12.8pp |

The model was systematically overestimating OVER probability in the 0.5–0.6 range. This is where the most bets were being placed, and where the most money was being lost.

**Fix**: Refitted Platt scaling parameters using all 610 settled predictions at that time.

| Parameter | Before | After |
|-----------|--------|-------|
| Platt A | 0.5750 | 0.8976 |
| Platt B | -0.0108 | -0.4242 |
| Brier Score | 0.1693 | 0.1590 |

---

## The Blowout Question

March 29 was suspicious. One day produced 74% of total P&L.

**Investigation**: BOS vs CHA — a blowout game where Charlotte players massively underperformed. The model bet NO (Under) on 93 CHA props and hit 83%.

| Game | Trades | Win Rate | P&L |
|------|--------|----------|-----|
| BOS vs CHA | 69 | 83% | +$19.47 |
| HOU vs NOP | 105 | 62% | +$5.02 |

**Verdict**: Not a bug — the model correctly identified value on the Under side. But this concentration risk is real. Without the blowout, the other 3 days combined produced only +$8.69.

> *The model doesn't predict blowouts. It happens to profit from them when they occur. That's not the same as having a repeatable edge on non-blowout days.*

---

## What We Changed

Based on the validation data, three major parameter changes:

### 1. Edge Threshold Increase

`MIN_EDGE`: 0.10 → 0.20

The data was unambiguous — edge-ROI relationship is monotonically increasing:

| Threshold | ROI | Win Rate | n |
|-----------|-----|----------|---|
| 0.10+ | +2.9% | 50.0% | 108 |
| 0.15+ | +11.3% | 55.0% | 60 |
| 0.20+ | +13.0% | 60.6% | 33 |
| 0.25+ | +22.1% | 68.0% | 25 |
| 0.30+ | +35.3% | 82.4% | 17 |

### 2. Prop-Type Differential Thresholds

Not all markets are equally efficient:

```python
PROP_MIN_EDGE = {
    "assists":  0.15,   # strongest signal, more opportunities
    "points":   0.25,   # most efficient market, need bigger edge
    "rebounds":  0.25,   # similar to points
}
```

### 3. Platt Scaling Refit

Calibration parameters updated with real forward-test data. Mid-range overconfidence reduced.

---

## Automation Upgrades

### NBA Schedule-Aware Runner

The system now checks today's NBA schedule at startup:

- **No games** → logs "No NBA games today — skipping" and exits
- **Games scheduled** → sleeps until first tipoff minus 2 hours, runs until last game + 1 hour
- **Heartbeat** every 30 seconds during sleep (process health monitoring)
- **Fallback** if NBA API is down → defaults to 5PM–1AM ET window

### Reporting Pipeline

Fixed four reporting bugs that were producing wildly incorrect Discord alerts:

| Bug | Impact |
|-----|--------|
| P&L showing -$1,659 | PAPER mode had no sell records → all buys counted as losses |
| Brier Score showing 0.221 | Using wrong data source (uncalibrated, smaller sample) |
| Markets Recorded showing 0 | Hardcoded zero in Discord formatter |
| Balance incorrect | Using Kalshi API balance instead of virtual bankroll |

---

## Current State

```
Virtual Bankroll:  $333.18  (started $300)
Settled:           999 predictions
ROI (legacy):      +6.1% (fee-adjusted)
Brier Score:       0.1681
Best Prop:         Assists (+17.2% ROI)
Config:            v3_prop_edge (differential thresholds)
```

## What's Next

- **Weeks 1–2**: Accumulate 100+ settled predictions under v3 config
- **Validation checkpoint**: If assists ROI holds >15%, overall ROI >5% → evaluate live deployment
- **Pre-game spread logging**: Track blowout dependency quantitatively
- **Live transition**: $300 real capital — only after v3 validation passes

---

## Key Takeaways

1. **Brier Score ≠ Profit.** You can predict well and still lose money. Fees are the final boss.
2. **Not all markets are equal.** Assists have structural inefficiency. Points are priced too efficiently to beat after fees.
3. **Silent failures are existential.** The Kalshi expiration bug produced zero errors and zero signals. Without explicit validation at each layer, the system looked healthy while doing nothing.
4. **One good day isn't an edge.** 74% of P&L from a single blowout game means the system hasn't yet proven it can grind out consistent returns.
5. **The edge threshold matters more than the model.** Raising MIN_EDGE from 0.10 to 0.20 had a bigger impact on ROI than any model improvement.

---

*"In theory, there is no difference between theory and practice. In practice, there is."*
*— Yogi Berra (probably while looking at a Brier Score)*
