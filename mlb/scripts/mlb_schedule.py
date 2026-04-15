#!/usr/bin/env python3
"""
MLB Schedule Checker
======================
War Machine MLB

Checks today's MLB schedule using the MLB Stats API.

Usage:
    python mlb/scripts/mlb_schedule.py
"""

import sys
import os
import io
import json
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from tz import ET
import statsapi


def get_today_games(target_date=None):
    """
    Get today's MLB games.

    Args:
        target_date: date string 'MM/DD/YYYY' or None for today

    Returns:
        List of game dicts with: game_id, away, home, time_et,
        away_starter, home_starter, status, venue
    """
    if target_date is None:
        now = datetime.now(ET)
        target_date = now.strftime('%m/%d/%Y')

    schedule = statsapi.schedule(date=target_date)

    games = []
    for g in schedule:
        # Parse UTC time to ET
        utc_str = g.get('game_datetime', '')
        time_et = ''
        if utc_str:
            try:
                from dateutil.parser import parse as dtparse
                from tz import UTC
                utc_dt = dtparse(utc_str)
                if utc_dt.tzinfo is None:
                    from datetime import timezone
                    utc_dt = utc_dt.replace(tzinfo=timezone.utc)
                et_dt = utc_dt.astimezone(ET)
                time_et = et_dt.strftime('%I:%M %p ET')
            except Exception:
                time_et = utc_str

        games.append({
            'game_id': g.get('game_id'),
            'away': g.get('away_name', ''),
            'away_abbr': '',  # statsapi doesn't always have abbreviation
            'home': g.get('home_name', ''),
            'home_abbr': '',
            'time_et': time_et,
            'away_starter': g.get('away_probable_pitcher', ''),
            'home_starter': g.get('home_probable_pitcher', ''),
            'status': g.get('status', ''),
            'venue': g.get('venue_name', ''),
        })

    return games


def is_game_day(target_date=None):
    """Check if there are MLB games today."""
    return len(get_today_games(target_date)) > 0


def get_today_starters(target_date=None):
    """
    Get confirmed starting pitchers for today's games.

    Returns:
        dict[team_abbr] = pitcher_full_name
        Teams with TBD starters are excluded.
    """
    if target_date is None:
        now = datetime.now(ET)
        target_date = now.strftime('%m/%d/%Y')

    schedule = statsapi.schedule(date=target_date)

    # Build team_id -> abbreviation map
    teams_data = statsapi.get('teams', {'sportIds': 1})
    id_to_abbr = {t['id']: t['abbreviation'] for t in teams_data.get('teams', [])}

    starters = {}
    for g in schedule:
        away_abbr = id_to_abbr.get(g.get('away_id'), '')
        home_abbr = id_to_abbr.get(g.get('home_id'), '')
        away_sp = g.get('away_probable_pitcher', '')
        home_sp = g.get('home_probable_pitcher', '')

        if away_abbr and away_sp and away_sp != 'TBD':
            starters[away_abbr] = away_sp
        if home_abbr and home_sp and home_sp != 'TBD':
            starters[home_abbr] = home_sp

    return starters


def main():
    now = datetime.now(ET)
    print(f"\n  MLB Schedule — {now.strftime('%A, %B %d, %Y')} ({now.strftime('%I:%M %p ET')})")
    print("=" * 70)

    games = get_today_games()

    if not games:
        print("  No MLB games today.")
        return

    print(f"  {len(games)} game(s) scheduled:\n")
    for g in games:
        starter_away = g['away_starter'] or 'TBD'
        starter_home = g['home_starter'] or 'TBD'
        print(f"  {g['time_et']:>12s}  {g['away']:25s} @ {g['home']:25s}")
        print(f"               {starter_away:25s}   {starter_home:25s}")
        print(f"               Status: {g['status']}  |  {g['venue']}")
        print()

    print("=" * 70)


if __name__ == "__main__":
    main()
