# Kalshi NBA Player Props

A first side project, started for the wrong reasons and finished for the right ones.

Some friends and I had been trading small NBA player props on Kalshi for fun — five-dollar
bets to make the games more interesting. One evening I started wondering whether
something more disciplined than "guess and click" could actually find edge in those
markets, and whether I could build it myself.

It turned into a thirty-day experiment. Vibe coded nights and weekends, deployed to
live capital for a month, and now paused. This repository is the public log — five
chapters of real-time engineering notes, plus errata when I had to correct myself, plus
a closing chapter on why the bottom-line P&L is not published.

The trading code itself is private. The chapters and lessons are public.

---

## Tech Stack

Python 3.11 · XGBoost · Platt scaling · PostgreSQL · Kalshi REST and WebSocket APIs · `nba_api` · Kelly criterion (quarter-fraction sizing).

The trading code is not in this repository — only the public narrative.

---

## Project Snapshot

| Item | Value |
|---|---|
| Phase | Phase One — paused |
| Project window | 2026-03-25 → 2026-04-24 |
| Live capital phase | 2026-04-05 → 2026-04-24 (20 days) |
| Settled predictions | 1,660+ |
| Live trades placed | ~30 |
| Active prop type (live) | NBA Rebounds |
| Other props | Paper-only validation (Points, Assists) |
| Final config | `v5_kelly` (quarter-Kelly, 5% bankroll cap) |
| Database | PostgreSQL |
| System status | Runner offline, API key revoked |

Final P&L is not published. Chapter 5 explains the reasoning.

---

## Log

Each entry is a snapshot in time and is not edited after the fact. Corrections appear
as errata boxes on the affected chapter, with the full record in [`ERRATA.md`](./ERRATA.md).

| Date | Ch | Title | Outcome |
|---|---|---|---|
| 2026-03-28 | 1 | [System Audit & XGBoost Migration](./logs/2026-03-28-system-audit.md) | SQLite → PostgreSQL, logistic → XGBoost |
| 2026-04-02 | 2 | [Phase 1 Validation](./logs/2026-04-02-phase1-validation.md) | 999 predictions audited |
| 2026-04-04 | 3 | [The Price Was Wrong](./logs/2026-04-04-the-price-was-wrong.md) | Entry-price bug, v3 → v4 rebuild |
| 2026-04-09 | 4 | [Three Days, Four Bugs, One Repo Sanitized](./logs/2026-04-09-three-days-four-bugs.md) | Live transition, gap discovery |
| 2026-04-26 | 5 | [Closing the Books on Phase One](./logs/2026-04-26-closing-the-books.md) | End of live phase |

---

## Lessons (Short Version)

Expanded in Chapter 5:

- A calibrated probability model is necessary but not sufficient. Pipeline reliability is
  load-bearing and gets attention only after it breaks.
- Edge thresholds matter more than model improvements. Below ~15% edge, fees dominate.
- Different prop types have different efficiency. Rebounds had real edge in this window;
  points and assists did not at the thresholds tested.
- Quarter-fraction Kelly with a hard bankroll cap survived variance in a way adaptive
  sizing did not.
- Silent failures cost more than loud ones. The bugs that hurt this project never threw
  exceptions.
