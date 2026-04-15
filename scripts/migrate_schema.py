#!/usr/bin/env python3
"""
PostgreSQL Schema Migration
=============================
Prediction Market War Machine

Creates all tables in PostgreSQL matching the current SQLite schema,
with PostgreSQL-appropriate types and indexes.

Source SQLite databases:
  - market_data.db: price_snapshots, markets, trades
  - nba_data.db: nba_players, nba_teams, nba_games, nba_injuries
  - scanner.db: settled_markets

Usage:
    python scripts/migrate_schema.py              # create all tables
    python scripts/migrate_schema.py --drop       # drop and recreate
    python scripts/migrate_schema.py --verify     # verify tables exist
"""

import sys
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db_config import get_connection, release_connection

logger = logging.getLogger(__name__)

# ============================================================================
# PostgreSQL Schema DDL
# ============================================================================

SCHEMA_SQL = """
-- =========================================================================
-- From market_data.db
-- =========================================================================

-- Price snapshots: every observation of a market's price
CREATE TABLE IF NOT EXISTS price_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    ticker          VARCHAR(200) NOT NULL,
    event_ticker    VARCHAR(200) DEFAULT '',
    yes_bid         DOUBLE PRECISION DEFAULT 0,
    yes_ask         DOUBLE PRECISION DEFAULT 0,
    yes_price       DOUBLE PRECISION DEFAULT 0,
    no_bid          DOUBLE PRECISION DEFAULT 0,
    no_ask          DOUBLE PRECISION DEFAULT 0,
    volume          INTEGER DEFAULT 0,
    open_interest   INTEGER DEFAULT 0,
    source          VARCHAR(20) DEFAULT 'rest'
);

-- Market metadata: static info about each market
CREATE TABLE IF NOT EXISTS markets (
    ticker          VARCHAR(200) PRIMARY KEY,
    event_ticker    VARCHAR(200) DEFAULT '',
    title           TEXT DEFAULT '',
    subtitle        TEXT DEFAULT '',
    category        VARCHAR(100) DEFAULT '',
    status          VARCHAR(50) DEFAULT 'open',
    close_time      VARCHAR(50) DEFAULT '',
    expiration_time VARCHAR(50) DEFAULT '',
    result          VARCHAR(10) DEFAULT '',
    first_seen      TIMESTAMPTZ NOT NULL,
    last_updated    TIMESTAMPTZ NOT NULL,
    -- Classification columns (from market_classifier.py)
    sub_category    VARCHAR(100) DEFAULT '',
    market_type     VARCHAR(100) DEFAULT '',
    liquidity_grade VARCHAR(5) DEFAULT '',
    tradeable       INTEGER DEFAULT 0,
    classified_at   VARCHAR(50) DEFAULT ''
);

-- Trade records: individual fills (from WebSocket trade channel)
CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    ticker          VARCHAR(200) NOT NULL,
    yes_price       DOUBLE PRECISION DEFAULT 0,
    count           INTEGER DEFAULT 0,
    taker_side      VARCHAR(10) DEFAULT ''
);

-- =========================================================================
-- From nba_data.db
-- =========================================================================

-- Player stats: season averages + recent form
CREATE TABLE IF NOT EXISTS nba_players (
    player_id       INTEGER NOT NULL,
    player_name     VARCHAR(200) NOT NULL,
    team_abbr       VARCHAR(10) DEFAULT '',
    position        VARCHAR(10) DEFAULT '',
    season          VARCHAR(10) NOT NULL,

    -- Season averages
    games_played    INTEGER DEFAULT 0,
    minutes_pg      DOUBLE PRECISION DEFAULT 0,
    points_pg       DOUBLE PRECISION DEFAULT 0,
    rebounds_pg     DOUBLE PRECISION DEFAULT 0,
    assists_pg      DOUBLE PRECISION DEFAULT 0,
    steals_pg       DOUBLE PRECISION DEFAULT 0,
    blocks_pg       DOUBLE PRECISION DEFAULT 0,
    turnovers_pg    DOUBLE PRECISION DEFAULT 0,
    fg_pct          DOUBLE PRECISION DEFAULT 0,
    fg3_pct         DOUBLE PRECISION DEFAULT 0,
    ft_pct          DOUBLE PRECISION DEFAULT 0,
    fga_pg          DOUBLE PRECISION DEFAULT 0,
    fta_pg          DOUBLE PRECISION DEFAULT 0,
    fg3a_pg         DOUBLE PRECISION DEFAULT 0,
    plus_minus      DOUBLE PRECISION DEFAULT 0,
    usage_rate      DOUBLE PRECISION DEFAULT 0,

    -- Per-minute rates
    pts_per_min     DOUBLE PRECISION DEFAULT 0,
    reb_per_min     DOUBLE PRECISION DEFAULT 0,
    ast_per_min     DOUBLE PRECISION DEFAULT 0,

    -- Recent form (last 10 games)
    recent_pts_avg  DOUBLE PRECISION DEFAULT 0,
    recent_reb_avg  DOUBLE PRECISION DEFAULT 0,
    recent_ast_avg  DOUBLE PRECISION DEFAULT 0,
    recent_min_avg  DOUBLE PRECISION DEFAULT 0,
    recent_pts_std  DOUBLE PRECISION DEFAULT 0,
    recent_reb_std  DOUBLE PRECISION DEFAULT 0,
    recent_ast_std  DOUBLE PRECISION DEFAULT 0,

    -- Recent form (last 5 games)
    recent5_pts_avg DOUBLE PRECISION DEFAULT 0,
    recent5_reb_avg DOUBLE PRECISION DEFAULT 0,
    recent5_ast_avg DOUBLE PRECISION DEFAULT 0,

    -- Home/Away splits
    home_pts_avg    DOUBLE PRECISION DEFAULT 0,
    away_pts_avg    DOUBLE PRECISION DEFAULT 0,
    home_reb_avg    DOUBLE PRECISION DEFAULT 0,
    away_reb_avg    DOUBLE PRECISION DEFAULT 0,
    home_ast_avg    DOUBLE PRECISION DEFAULT 0,
    away_ast_avg    DOUBLE PRECISION DEFAULT 0,

    updated_at      TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (player_id, season)
);

-- Team defensive stats
CREATE TABLE IF NOT EXISTS nba_teams (
    team_id         INTEGER NOT NULL,
    team_abbr       VARCHAR(10) NOT NULL,
    team_name       VARCHAR(100) DEFAULT '',
    season          VARCHAR(10) NOT NULL,

    opp_pts_pg      DOUBLE PRECISION DEFAULT 0,
    opp_reb_pg      DOUBLE PRECISION DEFAULT 0,
    opp_ast_pg      DOUBLE PRECISION DEFAULT 0,
    opp_fg_pct      DOUBLE PRECISION DEFAULT 0,
    opp_fg3_pct     DOUBLE PRECISION DEFAULT 0,
    def_rating      DOUBLE PRECISION DEFAULT 0,
    pace            DOUBLE PRECISION DEFAULT 0,

    opp_pg_pts      DOUBLE PRECISION DEFAULT 0,
    opp_sg_pts      DOUBLE PRECISION DEFAULT 0,
    opp_sf_pts      DOUBLE PRECISION DEFAULT 0,
    opp_pf_pts      DOUBLE PRECISION DEFAULT 0,
    opp_c_pts       DOUBLE PRECISION DEFAULT 0,
    opp_pg_reb      DOUBLE PRECISION DEFAULT 0,
    opp_sg_reb      DOUBLE PRECISION DEFAULT 0,
    opp_sf_reb      DOUBLE PRECISION DEFAULT 0,
    opp_pf_reb      DOUBLE PRECISION DEFAULT 0,
    opp_c_reb       DOUBLE PRECISION DEFAULT 0,
    opp_pg_ast      DOUBLE PRECISION DEFAULT 0,
    opp_sg_ast      DOUBLE PRECISION DEFAULT 0,
    opp_sf_ast      DOUBLE PRECISION DEFAULT 0,
    opp_pf_ast      DOUBLE PRECISION DEFAULT 0,
    opp_c_ast       DOUBLE PRECISION DEFAULT 0,

    recent_def_rating   DOUBLE PRECISION DEFAULT 0,
    recent_opp_pts_pg   DOUBLE PRECISION DEFAULT 0,
    recent_pace         DOUBLE PRECISION DEFAULT 0,

    updated_at      TIMESTAMPTZ NOT NULL,

    PRIMARY KEY (team_id, season)
);

-- Game schedule context
CREATE TABLE IF NOT EXISTS nba_games (
    game_id             VARCHAR(50) PRIMARY KEY,
    game_date           VARCHAR(20) NOT NULL,
    home_team_id        INTEGER,
    away_team_id        INTEGER,
    home_team_abbr      VARCHAR(10) DEFAULT '',
    away_team_abbr      VARCHAR(10) DEFAULT '',
    home_score          INTEGER DEFAULT 0,
    away_score          INTEGER DEFAULT 0,
    status              VARCHAR(20) DEFAULT 'scheduled',
    is_back_to_back_home  INTEGER DEFAULT 0,
    is_back_to_back_away  INTEGER DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL
);

-- Injury report
CREATE TABLE IF NOT EXISTS nba_injuries (
    player_id       INTEGER PRIMARY KEY,
    player_name     VARCHAR(200) NOT NULL,
    team_abbr       VARCHAR(10) DEFAULT '',
    status          VARCHAR(30) DEFAULT '',
    injury_detail   TEXT DEFAULT '',
    updated_at      TIMESTAMPTZ NOT NULL
);

-- =========================================================================
-- From scanner.db
-- =========================================================================

-- Settled markets (history miner)
CREATE TABLE IF NOT EXISTS settled_markets (
    ticker          VARCHAR(200) PRIMARY KEY,
    event_ticker    VARCHAR(200) DEFAULT '',
    title           TEXT DEFAULT '',
    category        VARCHAR(100) DEFAULT '',
    result          VARCHAR(10) DEFAULT '',
    last_yes_bid    DOUBLE PRECISION DEFAULT 0,
    last_yes_ask    DOUBLE PRECISION DEFAULT 0,
    last_yes_mid    DOUBLE PRECISION DEFAULT 0,
    volume          DOUBLE PRECISION DEFAULT 0,
    close_time      VARCHAR(50) DEFAULT '',
    fetched_at      VARCHAR(50) DEFAULT ''
);
"""

