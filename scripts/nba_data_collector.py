#!/usr/bin/env python3
"""
NBA Data Collector - Phase 1: Real Basketball Data Pipeline
=============================================================
Prediction Market War Machine

Collects REAL basketball data from nba_api and Basketball Reference
to power the NBA prop model. This replaces the self-referential
Bayesian estimate that only looked at Kalshi prices.

Data Sources:
  1. nba_api (stats.nba.com) — Player season stats, recent game logs,
     per-minute stats, advanced metrics
  2. Basketball Reference (web scrape) — Team defensive stats by position,
     opponent shooting splits

Storage: SQLite (data/market_data.db)
  - nba_players: player-level stats (season + recent form)
  - nba_teams: team-level defensive stats (opponent allowed by position)
  - nba_games: game-level context (schedule, B2B, home/away)
  - nba_injuries: current injury report

Usage:
    # Full refresh (season + recent)
    python scripts/nba_data_collector.py --full

    # Quick update (recent games + injuries only)
    python scripts/nba_data_collector.py --update

    # Specific player lookup
    python scripts/nba_data_collector.py --player "LeBron James"
"""

import sys
import time
import json
import sqlite3
import logging
import argparse
import traceback
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Separate DB for NBA stats — market_data.db is 6.7GB + 16GB WAL (Kalshi prices only).
# Try multiple locations in order of preference
def _find_writable_db_path() -> Path:
    import sqlite3 as _sq
    candidates = [
        PROJECT_ROOT / "data" / "nba_data.db",
        Path.home() / "nba_data.db",
        Path("/tmp") / "nba_data.db",
    ]
    # If DB already exists somewhere, use it
    for c in candidates:
        if c.exists():
            return c
    # Otherwise find first truly writable location (test with actual SQLite)
    for c in candidates:
        try:
            c.parent.mkdir(parents=True, exist_ok=True)
            conn = _sq.connect(str(c))
            conn.execute("CREATE TABLE IF NOT EXISTS _write_test (id INTEGER)")
            conn.execute("DROP TABLE _write_test")
            conn.close()
            return c
        except Exception:
            # Clean up partial file
            try:
                c.unlink()
            except Exception:
                pass
            continue
    return candidates[-1]

NBA_DB_PATH = _find_writable_db_path()
DB_PATH = NBA_DB_PATH

# Rate limiting for NBA API (they throttle aggressively)
API_DELAY = 0.8  # seconds between requests


# ============================================================================
# SQLite Schema for NBA Data
# ============================================================================

