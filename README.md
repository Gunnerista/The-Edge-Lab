<h1 align="center">THE EDGE LAB</h1>

<p align="center">
  <strong>One man's quest to beat the market with math, code, and an unreasonable amount of caffeine.</strong>
</p>

<p align="center">
  <a href="#mission">Mission</a> •
  <a href="#projects">Projects</a> •
  <a href="#mission-log">Mission Log</a> •
  <a href="#tech-stack">Tech Stack</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-FORWARD_TESTING-yellow?style=flat-square" alt="Status"/>
  <img src="https://img.shields.io/badge/phase-1_of_3-blue?style=flat-square" alt="Phase"/>
  <img src="https://img.shields.io/badge/edge-positive_EV_only-green?style=flat-square" alt="Edge"/>
</p>

---

## Mission

I'm building systems that find asymmetric opportunities in prediction markets and sports — not through gut feeling, but through statistical edge, disciplined sizing, and relentless iteration.

This repo is the flight recorder. Every model built, every backtest run, every lesson learned — logged here in real time.

**The rules are simple:**
1. Never bet without an edge.
2. Let the math decide, not emotion.
3. Document everything. The process *is* the product.

---

## Projects

### 🎯 EDGE — Automated Betting System
> *Prediction market trading engine targeting Kalshi*

| Milestone | Status |
|---|---|
| NBA probability model (XGBoost) | ✅ Brier 0.1681 (999 predictions) |
| Fee-adjusted ROI positive @ edge >20% | ✅ +13.0% ROI (forward test) |
| Forward test automation (daily cron) | ✅ Running (NBA schedule-aware) |
| Prop-type differential strategy | ✅ Assists +17.2% ROI identified |
| PostgreSQL migration | ✅ 18.5M rows migrated |
| 100-prediction v3 config validation | 🔄 In progress |
| Live transition ($300) | ⏳ Pending v3 validation |

**Stack**: Python · XGBoost · Kalshi API · Kelly Criterion
**Key insight**: The model doesn't need to be perfect. It needs to be *less wrong* than the market, after fees.

### 🔗 NEXUS — AI Player-Club Matching Engine
> *L&K Agency internal tool — matches player needs with club needs*

| Milestone | Status |
|---|---|
| Architecture design | 🔄 In progress |
| Data pipeline | ⏳ Planned |
| Matching algorithm | ⏳ Planned |

### 📊 EDGE Dashboard
> *Real-time analytics command center*

Kelly Criterion bankroll management, pick analysis, parlay recommendations.
Built as a standalone HTML dashboard — no server required.

---

## Mission Log

> *"Space is big. You just won't believe how vastly, hugely, mind-bogglingly big it is."*
> — Douglas Adams, on markets probably

| Date | Entry | Tags |
|---|---|---|
| [2026-03-28](logs/2026-03-28.md) | Day 1: System audit complete. Swapped logistic regression → XGBoost. Backtest passed. Forward test automated. The journey begins. | `betting-system` `model` `milestone` |
| [2026-04-02](logs/2026-04-02-phase1-validation.md) | Phase 1 Validation: 999 predictions settled. Brier 0.1681 ✅. Discovered assists market edge (+17.2% ROI). Killed the Kalshi expiration bug. Migrated to PostgreSQL. Rebuilt calibration. System works — when pointed at the right targets. | `validation` `calibration` `infrastructure` `milestone` |
| [2026-04-04](logs/2026-04-04-the-price-was-wrong.md) | The Price Was Wrong: Entry price bug fix, NO betting activation, v3 156-bet diagnosis, v4 config rebuild | `bug-fix` `entry-price` `no-betting` `v3-diagnosis` `v4-config` `edge-threshold` |

📁 **[Browse all logs →](logs/)**

---

## Tech Stack

```
Languages    Python 3.11+ · JavaScript · SQL
ML/Stats     XGBoost · scikit-learn · Kelly Criterion · Brier Score
Data         NBA API · Kalshi API · ESPN · Sports Reference
Infra        GitHub Actions (cron) · Claude Code
Frontend     React · Chart.js · Tailwind
Philosophy   Positive EV or GTFO
```

---

## How to Read This Repo

**If you're here for the code** → Check [`projects/`](projects/) for each system's source
**If you're here for the story** → Start with the [Mission Log](#mission-log) and read chronologically
**If you're here because you also want to beat the market** → Welcome. Read the logs. Learn from my mistakes.

---

## About

Built by **Ikjun Jang** — sports agency director by day, quantitative degenerate by night.
Currently building AI systems at the intersection of sports, finance, and prediction markets.

- 🏢 Director @ [L&K Agency](https://lnkagency.com) (player transfers, club sponsorships, M&A)
- 🎓 Duke MMS:FOB '27 (starting July 2026)
- ⚽ Arsenal fan (yes, this is relevant to the betting models)

---

<p align="center">
  <sub>Started: March 28, 2026 · Updated daily(ish) · Built in public</sub>
</p>
