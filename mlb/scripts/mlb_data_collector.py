#!/usr/bin/env python3
"""
MLB Data Collector
===================
War Machine MLB

Collects MLB team, player, and game log data from the MLB Stats API.
Stores everything in PostgreSQL (warmachine DB, mlb_* tables).

Usage:
    python mlb/scripts/mlb_data_collector.py              # full collection
    python mlb/scripts/mlb_data_collector.py --teams-only  # teams + players only
    python mlb/scripts/mlb_data_collector.py --logs-only   # game logs only
"""

import sys
import os
import json
import time
import logging
import argparse
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

from db import get_connection, put_connection
from tz import ET

import statsapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ============================================================================
# Teams
# ============================================================================

def seed_teams():
    """Seed mlb_teams table with 30 MLB teams from Stats API."""
    logger.info("[Teams] Fetching MLB teams...")
    teams_data = statsapi.get('teams', {'sportIds': 1})
    teams = teams_data.get('teams', [])

    conn = get_connection()
    cur = conn.cursor()
    inserted = 0

    for t in teams:
        abbr = t.get('abbreviation', '')
        name = t.get('teamName', '')
        league_name = t.get('league', {}).get('name', '')
        league = 'AL' if 'American' in league_name else 'NL'
        div_name = t.get('division', {}).get('name', '')
        if 'East' in div_name:
            division = 'East'
        elif 'Central' in div_name:
            division = 'Central'
        elif 'West' in div_name:
            division = 'West'
        else:
            division = ''
        park = t.get('venue', {}).get('name', '')

        cur.execute("""
            INSERT INTO mlb_teams (abbr, name, league, division, park_name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (abbr) DO UPDATE SET
                name = EXCLUDED.name,
                league = EXCLUDED.league,
                division = EXCLUDED.division,
                park_name = EXCLUDED.park_name
        """, (abbr, name, league, division, park))
        inserted += 1

    conn.commit()
    put_connection(conn)
    logger.info(f"[Teams] Seeded {inserted} teams.")
    return inserted


# ============================================================================
# Players
# ============================================================================

def seed_players():
    """Seed mlb_players from 40-man rosters of all 30 teams."""
    logger.info("[Players] Fetching 40-man rosters for all teams...")
    teams_data = statsapi.get('teams', {'sportIds': 1})
    teams = teams_data.get('teams', [])

    conn = get_connection()
    cur = conn.cursor()
    total = 0

    for i, t in enumerate(teams):
        tid = t['id']
        abbr = t.get('abbreviation', '')
        try:
            roster = statsapi.get('team_roster', {'teamId': tid, 'rosterType': '40Man'})
            players = roster.get('roster', [])
        except Exception as e:
            logger.warning(f"[Players] Failed to fetch roster for {abbr}: {e}")
            continue

        for p in players:
            person = p.get('person', {})
            pid = person.get('id')
            name = person.get('fullName', '')
            pos = p.get('position', {}).get('abbreviation', '')
            jersey = p.get('jerseyNumber', '')
            jersey_num = int(jersey) if jersey and jersey.isdigit() else None

            # Get bats/throws from person detail (not in roster response)
            bats = None
            throws = None

            cur.execute("""
                INSERT INTO mlb_players (name, team, position, jersey_number, mlb_api_id, active)
                VALUES (%s, %s, %s, %s, %s, true)
                ON CONFLICT DO NOTHING
            """, (name, abbr, pos, jersey_num, pid))
            total += 1

        logger.info(f"[Players] {i+1}/{len(teams)} {abbr}: {len(players)} players")
        time.sleep(0.3)

    conn.commit()
    put_connection(conn)
    logger.info(f"[Players] Seeded {total} player records.")
    return total


# ============================================================================
# Pitcher Game Logs
# ============================================================================