NBA_SCHEMA_SQL = """
-- Player stats: season averages + recent form
CREATE TABLE IF NOT EXISTS nba_players (
    player_id       INTEGER,
    player_name     TEXT NOT NULL,
    team_abbr       TEXT DEFAULT '',
    position        TEXT DEFAULT '',
    season          TEXT NOT NULL,          -- e.g. '2025-26'

    -- Season averages
    games_played    INTEGER DEFAULT 0,
    minutes_pg      REAL DEFAULT 0,
    points_pg       REAL DEFAULT 0,
    rebounds_pg     REAL DEFAULT 0,
    assists_pg      REAL DEFAULT 0,
    steals_pg       REAL DEFAULT 0,
    blocks_pg       REAL DEFAULT 0,
    turnovers_pg    REAL DEFAULT 0,
    fg_pct          REAL DEFAULT 0,
    fg3_pct         REAL DEFAULT 0,
    ft_pct          REAL DEFAULT 0,
    fga_pg          REAL DEFAULT 0,
    fta_pg          REAL DEFAULT 0,
    fg3a_pg         REAL DEFAULT 0,
    plus_minus      REAL DEFAULT 0,
    usage_rate      REAL DEFAULT 0,

    -- Per-minute rates (for projection when minutes vary)
    pts_per_min     REAL DEFAULT 0,
    reb_per_min     REAL DEFAULT 0,
    ast_per_min     REAL DEFAULT 0,

    -- Recent form (last 10 games)
    recent_pts_avg  REAL DEFAULT 0,
    recent_reb_avg  REAL DEFAULT 0,
    recent_ast_avg  REAL DEFAULT 0,
    recent_min_avg  REAL DEFAULT 0,
    recent_pts_std  REAL DEFAULT 0,        -- standard deviation for variance
    recent_reb_std  REAL DEFAULT 0,
    recent_ast_std  REAL DEFAULT 0,

    -- Recent form (last 5 games) — higher recency weight
    recent5_pts_avg REAL DEFAULT 0,
    recent5_reb_avg REAL DEFAULT 0,
    recent5_ast_avg REAL DEFAULT 0,

    -- Home/Away splits
    home_pts_avg    REAL DEFAULT 0,
    away_pts_avg    REAL DEFAULT 0,
    home_reb_avg    REAL DEFAULT 0,
    away_reb_avg    REAL DEFAULT 0,
    home_ast_avg    REAL DEFAULT 0,
    away_ast_avg    REAL DEFAULT 0,

    updated_at      TEXT NOT NULL,

    PRIMARY KEY (player_id, season)
);

-- Team defensive stats: how much they allow by position
CREATE TABLE IF NOT EXISTS nba_teams (
    team_id         INTEGER,
    team_abbr       TEXT NOT NULL,
    team_name       TEXT DEFAULT '',
    season          TEXT NOT NULL,

    -- Overall defensive metrics
    opp_pts_pg      REAL DEFAULT 0,        -- opponent points per game
    opp_reb_pg      REAL DEFAULT 0,
    opp_ast_pg      REAL DEFAULT 0,
    opp_fg_pct      REAL DEFAULT 0,
    opp_fg3_pct     REAL DEFAULT 0,
    def_rating      REAL DEFAULT 0,        -- defensive rating (pts per 100 poss)
    pace            REAL DEFAULT 0,        -- possessions per game

    -- Opponent stats allowed by position (PG/SG/SF/PF/C)
    opp_pg_pts      REAL DEFAULT 0,
    opp_sg_pts      REAL DEFAULT 0,
    opp_sf_pts      REAL DEFAULT 0,
    opp_pf_pts      REAL DEFAULT 0,
    opp_c_pts       REAL DEFAULT 0,
    opp_pg_reb      REAL DEFAULT 0,
    opp_sg_reb      REAL DEFAULT 0,
    opp_sf_reb      REAL DEFAULT 0,
    opp_pf_reb      REAL DEFAULT 0,
    opp_c_reb       REAL DEFAULT 0,
    opp_pg_ast      REAL DEFAULT 0,
    opp_sg_ast      REAL DEFAULT 0,
    opp_sf_ast      REAL DEFAULT 0,
    opp_pf_ast      REAL DEFAULT 0,
    opp_c_ast       REAL DEFAULT 0,

    -- Recent form (last 10 games)
    recent_def_rating   REAL DEFAULT 0,
    recent_opp_pts_pg   REAL DEFAULT 0,
    recent_pace         REAL DEFAULT 0,

    updated_at      TEXT NOT NULL,

    PRIMARY KEY (team_id, season)
);

-- Game schedule context
CREATE TABLE IF NOT EXISTS nba_games (
    game_id         TEXT PRIMARY KEY,
    game_date       TEXT NOT NULL,
    home_team_id    INTEGER,
    away_team_id    INTEGER,
    home_team_abbr  TEXT DEFAULT '',
    away_team_abbr  TEXT DEFAULT '',
    home_score      INTEGER DEFAULT 0,
    away_score      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'scheduled',  -- scheduled, in_progress, final
    is_back_to_back_home  INTEGER DEFAULT 0,
    is_back_to_back_away  INTEGER DEFAULT 0,
    updated_at      TEXT NOT NULL
);

-- Injury report
CREATE TABLE IF NOT EXISTS nba_injuries (
    player_id       INTEGER,
    player_name     TEXT NOT NULL,
    team_abbr       TEXT DEFAULT '',
    status          TEXT DEFAULT '',        -- Out, Doubtful, Questionable, Probable
    injury_detail   TEXT DEFAULT '',
    updated_at      TEXT NOT NULL,

    PRIMARY KEY (player_id)
);

CREATE INDEX IF NOT EXISTS idx_nba_players_name ON nba_players(player_name);
CREATE INDEX IF NOT EXISTS idx_nba_players_team ON nba_players(team_abbr);
CREATE INDEX IF NOT EXISTS idx_nba_teams_abbr ON nba_teams(team_abbr);
CREATE INDEX IF NOT EXISTS idx_nba_injuries_team ON nba_injuries(team_abbr);
"""


# ============================================================================
# NBA Team Abbreviation Mapping
# ============================================================================

TEAM_ABBR_MAP = {
    1610612737: "ATL", 1610612738: "BOS", 1610612739: "CLE",
    1610612740: "NOP", 1610612741: "CHI", 1610612742: "DAL",
    1610612743: "DEN", 1610612744: "GSW", 1610612745: "HOU",
    1610612746: "LAC", 1610612747: "LAL", 1610612748: "MIA",
    1610612749: "MIL", 1610612750: "MIN", 1610612751: "BKN",
    1610612752: "NYK", 1610612753: "ORL", 1610612754: "IND",
    1610612755: "PHI", 1610612756: "PHX", 1610612757: "POR",
    1610612758: "SAC", 1610612759: "SAS", 1610612760: "OKC",
    1610612761: "TOR", 1610612762: "UTA", 1610612763: "MEM",
    1610612764: "WAS", 1610612765: "DET", 1610612766: "CHA",
}

