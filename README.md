<p align="center">
  <h1 align="center">Starlizard-Inspired Sports Betting System</h1>
  <p align="center">
    AI-powered sports betting analysis engine for prediction markets
    <br />
    Built with Python — Designed for Kalshi
    <br /><br />
    <img src="https://img.shields.io/badge/python-3.7+-blue?style=flat-square&logo=python&logoColor=white" />
    <img src="https://img.shields.io/badge/platform-Kalshi-orange?style=flat-square" />
    <img src="https://img.shields.io/badge/sports-NBA%20%7C%20MLB%20%7C%20Soccer-green?style=flat-square" />
    <img src="https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square" />
  </p>
</p>

---

## Overview

A comprehensive sports betting analysis framework modeled after [Tony Bloom's Starlizard](https://en.wikipedia.org/wiki/Tony_Bloom) operation — one of the most successful sports betting syndicates in history. This system combines statistical modeling, expected value analysis, and disciplined bankroll management to find value bets on the [Kalshi](https://kalshi.com) prediction market platform.

**Core Philosophy**: Never bet without an edge. Every bet must have positive expected value.

## Architecture

```
sports-betting-system/
├── scripts/
│   ├── analyzer.py              # Core analysis engine (984 lines)
│   ├── bankroll.py              # Bankroll management & Kelly Criterion (652 lines)
│   └── example_trading_session.py
├── skills/
│   ├── betting-consultant/      # AI betting consultant skill
│   │   ├── SKILL.md             # Starlizard methodology & decision framework
│   │   └── references/
│   │       └── formulas.md      # Mathematical formula reference (10 chapters)
│   └── sports-data-scout/       # Data collection skill
│       ├── SKILL.md             # 4-priority data collection protocol
│       └── scripts/
│           └── quick_search.py  # Search query generator
├── dashboards/
│   └── dashboard.html           # Interactive command center (Chart.js)
├── trackers/
│   └── betting_tracker.xlsx     # Excel tracking workbook (4 sheets)
├── analysis/                    # Generated analysis reports (PDF)
├── data/                        # Runtime data storage (JSON)
├── requirements.txt
└── .gitignore
```

## Features

### Analysis Engine (`scripts/analyzer.py`)

The core engine provides odds conversion, EV calculation, and sport-specific analysis models.

```python
from scripts.analyzer import BettingAnalysisEngine, american_to_probability, calculate_ev

# Odds conversion
prob = american_to_probability(-150)  # → 0.60

# Expected Value
ev = calculate_ev(
    probability=0.55,
    decimal_odds=1.909,
    stake=100
)  # → +$4.99

# Full game analysis
engine = BettingAnalysisEngine(bankroll=1000.0)
pick = engine.analyze_nba_matchup("Boston Celtics", "Los Angeles Lakers", odds_a=-110)
```

**Statistical Models**:
- ELO Rating System with home court advantage (+30 points)
- Poisson Distribution for soccer goal prediction (pure math, no scipy)
- Parlay/combo expected value calculator
- MLB pitcher advantage adjustment (ERA-based)

### Bankroll Manager (`scripts/bankroll.py`)

Kelly Criterion-based bet sizing with risk management guardrails.

```python
from scripts.bankroll import BankrollTracker, KellyCriterion

# Optimal bet sizing
kelly = KellyCriterion.calculate_kelly(
    probability=0.55,
    decimal_odds=1.909,
    kelly_fraction=0.25  # Quarter Kelly (conservative)
)

# Track performance
tracker = BankrollTracker(starting_bankroll=5000)
tracker.add_bet(amount=100, win=True, odds=1.909, description="Celtics ML")

summary = tracker.get_summary()
# → { bankroll: { current: 5090.9 }, performance: { win_rate: "100%" }, ... }
```

**Risk Rules** (enforced automatically):
- Max single bet: 5% of bankroll
- Daily loss limit: 10% of bankroll
- Weekly loss limit: 20% of bankroll

### AI Skills

Two Claude-compatible skills provide structured decision frameworks:

**Betting Consultant** (`skills/betting-consultant/SKILL.md`) — 882 lines covering:
- Pre-analysis 8-step checklist
- Sport-specific analysis guides (NBA / MLB / Soccer)
- Kalshi contract pricing strategy
- Daily workflow (9 steps)
- Red flags and common pitfalls

**Sports Data Scout** (`skills/sports-data-scout/SKILL.md`) — 661 lines covering:
- 4-priority data collection protocol
- Free source directory (ESPN, Sports Reference, Covers, etc.)
- Game analysis card templates
- Search query generator (`quick_search.py`)

### Dashboard (`dashboards/dashboard.html`)

Interactive dark-theme command center built with Chart.js. Open in any browser — no server required.

### Excel Tracker (`trackers/betting_tracker.xlsx`)

4-sheet workbook: Bet Log, Weekly Summary, Bankroll Dashboard, Kelly Calculator. Pre-formatted with formulas and conditional formatting.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/yourusername/sports-betting-system.git
cd sports-betting-system

# Install dependencies
pip install -r requirements.txt

# Run example session
python scripts/example_trading_session.py
```

## Mathematical Framework

The system implements these core formulas:

| Formula | Purpose |
|---------|---------|
| `EV = (p × profit) - (q × stake)` | Expected value per bet |
| `f* = (bp - q) / b` | Kelly Criterion optimal fraction |
| `P(k) = (λ^k × e^-λ) / k!` | Poisson goal prediction |
| `P(combo) = P(A) × P(B) × ...` | Independent parlay probability |
| `CLV = (p_close - p_open) / p_open` | Closing line value (edge measure) |

Full mathematical reference with worked examples: [`skills/betting-consultant/references/formulas.md`](skills/betting-consultant/references/formulas.md) (1,094 lines, 10 chapters)

## Methodology

Inspired by the Starlizard approach:

1. **Information Advantage** — Aggregate data from multiple free sources before the market prices it in
2. **Value Betting** — Only bet when your estimated probability > market implied probability
3. **Disciplined Sizing** — Quarter Kelly prevents catastrophic drawdowns
4. **Continuous Tracking** — Every bet logged, weekly review to refine models
5. **Emotional Detachment** — The math decides, not gut feeling

## Tech Stack

- **Python 3.7+** — Core engine (no heavy ML dependencies)
- **Pure Math** — Poisson PMF implemented without scipy
- **Chart.js** — Dashboard visualization
- **openpyxl** — Excel tracker generation
- **reportlab** — PDF analysis report generation
- **requests + BeautifulSoup** — Web scraping for standings data

## Disclaimer

This system is for educational and analytical purposes. Sports betting involves financial risk. Past performance does not guarantee future results. Always bet responsibly and only risk what you can afford to lose.

---

**Created**: March 2026 | **Python**: 3.7+ | **Platform**: Kalshi
