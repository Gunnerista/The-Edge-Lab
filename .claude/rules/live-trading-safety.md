---
paths:
  - "scripts/auto_trader.py"
  - "scripts/trade_engine.py"
---

# Live Trading Safety Rules

These rules load automatically when Claude touches `auto_trader.py` or `trade_engine.py`. Both files gate real money.

## Absolute rules

1. **NEVER** modify `LIVE_PROP_TYPES` without Ikjun's explicit chat approval. Currently `{"rebounds"}`. Adding a prop = capital risk.
2. **NEVER** remove, bypass, or soft-fail the kill switches at `trade_engine.py:462` (execute_order) and `trade_engine.py:556` (exit_position). Both gates are mandatory.
3. **NEVER** increase Kelly fraction beyond 0.25x without chat approval. Current value lives in `bankroll.py`.
4. **NEVER** raise `MAX_EDGE_CAP` above 0.45 without evidence. Edge ≥0.50 produced WR=0% in Phase 2.5 backtest — this is an adverse-selection ceiling, not a cap on good trades.
5. **NEVER** widen `MAX_SPREAD_CENTS` beyond 15 for rebounds. Rebounds markets are structurally illiquid; wider spreads absorb the edge.
6. **NEVER** remove or weaken the `GLOBAL_MIN_EDGE = 0.15` floor. Historical data: edge <15% produced net loss across all prop types after Kalshi fees.
7. **NEVER** disable the YES premium (`YES_EDGE_PREMIUM = 0.05`). YES is structurally disadvantaged in binary markets; premium compensates.
8. **NEVER** call `execute_order` or `exit_position` from test code or CLI tooling without `--dry-run`. Live orders only from `runner.py`.

## Before editing either file

1. `Read` the current file state. Code may have changed.
2. `git log --oneline -10 <file>` to see recent modifications.
3. Check `data/safety_state.json` — if `dd_level != "GREEN"`, STOP and report to Ikjun before any change.
4. If edit touches anything in the list above, use Plan Mode (`/plan`) and get Ikjun chat approval before executing.

## After editing either file

1. Runner restart is required. Confirm both parent and child Python PIDs after restart.
2. Tail `data/runner.log` for 60 seconds. Look for: `[Safety]`, `[Kill]`, `[OrderReject]`, Python tracebacks.
3. First post-edit trade: report to Ikjun with full diff before it clears settlement.
4. If anything looks wrong, kill the runner: `Stop-Process -Id <pid> -Force`. Investigate offline. Do not restart until Ikjun approves.

## Filter chain (`should_place_bet`)

Order matters. Any change to order or logic = Ikjun approval required.

```python
# 1. Prop type gate — PAPER vs LIVE
if prop_type not in LIVE_PROP_TYPES:
    return False  # paper-only prop, skip live

# 2. Prop-specific minimum edge
min_edge = PROP_CONFIG[prop_type]["MIN_EDGE"]

# 3. YES side structural penalty
if side == "YES":
    min_edge += YES_EDGE_PREMIUM  # +0.05

# 4. Global floor
min_edge = max(min_edge, GLOBAL_MIN_EDGE)  # 0.15

# 5. Adverse-selection ceiling
if edge > MAX_EDGE_CAP:  # 0.45
    return False

# 6. Final gate
return edge >= min_edge
```

## Adverse selection warning

If a proposed trade has edge > 0.35, log extra detail to `runner.log` and flag to Discord. Large edges often mean our model is missing information the market has (injury, late scratch, line move). Do not treat high edge as "more profit" — treat as "suspicious."

## Sync bug (known, unresolved)

`auto_trader.py` has a known `[Sync] Removed local position` bug: after a LIVE fill, the sync logic can falsely conclude the position is not on Kalshi and wipe `positions.json`. Consequences:

- `positions.json` cannot be trusted as ground truth
- `exit_position` cannot be called on wiped positions (blocks mid-position liquidation)
- Settlement still works (Kalshi handles it server-side)

**Until fixed**: always verify positions against Kalshi REST API before acting on `positions.json`. The canonical call is `KalshiClient().get_positions()`.

## Kill switch verification ritual

Before any production change to `trade_engine.py`, verify kill switches are wired:

```bash
grep -n "kill_switch_active\|KILL_SWITCH" scripts/trade_engine.py
# Must show references at line 462 and line 556 (or equivalent after edits).
```

If either is missing, the file is unsafe for live orders.
