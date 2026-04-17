# Cleanup Audit — 2026-04-17

## Summary

1. **3 .bak files, 5 zombie scripts (0 imports), 6 duplicate basenames across scripts/ and shared/, 2 orphan root dirs** — all safe to clean.
2. **CLAUDE.md is 385 lines but factually stale** (says v4/PAPER, actual is v5_kelly/HYBRID LIVE). 36% can be cut or moved to skills. Critical risk: Claude Code will make wrong assumptions from outdated config tables.
3. **1 unpushed commit, 0 sensitive data found** — safe to push.

---

## Phase 2 Action Items

### DELETE (safe, no imports, no runtime dependency)

| File | Reason | Evidence |
|------|--------|----------|
| `scripts/db.py.bak_pre_consolidate_20260415` | Backup from Phase 4 db consolidation | Original preserved in shared/db.py |
| `scripts/start_warmachine.bat.bak_20260417_1500` | Backup from timeout→ping fix today | Original updated and tested |
| `shared/db.py.bak_pre_consolidate_20260415` | Backup from Phase 4 | Original preserved in shared/db.py |
| `scripts/watchdog.py` | 0 imports, standalone CLI, never called by runner | grep: 0 matches across all .py |
| `scripts/backfill_settled.py` | 0 imports, one-shot tool (already ran) | grep: 0 matches |
| `scripts/backtest_platt.py` | 0 imports, standalone CLI | grep: 0 matches |
| `scripts/migrate_data.py` | 0 imports, SQLite→PG migration (done 3/29) | grep: 0 matches |
| `scripts/migrate_schema.py` | 0 imports, SQLite→PG schema (done 3/29) | grep: 0 matches |

### KEEP (duplicate basename but intentional shared/ architecture)

| scripts/ file | shared/ file | Relationship | Used by |
|---------------|-------------|--------------|---------|
| `db.py` (668B shim) | `db.py` (1717B real) | Shim re-exports shared/db.py | NBA: shim, MLB: direct |
| `bankroll.py` | `bankroll.py` | Both used | NBA + MLB |
| `kalshi_client.py` | `kalshi_client.py` | Both used | NBA + MLB |
| `tz.py` | `tz.py` | Both used | NBA + MLB |
| `filters.py` | `filters.py` | NBA-only in scripts/ | NBA only |
| `safety.py` | `safety.py` | NBA-only in scripts/ | NBA only |

> Note: scripts/db.py was consolidated to a shim in Phase 4 (4/15). The other 5 pairs are separate copies maintained in parallel — NOT shims. Future work: evaluate whether filters.py and safety.py should follow the db.py shim pattern.

### REVIEW (orphan directories at project root)

| Dir | Contents | Action |
|-----|----------|--------|
| `orchestrator/` | Unknown (new, untracked) | Review contents before deciding |
| `experiments/` | gitignored, likely empty | Delete if empty |
| `projects/` | Unknown | Review |
| `assets/` | 1 file | Review |
| `research/data/` | Empty (gitignored) | Keep (placeholder for backtest data) |
| `research/experiments/` | Empty (gitignored) | Keep (placeholder for experiment output) |

### REWRITE (factually stale)

| Section | Issue |
|---------|-------|
| CLAUDE.md §1 | Says "PAPER TRADING MODE", "v4_edge_optimized" — actual: HYBRID LIVE, v5_kelly |
| CLAUDE.md §2 | Config table shows v4 values, assists "Disabled" — actual: assists re-enabled in v5 |
| CLAUDE.md §4 | Directory tree missing: mlb/, tools/, research/, orchestrator/, shared/ |
| CLAUDE.md §5 | Table counts stale (markets "769만" → actual 11.18M, price_snapshots "428만" → 1.15M) |
| CLAUDE.md §15 | Says "1,111 settled, v4: 0" — actual: 2,308+ settled, v5_kelly active |

---

## Zombie Files Analysis

All 5 files verified via `grep -rn "import X\|from X import" scripts/*.py shared/*.py mlb/scripts/*.py`:

| File | Imports found | __main__ guard | Verdict |
|------|--------------|----------------|---------|
| `watchdog.py` | **0** | Yes | ZOMBIE — standalone process monitor, never integrated |
| `backfill_settled.py` | **0** | Yes | ZOMBIE — one-shot backfill, job complete |
| `backtest_platt.py` | **0** | Yes | ZOMBIE — standalone Platt backtest CLI |
| `migrate_data.py` | **0** | Yes | ZOMBIE — SQLite→PG migration complete (3/29) |
| `migrate_schema.py` | **0** | Yes | ZOMBIE — SQLite→PG schema complete (3/29) |

These were archived to `_cleanup_archive_20260415/zombies/` on 4/15 but restored by an unknown session on 4/16 08:52. No new imports were added — they remain dead code.

**Recommendation**: Delete from scripts/. If historical reference needed, they exist in git history and in `_cleanup_archive_20260415/zombies/`.

---

## CLAUDE.md Section Map

Current file: **385 lines, 15 sections**. Analysis based on: "If deleted, would Claude Code make a mistake?"

| Section | Lines | Verdict | Reason |
|---------|-------|---------|--------|
| §1 Project overview | 1-17 | **KEEP** (rewrite) | Essential context, but factually stale |
| §2 Current config | 20-70 | **KEEP** (rewrite) | Prevents wrong threshold assumptions; stale v4 values |
| §3 Config version history | 73-82 | **CUT** | Historical log; v5_kelly missing; no mistake prevention |
| §4 Directory structure | 86-127 | **KEEP** (rewrite) | Prevents wrong path assumptions; missing dirs |
| §5 Database | 131-148 | **KEEP** | PG connection info prevents SQLite mistakes |
| §6 File roles (detailed) | 152-204 | **SKILL** | 53 lines of per-file docs; rarely needed inline |
| §7 Data flow diagram | 207-226 | **SKILL** | Reference diagram; rarely needed |
| §8 Source tagging | 230-240 | **KEEP** | Prevents calibration report mistakes |
| §9 Schedule | 244-262 | **KEEP** | Prevents scheduler assumption errors |
| §10 Price filter | 266-272 | **KEEP** | Short; prevents filter mistakes |
| §11 Modification history | 276-297 | **CUT** | Historical changelog; no mistake prevention |
| §12 Coding rules | 300-333 | **KEEP** | Essential guardrails (read-before-write, append-only, etc.) |
| §13 Environment variables | 336-345 | **KEEP** | Prevents .env mistakes |
| §14 How to run | 349-371 | **SKILL** | Command reference; rarely needed inline |
| §15 Performance summary | 375-385 | **CUT** | Completely stale numbers; actively misleading |

### Projected result

| Category | Lines | Count |
|----------|-------|-------|
| KEEP (stays in CLAUDE.md) | ~188 | 9 sections |
| SKILL (move to .claude/skills/) | ~96 | 3 sections |
| CUT (delete) | ~43 | 3 sections |
| **New CLAUDE.md size** | **~250** | **35% smaller** |

### Skills file structure (proposed)

```
.claude/skills/
├── war-machine-file-roles.md      # §6 content
├── war-machine-data-flow.md       # §7 content
└── war-machine-run-commands.md    # §14 content
```

---

## Sensitive Data Risk Assessment

### Unpushed commits

| Commits ahead of origin | 1 (not 9 — git state changed since user's estimate) |
|--------------------------|------------------------------------------------------|
| Commit | `8888bec fix(dedup): replace ET startswith with UTC timedelta in scanner dedup` |
| Author email in diff | `ikjunj19@gmail.com` (normal, already in git config) |
| API keys | **NONE** |
| .env / .pem files | **NONE** |
| Hardcoded passwords | **NONE** |
| Kalshi secrets | **NONE** |
| **Verdict** | **SAFE TO PUSH** |

### Existing tracked files risk

| Risk | Status |
|------|--------|
| .env in .gitignore | Yes |
| keys/ in .gitignore | Yes |
| *.pem in .gitignore | Yes |
| _cleanup_archive in .gitignore | Yes |
| data/ in .gitignore | Yes |
| No hardcoded password in scripts/*.py | Verified (Phase 5 health_check) |

### warmachine_task.xml

Contains Task Scheduler XML config (logon trigger, hidden execution). **No secrets**, but reveals system username (`Dell`) and project path. Low risk for public repo — same info already visible in commit author metadata.