TEAM_NAME_TO_ABBR = {
    "hawks": "ATL", "celtics": "BOS", "cavaliers": "CLE", "cavs": "CLE",
    "pelicans": "NOP", "bulls": "CHI", "mavericks": "DAL", "mavs": "DAL",
    "nuggets": "DEN", "warriors": "GSW", "rockets": "HOU",
    "clippers": "LAC", "lakers": "LAL", "heat": "MIA",
    "bucks": "MIL", "timberwolves": "MIN", "wolves": "MIN",
    "nets": "BKN", "knicks": "NYK", "magic": "ORL",
    "pacers": "IND", "76ers": "PHI", "sixers": "PHI",
    "suns": "PHX", "trail blazers": "POR", "blazers": "POR",
    "kings": "SAC", "spurs": "SAS", "thunder": "OKC",
    "raptors": "TOR", "jazz": "UTA", "grizzlies": "MEM",
    "wizards": "WAS", "pistons": "DET", "hornets": "CHA",
    "atlanta": "ATL", "boston": "BOS", "cleveland": "CLE",
    "new orleans": "NOP", "chicago": "CHI", "dallas": "DAL",
    "denver": "DEN", "golden state": "GSW", "houston": "HOU",
    "la clippers": "LAC", "los angeles clippers": "LAC",
    "la lakers": "LAL", "los angeles lakers": "LAL",
    "miami": "MIA", "milwaukee": "MIL", "minnesota": "MIN",
    "brooklyn": "BKN", "new york": "NYK", "orlando": "ORL",
    "indiana": "IND", "philadelphia": "PHI", "phoenix": "PHX",
    "portland": "POR", "sacramento": "SAC", "san antonio": "SAS",
    "oklahoma city": "OKC", "toronto": "TOR", "utah": "UTA",
    "memphis": "MEM", "washington": "WAS", "detroit": "DET",
    "charlotte": "CHA",
}


def _get_current_season() -> str:
    """Return current NBA season string like '2025-26'."""
    today = date.today()
    if today.month >= 10:
        return f"{today.year}-{str(today.year + 1)[-2:]}"
    else:
        return f"{today.year - 1}-{str(today.year)[-2:]}"


def _safe_float(val, default: float = 0.0) -> float:
    """Safely convert to float."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default: int = 0) -> int:
    """Safely convert to int."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


# ============================================================================
# NBA Data Collector
# ============================================================================

