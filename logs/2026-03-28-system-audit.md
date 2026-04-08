> **📝 ERRATA — added 2026-04-09**  
> The metrics in this chapter (Brier 0.21 → 0.1852, 999-prediction baseline) were measured on the `legacy` config, before the entry-price bug was discovered in Chapter 3. They reflect the system as it stood on March 28 and are preserved here as a historical snapshot. They are not the system's current performance. See [`ERRATA.md`](../ERRATA.md) for the full record. The original text below is unedited.

---

# Day 1 — System Audit & Model Overhaul

**Date**: 2026-03-28
**Tags**: `betting-system` `model` `backtest` `milestone`

---

## TL;DR

Tore apart the entire betting system, rebuilt the core model, and came out the other side with a forward test running on autopilot. Day 1 of documenting the journey.

## What Happened

### 1. Full System Audit
Reviewed every component of the Phase 1 betting system — signal engine, data collector, model pipeline, auto-trader. Found several areas where the architecture was solid but the model was leaving edge on the table.

### 2. Model Swap: Logistic Regression → XGBoost
The original logistic regression model was... fine. But "fine" doesn't beat prediction markets after fees.

**Before (Logistic Regression)**:
- Brier Score: ~0.21
- Struggled with non-linear feature interactions
- Couldn't capture the complexity of NBA game dynamics

**After (XGBoost)**:
- Brier Score: **0.1852** (out-of-sample)
- Fee-adjusted ROI: **positive at edge >10% threshold**
- Much better at capturing pace, rest days, travel, and matchup-specific patterns

### 3. Backtest Validation
Ran the new model through historical data. Key findings:
- Edge >10% filter is critical — below that, fees eat the alpha
- Quarter Kelly sizing keeps drawdowns manageable
- Model performs best on spread-adjacent markets (not extreme favorites/underdogs)

### 4. Forward Test Automation
Set up daily cron job:
- Auto-collects NBA data each morning
- Generates predictions for today's games
- Logs predictions with timestamps (for later P&L verification)
- `FORCE_DRY_RUN=True` — no real money until 100-prediction validation

## Key Files Modified
- `signal_engine.py` — Updated signal generation logic
- `nba_model.py` — Replaced model pipeline
- `nba_data_collector.py` — Enhanced feature collection
- `auto_trader.py` — Forward test automation

## Metrics to Watch
| Metric | Target | Current |
|---|---|---|
| OOS Brier Score | < 0.20 | 0.1852 ✅ |
| Forward test predictions | 100+ | 0 (just started) |
| Fee-adjusted ROI | > 0% | TBD (forward test) |
| Max drawdown | < 15% | TBD |

## Lessons Learned
- Don't fall in love with model simplicity. Logistic regression is elegant but markets don't care about elegance.
- The fee structure on Kalshi is the real boss fight. Your model needs to be *significantly* better than the market, not just slightly.
- Automating the forward test early was the right call — removes the temptation to cherry-pick results.

## Next Steps
- [ ] Accumulate 100+ forward test predictions (ETA: ~3-5 days)
- [ ] Analyze forward test results vs backtest expectations
- [ ] If validated → transition to live trading with small bankroll
- [ ] If not → diagnose drift and iterate

---

*"The market can stay irrational longer than you can stay solvent." — Keynes*
*"That's why we use Kelly sizing." — Me, probably*
