---
paths:
  - "shared/db.py"
  - "scripts/db.py"
---

# Database Schema Rules

These rules load when Claude touches the database connection layer. All PostgreSQL access flows through `shared/db.py`.

## Architecture

- **Single source of truth**: `shared/db.py` — real connection pool
- **Shim**: `scripts/db.py` — re-exports from `shared.db` via `from shared.db import *`
- **NEVER** add connection logic to `scripts/db.py`. It is a redirect only.
- **NEVER** create a second connection pool. One `ThreadedConnectionPool` per process.

## Connection config

```python
# shared/db.py
_pool = pool.ThreadedConnectionPool(
    minconn=2,
    maxconn=10,
    host="localhost",
    port=5432,
    dbname="warmachine",
    user="postgres",
    password=os.environ.get("WARMACHINE_PG_PASSWORD", ""),
    options="-c statement_timeout=120000",  # 2 min query timeout
)
```

- Password externalized via `WARMACHINE_PG_PASSWORD` env var (set in `.env`)
- **NEVER** hardcode the password. Health check (`tools/health_check.py`) scans for this.

## Connection protocol

```python
from db import get_connection, put_connection

conn = get_connection()          # or get_connection(dict_cursor=True)
try:
    cur = conn.cursor()
    cur.execute("SELECT ...")
    conn.commit()
finally:
    put_connection(conn)         # ALWAYS return to pool
```

**IMPORTANT**: `put_connection()` calls `conn.rollback()` before returning to pool. Uncommitted changes are lost. Always `conn.commit()` before `put_connection()`.

## Tables — NBA

| Table | Rows | Purpose |
|-------|------|---------|
| `markets` | ~11M | Kalshi market metadata (all categories) |
| `price_snapshots` | ~660K | 60s price snapshots (**4/14+ only — earlier data lost**) |
| `nba_players` | 445 | Current season player stats |
| `nba_teams` | 30 | Team metadata |
| `nba_games` | 23 | Game schedule (status field often stale) |
| `nba_injuries` | varies | Injury reports |
| `trades` | 0 | Empty — real trades in `trade_log.jsonl` |
| `settled_markets` | 0 | Empty — settlement via `prediction_log.jsonl` |

## Tables — MLB

| Table | Rows | Purpose |
|-------|------|---------|
| `mlb_games` | 0 | Schedule (import pending) |
| `mlb_batter_gamelogs` | ~103K | Historical batter stats |
| `mlb_pitcher_gamelogs` | ~42K | Historical pitcher stats |
| `mlb_players` | varies | Player metadata |
| `mlb_teams` | 30 | Team metadata |
| `mlb_kalshi_markets` | varies | MLB Kalshi market tickers |
| `mlb_kalshi_prices` | varies | MLB price snapshots |
| `mlb_predictions` | 8 | Paper predictions |
| `mlb_trades` | 9 | Paper trades |

## Known pitfalls

1. **price_snapshots gap**: No data before 2026-04-14. The SQLite→PG migration (3/29) did not carry over historical snapshots. Backfill requires Kalshi historical API endpoint.
2. **SQLite legacy**: `data/market_data.db` was archived to `_cleanup_archive_20260415/`. **NEVER** reference it in new code.
3. **`nba_games.status`**: Often shows `"scheduled"` even for completed games. Do not rely on this for settlement — use `nba_api` live boxscores instead.
4. **`trades` and `settled_markets` tables are empty** by design. The system uses `.jsonl` files for trade records, not database tables. Don't try to query them expecting data.

## Before editing db.py

1. Verify both `scripts/db.py` (shim) and `shared/db.py` (real) — know which you're editing.
2. **NEVER** edit `scripts/db.py` to add real logic. If you need new DB functions, add them to `shared/db.py`.
3. After any pool config change, restart both NBA and MLB runners (they each hold their own pool).
4. Test with: `python -c "from shared.db import get_connection, put_connection; c=get_connection(); c.cursor().execute('SELECT 1'); put_connection(c); print('OK')"`