# ============================================================================
# Indexes (separate from table creation for clarity)
# ============================================================================

INDEX_SQL = """
-- price_snapshots indexes
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON price_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON price_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_time ON price_snapshots(ticker, timestamp);

-- trades indexes
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(timestamp);

-- markets indexes
CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_status ON markets(status);

-- nba_players indexes
CREATE INDEX IF NOT EXISTS idx_nba_players_name ON nba_players(player_name);
CREATE INDEX IF NOT EXISTS idx_nba_players_team ON nba_players(team_abbr);

-- nba_teams indexes
CREATE INDEX IF NOT EXISTS idx_nba_teams_abbr ON nba_teams(team_abbr);

-- nba_injuries indexes
CREATE INDEX IF NOT EXISTS idx_nba_injuries_team ON nba_injuries(team_abbr);

-- settled_markets indexes
CREATE INDEX IF NOT EXISTS idx_settled_result ON settled_markets(result);
CREATE INDEX IF NOT EXISTS idx_settled_volume ON settled_markets(volume);
"""

# Tables to drop (in dependency order) for --drop mode
ALL_TABLES = [
    "price_snapshots",
    "trades",
    "markets",
    "nba_players",
    "nba_teams",
    "nba_games",
    "nba_injuries",
    "settled_markets",
]


