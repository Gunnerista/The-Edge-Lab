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

Storage: PostgreSQL (warmachine DB)
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
import logging
import argparse
import traceback
import unicodedata
import re
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from psycopg2.extras import RealDictCursor
from db import get_connection, put_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Rate limiting for NBA API (they throttle aggressively)
API_DELAY = 0.8  # seconds between requests

# ============================================================================
# Injury fallback sources (ESPN JSON / HTML / cache)
# ============================================================================

ESPN_INJURY_JSON_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
ESPN_INJURY_HTML_URL = "https://www.espn.com/nba/injuries"
INJURY_CACHE_FILE = PROJECT_ROOT / "data" / "injury_cache.json"
INJURY_CACHE_MAX_AGE_HOURS = 24
HTTP_TIMEOUT_SEC = 5
HTTP_RETRIES = 2


def _normalize_player_name(name: str) -> str:
    """Unicode NFKD fold to ASCII for cross-source matching.

    'Nikola Vučević' → 'nikola vucevic'
    "De'Aaron Fox"   → 'deaaron fox'
    """
    if not name:
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_bytes = nfkd.encode("ascii", "ignore").decode("ascii")
    # lowercase, remove apostrophes/periods, collapse whitespace
    s = re.sub(r"[.'`]", "", ascii_bytes).lower().strip()
    return re.sub(r"\s+", " ", s)


def _normalize_injury_status(raw: str) -> str:
    """Map various status strings to a small canonical set."""
    if not raw:
        return "Unknown"
    s = raw.strip().lower()
    if s in ("out", "out (g)", "o"):
        return "Out"
    if "day" in s or s in ("dtd", "d"):
        return "Day-To-Day"
    if "probable" in s or s == "p":
        return "Probable"
    if "question" in s or s in ("gtd", "q"):
        return "Questionable"
    if "doubt" in s:
        return "Doubtful"
    return raw.strip()[:50] or "Unknown"


def _http_get_with_retry(url: str, headers: dict = None, timeout: int = HTTP_TIMEOUT_SEC,
                         retries: int = HTTP_RETRIES):
    """GET with retry. Returns requests.Response or None on all-failure."""
    import requests
    hdrs = headers or {"User-Agent": "Mozilla/5.0"}
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=hdrs, timeout=timeout)
            if r.status_code == 200:
                return r
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = str(e)
        if attempt < retries:
            time.sleep(0.5 * attempt)
    logger.debug(f"[Injury] GET {url} failed after {retries} attempts: {last_err}")
    return None


def _fetch_espn_json_injuries() -> List[dict]:
    """Fetch ESPN JSON injury API. Returns list of normalized entries."""
    r = _http_get_with_retry(ESPN_INJURY_JSON_URL)
    if not r:
        return []
    try:
        data = r.json()
    except Exception as e:
        logger.debug(f"[Injury] ESPN JSON parse failed: {e}")
        return []

    out = []
    for team_block in data.get("injuries", []):
        team_name = team_block.get("displayName", "")
        if not team_name:
            team_obj = team_block.get("team", {})
            team_name = team_obj.get("displayName", "") if isinstance(team_obj, dict) else ""
        for p in team_block.get("injuries", []):
            athlete = p.get("athlete", {})
            pname = athlete.get("displayName", "") if isinstance(athlete, dict) else ""
            if not pname:
                continue
            details = p.get("details") or {}
            out.append({
                "player_name": pname,
                "team_name": team_name,
                "status": _normalize_injury_status(p.get("status", "")),
                "body_part": details.get("type", "") if isinstance(details, dict) else "",
                "detail": details.get("detail", "") if isinstance(details, dict) else "",
                "return_date": details.get("returnDate", "") if isinstance(details, dict) else "",
            })
    return out


