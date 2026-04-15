#!/usr/bin/env python3
"""
SQLite to PostgreSQL Data Migration
=====================================
Prediction Market War Machine

Reads from SQLite databases and bulk-inserts into PostgreSQL.
Handles the large market_data.db (5.2GB) in chunks for performance.

Source databases:
  - data/market_data.db  (price_snapshots, markets, trades)
  - data/nba_data.db     (nba_players, nba_teams, nba_games, nba_injuries)
  - data/scanner.db      (settled_markets)

Usage:
    python scripts/migrate_data.py                # migrate all tables
    python scripts/migrate_data.py --table markets # migrate single table
    python scripts/migrate_data.py --dry-run       # count rows without migrating
"""

import sys
import sqlite3
import logging
import argparse
import time
from pathlib import Path
from io import StringIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_config import get_connection, release_connection, PG_CONFIG

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

BATCH_SIZE = 10_000

# ============================================================================
# Table migration configs: (sqlite_db, table, columns, pg_columns_override)
# ============================================================================

MIGRATIONS = [
    {
        "name": "markets",
        "sqlite_db": DATA_DIR / "market_data.db",
        "table": "markets",
        "sqlite_cols": [
            "ticker", "event_ticker", "title", "subtitle", "category",
            "status", "close_time", "expiration_time", "result",
            "first_seen", "last_updated",
        ],
        "pg_cols": [
            "ticker", "event_ticker", "title", "subtitle", "category",
            "status", "close_time", "expiration_time", "result",
            "first_seen", "last_updated",
        ],
        "extra_sqlite_cols": [
            "sub_category", "market_type", "liquidity_grade", "tradeable", "classified_at",
        ],
    },
    {
        "name": "price_snapshots",
        "sqlite_db": DATA_DIR / "market_data.db",
        "table": "price_snapshots",
        "sqlite_cols": [
            "timestamp", "ticker", "event_ticker",
            "yes_bid", "yes_ask", "yes_price",
            "no_bid", "no_ask", "volume", "open_interest", "source",
        ],
        "pg_cols": [
            "timestamp", "ticker", "event_ticker",
            "yes_bid", "yes_ask", "yes_price",
            "no_bid", "no_ask", "volume", "open_interest", "source",
        ],
    },
    {
        "name": "trades",
        "sqlite_db": DATA_DIR / "market_data.db",
        "table": "trades",
        "sqlite_cols": [
            "timestamp", "ticker", "yes_price", "count", "taker_side",
        ],
        "pg_cols": [
            "timestamp", "ticker", "yes_price", "count", "taker_side",
        ],
    },
    {
        "name": "nba_players",
        "sqlite_db": DATA_DIR / "nba_data.db",
        "table": "nba_players",
        "sqlite_cols": [
            "player_id", "player_name", "team_abbr", "position", "season",
            "games_played", "minutes_pg", "points_pg", "rebounds_pg", "assists_pg",
            "steals_pg", "blocks_pg", "turnovers_pg",
            "fg_pct", "fg3_pct", "ft_pct", "fga_pg", "fta_pg", "fg3a_pg",
            "plus_minus", "usage_rate",
            "pts_per_min", "reb_per_min", "ast_per_min",
            "recent_pts_avg", "recent_reb_avg", "recent_ast_avg", "recent_min_avg",
            "recent_pts_std", "recent_reb_std", "recent_ast_std",
            "recent5_pts_avg", "recent5_reb_avg", "recent5_ast_avg",
            "home_pts_avg", "away_pts_avg", "home_reb_avg", "away_reb_avg",
            "home_ast_avg", "away_ast_avg",
            "updated_at",
        ],
        "pg_cols": None,  # same as sqlite_cols
    },
    {
        "name": "nba_teams",
        "sqlite_db": DATA_DIR / "nba_data.db",
        "table": "nba_teams",
        "sqlite_cols": [
            "team_id", "team_abbr", "team_name", "season",
            "opp_pts_pg", "opp_reb_pg", "opp_ast_pg",
            "opp_fg_pct", "opp_fg3_pct", "def_rating", "pace",
            "opp_pg_pts", "opp_sg_pts", "opp_sf_pts", "opp_pf_pts", "opp_c_pts",
            "opp_pg_reb", "opp_sg_reb", "opp_sf_reb", "opp_pf_reb", "opp_c_reb",
            "opp_pg_ast", "opp_sg_ast", "opp_sf_ast", "opp_pf_ast", "opp_c_ast",
            "recent_def_rating", "recent_opp_pts_pg", "recent_pace",
            "updated_at",
        ],
        "pg_cols": None,
    },
    {
        "name": "nba_games",
        "sqlite_db": DATA_DIR / "nba_data.db",
        "table": "nba_games",
        "sqlite_cols": [
            "game_id", "game_date", "home_team_id", "away_team_id",
            "home_team_abbr", "away_team_abbr",
            "home_score", "away_score", "status",
            "is_back_to_back_home", "is_back_to_back_away",
            "updated_at",
        ],
        "pg_cols": None,
    },
    {
        "name": "nba_injuries",
        "sqlite_db": DATA_DIR / "nba_data.db",
        "table": "nba_injuries",
        "sqlite_cols": [
            "player_id", "player_name", "team_abbr",
            "status", "injury_detail", "updated_at",
        ],
        "pg_cols": None,
    },
    {
        "name": "settled_markets",
        "sqlite_db": DATA_DIR / "scanner.db",
        "table": "settled_markets",
        "sqlite_cols": [
            "ticker", "event_ticker", "title", "category", "result",
            "last_yes_bid", "last_yes_ask", "last_yes_mid",
            "volume", "close_time", "fetched_at",
        ],
        "pg_cols": None,
    },
]


