---
name: War Machine Research Backlog
description: Phase B+ integration candidates, external data sources, and API upgrade paths. Load when planning new features, data enrichment, or system upgrades.
---

# Research Backlog

## External system integration candidates

### 1. dylanpersonguy/Fully-Autonomous-Polymarket-AI-Trading-Bot
- **What**: Open-source Polymarket bot with 15+ risk checks, whale tracking, 9-tab dashboard
- **Why**: Reference architecture for risk management. Their multi-tab dashboard design could replace our Discord-only reporting.
- **Integration path**: Study their risk check taxonomy, adapt relevant checks to Kalshi context.
- **URL**: `github.com/dylanpersonguy/Fully-Autonomous-Polymarket-AI-Trading-Bot`

### 2. Kalshi social sentiment bot
- **What**: Twitter/Reddit/Telegram 5-dimension NLP sentiment scoring
- **Why**: Pre-game sentiment shifts correlate with line moves. Could add a sentiment feature to `nba_model.py`.
- **Integration path**: Build scraper → NLP pipeline → feature column in `nba_players` or new `sentiment_scores` table.
- **Risk**: Scraping ToS compliance, rate limits, signal decay speed.

### 3. mxufc29/nbainjuries
- **What**: NBA official injury report scraper, 15-minute snapshot intervals
- **Why**: Injury status is our `_assess_confidence()` input. Current data is from `nba_api` which lags official reports by hours.
- **Integration path**: Cron job → `nba_injuries` table update → `nba_model.py` reads at prediction time.
- **URL**: `github.com/mxufc29/nbainjuries`

### 4. BALLDONTLIE MCP
- **What**: MCP server wrapping the BallDontLie NBA API
- **Why**: Alternative to `nba_api` for boxscores. `nba_api` is fragile (frequent schema changes, rate limits).
- **Integration path**: Add as MCP connector, use for boxscore fallback in `settle_predictions.py`.

### 5. Fantasy Nerds API
- **What**: Free tier: injury reports + daily lineups
- **Why**: Lineup confirmation before tipoff is critical for prop accuracy. A player sitting out = model predicts on wrong assumption.
- **Integration path**: Pre-game check in `forward_test.py` → skip props for OUT/GTD players.
- **Cost**: Free tier sufficient for daily use.

### 6. Kalshi WebSocket orderbook depth
- **What**: `wss://trading-api/v1/ws` with L2 orderbook data
- **Why**: Current `kalshi_ws.py` uses Basic tier. Advanced tier provides full depth → better spread estimation → improved `transaction_cost_model`.
- **Integration path**: Upgrade tier (see below), modify `kalshi_ws.py` to subscribe to orderbook channel.

### 7. Polymarket GraphQL cross-reference
- **What**: Polymarket's GraphQL API for similar NBA prop markets
- **Why**: Cross-market price comparison. If Kalshi and Polymarket disagree on a prop, the disagreement itself is an edge signal.
- **Integration path**: New `cross_market` detector in `signal_engine.py`. Requires Polymarket API key.

### 8. Reddit JSON endpoint
- **What**: Reddit's `.json` URL suffix for subreddit scraping (no API key needed)
- **Why**: r/nba game threads, injury discussion, lineup speculation
- **Integration path**: `requests.get("https://reddit.com/r/nba/new.json", headers={"User-Agent": "WarMachine/1.0 by /u/Gunnerista"})`. Rate limit: 2-second interval minimum.
- **Risk**: Reddit may block without proper `User-Agent`.

## Kalshi API tier upgrade

| Tier | Current | Limits | How to upgrade |
|------|---------|--------|----------------|
| Basic | ✅ Active | 20 read/s, 10 write/s, basic WS | — |
| Advanced | Pending | Higher limits, full orderbook WS, faster fills | Apply at `kalshi.com/account/profile` → API section |

**Target**: Upgrade to Advanced before Phase C (June 2026). Required for in-game trading where orderbook depth and execution speed matter.

## Phase C timeline (June 2026)

- **Goal**: In-game live trading for NBA 2026-27 season
- **Prerequisites**:
  1. Advanced API tier approved
  2. 50+ LIVE trades with positive ROI (current: ~7 LIVE)
  3. Sync bug fixed (`positions.json` wipe)
  4. Assists validated to 50 settled trades (Phase B target)
  5. WebSocket orderbook integration tested in paper mode
- **Start date**: After NBA Playoffs end (~June 2026)
- **Target**: Ready for 2026-27 regular season (October)

## Priority ranking

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| P1 | Fantasy Nerds lineup check | 1 day | High (prevents OUT-player predictions) |
| P2 | nbainjuries scraper | 2 days | High (faster injury data) |
| P3 | Kalshi Advanced tier | 0 days (application) | Medium (better fills) |
| P4 | Reddit sentiment | 3 days | Medium (new feature) |
| P5 | Polymarket cross-ref | 3 days | Medium (edge discovery) |
| P6 | BALLDONTLIE MCP | 1 day | Low (redundancy) |
| P7 | Full dashboard | 5 days | Low (visibility only) |
