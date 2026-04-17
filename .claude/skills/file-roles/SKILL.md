---
name: War Machine File Roles
description: Per-file responsibilities in the War Machine codebase. Load when Claude needs to know which file does what, or when deciding which file to modify for a given task.
---

# File Roles

## scripts/ (NBA + shared entry points)

### Core runtime
- **`runner.py`** — Main orchestrator. Spawns parent+child Python processes. Observer thread (60s snapshots) + Scanner thread (120s signal+trade). Schedules 12pm/10pm/11:59pm jobs. Handles NBA game-window auto start/stop. Editing requires restart.
- **`auto_trader.py`** — `should_place_bet()` filter chain: `LIVE_PROP_TYPES` → prop `MIN_EDGE` → `YES_EDGE_PREMIUM` → `GLOBAL_MIN_EDGE`. This is the gatekeeper between paper and live.
- **`trade_engine.py`** — Order placement. Kill switches at `:462` (execute_order) and `:556` (exit_position). Kalshi order submission, fill tracking.
- **`signal_engine.py`** — Edge calculation. `entry_price`: YES = `yes_ask`, NO = `1 - yes_bid`. Appends to `prediction_log.jsonl` via `_append_prediction_log()` helper.

### Data collection
- **`market_recorder.py`** — `RESTRecorder.record_snapshot()` writes every 60s to PostgreSQL `price_snapshots`.
- **`kalshi_client.py`** — REST client with RSA-PSS auth. Reads private key from `KALSHI_PRIVATE_KEY_PATH`.
- **`kalshi_ws.py`** — WebSocket streaming (currently Basic tier; Advanced tier pending Phase C).

### Prediction + settlement
- **`forward_test.py`** — 12pm ET job. Queries `markets` table → `NBAModel.process_kalshi_nba_ticker()` → writes `prediction_log.jsonl`. Tickers: `KXNBAPTS` (points), `KXNBAREB` (rebounds). `CONFIG_VERSION` tag.
- **`settle_predictions.py`** — 11:59pm ET. `nba_api` boxscores → updates `prediction_log.jsonl` with `settled=True, actual_result`. Only modifies unsettled rows.
- **`nba_model.py`** — XGBoost + Platt scaling (A=0.8976, B=-0.4242).

### Safety + learning
- **`bankroll.py`** — Kelly Criterion sizing (0.25x fraction). Reads `safety_state.json`.
- **`safety.py`** — 4-level drawdown tracker. Computes GREEN/YELLOW/ORANGE/RED from HWM $220.
- **`self_learner.py`** — Sunday 10pm param tuning. Writes `tuned_params.json`. Does NOT touch `MAX_SPREAD_CENTS`.
- **`calibration.py`** — Tracks Brier score, calibration curves.
- **`risk_decomposition.py`** — Weekly P&L breakdown by prop type / source.

### Windows launchers
- **`start_warmachine.bat`** — NBA restart loop. Uses `ping` for wait (not `timeout` — hangs in hidden mode).
- **`run_hidden.vbs`** — `wscript` host that launches `start_warmachine.bat` in hidden mode.

## shared/ (NBA + MLB common)
- **`db.py`** — PostgreSQL connection pool. **Single source of truth**. `scripts/db.py` is a shim re-exporting this.
- **`bankroll.py`, `kalshi_client.py`, `tz.py`, `filters.py`, `safety.py`** — parallel copies; NBA uses `scripts/`, MLB uses `shared/` directly. Future work: migrate rest to shim pattern.

## mlb/scripts/
- **`runner_mlb.py`** — MLB orchestrator, parallel to `runner.py`. Paper-only, virtual $100 bankroll.
- **`start_warmachine_mlb.bat`** — MLB restart loop (same structure as NBA).
- **`run_hidden_mlb.vbs`** — MLB hidden launcher.
- XGBoost + Platt strikeouts model (AUC 0.73, Brier 0.18). 9 `mlb_*` tables in `warmachine` DB. Confirmed-starter gate active.

## tools/ (permanent utilities, NOT run by runner)
- `health_check.py` — 25-item system audit. Run before every Sprint.
- Other one-off tools — check filename; most are Sprint-specific.

## orchestrator/ (claude-agent-sdk, experimental)
- 4 prompt playbooks (Strategist / Risk Manager / Executor / etc.). Not wired into live trading. Read-only experiments.

## data/ (gitignored runtime state)
- **`prediction_log.jsonl`** — APPEND-ONLY. Never overwrite. Never modify `settled=True` rows.
- **`trade_log.jsonl`** / **`paper_trades.jsonl`** — Execution records. Missing `execution_mode` field (LIVE/PAPER distinction lives in `runner.log` text).
- **`safety_state.json`** — Runner-managed. Do not hand-edit.
- **`positions.json`** — Known bug: can be falsely wiped by `auto_trader [Sync]`. Treat Kalshi API as truth.
- **`tuned_params.json`** — `self_learner` writes every Sun 10pm. Manual edits get overwritten.
- **`equity_curve.json`** — Date-indexed bankroll snapshots.
- **`runner.log`** — Main log. `tail -f` for live debugging.