def _escape_copy_value(val) -> str:
    """Escape a value for PostgreSQL COPY format (tab-separated)."""
    if val is None:
        return "\\N"
    s = str(val)
    s = s.replace("\\", "\\\\")
    s = s.replace("\t", "\\t")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    return s


def migrate_table(config: dict, dry_run: bool = False):
    """Migrate a single table from SQLite to PostgreSQL."""
    name = config["name"]
    sqlite_db = config["sqlite_db"]
    table = config["table"]
    sqlite_cols = list(config["sqlite_cols"])
    extra_cols = config.get("extra_sqlite_cols", [])

    # Include extra columns if they exist in the SQLite table
    all_sqlite_cols = sqlite_cols + extra_cols
    pg_cols = config.get("pg_cols") or sqlite_cols
    all_pg_cols = pg_cols + extra_cols

    if not sqlite_db.exists():
        print(f"  [{name}] SQLite DB not found: {sqlite_db} -- SKIP")
        return 0

    # Check if SQLite table has the extra columns
    sqlite_conn = sqlite3.connect(str(sqlite_db), timeout=30)
    cur = sqlite_conn.cursor()

    # Check which extra columns actually exist in SQLite
    existing_cols = set()
    try:
        pragma = cur.execute(f"PRAGMA table_info({table})").fetchall()
        existing_cols = {row[1] for row in pragma}
    except Exception:
        print(f"  [{name}] Table '{table}' not found in SQLite -- SKIP")
        sqlite_conn.close()
        return 0

    # Filter to only columns that exist
    final_sqlite_cols = [c for c in all_sqlite_cols if c in existing_cols]
    final_pg_cols = [c for c in all_pg_cols if c in existing_cols]

    # Count rows
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    total_rows = cur.fetchone()[0]
    print(f"  [{name}] {total_rows:,} rows to migrate")

    if dry_run or total_rows == 0:
        sqlite_conn.close()
        return total_rows

    # PostgreSQL connection
    pg_conn = get_connection()
    pg_cur = pg_conn.cursor()

    col_list_str = ", ".join(final_sqlite_cols)
    pg_col_list_str = ", ".join(final_pg_cols)

    migrated = 0
    start_time = time.time()

    # Read in batches from SQLite and COPY into PostgreSQL
    offset = 0
    while offset < total_rows:
        rows = cur.execute(
            f"SELECT {col_list_str} FROM {table} LIMIT {BATCH_SIZE} OFFSET {offset}"
        ).fetchall()

        if not rows:
            break

        # Build COPY data
        buf = StringIO()
        for row in rows:
            line = "\t".join(_escape_copy_value(v) for v in row)
            buf.write(line + "\n")

        buf.seek(0)
        try:
            pg_cur.copy_from(buf, table, columns=final_pg_cols, sep="\t", null="\\N")
            pg_conn.commit()
        except Exception as e:
            pg_conn.rollback()
            logger.error(f"  [{name}] COPY failed at offset {offset}: {e}")
            # Fallback: try row-by-row insert for this batch
            placeholders = ", ".join(["%s"] * len(final_pg_cols))
            insert_sql = f"INSERT INTO {table} ({pg_col_list_str}) VALUES ({placeholders})"
            error_count = 0
            for row in rows:
                try:
                    pg_cur.execute(insert_sql, row)
                except Exception as row_err:
                    error_count += 1
                    if error_count <= 3:
                        logger.warning(f"  [{name}] Row insert error: {row_err}")
                    pg_conn.rollback()
                    continue
                pg_conn.commit()
            if error_count > 0:
                print(f"  [{name}] {error_count} row errors in batch at offset {offset}")

        migrated += len(rows)
        offset += BATCH_SIZE

        elapsed = time.time() - start_time
        rate = migrated / elapsed if elapsed > 0 else 0
        pct = migrated / total_rows * 100
        print(f"  [{name}] {migrated:>12,} / {total_rows:,} ({pct:.1f}%) | {rate:.0f} rows/s", end="\r")

    print(f"  [{name}] {migrated:>12,} / {total_rows:,} (100.0%) -- DONE ({time.time() - start_time:.1f}s)")

    release_connection(pg_conn)
    sqlite_conn.close()
    return migrated


def migrate_all(table_filter: str = None, dry_run: bool = False):
    """Migrate all tables (or a specific one) from SQLite to PostgreSQL."""
    print("=" * 60)
    print("SQLite -> PostgreSQL Data Migration")
    print("=" * 60)

    total_migrated = 0
    for config in MIGRATIONS:
        if table_filter and config["name"] != table_filter:
            continue
        count = migrate_table(config, dry_run=dry_run)
        total_migrated += count

    print()
    print(f"Total rows migrated: {total_migrated:,}")
    if dry_run:
        print("(DRY RUN - no data was written)")


def main():
    parser = argparse.ArgumentParser(description="SQLite to PostgreSQL Data Migration")
    parser.add_argument("--table", type=str, help="Migrate specific table only")
    parser.add_argument("--dry-run", action="store_true", help="Count rows without migrating")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    migrate_all(table_filter=args.table, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
