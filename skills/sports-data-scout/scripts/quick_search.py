#!/usr/bin/env python3
"""
Sports Data Scout - Quick Search Query Generator

This script generates optimized web search queries for sports betting analysis.
It structures searches by priority (schedule/odds first, then injuries, then stats)
and sport-specific needs (weather for baseball, xG for soccer, etc.)

Usage:
    python quick_search.py NBA 2026-03-23
    python quick_search.py MLB 2026-03-23
    python quick_search.py SOCCER 2026-03-23

    python quick_search.py --template NBA
    python quick_search.py --analysis "Celtics vs Heat" NBA
    python quick_search.py --game-card
"""

import sys
import argparse
from datetime import datetime
from typing import List, Dict, Tuple


class SearchQueryGenerator:
    """Generate optimized search queries for sports betting analysis."""

    def __init__(self, sport: str, date: str = None):
        """
        Initialize the query generator.

        Args:
            sport: 'NBA', 'MLB', or 'SOCCER'
            date: Analysis date in YYYY-MM-DD format (defaults to today)
        """
        self.sport = sport.upper()
        self.date = date or datetime.now().strftime('%Y-%m-%d')

        # Validate sport
        valid_sports = ['NBA', 'MLB', 'SOCCER']
        if self.sport not in valid_sports:
            raise ValueError(f"Sport must be one of {valid_sports}")

    def get_priority_1_queries(self) -> List[str]:
        """Priority 1: Game Schedule & Odds (MUST HAVE)"""
        queries = []

        if self.sport == 'NBA':
            queries = [
                f"NBA games today {self.date} schedule",
                f"NBA odds today {self.date} Kalshi",
                f"NBA odds today {self.date}",
                f"NBA spread movement today sharp money"
            ]
        elif self.sport == 'MLB':
            queries = [
                f"MLB schedule today {self.date}",
                f"MLB odds today {self.date}",
                f"MLB games {self.date} schedule",
                f"MLB spread movement today sharp money"
            ]
        elif self.sport == 'SOCCER':
            queries = [
                f"Premier League fixtures {self.date} schedule odds",
                f"La Liga fixtures {self.date} schedule odds",
                f"Soccer odds today {self.date} Kalshi",
                f"soccer spread movement today sharp money"
            ]

        return queries

    def get_priority_2_queries(self) -> List[str]:
        """Priority 2: Injury & Lineup (CRITICAL)"""
        queries = []

        if self.sport == 'NBA':
            queries = [
                f"NBA injury report today {self.date}",
                f"NBA starting lineups today {self.date}",
                f"NBA player status out questionable {self.date}"
            ]
        elif self.sport == 'MLB':
            queries = [
                f"MLB injury report today {self.date}",
                f"MLB probable pitchers today {self.date}",
                f"MLB starting lineups today {self.date}",
                f"MLB roster updates {self.date}"
            ]
        elif self.sport == 'SOCCER':
            queries = [
                f"soccer injury report today {self.date}",
                f"Premier League team news injuries {self.date}",
                f"La Liga team news injuries {self.date}",
                f"soccer squad news {self.date}"
            ]

        return queries

    def get_priority_3_queries(self) -> List[str]:
        """Priority 3: Team Stats & Form"""
        queries = []

        if self.sport == 'NBA':
            queries = [
                f"NBA standings 2025-26 season",
                f"[TEAM] stats 2025-26 season",
                f"[TEAM] last 10 games results record",
                f"[TEAM] net rating offensive rating 2026",
                f"[TEAM] home away split stats"
            ]
        elif self.sport == 'MLB':
            queries = [
                f"MLB standings 2025-26 season",
                f"[TEAM] stats 2025-26 season",
                f"[TEAM] last 10 games results",
                f"[TEAM] team batting average OPS ERA",
                f"[TEAM] home away split stats"
            ]
        elif self.sport == 'SOCCER':
            queries = [
                f"Premier League table 2025-26 standings",
                f"La Liga table 2025-26 standings",
                f"[TEAM] last 5 results form",
                f"[TEAM] xG expected goals stats",
                f"[TEAM] defensive record goals conceded"
            ]

        return queries

    def get_priority_4_queries(self) -> List[str]:
        """Priority 4: Contextual Factors"""
        queries = []

        if self.sport == 'NBA':
            queries = [
                f"[TEAM A] vs [TEAM B] head to head last 5",
                f"NBA games back to back {self.date}",
                f"NBA rest advantage {self.date}"
            ]
        elif self.sport == 'MLB':
            queries = [
                f"[TEAM A] vs [TEAM B] head to head history",
                f"[PITCHER] stats 2026 game log",
                f"[CITY] weather forecast {self.date} game time",
                f"MLB back to back games {self.date}"
            ]
        elif self.sport == 'SOCCER':
            queries = [
                f"[TEAM A] vs [TEAM B] head to head history",
                f"[TEAM] recent form last 5 games",
                f"soccer weather forecast {self.date}",
                f"squad depth injuries availability {self.date}"
            ]

        return queries

    def get_all_queries(self) -> Dict[str, List[str]]:
        """Get all search queries organized by priority."""
        return {
            'Priority 1 - Schedule & Odds': self.get_priority_1_queries(),
            'Priority 2 - Injuries & Lineup': self.get_priority_2_queries(),
            'Priority 3 - Stats & Form': self.get_priority_3_queries(),
            'Priority 4 - Context': self.get_priority_4_queries()
        }

    def print_queries(self, verbose: bool = False):
        """Print all queries organized by priority."""
        all_queries = self.get_all_queries()

        print(f"\n{'=' * 70}")
        print(f"🔍 SPORTS DATA SCOUT - SEARCH QUERY GENERATOR")
        print(f"{'=' * 70}")
        print(f"Sport: {self.sport}")
        print(f"Date: {self.date}")
        print(f"{'=' * 70}\n")

        counter = 1
        for priority, queries in all_queries.items():
            print(f"\n{priority}:")
            print("-" * 70)
            for query in queries:
                print(f"{counter:2d}. {query}")
                counter += 1

        print(f"\n{'=' * 70}")
        print(f"Total Queries: {counter - 1}")
        print(f"Estimated Time: {(counter - 1) * 1.5:.0f} minutes")
        print(f"{'=' * 70}\n")

        if verbose:
            print("\n📋 INSTRUCTIONS:")
            print("1. Copy queries in order (priority 1 first)")
            print("2. Paste into search engine or API")
            print("3. Record results in Game Analysis Cards")
            print("4. Fill remaining template fields with context data")

    def get_per_game_queries(self, team_a: str, team_b: str) -> List[str]:
        """Get queries for a specific game matchup."""
        queries = []

        if self.sport == 'NBA':
            queries = [
                f"{team_a} injury report today",
                f"{team_b} injury report today",
                f"{team_a} vs {team_b} head to head stats",
                f"{team_a} stats this season",
                f"{team_b} stats this season"
            ]
        elif self.sport == 'MLB':
            queries = [
                f"{team_a} injury report today",
                f"{team_b} injury report today",
                f"{team_a} vs {team_b} prediction stats",
                f"{team_a} probable pitcher today",
                f"{team_b} probable pitcher today"
            ]
        elif self.sport == 'SOCCER':
            queries = [
                f"{team_a} injury report {self.date}",
                f"{team_b} injury report {self.date}",
                f"{team_a} vs {team_b} head to head history",
                f"{team_a} recent form",
                f"{team_b} recent form"
            ]

        return queries