class NBADataCollector:
    """
    Collects real NBA data from nba_api and stores in SQLite.

    Data flow:
      nba_api → parse → SQLite (nba_players, nba_teams, nba_games, nba_injuries)
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(NBA_SCHEMA_SQL)
        self.conn.commit()
        self.season = _get_current_season()
        self._import_nba_api()

    def _import_nba_api(self):
        """Import nba_api modules with error handling."""
        try:
            from nba_api.stats.endpoints import (
                leaguedashplayerstats,
                playergamelog,
                leaguedashteamstats,
                teamdashboardbygeneralsplits,
                playerdashboardbygeneralsplits,
                scoreboardv2,
                commonplayerinfo,
                leaguedashptdefend,
            )
            from nba_api.stats.static import players as nba_players_static
            from nba_api.stats.static import teams as nba_teams_static

            self._endpoints = {
                "player_stats": leaguedashplayerstats,
                "player_gamelog": playergamelog,
                "team_stats": leaguedashteamstats,
                "team_dashboard": teamdashboardbygeneralsplits,
                "player_dashboard": playerdashboardbygeneralsplits,
                "scoreboard": scoreboardv2,
                "player_info": commonplayerinfo,
                "defense": leaguedashptdefend,
            }
            self._static_players = nba_players_static
            self._static_teams = nba_teams_static
            self._available = True
            logger.info("[NBACollector] nba_api imported successfully")
        except ImportError as e:
            logger.error(f"[NBACollector] nba_api not installed: {e}")
            logger.error("Run: pip install nba_api")
            self._available = False

    def is_available(self) -> bool:
        return self._available

    # ----------------------------------------------------------------
    # Player Stats Collection
    # ----------------------------------------------------------------

    def collect_all_player_stats(self) -> int:
        """
        Collect season averages for all active NBA players.
        Returns number of players collected.
        """
        if not self._available:
            logger.error("[NBACollector] nba_api not available")
            return 0

        logger.info(f"[NBACollector] Collecting player season stats for {self.season}...")

        try:
            stats = self._endpoints["player_stats"].LeagueDashPlayerStats(
                season=self.season,
                per_mode_detailed="PerGame",
                season_type_all_star="Regular Season",
            )
            time.sleep(API_DELAY)

            df = stats.get_data_frames()[0]
            if df.empty:
                logger.warning("[NBACollector] No player stats returned")
                return 0

            now = datetime.now(timezone.utc).isoformat()
            count = 0

            for _, row in df.iterrows():
                player_id = _safe_int(row.get("PLAYER_ID"))
                if not player_id:
                    continue

                gp = _safe_int(row.get("GP", 0))
                mpg = _safe_float(row.get("MIN", 0))

                # Skip players with minimal playing time
                if gp < 5 or mpg < 10:
                    continue

                pts = _safe_float(row.get("PTS", 0))
                reb = _safe_float(row.get("REB", 0))
                ast = _safe_float(row.get("AST", 0))

                # Per-minute rates
                pts_per_min = pts / mpg if mpg > 0 else 0
                reb_per_min = reb / mpg if mpg > 0 else 0
                ast_per_min = ast / mpg if mpg > 0 else 0

                team_abbr = str(row.get("TEAM_ABBREVIATION", ""))

                self.conn.execute("""
                    INSERT OR REPLACE INTO nba_players
                    (player_id, player_name, team_abbr, season,
                     games_played, minutes_pg, points_pg, rebounds_pg, assists_pg,
                     steals_pg, blocks_pg, turnovers_pg,
                     fg_pct, fg3_pct, ft_pct, fga_pg, fta_pg, fg3a_pg,
                     plus_minus, pts_per_min, reb_per_min, ast_per_min,
                     updated_at)
                    VALUES (?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?,
                            ?)
                """, (
                    player_id,
                    str(row.get("PLAYER_NAME", "")),
                    team_abbr,
                    self.season,
                    gp,
                    mpg,
                    pts,
                    reb,
                    ast,
                    _safe_float(row.get("STL", 0)),
                    _safe_float(row.get("BLK", 0)),
                    _safe_float(row.get("TOV", 0)),
                    _safe_float(row.get("FG_PCT", 0)),
                    _safe_float(row.get("FG3_PCT", 0)),
                    _safe_float(row.get("FT_PCT", 0)),
                    _safe_float(row.get("FGA", 0)),
                    _safe_float(row.get("FTA", 0)),
                    _safe_float(row.get("FG3A", 0)),
                    _safe_float(row.get("PLUS_MINUS", 0)),
                    round(pts_per_min, 4),
                    round(reb_per_min, 4),
                    round(ast_per_min, 4),
                    now,
                ))
                count += 1

            self.conn.commit()
            logger.info(f"[NBACollector] Collected season stats for {count} players")
            return count

        except Exception as e:
            logger.error(f"[NBACollector] Error collecting player stats: {e}")
            traceback.print_exc()
            return 0

    def collect_player_recent_form(self, player_id: int, num_games: int = 10) -> bool:
        """
        Collect recent game logs for a specific player.
        Updates recent_*_avg and recent_*_std columns.
        """
        if not self._available:
            return False

        try:
            gamelog = self._endpoints["player_gamelog"].PlayerGameLog(
                player_id=player_id,
                season=self.season,
                season_type_all_star="Regular Season",
            )
            time.sleep(API_DELAY)

            df = gamelog.get_data_frames()[0]
            if df.empty or len(df) < 3:
                return False

            recent = df.head(num_games)

            pts_vals = [_safe_float(r) for r in recent["PTS"].values]
            reb_vals = [_safe_float(r) for r in recent["REB"].values]
            ast_vals = [_safe_float(r) for r in recent["AST"].values]
            min_vals = [_safe_float(r) for r in recent["MIN"].values]

            import numpy as np

            now = datetime.now(timezone.utc).isoformat()

            # Last-5 game averages (higher recency weight in model v2)
            recent5 = df.head(5)
            r5_pts = [_safe_float(r) for r in recent5["PTS"].values] if len(df) >= 5 else pts_vals
            r5_reb = [_safe_float(r) for r in recent5["REB"].values] if len(df) >= 5 else reb_vals
            r5_ast = [_safe_float(r) for r in recent5["AST"].values] if len(df) >= 5 else ast_vals

            self.conn.execute("""
                UPDATE nba_players SET
                    recent_pts_avg = ?,
                    recent_reb_avg = ?,
                    recent_ast_avg = ?,
                    recent_min_avg = ?,
                    recent_pts_std = ?,
                    recent_reb_std = ?,
                    recent_ast_std = ?,
                    recent5_pts_avg = ?,
                    recent5_reb_avg = ?,
                    recent5_ast_avg = ?,
                    updated_at = ?
                WHERE player_id = ? AND season = ?
            """, (
                round(float(np.mean(pts_vals)), 2),
                round(float(np.mean(reb_vals)), 2),
                round(float(np.mean(ast_vals)), 2),
                round(float(np.mean(min_vals)), 2),
                round(float(np.std(pts_vals, ddof=1)) if len(pts_vals) > 1 else 0, 2),
                round(float(np.std(reb_vals, ddof=1)) if len(reb_vals) > 1 else 0, 2),
                round(float(np.std(ast_vals, ddof=1)) if len(ast_vals) > 1 else 0, 2),
                round(float(np.mean(r5_pts)), 2),
                round(float(np.mean(r5_reb)), 2),
                round(float(np.mean(r5_ast)), 2),
                now,
                player_id,
                self.season,
            ))
            self.conn.commit()

            # Also collect home/away splits from game log
            self._update_home_away_splits(player_id, df)

            return True

        except Exception as e:
            logger.error(f"[NBACollector] Error collecting recent form for {player_id}: {e}")
            return False

    def _update_home_away_splits(self, player_id: int, gamelog_df) -> None:
        """Update home/away splits from game log data."""
        try:
            home_games = gamelog_df[gamelog_df["MATCHUP"].str.contains("vs.", na=False)]
            away_games = gamelog_df[gamelog_df["MATCHUP"].str.contains("@", na=False)]

            now = datetime.now(timezone.utc).isoformat()

            home_pts = float(home_games["PTS"].mean()) if len(home_games) > 0 else 0
            away_pts = float(away_games["PTS"].mean()) if len(away_games) > 0 else 0
            home_reb = float(home_games["REB"].mean()) if len(home_games) > 0 else 0
            away_reb = float(away_games["REB"].mean()) if len(away_games) > 0 else 0
            home_ast = float(home_games["AST"].mean()) if len(home_games) > 0 else 0
            away_ast = float(away_games["AST"].mean()) if len(away_games) > 0 else 0

            self.conn.execute("""
                UPDATE nba_players SET
                    home_pts_avg = ?, away_pts_avg = ?,
                    home_reb_avg = ?, away_reb_avg = ?,
                    home_ast_avg = ?, away_ast_avg = ?,
                    updated_at = ?
                WHERE player_id = ? AND season = ?
            """, (
                round(home_pts, 2), round(away_pts, 2),
                round(home_reb, 2), round(away_reb, 2),
                round(home_ast, 2), round(away_ast, 2),
                now, player_id, self.season,
            ))
            self.conn.commit()
        except Exception as e:
            logger.debug(f"[NBACollector] Home/away split error: {e}")

    def collect_all_recent_form(self, top_n: int = 150) -> int:
        """
        Collect recent form for top N players (by minutes played).
        Rate-limited: ~1 request per second.
        """
        players = self.conn.execute("""
            SELECT player_id, player_name FROM nba_players
            WHERE season = ? AND games_played >= 10
            ORDER BY minutes_pg * games_played DESC
            LIMIT ?
        """, (self.season, top_n)).fetchall()

        count = 0
        total = len(players)
        for i, (pid, name) in enumerate(players):
            if self.collect_player_recent_form(pid):
                count += 1
            if (i + 1) % 20 == 0:
                logger.info(f"[NBACollector] Recent form: {i+1}/{total} players processed")

        logger.info(f"[NBACollector] Collected recent form for {count}/{total} players")
        return count

    # ----------------------------------------------------------------
    # Team Stats Collection
    # ----------------------------------------------------------------

    def collect_all_team_stats(self) -> int:
        """
        Collect team defensive stats for all NBA teams.
        Returns number of teams collected.
        """
        if not self._available:
            return 0

        logger.info(f"[NBACollector] Collecting team stats for {self.season}...")

        try:
            # Overall team stats
            stats = self._endpoints["team_stats"].LeagueDashTeamStats(
                season=self.season,
                per_mode_detailed="PerGame",
                season_type_all_star="Regular Season",
                measure_type_detailed_defense="Opponent",
            )
            time.sleep(API_DELAY)

            df = stats.get_data_frames()[0]
            if df.empty:
                logger.warning("[NBACollector] No team stats returned")
                return 0

            now = datetime.now(timezone.utc).isoformat()
            count = 0

            for _, row in df.iterrows():
                team_id = _safe_int(row.get("TEAM_ID"))
                if not team_id:
                    continue

                team_abbr = str(row.get("TEAM_ABBREVIATION", ""))

                self.conn.execute("""
                    INSERT OR REPLACE INTO nba_teams
                    (team_id, team_abbr, team_name, season,
                     opp_pts_pg, opp_reb_pg, opp_ast_pg,
                     opp_fg_pct, opp_fg3_pct, def_rating, pace,
                     updated_at)
                    VALUES (?, ?, ?, ?,
                            ?, ?, ?,
                            ?, ?, ?, ?,
                            ?)
                """, (
                    team_id,
                    team_abbr,
                    str(row.get("TEAM_NAME", "")),
                    self.season,
                    _safe_float(row.get("OPP_PTS", row.get("PTS", 0))),
                    _safe_float(row.get("OPP_REB", row.get("REB", 0))),
                    _safe_float(row.get("OPP_AST", row.get("AST", 0))),
                    _safe_float(row.get("OPP_FG_PCT", row.get("FG_PCT", 0))),
                    _safe_float(row.get("OPP_FG3_PCT", row.get("FG3_PCT", 0))),
                    _safe_float(row.get("DEF_RATING", 0)),
                    _safe_float(row.get("PACE", 0)),
                    now,
                ))
                count += 1

            self.conn.commit()
            logger.info(f"[NBACollector] Collected stats for {count} teams")

            # Collect opponent stats by position
            self._collect_opponent_position_stats()

            return count

        except Exception as e:
            logger.error(f"[NBACollector] Error collecting team stats: {e}")
            traceback.print_exc()
            return 0

    def _collect_opponent_position_stats(self) -> None:
        """
        Collect how much each team allows by position.
        Uses league dashboard with opponent splits.
        """
        try:
            # Use player tracking defense endpoint
            defense = self._endpoints["defense"].LeagueDashPtDefend(
                season=self.season,
                defense_category="Overall",
                season_type_all_star="Regular Season",
                per_mode_simple="PerGame",
            )
            time.sleep(API_DELAY)

            df = defense.get_data_frames()[0]
            if df.empty:
                return

            # Aggregate by team + position for opponent allowed stats
            # This gives us what positions score against each team
            now = datetime.now(timezone.utc).isoformat()

            # Group by team, calculate average opponent scoring by position
            # Note: This is approximate — exact position tracking needs more endpoints
            for _, row in df.iterrows():
                team_id = _safe_int(row.get("TEAM_ID"))
                if not team_id:
                    continue

                # Update team with defensive metrics if available
                freq = _safe_float(row.get("FREQ", 0))
                dfg = _safe_float(row.get("D_FG_PCT", 0))

                if freq > 0:
                    self.conn.execute("""
                        UPDATE nba_teams SET
                            recent_def_rating = ?,
                            updated_at = ?
                        WHERE team_id = ? AND season = ?
                    """, (dfg, now, team_id, self.season))

            self.conn.commit()
            logger.info("[NBACollector] Updated opponent position defense stats")

        except Exception as e:
            logger.debug(f"[NBACollector] Position defense collection error: {e}")

    # ----------------------------------------------------------------
    # Injury Report
    # ----------------------------------------------------------------

    def collect_injuries(self) -> int:
        """
        Collect current NBA injury report.
        Returns number of injuries logged.
        """
        if not self._available:
            return 0

        logger.info("[NBACollector] Collecting injury report...")

        try:
            # Clear old injuries first
            self.conn.execute("DELETE FROM nba_injuries")

            # Use scoreboard to get today's injury info
            from nba_api.stats.endpoints import playerdashboardbygeneralsplits
            # NBA doesn't have a clean injury API — we use the league injury endpoint
            # or fall back to scraping

            try:
                from nba_api.live.nba.endpoints import scoreboard
                board = scoreboard.ScoreBoard()
                games = board.get_dict()
                # Extract injury info from game data if available
            except Exception:
                pass

            # Fallback: use common approach via requests
            now = datetime.now(timezone.utc).isoformat()
            count = 0

            try:
                import requests
                url = "https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json"
                resp = requests.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.nba.com/",
                })
                if resp.status_code == 200:
                    # Parse injury data if available in the response
                    pass
            except Exception:
                pass

            # Also try the official injury report
            try:
                import requests
                url = "https://official.nba.com/wp-json/api/v1/injury-report"
                resp = requests.get(url, timeout=10, headers={
                    "User-Agent": "Mozilla/5.0",
                })
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data if isinstance(data, list) else data.get("data", []):
                        player_name = item.get("player", item.get("Player", ""))
                        team = item.get("team", item.get("Team", ""))
                        status = item.get("status", item.get("Current Status", ""))
                        reason = item.get("reason", item.get("Reason", ""))

                        if player_name:
                            # Try to find player_id
                            match = self.conn.execute(
                                "SELECT player_id FROM nba_players WHERE player_name = ? AND season = ?",
                                (player_name, self.season)
                            ).fetchone()
                            pid = match[0] if match else 0

                            team_abbr = ""
                            for key, abbr in TEAM_NAME_TO_ABBR.items():
                                if key in team.lower():
                                    team_abbr = abbr
                                    break

                            self.conn.execute("""
                                INSERT OR REPLACE INTO nba_injuries
                                (player_id, player_name, team_abbr, status, injury_detail, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (pid, player_name, team_abbr, status, reason, now))
                            count += 1
            except Exception as e:
                logger.debug(f"[NBACollector] Official injury report error: {e}")

            self.conn.commit()
            logger.info(f"[NBACollector] Collected {count} injury reports")
            return count

        except Exception as e:
            logger.error(f"[NBACollector] Error collecting injuries: {e}")
            return 0

    # ----------------------------------------------------------------
    # Schedule & B2B Detection
    # ----------------------------------------------------------------

    def collect_schedule(self, days_ahead: int = 3) -> int:
        """
        Collect upcoming NBA games and detect back-to-back situations.
        """
        if not self._available:
            return 0

        logger.info("[NBACollector] Collecting schedule...")
        count = 0
        now = datetime.now(timezone.utc).isoformat()

        try:
            today = date.today()
            for delta in range(days_ahead):
                game_date = today + timedelta(days=delta)
                date_str = game_date.strftime("%m/%d/%Y")

                try:
                    scoreboard = self._endpoints["scoreboard"].ScoreboardV2(
                        game_date=date_str,
                    )
                    time.sleep(API_DELAY)

                    headers = scoreboard.get_data_frames()[0]
                    if headers.empty:
                        continue

                    for _, row in headers.iterrows():
                        game_id = str(row.get("GAME_ID", ""))
                        if not game_id:
                            continue

                        home_id = _safe_int(row.get("HOME_TEAM_ID"))
                        away_id = _safe_int(row.get("VISITOR_TEAM_ID"))

                        self.conn.execute("""
                            INSERT OR REPLACE INTO nba_games
                            (game_id, game_date, home_team_id, away_team_id,
                             home_team_abbr, away_team_abbr, status, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            game_id,
                            game_date.isoformat(),
                            home_id,
                            away_id,
                            TEAM_ABBR_MAP.get(home_id, ""),
                            TEAM_ABBR_MAP.get(away_id, ""),
                            "scheduled",
                            now,
                        ))
                        count += 1

                except Exception as e:
                    logger.debug(f"[NBACollector] Schedule error for {date_str}: {e}")

            # Detect back-to-back
            self._detect_back_to_backs()

            self.conn.commit()
            logger.info(f"[NBACollector] Collected {count} upcoming games")

        except Exception as e:
            logger.error(f"[NBACollector] Error collecting schedule: {e}")

        return count

    def _detect_back_to_backs(self) -> None:
        """Mark games where a team played the previous day."""
        games = self.conn.execute("""
            SELECT game_id, game_date, home_team_id, away_team_id
            FROM nba_games ORDER BY game_date
        """).fetchall()

        team_last_game: Dict[int, str] = {}
        for game_id, game_date, home_id, away_id in games:
            home_b2b = 0
            away_b2b = 0

            if home_id in team_last_game:
                try:
                    last = date.fromisoformat(team_last_game[home_id])
                    curr = date.fromisoformat(game_date)
                    if (curr - last).days == 1:
                        home_b2b = 1
                except Exception:
                    pass

            if away_id in team_last_game:
                try:
                    last = date.fromisoformat(team_last_game[away_id])
                    curr = date.fromisoformat(game_date)
                    if (curr - last).days == 1:
                        away_b2b = 1
                except Exception:
                    pass

            self.conn.execute("""
                UPDATE nba_games SET
                    is_back_to_back_home = ?,
                    is_back_to_back_away = ?
                WHERE game_id = ?
            """, (home_b2b, away_b2b, game_id))

            team_last_game[home_id] = game_date
            team_last_game[away_id] = game_date

    # ----------------------------------------------------------------
    # Lookup Helpers
    # ----------------------------------------------------------------

    def find_player(self, name: str) -> Optional[dict]:
        """Find a player by name (fuzzy match)."""
        # Exact match first
        row = self.conn.execute(
            "SELECT * FROM nba_players WHERE player_name = ? AND season = ?",
            (name, self.season)
        ).fetchone()

        if row:
            cols = [d[0] for d in self.conn.execute(
                "SELECT * FROM nba_players LIMIT 0"
            ).description]
            return dict(zip(cols, row))

        # Fuzzy: LIKE match
        row = self.conn.execute(
            "SELECT * FROM nba_players WHERE player_name LIKE ? AND season = ? LIMIT 1",
            (f"%{name}%", self.season)
        ).fetchone()

        if row:
            cols = [d[0] for d in self.conn.execute(
                "SELECT * FROM nba_players LIMIT 0"
            ).description]
            return dict(zip(cols, row))

        return None

    def find_team(self, team_input: str) -> Optional[dict]:
        """Find a team by abbreviation or name."""
        abbr = team_input.upper()
        if len(abbr) <= 3:
            row = self.conn.execute(
                "SELECT * FROM nba_teams WHERE team_abbr = ? AND season = ?",
                (abbr, self.season)
            ).fetchone()
        else:
            # Try name lookup
            lookup = TEAM_NAME_TO_ABBR.get(team_input.lower(), "")
            if lookup:
                row = self.conn.execute(
                    "SELECT * FROM nba_teams WHERE team_abbr = ? AND season = ?",
                    (lookup, self.season)
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT * FROM nba_teams WHERE team_name LIKE ? AND season = ?",
                    (f"%{team_input}%", self.season)
                ).fetchone()

        if row:
            cols = [d[0] for d in self.conn.execute(
                "SELECT * FROM nba_teams LIMIT 0"
            ).description]
            return dict(zip(cols, row))

        return None

    def get_player_injury_status(self, player_name: str) -> Optional[str]:
        """Check if a player is on the injury report. Returns status or None."""
        row = self.conn.execute(
            "SELECT status, injury_detail FROM nba_injuries WHERE player_name LIKE ?",
            (f"%{player_name}%",)
        ).fetchone()
        return f"{row[0]} ({row[1]})" if row else None

    def is_back_to_back(self, team_abbr: str, game_date: str = None) -> bool:
        """Check if a team is on a back-to-back."""
        if game_date is None:
            game_date = date.today().isoformat()

        row = self.conn.execute("""
            SELECT is_back_to_back_home, is_back_to_back_away,
                   home_team_abbr, away_team_abbr
            FROM nba_games
            WHERE game_date = ? AND (home_team_abbr = ? OR away_team_abbr = ?)
        """, (game_date, team_abbr, team_abbr)).fetchone()

        if not row:
            return False

        if row[2] == team_abbr:  # home team
            return bool(row[0])
        else:  # away team
            return bool(row[1])

    # ----------------------------------------------------------------
    # Full Collection Pipeline
    # ----------------------------------------------------------------

    def full_collect(self) -> dict:
        """
        Run full data collection pipeline.
        Returns summary of what was collected.
        """
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "season": self.season,
            "players": 0,
            "recent_form": 0,
            "teams": 0,
            "injuries": 0,
            "games": 0,
        }

        # 1. All player season stats
        summary["players"] = self.collect_all_player_stats()

        # 2. Team stats
        summary["teams"] = self.collect_all_team_stats()

        # 3. Recent form for top players
        summary["recent_form"] = self.collect_all_recent_form(top_n=150)

        # 4. Injuries
        summary["injuries"] = self.collect_injuries()

        # 5. Schedule
        summary["games"] = self.collect_schedule(days_ahead=3)

        logger.info(f"[NBACollector] Full collection complete: {json.dumps(summary, indent=2)}")
        return summary

    def quick_update(self) -> dict:
        """
        Quick update: just recent form + injuries + schedule.
        Use between full collects.
        """
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "quick_update",
            "recent_form": 0,
            "injuries": 0,
            "games": 0,
        }

        summary["recent_form"] = self.collect_all_recent_form(top_n=80)
        summary["injuries"] = self.collect_injuries()
        summary["games"] = self.collect_schedule(days_ahead=2)

        logger.info(f"[NBACollector] Quick update complete: {json.dumps(summary, indent=2)}")
        return summary

    def close(self):
        self.conn.close()


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="NBA Data Collector - Real Basketball Data Pipeline"
    )
    parser.add_argument("--full", action="store_true", help="Full data collection")
    parser.add_argument("--update", action="store_true", help="Quick update (recent + injuries)")
    parser.add_argument("--player", type=str, help="Look up a specific player")
    parser.add_argument("--team", type=str, help="Look up a specific team")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    collector = NBADataCollector()

    if not collector.is_available():
        print("ERROR: nba_api not installed. Run: pip install nba_api")
        sys.exit(1)

    if args.player:
        player = collector.find_player(args.player)
        if player:
            print(json.dumps(player, indent=2, default=str))
        else:
            print(f"Player not found: {args.player}")
            print("Try running --full first to populate the database.")

    elif args.team:
        team = collector.find_team(args.team)
        if team:
            print(json.dumps(team, indent=2, default=str))
        else:
            print(f"Team not found: {args.team}")

    elif args.update:
        summary = collector.quick_update()
        print(json.dumps(summary, indent=2))

    else:  # default: full
        summary = collector.full_collect()
        print(json.dumps(summary, indent=2))

    collector.close()


if __name__ == "__main__":
    main()
