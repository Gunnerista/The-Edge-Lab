---
name: War Machine Run Commands
description: CLI invocations for manual operation and debugging. Load when Claude needs to run, restart, test, or debug the system.
---

# Run Commands

## Main runner

```bash
# Start main orchestrator (prod)
python scripts/runner.py

# Flags
python scripts/runner.py --observe-only      # data collection only
python scripts/runner.py --scan-only         # scanning only
python scripts/runner.py --no-auto-trade     # signals without trades
python scripts/runner.py --dry-run           # no real orders
```

## Manual jobs

```bash
# Forward test (12pm cron equivalent)
python scripts/forward_test.py                    # today + tomorrow, source=auto-cli
python scripts/forward_test.py --date 2026-04-19  # specific date

# Settlement
python scripts/settle_predictions.py              # settle all unsettled
python scripts/settle_predictions.py --report     # report only

# Data collection standalone
python scripts/market_recorder.py
```

## MLB (paper only)

```bash
python mlb/scripts/runner_mlb.py
```

## Windows auto-start (installed)

- Startup folder: `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`
  - `WarMachine.lnk` → `wscript run_hidden.vbs` → `start_warmachine.bat` → `runner.py`
  - `run_hidden_mlb.vbs` → `start_warmachine_mlb.bat` → `runner_mlb.py`

## Debug snippets

```bash
# Current balance + open positions (verify Kalshi truth)
python -c "from shared.kalshi_client import KalshiClient; c=KalshiClient(); print(c.get_balance()); print(c.get_positions())"

# Prediction log tail
tail -20 data/prediction_log.jsonl

# Runner log tail (live)
tail -f data/runner.log

# DB sanity
psql -d warmachine -c "SELECT COUNT(*) FROM markets; SELECT MAX(snapshot_time) FROM price_snapshots;"

# Find which process is running runner
# Windows:
wmic process where "name='python.exe'" get ProcessId,CommandLine

# Settle today only
python scripts/settle_predictions.py --date today
```

## Restart workflow

```bash
# 1. Find current runner PID (parent+child)
wmic process where "name='python.exe' and CommandLine like '%runner.py%'" get ProcessId,CommandLine

# 2. Kill both
taskkill /F /PID <parent_pid>
# child dies with parent

# 3. Hidden restart
wscript C:\Users\Dell\Desktop\Projects\sports-betting-system\scripts\run_hidden.vbs

# 4. Verify
Start-Sleep 10
Get-Process python | Where-Object {$_.CommandLine -match "runner.py"}
```

## Health check

```bash
python tools/health_check.py    # 25-item audit, run before every Sprint
```
