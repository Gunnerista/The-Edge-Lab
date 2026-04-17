---
description: Git workflow rules for the War Machine project. Always active — no path filter.
---

# Git Workflow Rules

These rules are unconditional — they apply to every git operation in this project.

## Branch structure

| Branch | Purpose | Push allowed? |
|--------|---------|--------------|
| `origin/main` | **Sanitized public repo** (Chapters, README, ERRATA). `scripts/` is gitignored on main. | **NEVER push from Claude Code** |
| `sprint1-day1-20260414` | Active dev branch. All code changes go here. | Yes |
| `claude/{task}-{YYYYMMDD}` | Feature branches forked from sprint branch | Yes |

## Absolute rules

1. **NEVER** push to `origin/main`. It is a curated public narrative at `github.com/Gunnerista/The-Edge-Lab`. Main's `.gitignore` blocks `scripts/`, `CLAUDE.md`, `requirements.txt`, `*.bat`, `*.xml`. Pushing code there breaks the sanitization.
2. **NEVER** `git push --force` without Ikjun's explicit chat approval. Force-push rewrites history visible to others.
3. **NEVER** `git rebase` the sprint branch against main without Ikjun's approval. Main was force-pushed (sanitized) and has diverged history.
4. **NEVER** commit files matching `.gitignore`: `.env`, `keys/`, `*.pem`, `data/`, `*.json` (runtime state), `*.log`.
5. **ALWAYS** run `git status` before staging. Look for unexpected modified/untracked files.
6. **ALWAYS** run `git diff --cached` before committing. Review every hunk for secrets.

## Commit message format

```
<prefix>: <short description>

<optional body>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

**Prefixes** (required):
- `feat:` — new feature
- `fix:` — bug fix
- `refactor:` — code restructure (no behavior change)
- `docs:` — documentation only
- `chore:` — cleanup, dependency, config

One commit per logical change. Squash related edits before push.

## Pre-commit checklist

```bash
# 1. Status check
git status
# Look for: .env, *.pem, data/*.json, keys/ — NONE should appear

# 2. Diff review
git diff --cached
# Grep for secrets:
git diff --cached | grep -iE "KALSHI_API_KEY|BEGIN RSA|password.*=.*['\"][^'\"]+['\"]|1203"
# Empty = safe

# 3. Staged file list
git diff --cached --name-only
# Every file should be intentional
```

## Secret patterns to scan

Before every push, verify none of these appear in the diff:

| Pattern | What it catches |
|---------|----------------|
| `KALSHI_API_KEY_ID=` | API key value |
| `BEGIN RSA` | Private key content |
| `password="..."` or `password='...'` | Hardcoded passwords |
| `.pem` file content | Certificate/key material |
| `DISCORD_WEBHOOK_URL=https://` | Webhook with token |

## Branch divergence (current state)

```
origin/main:              6768b88 (sanitized, force-pushed, Chapter docs only)
  ↑ diverged from local main at bd4dd64
  
sprint1-day1-20260414:    163dd17 (active dev, synced with origin)
```

Local `main` branch is stale (ahead 7, behind 12 of origin/main). Do not attempt to reconcile — the histories are intentionally different.

## When to ask Ikjun

- Any `git push --force`
- Any operation on `main` branch
- Any `git rebase` involving shared history
- Merge conflicts touching `auto_trader.py`, `trade_engine.py`, or `signal_engine.py`
- If `git status` shows unexpected changes to `safety_state.json` or `positions.json`