def parse_stats_format(stat_string: str) -> Dict[str, str]:
    """
    Helper function to parse common stats formats.

    Examples:
        "W-L: 42-28" -> {'wins': '42', 'losses': '28'}
        "2.84 ERA" -> {'era': '2.84'}
    """
    stat_dict = {}

    # Record parsing (W-L format)
    if 'W-L' in stat_string or '-' in stat_string:
        parts = stat_string.split('-')
        if len(parts) >= 2:
            stat_dict['wins'] = parts[0].strip()
            stat_dict['losses'] = parts[1].strip()

    # Single metrics
    if 'ERA' in stat_string:
        stat_dict['era'] = stat_string.replace('ERA', '').strip()
    if 'OPS' in stat_string:
        stat_dict['ops'] = stat_string.replace('OPS', '').strip()
    if 'xG' in stat_string:
        stat_dict['xg'] = stat_string.replace('xG', '').strip()

    return stat_dict


def format_game_card(team_a: str, team_b: str, sport: str, date: str) -> str:
    """Generate a blank game analysis card template."""

    if sport.upper() == 'NBA':
        template = f"""
GAME: {team_a} vs {team_b}
DATE: {date} | TIME: [Time ET]
VENUE: [Arena Name]

=== {team_a.upper()} ===
Record: [W-L] (Home: [W-L], Away: [W-L])
Last 5: [W/L/W/L/W]
Key Stats:
  - [Stat 1: value]
  - [Stat 2: value]
  - [Stat 3: value]
Injuries:
  - [Player Name]: [Status]

=== {team_b.upper()} ===
Record: [W-L] (Home: [W-L], Away: [W-L])
Last 5: [W/L/W/L/W]
Key Stats:
  - [Stat 1: value]
  - [Stat 2: value]
  - [Stat 3: value]
Injuries:
  - [Player Name]: [Status]

=== MARKET ===
Spread: [Team] [+/-]X.X
Moneyline: [Team A -XXX] / [Team B +XXX]
Total: O/U [Number]
Kalshi YES: $X.XX | NO: $X.XX
Public: [%] on [Favorite]

=== CONTEXT ===
H2H Last 5: [Team A] X-X [Team B]
Schedule Notes: [B2B, rest, travel]
Weather: N/A (Indoor)
Motivation: [Playoff, rivalry, etc.]
"""

    elif sport.upper() == 'MLB':
        template = f"""
GAME: {team_a} vs {team_b}
DATE: {date} | TIME: [Time ET]
VENUE: [Stadium Name]

=== {team_a.upper()} ===
Record: [W-L] (Home: [W-L], Away: [W-L])
Last 5: [W/L/W/L/W]
Probable Pitcher: [Name]
  - ERA: [X.XX], K/9: [X.X], BB/9: [X.X]
Key Stats:
  - Team OPS: [X.XXX]
  - Team ERA: [X.XX]
Injuries:
  - [Player Name]: [Status]

=== {team_b.upper()} ===
Record: [W-L] (Home: [W-L], Away: [W-L])
Last 5: [W/L/W/L/W]
Probable Pitcher: [Name]
  - ERA: [X.XX], K/9: [X.F], BB/9: [X.X]
Key Stats:
  - Team OPS: [X.XXX]
  - Team ERA: [X.XX]
Injuries:
  - [Player Name]: [Status]

=== MARKET ===
Spread: [Team] [+/-]X.X
Moneyline: [Team A -XXX] / [Team B +XXX]
Total: O/U [Number]
Kalshi YES: $X.XX | NO: $X.XX
Public: [%] on [Favorite]

=== CONTEXT ===
H2H Last 5: [Team A] X-X [Team B]
Schedule Notes: [B2B, rest, travel]
Weather: [Temp, wind, precipitation]
Motivation: [Playoff, deadline, etc.]
"""

    elif sport.upper() == 'SOCCER':
        template = f"""
GAME: {team_a} vs {team_b}
DATE: {date} | TIME: [Time ET]
VENUE: [Stadium Name]

=== {team_a.upper()} ===
Record: [W-D-L] (Points: X)
Last 5: [W/D/L/W/L]
Key Stats:
  - Goals For: [X], Goals Against: [X]
  - xG: [X.X], xGA: [X.X]
  - Possession %: [X.X%]
Injuries:
  - [Player Name]: [Status]

=== {team_b.upper()} ===
Record: [W-D-L] (Points: X)
Last 5: [W/D/L/W/L]
Key Stats:
  - Goals For: [X], Goals Against: [X]
  - xG: [X.X], xGA: [X.X]
  - Possession %: [X.X%]
Injuries:
  - [Player Name]: [Status]

=== MARKET ===
Spread: [Team] [+/-]X.X goals
Moneyline: [Team A -XXX] / [Team B +XXX] / Draw [+XXX]
Total: O/U [Number] goals
Kalshi YES: $X.XX | NO: $X.XX
Public: [%] on [Favorite]

=== CONTEXT ===
H2H Last 5: [Team A] W-D-L [Team B]
Schedule Notes: [Rest advantage, travel]
Weather: [Temp, wind, rain probability]
Motivation: [Title race, CL spots, relegation]
"""

    return template