def collect_pitcher_gamelogs(season=None):
    """Collect pitcher game logs from MLB Stats API.

    Args:
        season: int year (e.g. 2024). None = current season.
    """
    season_label = season or "current"
    logger.info(f"[PitcherLogs] Collecting pitcher game logs (season={season_label})...")

    # For historical seasons, get player list from that season's API
    if season:
        logger.info(f"[PitcherLogs] Fetching {season} season player list...")
        sp_data = statsapi.get('sports_players', {'sportId': 1, 'season': season})
        all_players = sp_data.get('people', [])
        pitchers = [
            {'mlb_api_id': p['id'], 'name': p.get('fullName', ''), 'team': p.get('currentTeam', {}).get('abbreviation', '')}
            for p in all_players
            if p.get('primaryPosition', {}).get('abbreviation', '') in ('P', 'SP', 'RP', 'CL')
        ]
    else:
        conn = get_connection(dict_cursor=True)
        cur = conn.cursor()
        cur.execute("""
            SELECT mlb_api_id, name, team FROM mlb_players
            WHERE active = true AND position IN ('P', 'SP', 'RP', 'CL')
            ORDER BY team, name
        """)
        pitchers = cur.fetchall()
        put_connection(conn)

    logger.info(f"[PitcherLogs] Found {len(pitchers)} pitchers to process.")

    total_inserted = 0
    errors = 0

    for i, p in enumerate(pitchers):
        pid = p['mlb_api_id']
        name = p['name']

        if not pid:
            continue

        try:
            hydrate = f'stats(group=[pitching],type=[gameLog],season={season})' if season else 'stats(group=[pitching],type=[gameLog])'
            data = statsapi.get('people', {
                'personIds': pid,
                'hydrate': hydrate,
            })
            people = data.get('people', [])
            if not people or not people[0].get('stats'):
                continue

            splits = people[0]['stats'][0].get('splits', [])

            for sp in splits:
                game_date = sp.get('date')
                if not game_date:
                    continue

                stat = sp.get('stat', {})
                opponent = sp.get('opponent', {}).get('abbreviation', '')
                is_home = sp.get('isHome', False)
                home_away = 'H' if is_home else 'A'
                team_abbr = sp.get('team', {}).get('abbreviation', p['team'])
                result = sp.get('isWin')
                if result is True:
                    result_str = 'W'
                elif result is False:
                    result_str = 'L'
                else:
                    result_str = ''

                ip_str = stat.get('inningsPitched', '0')
                try:
                    ip = float(ip_str)
                except (ValueError, TypeError):
                    ip = 0

                conn2 = get_connection()
                cur2 = conn2.cursor()
                try:
                    cur2.execute("""
                        INSERT INTO mlb_pitcher_gamelogs
                            (player_name, bbref_id, date, team, opponent, home_away,
                             result, innings_pitched, hits_allowed, runs_allowed,
                             earned_runs, strikeouts, walks, home_runs_allowed,
                             pitch_count, game_score)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (bbref_id, date) DO NOTHING
                    """, (
                        name, str(pid), game_date, team_abbr, opponent, home_away,
                        result_str, ip,
                        stat.get('hits', 0), stat.get('runs', 0),
                        stat.get('earnedRuns', 0), stat.get('strikeOuts', 0),
                        stat.get('baseOnBalls', 0), stat.get('homeRuns', 0),
                        stat.get('numberOfPitches', 0), stat.get('gameScore', 0),
                    ))
                    if cur2.rowcount > 0:
                        total_inserted += 1
                    conn2.commit()
                except Exception as e:
                    conn2.rollback()
                    logger.debug(f"[PitcherLogs] Insert error for {name} {game_date}: {e}")
                finally:
                    put_connection(conn2)

        except Exception as e:
            errors += 1
            logger.debug(f"[PitcherLogs] Error fetching {name} (id={pid}): {e}")

        if (i + 1) % 50 == 0:
            logger.info(f"[PitcherLogs] {i+1}/{len(pitchers)} done, {total_inserted} rows inserted")

        time.sleep(1)

    logger.info(f"[PitcherLogs] Done. {total_inserted} rows inserted, {errors} errors.")
    return total_inserted


# ============================================================================
# Batter Game Logs
# ============================================================================

