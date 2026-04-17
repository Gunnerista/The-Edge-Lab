# War Machine — Claude Context

> **IMPORTANT**: Read this file first. When uncertain on anything that touches live capital, STOP and ask Ikjun in chat. This file is the architectural brain — details live in `.claude/skills/`.

Last verified: 2026-04-17

---

## What this is

Autonomous Kalshi prediction market trading system. Real money (~$219 bankroll). Kelly-sized NBA/MLB player props. XGBoost + Platt scaling for probability estimation. Sole operator: Ikjun (non-coder, drives via Claude Code).

**Phase A/B**: rebounds-only live, other props paper. Full live target: 2026-27 NBA regular season (Oct).

**Single truth**: Project path is `C:\Users\Dell\Desktop\Projects\sports-betting-system`. Not `D:\`. Not Documents folder.

---

## Hard rules (NEVER violate)

1. **NEVER** add a new prop_type to `LIVE_PROP_TYPES` without Ikjun's explicit chat approval. Currently only `{"rebounds"}`.
2. **NEVER** modify `data/safety_state.json`, `data/positions.json`, `data/tuned_params.json` by hand. Runner manages these.
3. **NEVER** overwrite `data/prediction_log.jsonl` — append-only. If you must touch it, backup first: `prediction_log.jsonl.bak.YYYYMMDD_HHMMSS`. Never modify records where `settled=True`.
4. **NEVER** commit `.env`, `keys/`, `*.pem`, or anything matching `.gitignore`. Check `git status` before every commit.
5. **NEVER** push to `origin/main`. That branch is sanitized public narrative (github.com/Gunnerista/The-Edge-Lab, Chapter docs only, `scripts/` gitignored). Dev work goes on `sprint1-day1-20260414` or feature branches.
6. **NEVER** modify Platt scaling params (A=0.8976, B=-0.4242) without explicit instruction. These are calibrated.
7. **NEVER** `git push --force` without Ikjun's chat approval.
8. **NEVER** assume paper mode. System is HYBRID: rebounds LIVE, others PAPER. Live and paper code share the same runner.
9. **ALWAYS** `Read` the current file state before `Edit`. Memory and prior session are not truth.
10. **ALWAYS** verify claims against files/git/DB before citing. "Memory said X" is not evidence.
11. **IF uncertain about a live-trading decision, STOP and ask Ikjun.** Do not guess on anything that touches capital.

---

## What we are NOT doing

- No DraftKings / FanDuel (5-10% vig kills edge, F-1 visa constraint).
- No manual betting (Ikjun pledged 2026-04-13; Kalshi app deleted, password randomized).
- No points/assists/threes live trading (not yet validated).
- No new ML models without ≥50 settled-trade validation first.
- No Agent Teams / experimental features on live-trading code paths.
- No "improvements" outside the requested scope (no added type hints, docstrings, error handling, refactors).

---

## Current config — v5_kelly

| Parameter | Value | File |
|---|---|---|
| Kelly fraction | 0.25x | `bankroll.py` |
| `GLOBAL_MIN_EDGE` | 0.15 | `signal_engine.py` |
| `MAX_EDGE_CAP` | 0.45 | `auto_trader.py` (edge≥0.50 had WR 0%) |
| `YES_EDGE_PREMIUM` | +0.05 | `signal_engine.py` |
| `MAX_SPREAD_CENTS` | 15 (rebounds) | `tuned_params.json` |
| Price filter | 0.05 ≤ p ≤ 0.95 | `forward_test.py` |
| Platt A / B | 0.8976 / -0.4242 | `nba_model.py` |
| Max single trade | 15% of bankroll | (Sprint 1 under review) |
| `LIVE_PROP_TYPES` | `{"rebounds"}` | `auto_trader.py:143` |

**Prop edges** (all MIN_EDGE, YES/NO):

- Rebounds: 0.20 / 0.15 — **LIVE**
- Points: 0.30 / 0.25 — paper
- Assists: 0.25 — paper (review at 50 settled)
- Threes: 0.30 / 0.25 — paper

**Safety — 4-level drawdown on HWM $220:**

| Level | DD range | Action |
|---|---|---|
| GREEN | 0 to -7% | normal |
| YELLOW | -7% to -12% | size × 0.5 |
| ORANGE | -12% to -15% | no new entries |
| RED | < -15% | hard stop + Discord alert |
| Bankrupt | bankroll < $100 | force PAPER + 2-week cooldown |

Kill switch points: `trade_engine.py:462` (execute_order) and `:556` (exit_position).

---

## Repo layout
sports-betting-system/
├── CLAUDE.md                     # this file
├── .env, keys/                   # gitignored — never stage
├── scripts/                      # NBA system + shared entry points
│   ├── runner.py                 # main orchestrator (parent+child PIDs)
│   ├── signal_engine.py          # edge calc + filters
│   ├── auto_trader.py            # LIVE_PROP_TYPES gate
│   ├── trade_engine.py           # order placement + kill switch
│   ├── forward_test.py           # 12pm ET prediction
│   ├── settle_predictions.py     # 11:59pm ET settle
│   ├── market_recorder.py        # 60s snapshots → PG
│   ├── self_learner.py           # Sun 10pm param tuning
│   ├── start_warmachine.bat      # NBA restart loop (uses ping, NOT timeout)
│   ├── run_hidden.vbs            # Windows hidden launcher
│   └── ...                       # see .claude/skills/file-roles.md
├── shared/                       # cross-system modules (NBA+MLB)
│   └── db.py                     # PostgreSQL single source of truth
├── mlb/scripts/                  # MLB system (paper only)
│   ├── runner_mlb.py
│   ├── start_warmachine_mlb.bat
│   └── run_hidden_mlb.vbs
├── tools/                        # permanent utilities (health_check etc.)
├── orchestrator/                 # claude-agent-sdk prompts (experimental)
├── data/                         # runtime state (gitignored)
│   ├── prediction_log.jsonl      # APPEND-ONLY
│   ├── trade_log.jsonl, paper_trades.jsonl
│   ├── safety_state.json, positions.json, tuned_params.json
│   └── runner.log
├── docs/                         # ADRs, audits, session logs
└── _cleanup_archive_20260415/    # scheduled delete 2026-04-22

---

## Database — PostgreSQL `warmachine`

- `localhost:5432`, `psycopg2` + `ThreadedConnectionPool`
- Single source: `shared/db.py`. `scripts/db.py` is a shim that re-exports.
- Tables: `markets` (~11M rows), `price_snapshots` (~660K, 4/14+), `nba_players`, `nba_games`, `mlb_*` (9 tables).
- `trades` and `settled_markets` tables exist but are empty — real trade data lives in `.jsonl` files.
- SQLite migration completed 2026-03-29. Never reference `data/market_data.db`.

---

## Workflow rules

### Branching
- Dev branch: `sprint1-day1-20260414` (or feature branches forked from it).
- Feature branch name: `claude/{task}-{YYYYMMDD}`.
- Commit prefixes: `feat: / fix: / refactor: / docs: / chore:`.
- One commit per logical change; squash before merge.

### Before editing any file
1. `Read` the current file state.
2. `git log --oneline -5 <file>` to understand recent changes.
3. Use Plan Mode (`/plan`) for anything touching ≥2 files or any live-trading path.

### After editing
1. If you touched `runner.py`, restart is required. Confirm both parent+child PIDs after restart.
2. If you touched `auto_trader.py`, `trade_engine.py`, `signal_engine.py`, or anything under `LIVE_PROP_TYPES`: **STOP and ask Ikjun in chat before commit.**
3. Never commit with `safety_state.json` changes that weren't runner-generated.

### Before commit
- `git status` — check nothing under `.gitignore` is staged.
- `git diff --cached` — review every hunk.
- No API keys, no `.env` values, no `.pem` content in the diff.

### Ticker format
Day is zero-padded: `f"{d.day:02d}"` → `KXNBAPTS-26APR04`, never `APR4`. This bug caused 5-day signal outage 2026-04-01→04-05.

### Timezone
All scheduling uses `tz.ET` (US/Eastern). Never naive datetime. Never system local time.

---

## Schedule (all ET)

- Runner auto-start: tipoff − 2h. Auto-stop: last game + 1h (minimum 12:30 AM).
- Outside game window: sleep until 4 PM ET (prevents restart loop — 2026-04-02 bug).
- 12:00 PM: `forward_test.run_forward_test(source="auto")`.
- 10:00 PM: Discord daily summary (Korean).
- 11:59 PM: `settle_predictions.settle()`.
- Sunday 10 PM: weekly risk decomposition + `self_learner` param tuning.

**Startup catch-up**: if (now > 12pm ET) AND (no `source="auto"` predictions today) AND (active props exist) → run forward test immediately.

---

## Source tagging (`prediction_log.jsonl`)

- `"auto"` — runner scheduler (12pm cron + catch-up).
- `"auto-cli"` — CLI run (`python scripts/forward_test.py`).
- `"manual"` — legacy only (manual betting is now banned).
- Calibration reports use `auto + auto-cli` only. Exclude `manual`.

---

## Known pitfalls (seen in this project)

- `timeout /t 5 /nobreak` in `.bat` files **hangs inside hidden wscript**. Use `ping 127.0.0.1 -n 6 -w 1000 > nul` instead.
- `self_learner` writes `tuned_params.json`. Manual edits get overwritten at Sunday 10 PM — don't fight it; update `self_learner` logic instead.
- Kalshi `expiration_time` is season-end, not game date. Parse the ticker for game day.
- `auto_trader.py [Sync]` position-wipe bug (known, unresolved): after live fills, sync can falsely clear `positions.json`. Do not rely on `positions.json` as ground truth — Kalshi API is the truth. (Fix pending.)

---

## When to ask Ikjun in chat (not execute)

- Any change to `LIVE_PROP_TYPES`, Kelly fraction, DD thresholds, kill-switch logic.
- Schema changes (PostgreSQL DDL, `.jsonl` structure).
- Git operations beyond branch commit/push: rebase, force-push, merge to main.
- Anything touching `.env`, `keys/`, credentials.
- Refactors spanning >100 lines across ≥3 files.
- When memory claims and actual file state conflict.

Ikjun decides strategy. Code implements.

---

## Skills (load on demand)

- `.claude/skills/file-roles/SKILL.md` — per-file responsibilities, when to touch each.
- `.claude/skills/data-flow/SKILL.md` — signal → trade → settle pipeline, data-integrity rules.
- `.claude/skills/run-commands/SKILL.md` — CLI invocation reference, debug commands.
