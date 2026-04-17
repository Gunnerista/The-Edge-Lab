---
paths:
  - "scripts/forward_test.py"
  - "scripts/settle_predictions.py"
  - "scripts/signal_engine.py"
---

# Prediction Log Integrity Rules

These rules load when Claude touches any file that reads or writes `data/prediction_log.jsonl`. This file is the single source of truth for model calibration and P&L accounting.

## Absolute rules

1. **NEVER** overwrite `prediction_log.jsonl`. It is append-only. The only exception is `settle_predictions.save_predictions()`, which rewrites the entire file to flip `settled=False → True`.
2. **NEVER** modify a row where `settled=True`. These are immutable historical records. Changing them corrupts calibration metrics.
3. **NEVER** call `save_predictions()` without a backup first:
   ```bash
   cp data/prediction_log.jsonl data/prediction_log.jsonl.bak.$(date +%Y%m%d_%H%M%S)
   ```
4. **NEVER** write to prediction_log from new code paths without using `_append_prediction_log()` (signal_engine.py:106). This helper handles mkdir, UTF-8 encoding, and failure logging.
5. **ALWAYS** include `source` tag in every new entry. Valid values: `"auto"`, `"auto-cli"`, `"scanner"`. Never `"manual"` (manual betting is banned).
6. **ALWAYS** include `config_version` tag. Current value: `"v5_kelly"` (forward_test.py:46).

## Two write paths (know which you're using)

### Safe path: `_append_prediction_log(entry)` — signal_engine.py:106
- Opens file in append mode (`"a"`)
- Writes one JSON line
- Catches exceptions silently (logs warning, doesn't kill scan cycle)
- Used by: scanner path during `run_cycle()`

### Dangerous path: `save_predictions(preds)` — settle_predictions.py:50
- Opens file in **write mode** (`"w"`) — full rewrite
- Reads all rows, modifies unsettled ones, writes everything back
- If interrupted mid-write: **data loss**
- Used by: settlement only (11:59 PM ET cron)

**IMPORTANT**: If you must call `save_predictions()`, verify the `preds` list length matches `wc -l data/prediction_log.jsonl` before writing. A short list = data loss.

## Source tagging contract

| Source | Who writes | When | Include in calibration? |
|--------|-----------|------|------------------------|
| `"auto"` | runner.py scheduler | 12pm ET cron + startup catch-up | Yes |
| `"auto-cli"` | CLI invocation | `python scripts/forward_test.py` | Yes |
| `"scanner"` | signal_engine scan cycle | Every 120s during game window | Yes |
| `"manual"` | Legacy only | Never (banned 2026-04-13) | No |

## Required fields (every entry)

```python
{
    "ticker": "KXNBAREB-26APR19-...",    # zero-padded day
    "player": "Player Name",
    "prop_type": "rebounds",              # rebounds/points/assists
    "line": 11.5,
    "model_prob": 0.72,
    "kalshi_price": 0.51,
    "edge": 0.21,
    "confidence": "medium",              # high/medium/low
    "game_date": "2026-04-19",
    "prediction_time": "2026-04-19T...", # ISO 8601 UTC
    "source": "auto",
    "config_version": "v5_kelly",
    "actual_result": null,               # set by settle
    "settled": false,                    # set by settle
}
```

## Deduplication

Scanner path uses `_load_recently_logged_scanner_tickers(window_seconds=600)` (signal_engine.py:75) to avoid duplicate entries. This replaced a broken ET-based dedup that failed after UTC midnight (fixed in commit `8888bec`).

## Known data gaps

- 2026-04-01, 04-02: zero-padding bug + restart loop (5-day outage)
- 2026-04-06, 04-09→04-13: computer repair downtime
- Total: 7 of 12 days missing in early period. Calibration must account for this.

## Before editing any writer

1. `Read` the current file. The append helper and dedup logic have been rewritten multiple times.
2. Count current rows: `wc -l data/prediction_log.jsonl`
3. After editing, verify row count hasn't decreased.
4. Run `python scripts/settle_predictions.py --report` to confirm calibration still computes.
