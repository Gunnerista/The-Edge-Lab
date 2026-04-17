---
name: War Machine Data Flow
description: Signal-to-settlement pipeline. Load when Claude needs to trace how a prediction becomes a trade, or debug where data is getting lost between stages.
---

# Data Flow

## Full pipeline (happy path)

```
[Kalshi REST API]
   │
   │ every 60s
   ▼
market_recorder.RESTRecorder.record_snapshot()
   │
   │ INSERT
   ▼
PostgreSQL warmachine.price_snapshots (+ markets metadata)
   │
   │ query by date + ticker prefix
   ▼
forward_test.get_active_props()              ← 12:00 PM ET cron
   │
   │ for each ticker
   ▼
nba_model.process_kalshi_nba_ticker()
   │  - box score stats from nba_api
   │  - XGBoost → raw prob
   │  - Platt scale (A=0.8976, B=-0.4242) → calibrated prob
   │
   ▼
signal_engine (entry price, edge calc)
   │  - YES: entry = yes_ask
   │  - NO:  entry = 1 - yes_bid
   │  - edge = |model_prob - market_implied|
   │
   ▼
_append_prediction_log() → data/prediction_log.jsonl  ← source="auto"
   │
   │ (trading path, live props only)
   ▼
auto_trader.should_place_bet()
   │  - if prop_type not in LIVE_PROP_TYPES: skip
   │  - min_edge = PROP_CONFIG[prop_type]["MIN_EDGE"]
   │  - if side == "YES": min_edge += YES_EDGE_PREMIUM
   │  - min_edge = max(min_edge, GLOBAL_MIN_EDGE)
   │  - if edge > MAX_EDGE_CAP: skip (adverse selection)
   │  - if edge >= min_edge: PLACE
   │
   ▼
trade_engine.execute_order()           ← kill switch here (line 462)
   │
   │ Kalshi POST order
   ▼
positions.json + trade_log.jsonl       ← ⚠️ [Sync] bug can wipe positions.json
   │
   │ later, 11:59 PM ET
   ▼
settle_predictions.settle()
   │  - nba_api boxscores (status=3 only)
   │  - for each unsettled: compute actual_result
   │  - update prediction_log.jsonl row (settled=True)
   │
   ▼
calibration.py + risk_decomposition.py → Discord summary (10 PM)
```

## Ticker format

- Regex: `KXNBA(PTS|REB|AST|3PT)-YYMONDD-PLAYER-THRESHOLD`
- Day MUST be zero-padded (`04`, not `4`). Non-padded ticker was the root cause of the 2026-04-01→04-05 five-day signal outage.
- Kalshi `expiration_time` is season end, not game day. Always parse the ticker.

## Source tag flow

- `source="auto"` — runner scheduler (12pm + startup catch-up).
- `source="auto-cli"` — manual CLI invocation.
- `source="manual"` — legacy only; Ikjun no longer bets manually (pledge 2026-04-13).
- Calibration uses `auto + auto-cli`. `manual` is excluded.

## Integrity rules

- `prediction_log.jsonl` is append-only. Once written, only `settle_predictions.save_predictions()` may update a row — and only to flip `settled=False → True`.
- A row with `settled=True` is immutable. Rewrite breaks calibration.
- If you must repair, backup first: `cp prediction_log.jsonl prediction_log.jsonl.bak.$(date +%Y%m%d_%H%M%S)`.

## Known data gaps

- `price_snapshots` has no data before 2026-04-14 (earlier data lost). Historical backfill requires Kalshi historical endpoint — not yet implemented.
- `prediction_log.jsonl` has gaps on 4/1, 4/2, 4/6, 4/9–4/13 (7 of 12 days missing predictions during that window).
- `equity_curve.json` stale since 2026-04-07.
- `trade_log.jsonl` lacks `execution_mode` column — LIVE vs PAPER must be inferred from `runner.log` text.

## Schedule dependencies

- `forward_test` requires `market_recorder` to have written at least one `price_snapshots` row for today's tickers.
- `settle_predictions` requires `nba_api` boxscore status=3 (final).
- `self_learner` reads `prediction_log.jsonl` — runs only Sunday 10pm, so fresh settles before Sunday affect next week's params.
