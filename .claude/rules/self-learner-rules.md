---
paths:
  - "scripts/self_learner.py"
  - "data/tuned_params.json"
---

# Self-Learner Rules

These rules load when Claude touches the auto-tuning system. `self_learner.py` modifies trading parameters autonomously — incorrect changes compound over weeks.

## Schedule

- Runs: **Sunday 10:00 PM ET** (triggered by runner.py scheduler)
- Reads: `data/prediction_log.jsonl` (settled predictions for the past week)
- Writes: `data/tuned_params.json` (overwritten every run)
- Also writes: `data/parameter_history.jsonl` (append-only audit trail)

## Absolute rules

1. **NEVER** manually edit `data/tuned_params.json` expecting it to persist. `self_learner` overwrites it every Sunday. To change a parameter permanently, modify `self_learner.py` logic or `DEFAULT_PARAMS`.
2. **NEVER** modify `DEFAULT_PARAMS` without understanding the tuning ranges:
   ```python
   DEFAULT_PARAMS = {
       "kelly_high": 0.50,      # range: 0.15 – 0.75
       "kelly_medium": 0.25,    # range: 0.08 – 0.50
   }
   ```
3. **NEVER** add `MAX_SPREAD_CENTS` to the tunable set. It is intentionally excluded — spread sensitivity differs by prop type and market maturity. Hardcoded in `signal_engine.py`.
4. **NEVER** let `self_learner` tune `GLOBAL_MIN_EDGE` or `YES_EDGE_PREMIUM`. These are strategic parameters set by Ikjun, not performance-tuned.
5. **ALWAYS** check `data/parameter_history.jsonl` after a Sunday run to verify changes are reasonable.

## What self_learner tunes

| Parameter | Default | Range | Trigger |
|-----------|---------|-------|---------|
| `kelly_high` | 0.50 | 0.15 – 0.75 | Calibration bias detected |
| `kelly_medium` | 0.25 | 0.08 – 0.50 | Calibration bias detected |

## What self_learner does NOT tune

- `GLOBAL_MIN_EDGE` (strategic, not performance)
- `YES_EDGE_PREMIUM` (structural market bias)
- `MAX_SPREAD_CENTS` (per-prop, hardcoded)
- `PROP_MIN_EDGE` per prop type (set in signal_engine.py)
- `MAX_EDGE_CAP` (adverse selection ceiling)
- `LIVE_PROP_TYPES` (capital allocation decision)

## Tuning logic (simplified)

```python
# Rule 1: Calibration bias → adjust Kelly fractions
if model_overconfident:
    kelly_high -= adjustment    # shrink position sizes
    kelly_medium -= adjustment
elif model_underconfident:
    kelly_high += adjustment    # grow position sizes
    kelly_medium += adjustment
```

Adjustments are small (typically ±0.01 to ±0.05 per week) and clamped to ranges.

## How `get_params()` works

Other modules read tuned params via:
```python
from self_learner import get_params
params = get_params()  # merges DEFAULT_PARAMS + tuned_params.json
```

Priority: `tuned_params.json` values override `DEFAULT_PARAMS`. If `tuned_params.json` is missing or corrupt, defaults are used silently.

## Before editing self_learner.py

1. Read `data/tuned_params.json` to see current tuned values.
2. Read `data/parameter_history.jsonl` (last 5 entries) to see tuning trajectory.
3. If you change tuning ranges, verify the new range doesn't allow dangerous values (e.g., `kelly_high > 1.0` = betting more than Kelly suggests = ruin risk).
4. After editing, the change takes effect at next Sunday 10 PM. To test immediately: `python -c "from self_learner import SelfLearner; s=SelfLearner(); s.tune()"` — but this writes `tuned_params.json` immediately.

## Recovery

If `tuned_params.json` is corrupted:
```bash
# Option 1: Delete (falls back to DEFAULT_PARAMS)
rm data/tuned_params.json

# Option 2: Restore from history
tail -1 data/parameter_history.jsonl | python -m json.tool
# Copy the params block back to tuned_params.json
```