def collect_batter_gamelogs(season=None):
    """Collect batter game logs from MLB Stats API.

    Args:
        season: int year (e.g. 2024). None = current season.
    """
    season_label = season or "current"
    logger.info(f"[BatterLogs] Collecting batter game logs (season={season_label})...")

    if season:
        logger.info(f"[BatterLogs] Fetching {season} season player list...")
        sp_data = statsapi.get('sports_players', {'sportId': 1, 'season': season})
        all_players = sp_data.get('people', [])
        batters = [
            {'mlb_api_id': p['id'], 'name': p.get('fullName', ''), 'team': p.get('currentTeam', {}).get('abbreviation', '')}
            for p in all_players
            if p.get('primaryPosition', {}).get('abbreviation', '') not in ('P', 'SP', 'RP', 'CL', 'TWP')
        ]
    else:
        conn = get_connection(dict_cursor=True)
        cur = conn.cursor()
        cur.execute("""
            SELECT mlb_api_id, name, team FROM mlb_players
            WHERE active = true AND position NOT IN ('P', 'SP', 'RP', 'CL')
            ORDER BY team, name
        """)
        batters = cur.fetchall()
        put_connection(conn)

    logger.info(f"[BatterLogs] Found {len(batters)} batters to process.")

    total_inserted = 0
    errors = 0

    for i, b in enumerate(batters):
        pid = b['mlb_api_id']
        name = b['name']

        if not pid:
            continue

        try:
            hydrate = f'stats(group=[hitting],type=[gameLog],season={season})' if season else 'stats(group=[hitting],type=[gameLog])'
            data = statsapi.get('people', {
                'personIds': pid,
                'hydrate': hydrate,
            })
            people = data.get('people', [])
            if not people or not people[0].get('stats'):
                continue

            splits = people[0]['stats'][0].get('splits', [])

            for sp in splits:
                game_date = sp.get('date')
                if not game_date:
                    continue

                stat = sp.get('stat', {})
                opponent = sp.get('opponent', {}).get('abbreviation', '')
                is_home = sp.get('isHome', False)
                home_away = 'H' if is_home else 'A'
                team_abbr = sp.get('team', {}).get('abbreviation', b['team'])

                conn2 = get_connection()
                cur2 = conn2.cursor()
                try:
                    cur2.execute("""
                        INSERT INTO mlb_batter_gamelogs
                            (player_name, bbref_id, date, team, opponent, home_away,
                             at_bats, hits, doubles, triples, home_runs, rbis,
                             runs, strikeouts, walks, total_bases)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (bbref_id, date) DO NOTHING
                    """, (
                        name, str(pid), game_date, team_abbr, opponent, home_away,
                        stat.get('atBats', 0), stat.get('hits', 0),
                        stat.get('doubles', 0), stat.get('triples', 0),
                        stat.get('homeRuns', 0), stat.get('rbi', 0),
                        stat.get('runs', 0), stat.get('strikeOuts', 0),
                        stat.get('baseOnBalls', 0), stat.get('totalBases', 0),
                    ))
                    if cur2.rowcount > 0:
                        total_inserted += 1
                    conn2.commit()
                except Exception as e:
                    conn2.rollback()
                    logger.debug(f"[BatterLogs] Insert error for {name} {game_date}: {e}")
                finally:
                    put_connection(conn2)

        except Exception as e:
            errors += 1
            logger.debug(f"[BatterLogs] Error fetching {name} (id={pid}): {e}")

        if (i + 1) % 50 == 0:
            logger.info(f"[BatterLogs] {i+1}/{len(batters)} done, {total_inserted} rows inserted")

        time.sleep(1)

    logger.info(f"[BatterLogs] Done. {total_inserted} rows inserted, {errors} errors.")
    return total_inserted


# ============================================================================
# Summary
# ============================================================================

def print_summary():
    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()

    tables = ['mlb_teams', 'mlb_players', 'mlb_pitcher_gamelogs', 'mlb_batter_gamelogs']
    print()
    print("=" * 60)
    print("  MLB DATA COLLECTION SUMMARY")
    print("=" * 60)
    for t in tables:
        cur.execute(f"SELECT COUNT(*) as cnt FROM {t}")
        cnt = cur.fetchone()['cnt']
        print(f"  {t:30s} {cnt:>8,} rows")

    cur.execute("SELECT COUNT(*) as cnt FROM mlb_players WHERE active = true")
    active = cur.fetchone()['cnt']
    print(f"  {'mlb_players (active only)':30s} {active:>8,} rows")

    cur.execute("SELECT team, COUNT(*) as cnt FROM mlb_players WHERE active = true GROUP BY team ORDER BY team")
    teams = cur.fetchall()
    print()
    print(f"  Players per team: {len(teams)} teams")
    for t in teams[:5]:
        print(f"    {t['team']}: {t['cnt']}")
    if len(teams) > 5:
        print(f"    ... and {len(teams)-5} more")

    print("=" * 60)
    put_connection(conn)


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="MLB Data Collector")
    parser.add_argument("--teams-only", action="store_true", help="Only seed teams + players")
    parser.add_argument("--logs-only", action="store_true", help="Only collect game logs")
    parser.add_argument("--season", type=int, help="Season year (e.g. 2024). Omit for current season.")
    args = parser.parse_args()

    if args.teams_only:
        seed_teams()
        seed_players()
    elif args.logs_only:
        collect_pitcher_gamelogs(season=args.season)
        collect_batter_gamelogs(season=args.season)
    elif args.season:
        collect_pitcher_gamelogs(season=args.season)
        collect_batter_gamelogs(season=args.season)
    else:
        seed_teams()
        seed_players()
        collect_pitcher_gamelogs()
        collect_batter_gamelogs()

    print_summary()


if __name__ == "__main__":
    main()