def _fetch_espn_html_injuries() -> List[dict]:
    """Scrape ESPN injuries HTML page. Fallback for when JSON API fails."""
    r = _http_get_with_retry(ESPN_INJURY_HTML_URL)
    if not r:
        return []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("[Injury] bs4 not installed, HTML fallback unavailable")
        return []

    try:
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        logger.debug(f"[Injury] ESPN HTML parse failed: {e}")
        return []

    out = []
    # ESPN structures: each team has a section with a Table__Title + Table
    for section in soup.select(".Table__Title, .ResponsiveTable"):
        team_name_el = section.select_one(".injuries__teamName, .Table__Title")
        team_name = team_name_el.get_text(strip=True) if team_name_el else ""
        table = section.select_one("table") or section.find_next("table")
        if not table:
            continue
        for row in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) < 4:
                continue
            pname = cells[0]
            if not pname:
                continue
            out.append({
                "player_name": pname,
                "team_name": team_name,
                "status": _normalize_injury_status(cells[3] if len(cells) > 3 else ""),
                "body_part": "",
                "detail": cells[4] if len(cells) > 4 else "",
                "return_date": cells[2] if len(cells) > 2 else "",
            })
    return out


def _load_injury_cache() -> Tuple[List[dict], float]:
    """Load cached injuries. Returns (entries, age_hours). ([], inf) if missing/invalid."""
    if not INJURY_CACHE_FILE.exists():
        return [], float("inf")
    try:
        with open(INJURY_CACHE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        ts_str = d.get("cached_at", "")
        entries = d.get("entries", [])
        ts = datetime.fromisoformat(ts_str) if ts_str else None
        if ts is None:
            return entries, float("inf")
        age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        return entries, age_h
    except Exception as e:
        logger.debug(f"[Injury] Cache read failed: {e}")
        return [], float("inf")


def _save_injury_cache(entries: List[dict], source: str):
    """Persist successful fetch to data/injury_cache.json."""
    try:
        INJURY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "count": len(entries),
            "entries": entries,
        }
        with open(INJURY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[Injury] Cache write failed: {e}")


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
    Collects real NBA data from nba_api and stores in PostgreSQL.

    Data flow:
      nba_api → parse → PostgreSQL (nba_players, nba_teams, nba_games, nba_injuries)
    """

    def __init__(self):
        self.conn = get_connection()
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
            cur = self.conn.cursor()

            for _, row in df.iterrows():
                player_id = _safe_int(row.get("PLAYER_ID"))
                if not player_id:
                    continue

                gp = _safe_int(row.get("GP", 0))
                mpg = _safe_float(row.get("MIN", 0))

                if gp < 5 or mpg < 10:
                    continue

                pts = _safe_float(row.get("PTS", 0))
                reb = _safe_float(row.get("REB", 0))
                ast = _safe_float(row.get("AST", 0))

                pts_per_min = pts / mpg if mpg > 0 else 0
                reb_per_min = reb / mpg if mpg > 0 else 0
                ast_per_min = ast / mpg if mpg > 0 else 0

                team_abbr = str(row.get("TEAM_ABBREVIATION", ""))

                cur.execute("""
                    INSERT INTO nba_players
                    (player_id, player_name, team_abbr, season,
                     games_played, minutes_pg, points_pg, rebounds_pg, assists_pg,
                     steals_pg, blocks_pg, turnovers_pg,
                     fg_pct, fg3_pct, ft_pct, fga_pg, fta_pg, fg3a_pg,
                     plus_minus, pts_per_min, reb_per_min, ast_per_min,
                     updated_at)
                    VALUES (%s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s)
                    ON CONFLICT (player_id, season) DO UPDATE SET
                        player_name = EXCLUDED.player_name,
                        team_abbr = EXCLUDED.team_abbr,
                        games_played = EXCLUDED.games_played,
                        minutes_pg = EXCLUDED.minutes_pg,
                        points_pg = EXCLUDED.points_pg,
                        rebounds_pg = EXCLUDED.rebounds_pg,
                        assists_pg = EXCLUDED.assists_pg,
                        steals_pg = EXCLUDED.steals_pg,
                        blocks_pg = EXCLUDED.blocks_pg,
                        turnovers_pg = EXCLUDED.turnovers_pg,
                        fg_pct = EXCLUDED.fg_pct,
                        fg3_pct = EXCLUDED.fg3_pct,
                        ft_pct = EXCLUDED.ft_pct,
                        fga_pg = EXCLUDED.fga_pg,
                        fta_pg = EXCLUDED.fta_pg,
                        fg3a_pg = EXCLUDED.fg3a_pg,
                        plus_minus = EXCLUDED.plus_minus,
                        pts_per_min = EXCLUDED.pts_per_min,
                        reb_per_min = EXCLUDED.reb_per_min,
                        ast_per_min = EXCLUDED.ast_per_min,
                        updated_at = EXCLUDED.updated_at
                """, (
                    player_id,
                    str(row.get("PLAYER_NAME", "")),
                    team_abbr,
                    self.season,
                    gp, mpg, pts, reb, ast,
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

            recent5 = df.head(5)
            r5_pts = [_safe_float(r) for r in recent5["PTS"].values] if len(df) >= 5 else pts_vals
            r5_reb = [_safe_float(r) for r in recent5["REB"].values] if len(df) >= 5 else reb_vals
            r5_ast = [_safe_float(r) for r in recent5["AST"].values] if len(df) >= 5 else ast_vals

            cur = self.conn.cursor()
            cur.execute("""
                UPDATE nba_players SET
                    recent_pts_avg = %s,
                    recent_reb_avg = %s,
                    recent_ast_avg = %s,
                    recent_min_avg = %s,
                    recent_pts_std = %s,
                    recent_reb_std = %s,
                    recent_ast_std = %s,
                    recent5_pts_avg = %s,
                    recent5_reb_avg = %s,
                    recent5_ast_avg = %s,
                    updated_at = %s
                WHERE player_id = %s AND season = %s
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

            cur = self.conn.cursor()
            cur.execute("""
                UPDATE nba_players SET
                    home_pts_avg = %s, away_pts_avg = %s,
                    home_reb_avg = %s, away_reb_avg = %s,
                    home_ast_avg = %s, away_ast_avg = %s,
                    updated_at = %s
                WHERE player_id = %s AND season = %s
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

        Two passes:
          1. First pass: top N by minutes_pg × games_played DESC (existing behavior)
          2. Guaranteed pass: for each team playing in next 3 days (nba_games),
             take top 8 HEALTHY (not Out/DTD/Questionable) by minutes_pg and
             fetch R5 if missing. Covers injured-returning stars (low GP,
             high MIN) that fall below healthy bench players in the first sort.
        """
        cur = self.conn.cursor()
        cur.execute("""
            SELECT player_id, player_name FROM nba_players
            WHERE season = %s AND games_played >= 10
            ORDER BY minutes_pg * games_played DESC
            LIMIT %s
        """, (self.season, top_n))
        players = cur.fetchall()
        first_pass_ids = set(p[0] for p in players)

        count = 0
        total = len(players)
        for i, (pid, name) in enumerate(players):
            if self.collect_player_recent_form(pid):
                count += 1
            if (i + 1) % 20 == 0:
                logger.info(f"[NBACollector] Recent form: {i+1}/{total} players processed")

        logger.info(f"[NBACollector] Collected recent form for {count}/{total} players (first pass)")

        # Second pass: Play-In / Playoff team healthy stars (graceful degradation)
        try:
            patched = self._collect_playoff_team_guaranteed(first_pass_ids)
            if patched > 0:
                count += patched
                logger.info(f"[NBACollector] Guaranteed pass added {patched} players")
        except Exception as e:
            logger.warning(
                f"[NBACollector] Guaranteed pass failed (first pass preserved): {e}"
            )

        return count

    def _collect_playoff_team_guaranteed(self, already_done_ids: set) -> int:
        """Backfill R5 for Play-In/Playoff team healthy stars.

        Selection:
          1. Teams playing in the next 3 days (from nba_games)
          2. Per team: top 8 by minutes_pg DESC (NO games_played weight)
          3. Skip: already in first_pass, injured (Out/DTD/Questionable/Doubtful),
             R5 already populated
          4. Rate-limit-aware retry (2s backoff, 1 retry on 429-like failure)
        """
        cur = self.conn.cursor()

        # STEP A: teams with games in next 3 days
        cur.execute("""
            SELECT DISTINCT team_abbr
            FROM (
                SELECT home_team_abbr AS team_abbr FROM nba_games
                WHERE game_date::date >= CURRENT_DATE
                  AND game_date::date <= CURRENT_DATE + INTERVAL '3 days'
                UNION
                SELECT away_team_abbr FROM nba_games
                WHERE game_date::date >= CURRENT_DATE
                  AND game_date::date <= CURRENT_DATE + INTERVAL '3 days'
            ) t
            WHERE team_abbr IS NOT NULL AND team_abbr != ''
        """)
        teams = [r[0] for r in cur.fetchall()]
        if not teams:
            logger.info("[guaranteed_patch] No upcoming games in next 3 days — skip")
            return 0
        logger.info(f"[guaranteed_patch] Upcoming teams ({len(teams)}): {sorted(teams)}")

        # STEP B: injury set (unicode-normalized names) — exclude these
        cur.execute("SELECT player_name, status FROM nba_injuries")
        INJURY_KEYS = ("out", "doubtful", "day-to-day", "day to day", "dtd", "questionable")
        injured_names = set()
        for row in cur.fetchall():
            pname, status = row
            if not pname or not status:
                continue
            if any(k in status.lower() for k in INJURY_KEYS):
                injured_names.add(_normalize_player_name(pname))

        # STEP C + D: per-team top 8 healthy, R5 missing only
        patched = []
        skipped = {"first_pass": 0, "r5_present": 0, "injured": 0}

        for team in teams:
            cur.execute("""
                SELECT player_id, player_name, minutes_pg, games_played,
                       COALESCE(recent5_pts_avg, 0),
                       COALESCE(recent5_reb_avg, 0),
                       COALESCE(recent5_ast_avg, 0)
                FROM nba_players
                WHERE season = %s AND team_abbr = %s AND games_played >= 3
                ORDER BY minutes_pg DESC
                LIMIT 16
            """, (self.season, team))
            candidates = cur.fetchall()

            added_for_team = 0
            for pid, name, mpg, gp, r5_p, r5_r, r5_a in candidates:
                if added_for_team >= 8:
                    break

                if pid in already_done_ids:
                    skipped["first_pass"] += 1
                    added_for_team += 1  # counts toward team coverage
                    continue

                if _normalize_player_name(name) in injured_names:
                    skipped["injured"] += 1
                    continue

                if (r5_p or 0) > 0 or (r5_r or 0) > 0 or (r5_a or 0) > 0:
                    skipped["r5_present"] += 1
                    added_for_team += 1
                    continue

                # Fetch R5 with 1-retry rate-limit backoff
                ok = False
                for attempt in (1, 2):
                    try:
                        ok = self.collect_player_recent_form(pid)
                        if ok:
                            break
                    except Exception as e:
                        if attempt == 1 and "429" in str(e):
                            logger.debug(f"[guaranteed_patch] 429 for {name}, backoff 2s")
                            time.sleep(2.0)
                            continue
                        logger.debug(f"[guaranteed_patch] {name} failed: {e}")
                        break

                if ok:
                    # Log before/after by re-reading the row
                    cur.execute("""
                        SELECT recent5_pts_avg, recent5_reb_avg, recent5_ast_avg
                        FROM nba_players WHERE player_id = %s
                    """, (pid,))
                    after = cur.fetchone() or (0, 0, 0)
                    logger.info(
                        f"[guaranteed_patch] + {name} ({team}) "
                        f"R5: pts {r5_p:.1f}->{after[0] or 0:.1f} | "
                        f"reb {r5_r:.1f}->{after[1] or 0:.1f} | "
                        f"ast {r5_a:.1f}->{after[2] or 0:.1f}"
                    )
                    patched.append(name)
                    added_for_team += 1

        logger.info(
            f"[guaranteed_patch] Patched {len(patched)} players | "
            f"skipped: first_pass={skipped['first_pass']}, "
            f"r5_present={skipped['r5_present']}, injured={skipped['injured']}"
        )
        if patched:
            logger.info(f"[guaranteed_patch] Names: {patched}")
        return len(patched)

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
            cur = self.conn.cursor()

            for _, row in df.iterrows():
                team_id = _safe_int(row.get("TEAM_ID"))
                if not team_id:
                    continue

                team_abbr = str(row.get("TEAM_ABBREVIATION", ""))

                cur.execute("""
                    INSERT INTO nba_teams
                    (team_id, team_abbr, team_name, season,
                     opp_pts_pg, opp_reb_pg, opp_ast_pg,
                     opp_fg_pct, opp_fg3_pct, def_rating, pace,
                     updated_at)
                    VALUES (%s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s,
                            %s)
                    ON CONFLICT (team_id, season) DO UPDATE SET
                        team_abbr = EXCLUDED.team_abbr,
                        team_name = EXCLUDED.team_name,
                        opp_pts_pg = EXCLUDED.opp_pts_pg,
                        opp_reb_pg = EXCLUDED.opp_reb_pg,
                        opp_ast_pg = EXCLUDED.opp_ast_pg,
                        opp_fg_pct = EXCLUDED.opp_fg_pct,
                        opp_fg3_pct = EXCLUDED.opp_fg3_pct,
                        def_rating = EXCLUDED.def_rating,
                        pace = EXCLUDED.pace,
                        updated_at = EXCLUDED.updated_at
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

            now = datetime.now(timezone.utc).isoformat()
            cur = self.conn.cursor()

            for _, row in df.iterrows():
                team_id = _safe_int(row.get("TEAM_ID"))
                if not team_id:
                    continue

                freq = _safe_float(row.get("FREQ", 0))
                dfg = _safe_float(row.get("D_FG_PCT", 0))

                if freq > 0:
                    cur.execute("""
                        UPDATE nba_teams SET
                            recent_def_rating = %s,
                            updated_at = %s
                        WHERE team_id = %s AND season = %s
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
        Collect current NBA injury report with 3-tier fallback:
          1. official.nba.com JSON API
          2. ESPN: JSON API → HTML scrape
          3. Last successful cache (if < 24h old)

        Returns number of injuries logged.
        """
        if not self._available:
            return 0

        logger.info("[NBACollector] Collecting injury report...")

        source = "none"
        entries = []  # list of {player_name, team_name, status, body_part, detail, return_date}

        # ----- Tier 1: Official NBA -----
        try:
            official = self._fetch_nba_official_injuries()
            if official:
                entries = official
                source = "nba_official"
                logger.info(f"[Injury] nba_official: {len(entries)} entries")
        except Exception as e:
            logger.debug(f"[Injury] nba_official failed: {e}")

        # ----- Tier 2a: ESPN JSON -----
        if not entries:
            try:
                espn_json = _fetch_espn_json_injuries()
                if espn_json:
                    entries = espn_json
                    source = "espn_json"
                    logger.info(f"[Injury] espn_json: {len(entries)} entries")
            except Exception as e:
                logger.debug(f"[Injury] espn_json failed: {e}")

        # ----- Tier 2b: ESPN HTML -----
        if not entries:
            try:
                espn_html = _fetch_espn_html_injuries()
                if espn_html:
                    entries = espn_html
                    source = "espn_html"
                    logger.info(f"[Injury] espn_html: {len(entries)} entries")
            except Exception as e:
                logger.debug(f"[Injury] espn_html failed: {e}")

        # ----- Tier 3: Cache (24h) -----
        if not entries:
            cached, age_h = _load_injury_cache()
            if cached and age_h <= INJURY_CACHE_MAX_AGE_HOURS:
                entries = cached
                source = "cache"
                logger.warning(f"[Injury] Using cache ({age_h:.1f}h old, {len(entries)} entries)")
            elif cached:
                logger.warning(f"[Injury] Cache too stale ({age_h:.1f}h > {INJURY_CACHE_MAX_AGE_HOURS}h), ignoring")

        if not entries:
            logger.warning("[Injury] All sources failed (nba_official/espn_json/espn_html/cache)")
            return 0

        # ----- Write to DB -----
        try:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM nba_injuries")

            now = datetime.now(timezone.utc).isoformat()
            count = 0
            synthetic_id = -1  # negative IDs for players not in nba_players

            # Build ASCII-fold → player_id map from nba_players for robust matching
            cur.execute("SELECT player_id, player_name FROM nba_players WHERE season = %s", (self.season,))
            name_to_pid = {}
            for pid, pname in cur.fetchall():
                name_to_pid[_normalize_player_name(pname)] = pid

            for e in entries:
                pname = e.get("player_name", "")
                if not pname:
                    continue

                # Unicode-safe player_id lookup
                normalized = _normalize_player_name(pname)
                pid = name_to_pid.get(normalized)
                if pid is None:
                    pid = synthetic_id
                    synthetic_id -= 1  # unique negative id for each unmatched

                # Team abbreviation resolution
                team_lower = (e.get("team_name", "") or "").lower()
                team_abbr = ""
                for key, abbr in TEAM_NAME_TO_ABBR.items():
                    if key in team_lower:
                        team_abbr = abbr
                        break

                # Compose injury_detail: body_part + detail + return_date + source tag
                parts = []
                if e.get("body_part"):
                    parts.append(e["body_part"])
                if e.get("detail"):
                    parts.append(e["detail"])
                if e.get("return_date"):
                    parts.append(f"return={e['return_date']}")
                parts.append(f"[src={source}]")
                injury_detail = " | ".join(parts)

                try:
                    cur.execute("""
                        INSERT INTO nba_injuries
                        (player_id, player_name, team_abbr, status, injury_detail, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (player_id) DO UPDATE SET
                            player_name = EXCLUDED.player_name,
                            team_abbr = EXCLUDED.team_abbr,
                            status = EXCLUDED.status,
                            injury_detail = EXCLUDED.injury_detail,
                            updated_at = EXCLUDED.updated_at
                    """, (pid, pname, team_abbr, e.get("status", "Unknown"), injury_detail, now))
                    count += 1
                except Exception as ie:
                    logger.debug(f"[Injury] DB insert failed for {pname}: {ie}")

            self.conn.commit()

            # Persist successful fetch to cache (not re-cache if we pulled from cache)
            if source != "cache":
                _save_injury_cache(entries, source)

            logger.info(f"[NBACollector] Collected {count} injury reports (source={source})")
            return count

        except Exception as e:
            logger.error(f"[NBACollector] Error writing injuries: {e}")
            try:
                self.conn.rollback()
            except Exception:
                pass
            return 0

    def _fetch_nba_official_injuries(self) -> List[dict]:
        """Existing official.nba.com source (wrapped to match new entry format)."""
        url = "https://official.nba.com/wp-json/api/v1/injury-report"
        r = _http_get_with_retry(url)
        if not r:
            return []
        try:
            data = r.json()
        except Exception:
            return []
        out = []
        for item in (data if isinstance(data, list) else data.get("data", [])):
            pname = item.get("player", item.get("Player", ""))
            if not pname:
                continue
            out.append({
                "player_name": pname,
                "team_name": item.get("team", item.get("Team", "")) or "",
                "status": _normalize_injury_status(item.get("status", item.get("Current Status", ""))),
                "body_part": "",
                "detail": item.get("reason", item.get("Reason", "")) or "",
                "return_date": "",
            })
        return out

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
        cur = self.conn.cursor()

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

                        cur.execute("""
                            INSERT INTO nba_games
                            (game_id, game_date, home_team_id, away_team_id,
                             home_team_abbr, away_team_abbr, status, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (game_id) DO UPDATE SET
                                home_team_id = EXCLUDED.home_team_id,
                                away_team_id = EXCLUDED.away_team_id,
                                home_team_abbr = EXCLUDED.home_team_abbr,
                                away_team_abbr = EXCLUDED.away_team_abbr,
                                status = EXCLUDED.status,
                                updated_at = EXCLUDED.updated_at
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

            self._detect_back_to_backs()

            self.conn.commit()
            logger.info(f"[NBACollector] Collected {count} upcoming games")

        except Exception as e:
            logger.error(f"[NBACollector] Error collecting schedule: {e}")

        return count

    def _detect_back_to_backs(self) -> None:
        """Mark games where a team played the previous day."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT game_id, game_date, home_team_id, away_team_id
            FROM nba_games ORDER BY game_date
        """)
        games = cur.fetchall()

        team_last_game: Dict[int, str] = {}
        for game_id, game_date, home_id, away_id in games:
            home_b2b = 0
            away_b2b = 0

            game_date_str = game_date if isinstance(game_date, str) else game_date.isoformat()

            if home_id in team_last_game:
                try:
                    last = date.fromisoformat(team_last_game[home_id])
                    curr = date.fromisoformat(game_date_str[:10])
                    if (curr - last).days == 1:
                        home_b2b = 1
                except Exception:
                    pass

            if away_id in team_last_game:
                try:
                    last = date.fromisoformat(team_last_game[away_id])
                    curr = date.fromisoformat(game_date_str[:10])
                    if (curr - last).days == 1:
                        away_b2b = 1
                except Exception:
                    pass

            cur.execute("""
                UPDATE nba_games SET
                    is_back_to_back_home = %s,
                    is_back_to_back_away = %s
                WHERE game_id = %s
            """, (home_b2b, away_b2b, game_id))

            team_last_game[home_id] = game_date_str[:10]
            team_last_game[away_id] = game_date_str[:10]

    # ----------------------------------------------------------------
    # Lookup Helpers
    # ----------------------------------------------------------------

    def find_player(self, name: str) -> Optional[dict]:
        """Find a player by name (fuzzy match)."""
        cur = self.conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            "SELECT * FROM nba_players WHERE player_name = %s AND season = %s",
            (name, self.season)
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        cur.execute(
            "SELECT * FROM nba_players WHERE player_name ILIKE %s AND season = %s LIMIT 1",
            (f"%{name}%", self.season)
        )
        row = cur.fetchone()
        if row:
            return dict(row)

        return None

    def find_team(self, team_input: str) -> Optional[dict]:
        """Find a team by abbreviation or name."""
        cur = self.conn.cursor(cursor_factory=RealDictCursor)
        abbr = team_input.upper()

        if len(abbr) <= 3:
            cur.execute(
                "SELECT * FROM nba_teams WHERE team_abbr = %s AND season = %s",
                (abbr, self.season)
            )
        else:
            lookup = TEAM_NAME_TO_ABBR.get(team_input.lower(), "")
            if lookup:
                cur.execute(
                    "SELECT * FROM nba_teams WHERE team_abbr = %s AND season = %s",
                    (lookup, self.season)
                )
            else:
                cur.execute(
                    "SELECT * FROM nba_teams WHERE team_name ILIKE %s AND season = %s",
                    (f"%{team_input}%", self.season)
                )

        row = cur.fetchone()
        return dict(row) if row else None

    def get_player_injury_status(self, player_name: str) -> Optional[str]:
        """Check if a player is on the injury report. Returns status or None."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT status, injury_detail FROM nba_injuries WHERE player_name ILIKE %s",
            (f"%{player_name}%",)
        )
        row = cur.fetchone()
        return f"{row[0]} ({row[1]})" if row else None

    def is_back_to_back(self, team_abbr: str, game_date: str = None) -> bool:
        """Check if a team is on a back-to-back."""
        if game_date is None:
            game_date = date.today().isoformat()

        cur = self.conn.cursor()
        cur.execute("""
            SELECT is_back_to_back_home, is_back_to_back_away,
                   home_team_abbr, away_team_abbr
            FROM nba_games
            WHERE game_date = %s AND (home_team_abbr = %s OR away_team_abbr = %s)
        """, (game_date, team_abbr, team_abbr))
        row = cur.fetchone()

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

        summary["players"] = self.collect_all_player_stats()
        summary["teams"] = self.collect_all_team_stats()
        summary["recent_form"] = self.collect_all_recent_form(top_n=150)
        summary["injuries"] = self.collect_injuries()
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

        summary["recent_form"] = self.collect_all_recent_form(top_n=120)
        summary["injuries"] = self.collect_injuries()
        summary["games"] = self.collect_schedule(days_ahead=2)

        logger.info(f"[NBACollector] Quick update complete: {json.dumps(summary, indent=2)}")
        return summary

    def close(self):
        put_connection(self.conn)
        self.conn = None


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