def create_schema(drop_first: bool = False):
    """Create all PostgreSQL tables and indexes."""
    conn = get_connection()
    try:
        cur = conn.cursor()

        if drop_first:
            print("Dropping existing tables...")
            for table in ALL_TABLES:
                cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            conn.commit()
            print("Tables dropped.")

        print("Creating tables...")
        cur.execute(SCHEMA_SQL)
        conn.commit()

        print("Creating indexes...")
        cur.execute(INDEX_SQL)
        conn.commit()

        print("Schema creation complete.")

    finally:
        release_connection(conn)


def verify_schema():
    """List all tables and their row counts."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cur.fetchall()]

        if not tables:
            print("No tables found in 'public' schema.")
            return

        print(f"\nFound {len(tables)} tables in 'warmachine' database:")
        print("-" * 50)
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            print(f"  {table:30s}  {count:>12,} rows")
        print("-" * 50)

    finally:
        release_connection(conn)


def main():
    parser = argparse.ArgumentParser(description="PostgreSQL Schema Migration")
    parser.add_argument("--drop", action="store_true", help="Drop and recreate all tables")
    parser.add_argument("--verify", action="store_true", help="Verify tables and show row counts")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.verify:
        verify_schema()
    else:
        create_schema(drop_first=args.drop)
        verify_schema()


if __name__ == "__main__":
    main()
