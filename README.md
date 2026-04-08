<h1 align="center">THE EDGE LAB</h1>

<p align="center">
  <strong>Building automated trading systems for prediction markets — in public, with receipts.</strong>
</p>

<p align="center">
  <em>NBA player props on Kalshi · XGBoost + Platt calibration · Kelly sizing · PostgreSQL</em>
</p>

<p align="center">
  <code>STATUS: LIVE</code> · <code>PHASE 1 of 3</code> · <code>POSITIVE EV ONLY</code>
</p>

---

## What This Is

The Edge Lab is the public log of an automated trading system I'm building for prediction markets — currently focused on NBA player rebounds on Kalshi.

It's not a tutorial. It's not a course. It's a working system, documented as it evolves: every model rebuild, every bug, every losing trade, every uncomfortable lesson.

The repo holds the **story** of the system. The system itself stays private — alpha decays the moment it's published.

**The rules I trade by:**

1. Never bet without an edge.
2. Let the math decide, not the gut.
3. Document everything — including the failures. Especially the failures.

---

## Where The System Stands

| Metric | Value | As of |
|---|---|---|
| Total settled predictions | 1,660 | 2026-04-09 |
| Live capital deployed | $63.76 | 2026-04-09 |
| Current model | XGBoost + Platt scaling | v5_kelly config |
| Active prop type (live) | NBA Rebounds | Real money |
| Active prop types (paper) | Points, Assists | Validation only |
| Database | PostgreSQL (`warmachine`) | Migrated from SQLite |
| Schedule | Daily, NBA-aware cron | Windows Task Scheduler |

Numbers update as the system runs. They are not all flattering. That's the point.

---

## Mission Log

The chronological story of the system. Each entry is a snapshot in time — they are not edited after the fact. When something turns out to be wrong, an errata box goes on top of the original and a full record lives in [`ERRATA.md`](./ERRATA.md).

| Date | Ch | Title | Outcome |
|---|---|---|---|
| 2026-03-28 | 1 | [System Audit & XGBoost Migration](./logs/2026-03-28-system-audit.md) | SQLite → PostgreSQL, logistic → XGBoost, Brier ↓ |
| 2026-04-02 | 2 | [Phase 1 Validation](./logs/2026-04-02-phase1-validation.md) | 999 predictions audited, edge by prop type isolated |
| 2026-04-04 | 3 | [The Price Was Wrong](./logs/2026-04-04-the-price-was-wrong.md) | Entry-price bug exposed, v3 → v4 config rebuild |
| 2026-04-09 | 4 | [Three Days, Four Bugs, One Repo Sanitized](./logs/2026-04-09-three-days-four-bugs.md) | Live transition, zero-padding bug, public IP audit, gap discovery |

---

## Tech Stack

Python 3.11+ · XGBoost · Platt scaling · PostgreSQL · Kalshi API · `nba_api` · Kelly criterion (quarter-fraction)

Code is private. Decisions, results, and lessons are public.

---

## About

Built by **Ikjun Jang** — sports agency director by day, prediction-market quant by night.

🏢 Director @ L&K Agency (player transfers, club sponsorships, sports M&A)  
🎓 Duke MMS:FOB '27 (incoming, July 2026)  
⚽ Arsenal supporter  

> *"The edge is in the work nobody sees. The point of writing it down is to make sure I keep doing it."*

**Started:** March 28, 2026 · **Updated:** as the system runs · **Built in public**
