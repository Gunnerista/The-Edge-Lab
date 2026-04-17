# Postmortems

Incident records for the War Machine project. Each file documents a production issue, its root cause, and the fix applied.

## Filename format

```
YYYY-MM-DD-short-name.md
```

Example: `2026-04-01-zero-padding-outage.md`

## Template

```markdown
# Incident: <short title>

**Date**: YYYY-MM-DD
**Severity**: HIGH / MEDIUM / LOW
**Duration**: X hours/days
**Config at time**: v4_edge_optimized / v5_kelly / etc.

## What happened
<1-3 sentences describing the observable symptom>

## Root cause
<Technical explanation of why it happened>

## Impact
- Predictions missed: N
- Revenue impact: $X (or "paper only")
- Data integrity: affected / not affected

## Fix applied
- Commit: <hash>
- Files changed: <list>
- <Brief description of the code change>

## Prevention
- <What rule/check/test was added to prevent recurrence>
- <Reference to relevant .claude/rules/ file if applicable>
```

## Known incidents (not yet documented as postmortems)

### 1. Zero-padding signal outage (2026-04-01 → 04-05)
- **Root cause**: Ticker date format `APR4` instead of `APR04`. `forward_test.py` generated tickers that didn't match any market in PostgreSQL.
- **Compounded by**: `runner.py` restart loop bug (no game-window sleep guard) caused continuous restarts during the outage.
- **Fix**: `f"{d.day:02d}"` zero-padding + 4PM ET sleep guard.
- **Duration**: 5 days of missing predictions.

### 2. auto_trader [Sync] position wipe (ongoing)
- **Root cause**: After a LIVE fill, `sync_with_kalshi()` in `auto_trader.py` can falsely conclude a position doesn't exist on Kalshi and removes it from `positions.json`.
- **Impact**: `positions.json` cannot be trusted. Exit logic is blocked for wiped positions.
- **Workaround**: Use `KalshiClient().get_positions()` as ground truth. Settlement still works server-side.
- **Status**: Unresolved. Fix pending.

### 3. timeout hidden-mode hang (resolved 2026-04-17)
- **Root cause**: `timeout /t 5 /nobreak` in `start_warmachine.bat` requires a console TTY. Inside `wscript` hidden mode, no TTY exists → process hangs forever after first runner exit.
- **Fix**: Replaced with `ping 127.0.0.1 -n 6 -w 1000 >nul`.
- **Note**: MLB batch still uses `timeout` (paper-only, lower priority).