def print_blank_game_card(team_a: str, team_b: str, sport: str, date: str = None):
    """Print a blank game analysis card template."""
    date = date or datetime.now().strftime('%Y-%m-%d')
    card = format_game_card(team_a, team_b, sport, date)
    print(card)


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description='Sports Data Scout - Query Generator for Betting Analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python quick_search.py NBA 2026-03-23
  python quick_search.py MLB 2026-03-23
  python quick_search.py SOCCER
  python quick_search.py --game-card "Boston Celtics" "Miami Heat" NBA
  python quick_search.py --template NBA
        """
    )

    parser.add_argument('sport', nargs='?', help='Sport: NBA, MLB, or SOCCER')
    parser.add_argument('date', nargs='?', help='Analysis date (YYYY-MM-DD, default: today)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output with instructions')
    parser.add_argument('--template', help='Show query template for sport (no date needed)')
    parser.add_argument('--game-card', action='store_true', help='Generate blank game card template')
    parser.add_argument('--game', nargs=3, metavar=('TEAM_A', 'TEAM_B', 'SPORT'),
                       help='Get per-game queries: --game "Team A" "Team B" NBA')

    args = parser.parse_args()

    try:
        if args.template:
            # Show template for a sport
            gen = SearchQueryGenerator(args.template)
            gen.print_queries(verbose=True)

        elif args.game:
            # Get per-game queries
            team_a, team_b, sport = args.game
            gen = SearchQueryGenerator(sport, args.date)
            queries = gen.get_per_game_queries(team_a, team_b)
            print(f"\n🎮 PER-GAME QUERIES: {team_a} vs {team_b}")
            print("-" * 70)
            for i, query in enumerate(queries, 1):
                print(f"{i}. {query}")

        elif args.game_card:
            # Generate blank game card (requires manual input)
            if len(sys.argv) > 2:
                # Try to parse from command line
                print("Usage: python quick_search.py --game-card")
                print("\nExample blank game card for manual entry:")
            print_blank_game_card('[TEAM A]', '[TEAM B]', 'NBA')

        elif args.sport:
            # Generate full query set
            gen = SearchQueryGenerator(args.sport, args.date)
            gen.print_queries(verbose=args.verbose)

        else:
            parser.print_help()

    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    # If run without arguments, show help
    if len(sys.argv) == 1:
        main()
    else:
        main()
