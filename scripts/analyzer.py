#!/usr/bin/env python3
"""
Comprehensive Sports Betting Analysis Engine
Designed for Kalshi prediction market platform
Focus: NBA, MLB, Soccer (EPL/La Liga)

Features:
- Web scraping from free public sources
- Statistical analysis and EV calculation
- Risk assessment and bankroll management
- Daily pick generation and reporting
"""

import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import math
from dataclasses import dataclass, asdict
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
import numpy as np
# Pure math Poisson implementation (no scipy dependency)
def _poisson_pmf(k, lam):
    """Poisson probability mass function without scipy."""
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class TeamStats:
    """Team performance statistics"""
    name: str
    sport: str
    wins: int
    losses: int
    win_pct: float
    elo_rating: float = 1500.0
    recent_form: List[str] = None  # List of W/L/D for last 10 games
    strength_of_schedule: float = 0.0

    def __post_init__(self):
        if self.recent_form is None:
            self.recent_form = []


@dataclass
class BettingOdds:
    """Betting odds and probability"""
    team_a: str
    team_b: str
    spread: float
    line_a: float  # American odds
    line_b: float
    implied_prob_a: float
    implied_prob_b: float
    source: str = "unknown"
    timestamp: str = ""


@dataclass
class PickAnalysis:
    """Complete analysis of a single bet"""
    sport: str
    match: str
    team_a: str
    team_b: str
    pick: str  # Team or outcome being picked
    your_probability: float
    implied_probability: float
    expected_value: float
    odds: float
    recommended_bet_amount: float
    confidence: int  # 1-10 scale
    risk_level: str  # Low/Medium/High
    reasoning: List[str]
    timestamp: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================================
# UTILITY FUNCTIONS - PROBABILITY & ODDS
# ============================================================================

def american_to_decimal(american_odds: float) -> float:
    """Convert American odds to decimal odds"""
    if american_odds > 0:
        return (american_odds / 100) + 1
    else:
        return (100 / abs(american_odds)) + 1


def decimal_to_probability(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability"""
    if decimal_odds <= 1:
        return 0.0
    return 1 / decimal_odds


def american_to_probability(american_odds: float) -> float:
    """Convert American odds to implied probability"""
    decimal = american_to_decimal(american_odds)
    return decimal_to_probability(decimal)


def calculate_ev(your_prob: float, implied_prob: float, odds: float) -> float:
    """
    Calculate expected value of a bet

    EV = (your_prob * (odds - 1)) - (1 - your_prob)
    For a bet where you win at 'odds' or lose your stake
    """
    if odds <= 1:
        return 0
    win_amount = your_prob * (odds - 1)
    lose_amount = (1 - your_prob) * 1
    return win_amount - lose_amount


def kelly_criterion(prob_win: float, odds: float, kelly_fraction: float = 0.25) -> float:
    """
    Kelly Criterion for optimal bet sizing

    f* = (p * b - q) / b
    where p = probability of win, q = probability of loss, b = odds - 1

    kelly_fraction: use fractional kelly (25% = more conservative)
    """
    if odds <= 1 or prob_win <= 0 or prob_win >= 1:
        return 0

    b = odds - 1
    q = 1 - prob_win
    kelly_frac = (prob_win * b - q) / b
    kelly_frac = max(0, kelly_frac)  # Don't go negative

    return kelly_frac * kelly_fraction


def calculate_parlay_ev(picks: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    """
    Calculate expected value for parlay/combo bets

    picks: List of (your_prob, decimal_odds) tuples
    Returns: (ev, parlay_probability, parlay_odds)
    """
    if not picks:
        return 0, 0, 0

    parlay_prob = 1.0
    parlay_odds = 1.0

    for your_prob, odds in picks:
        parlay_prob *= your_prob
        parlay_odds *= odds

    ev = calculate_ev(parlay_prob, 1 / parlay_odds, parlay_odds)
    return ev, parlay_prob, parlay_odds


# ============================================================================
# ELO RATING SYSTEM
# ============================================================================

class ELORating:
    """Simple ELO rating system for team strength comparison"""

    K_FACTOR = 32  # Standard chess K-factor
    HOME_ADVANTAGE = 30  # Points advantage for home team

    @staticmethod
    def expected_win_probability(elo_a: float, elo_b: float, home: bool = False) -> float:
        """Calculate expected win probability for team A vs team B"""
        diff = elo_a - elo_b
        if home:
            diff += ELORating.HOME_ADVANTAGE
        return 1 / (1 + 10 ** (-diff / 400))

    @staticmethod
    def update_elo(elo_a: float, elo_b: float, result: int, k_factor: int = K_FACTOR) -> Tuple[float, float]:
        """
        Update ELO ratings after a game
        result: 1 if team A won, 0 if team B won
        """
        expected_a = ELORating.expected_win_probability(elo_a, elo_b)
        expected_b = 1 - expected_a

        new_elo_a = elo_a + k_factor * (result - expected_a)
        new_elo_b = elo_b + k_factor * ((1 - result) - expected_b)

        return new_elo_a, new_elo_b

    @staticmethod
    def win_probability_from_elo_diff(elo_diff: float) -> float:
        """Calculate win probability from ELO difference"""
        return 1 / (1 + 10 ** (-elo_diff / 400))


# ============================================================================
# NBA ANALYSIS
# ============================================================================

class NBAAnalyzer:
    """NBA data gathering and analysis"""

    BASE_URLS = {
        'standings': 'https://www.espn.com/nba/standings',
        'scores': 'https://www.espn.com/nba/scores',
        'injuries': 'https://www.espn.com/nba/injuries',
    }

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    @staticmethod
    def fetch_standings() -> Dict[str, TeamStats]:
        """Fetch NBA standings and calculate basic stats"""
        standings = {}

        try:
            response = requests.get(NBAAnalyzer.BASE_URLS['standings'],
                                   headers=NBAAnalyzer.HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # Parse standings table
            rows = soup.find_all('tr', class_='Table__TR')

            for row in rows[:30]:  # First 30 teams max
                try:
                    cols = row.find_all('td')
                    if len(cols) < 3:
                        continue

                    # Extract team name
                    team_name_elem = cols[0].find('a')
                    if not team_name_elem:
                        continue
                    team_name = team_name_elem.text.strip()

                    # Extract wins and losses
                    record = cols[1].text.strip()
                    match = re.search(r'(\d+)-(\d+)', record)
                    if not match:
                        continue

                    wins, losses = int(match.group(1)), int(match.group(2))
                    win_pct = wins / (wins + losses) if (wins + losses) > 0 else 0

                    standings[team_name] = TeamStats(
                        name=team_name,
                        sport='NBA',
                        wins=wins,
                        losses=losses,
                        win_pct=win_pct,
                        elo_rating=1500 + (100 * (win_pct - 0.5))  # Simple ELO initialization
                    )
                except (IndexError, ValueError, AttributeError):
                    continue

        except requests.RequestException as e:
            print(f"Error fetching NBA standings: {e}")

        return standings

    @staticmethod
    def fetch_recent_games(team_name: str, limit: int = 10) -> List[str]:
        """Fetch recent game results for a team (W/L)"""
        # This would require more complex scraping - placeholder implementation
        # In production, use official NBA API or ESPN detailed pages
        recent_form = []
        try:
            # Simplified mock data generation based on team performance
            # Real implementation would scrape actual game data
            pass
        except Exception as e:
            print(f"Error fetching recent games for {team_name}: {e}")

        return recent_form

    @staticmethod
    def estimate_team_strength(team_stats: TeamStats) -> float:
        """Estimate team strength on scale 0-100"""
        # Base on win percentage and adjusted for strength of schedule
        return min(100, team_stats.win_pct * 150)


# ============================================================================
# MLB ANALYSIS
# ============================================================================

class MLBAnalyzer:
    """MLB data gathering and analysis"""

    BASE_URLS = {
        'standings': 'https://www.espn.com/mlb/standings',
        'scores': 'https://www.espn.com/mlb/scores',
    }

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    @staticmethod
    def fetch_standings() -> Dict[str, TeamStats]:
        """Fetch MLB standings"""
        standings = {}

        try:
            response = requests.get(MLBAnalyzer.BASE_URLS['standings'],
                                   headers=MLBAnalyzer.HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            rows = soup.find_all('tr', class_='Table__TR')

            for row in rows[:35]:  # 30 MLB teams + buffer
                try:
                    cols = row.find_all('td')
                    if len(cols) < 3:
                        continue

                    team_name_elem = cols[0].find('a')
                    if not team_name_elem:
                        continue
                    team_name = team_name_elem.text.strip()

                    record = cols[1].text.strip()
                    match = re.search(r'(\d+)-(\d+)', record)
                    if not match:
                        continue

                    wins, losses = int(match.group(1)), int(match.group(2))
                    win_pct = wins / (wins + losses) if (wins + losses) > 0 else 0

                    standings[team_name] = TeamStats(
                        name=team_name,
                        sport='MLB',
                        wins=wins,
                        losses=losses,
                        win_pct=win_pct,
                        elo_rating=1500 + (100 * (win_pct - 0.5))
                    )
                except (IndexError, ValueError, AttributeError):
                    continue

        except requests.RequestException as e:
            print(f"Error fetching MLB standings: {e}")

        return standings

    @staticmethod
    def estimate_pitcher_advantage(home_pitcher_era: float,
                                   away_pitcher_era: float) -> float:
        """
        Estimate home team advantage from pitcher matchup
        Returns adjustment to home team win probability (-0.1 to +0.1)
        """
        if home_pitcher_era <= 0 or away_pitcher_era <= 0:
            return 0

        era_diff = away_pitcher_era - home_pitcher_era
        # Normalize: each 0.5 ERA difference ≈ 2-3% win prob difference
        return min(0.1, max(-0.1, era_diff * 0.04))


# ============================================================================
# SOCCER ANALYSIS
# ============================================================================

class SoccerAnalyzer:
    """Soccer data gathering and analysis (EPL, La Liga)"""

    BASE_URLS = {
        'epl_standings': 'https://www.espn.com/soccer/standings/_/league/eng.1',
        'la_liga_standings': 'https://www.espn.com/soccer/standings/_/league/esp.1',
    }

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    @staticmethod
    def fetch_standings(league: str = 'EPL') -> Dict[str, TeamStats]:
        """Fetch soccer league standings"""
        standings = {}
        url = SoccerAnalyzer.BASE_URLS.get(f'{league.lower()}_standings')

        if not url:
            print(f"Unknown league: {league}")
            return standings

        try:
            response = requests.get(url, headers=SoccerAnalyzer.HEADERS, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            rows = soup.find_all('tr', class_='Table__TR')

            for row in rows[:25]:  # Up to 25 teams
                try:
                    cols = row.find_all('td')
                    if len(cols) < 4:
                        continue

                    team_name_elem = cols[1].find('a')
                    if not team_name_elem:
                        continue
                    team_name = team_name_elem.text.strip()

                    # Extract W-D-L record
                    record = cols[3].text.strip()
                    match = re.search(r'(\d+)-(\d+)-(\d+)', record)
                    if not match:
                        continue

                    wins = int(match.group(1))
                    draws = int(match.group(2))
                    losses = int(match.group(3))

                    total_games = wins + draws + losses
                    win_pct = wins / total_games if total_games > 0 else 0

                    standings[team_name] = TeamStats(
                        name=team_name,
                        sport=f'Soccer ({league})',
                        wins=wins,
                        losses=losses,
                        win_pct=win_pct,
                        elo_rating=1500 + (100 * (win_pct - 0.333)),  # Adjust for 3 outcomes
                        recent_form=[]
                    )
                except (IndexError, ValueError, AttributeError):
                    continue

        except requests.RequestException as e:
            print(f"Error fetching {league} standings: {e}")

        return standings

    @staticmethod
    def poisson_goal_probability(expected_goals: float, goals: int) -> float:
        """
        Calculate probability of exactly N goals using Poisson distribution

        Args:
            expected_goals: Expected goals for the team
            goals: Number of goals to calculate probability for

        Returns:
            Probability of scoring exactly N goals
        """
        if expected_goals <= 0:
            return 0
        return _poisson_pmf(goals, expected_goals)

    @staticmethod
    def expected_goals_team_strength(elo_rating: float, opponent_elo: float,
                                     league_avg_goals: float = 2.8) -> float:
        """
        Estimate expected goals for a team based on ELO and league average

        Args:
            elo_rating: Team's ELO rating
            opponent_elo: Opponent's ELO rating
            league_avg_goals: Average goals per team per match

        Returns:
            Expected goals for the team
        """
        # Strength difference
        elo_diff = elo_rating - opponent_elo
        strength_mult = 10 ** (elo_diff / 600)  # Normalize for soccer

        return league_avg_goals * strength_mult

    @staticmethod
    def calculate_match_probabilities(team_a_elo: float, team_b_elo: float,
                                      league_avg_goals: float = 2.8,
                                      home_advantage: float = 1.5) -> Tuple[float, float, float]:
        """
        Calculate win/draw/loss probabilities for a soccer match

        Returns:
            (prob_team_a_wins, prob_draw, prob_team_b_wins)
        """
        # Expected goals
        exp_goals_a = SoccerAnalyzer.expected_goals_team_strength(
            team_a_elo + 30, team_b_elo, league_avg_goals
        )
        exp_goals_b = SoccerAnalyzer.expected_goals_team_strength(
            team_b_elo, team_a_elo + 30, league_avg_goals
        )

        # Calculate probabilities using Poisson
        prob_a_wins = 0
        prob_draw = 0
        prob_b_wins = 0

        # Sum probabilities for each score combination
        for goals_a in range(0, 10):
            for goals_b in range(0, 10):
                prob = (SoccerAnalyzer.poisson_goal_probability(exp_goals_a, goals_a) *
                       SoccerAnalyzer.poisson_goal_probability(exp_goals_b, goals_b))

                if goals_a > goals_b:
                    prob_a_wins += prob
                elif goals_a == goals_b:
                    prob_draw += prob
                else:
                    prob_b_wins += prob

        # Normalize
        total = prob_a_wins + prob_draw + prob_b_wins
        if total > 0:
            prob_a_wins /= total
            prob_draw /= total
            prob_b_wins /= total

        return prob_a_wins, prob_draw, prob_b_wins


# ============================================================================
# BETTING ANALYSIS ENGINE
# ============================================================================

class BettingAnalysisEngine:
    """Main betting analysis engine"""

    def __init__(self, bankroll: float = 1000.0):
        """
        Initialize the betting engine

        Args:
            bankroll: Total bankroll for bankroll management
        """
        self.bankroll = bankroll
        self.picks = []
        self.nba_standings = {}
        self.mlb_standings = {}
        self.epl_standings = {}
        self.la_liga_standings = {}

    def load_all_standings(self) -> None:
        """Load standings from all sports"""
        print("Loading NBA standings...")
        self.nba_standings = NBAAnalyzer.fetch_standings()

        print("Loading MLB standings...")
        self.mlb_standings = MLBAnalyzer.fetch_standings()

        print("Loading soccer standings...")
        self.epl_standings = SoccerAnalyzer.fetch_standings('EPL')
        self.la_liga_standings = SoccerAnalyzer.fetch_standings('La Liga')

    def assess_confidence_level(self, ev: float, prob_diff: float,
                                data_recency: int = 0) -> Tuple[int, str]:
        """
        Assess confidence level (1-10) and risk classification

        Args:
            ev: Expected value of the bet
            prob_diff: Difference between your probability and implied probability
            data_recency: Hours since data was last updated (0 = fresh)

        Returns:
            (confidence_level, risk_classification)
        """
        confidence = 5  # Base confidence

        # Adjust for EV
        if ev > 0.15:
            confidence += 3
        elif ev > 0.10:
            confidence += 2
        elif ev > 0.05:
            confidence += 1
        elif ev < -0.10:
            confidence -= 2
        elif ev < -0.05:
            confidence -= 1

        # Adjust for probability difference
        if prob_diff > 0.15:
            confidence += 1
        if prob_diff < -0.10:
            confidence -= 2

        # Adjust for data recency
        if data_recency > 24:
            confidence -= 1

        # Clamp to 1-10
        confidence = max(1, min(10, confidence))

        # Classify risk
        if confidence >= 8:
            risk_level = "Low"
        elif confidence >= 6:
            risk_level = "Medium"
        else:
            risk_level = "High"

        return confidence, risk_level

    def analyze_nba_matchup(self, team_a: str, team_b: str,
                           odds_a: float) -> Optional[PickAnalysis]:
        """
        Analyze an NBA matchup and generate a pick

        Args:
            team_a: Team A name
            team_b: Team B name
            odds_a: American odds for team A

        Returns:
            PickAnalysis object or None
        """
        if team_a not in self.nba_standings or team_b not in self.nba_standings:
            return None

        stats_a = self.nba_standings[team_a]
        stats_b = self.nba_standings[team_b]

        # Calculate ELO win probability
        elo_prob_a = ELORating.expected_win_probability(
            stats_a.elo_rating, stats_b.elo_rating, home=True
        )

        # Implied probability from odds
        implied_prob_a = american_to_probability(odds_a)
        decimal_odds_a = american_to_decimal(odds_a)

        # Expected value
        ev = calculate_ev(elo_prob_a, implied_prob_a, decimal_odds_a)

        # Determine pick
        pick = team_a if ev > 0 else team_b

        confidence, risk_level = self.assess_confidence_level(
            ev, elo_prob_a - implied_prob_a
        )

        # Bankroll management
        kelly_pct = kelly_criterion(elo_prob_a, decimal_odds_a)
        recommended_bet = self.bankroll * kelly_pct
        recommended_bet = max(50, min(100, recommended_bet))  # $50-$100 range

        reasoning = [
            f"{team_a} win %: {elo_prob_a:.1%} vs implied {implied_prob_a:.1%}",
            f"ELO ratings: {team_a} {stats_a.elo_rating:.0f}, {team_b} {stats_b.elo_rating:.0f}",
            f"W-L records: {team_a} {stats_a.wins}-{stats_a.losses}, {team_b} {stats_b.wins}-{stats_b.losses}",
        ]

        return PickAnalysis(
            sport='NBA',
            match=f"{team_a} vs {team_b}",
            team_a=team_a,
            team_b=team_b,
            pick=pick,
            your_probability=elo_prob_a,
            implied_probability=implied_prob_a,
            expected_value=ev,
            odds=decimal_odds_a,
            recommended_bet_amount=recommended_bet,
            confidence=confidence,
            risk_level=risk_level,
            reasoning=reasoning,
            timestamp=datetime.now().isoformat()
        )

    def analyze_mlb_matchup(self, team_a: str, team_b: str,
                           odds_a: float) -> Optional[PickAnalysis]:
        """
        Analyze an MLB matchup and generate a pick

        Args:
            team_a: Home team
            team_b: Away team
            odds_a: American odds for team A

        Returns:
            PickAnalysis object or None
        """
        if team_a not in self.mlb_standings or team_b not in self.mlb_standings:
            return None

        stats_a = self.mlb_standings[team_a]
        stats_b = self.mlb_standings[team_b]

        # Base ELO probability
        elo_prob_a = ELORating.expected_win_probability(
            stats_a.elo_rating, stats_b.elo_rating, home=True
        )

        # In real implementation, would include pitcher analysis
        # For now, use simple adjustment
        pitcher_adj = 0.02  # Placeholder
        final_prob_a = min(0.95, max(0.05, elo_prob_a + pitcher_adj))

        implied_prob_a = american_to_probability(odds_a)
        decimal_odds_a = american_to_decimal(odds_a)

        ev = calculate_ev(final_prob_a, implied_prob_a, decimal_odds_a)

        pick = team_a if ev > 0 else team_b

        confidence, risk_level = self.assess_confidence_level(
            ev, final_prob_a - implied_prob_a
        )

        kelly_pct = kelly_criterion(final_prob_a, decimal_odds_a)
        recommended_bet = self.bankroll * kelly_pct
        recommended_bet = max(50, min(100, recommended_bet))

        reasoning = [
            f"{team_a} win %: {final_prob_a:.1%} vs implied {implied_prob_a:.1%}",
            f"W-L records: {team_a} {stats_a.wins}-{stats_a.losses}, {team_b} {stats_b.wins}-{stats_b.losses}",
        ]

        return PickAnalysis(
            sport='MLB',
            match=f"{team_a} vs {team_b}",
            team_a=team_a,
            team_b=team_b,
            pick=pick,
            your_probability=final_prob_a,
            implied_probability=implied_prob_a,
            expected_value=ev,
            odds=decimal_odds_a,
            recommended_bet_amount=recommended_bet,
            confidence=confidence,
            risk_level=risk_level,
            reasoning=reasoning,
            timestamp=datetime.now().isoformat()
        )

    def analyze_soccer_matchup(self, team_a: str, team_b: str,
                              odds_a: float, league: str = 'EPL') -> Optional[PickAnalysis]:
        """
        Analyze a soccer matchup and generate a pick

        Args:
            team_a: Home team
            team_b: Away team
            odds_a: American odds for team A to win
            league: 'EPL' or 'La Liga'

        Returns:
            PickAnalysis object or None
        """
        standings = self.epl_standings if league == 'EPL' else self.la_liga_standings

        if team_a not in standings or team_b not in standings:
            return None

        stats_a = standings[team_a]
        stats_b = standings[team_b]

        # Calculate match probabilities using Poisson
        prob_a_wins, prob_draw, prob_b_wins = SoccerAnalyzer.calculate_match_probabilities(
            stats_a.elo_rating, stats_b.elo_rating
        )

        # Convert to 3-way implied probability
        implied_prob_a = american_to_probability(odds_a)
        decimal_odds_a = american_to_decimal(odds_a)

        ev = calculate_ev(prob_a_wins, implied_prob_a, decimal_odds_a)

        pick = team_a if ev > 0 else team_b

        confidence, risk_level = self.assess_confidence_level(
            ev, prob_a_wins - implied_prob_a
        )

        kelly_pct = kelly_criterion(prob_a_wins, decimal_odds_a)
        recommended_bet = self.bankroll * kelly_pct
        recommended_bet = max(50, min(100, recommended_bet))

        reasoning = [
            f"{team_a} win %: {prob_a_wins:.1%} (draw: {prob_draw:.1%}, loss: {prob_b_wins:.1%})",
            f"Implied probability: {implied_prob_a:.1%}",
            f"Expected goals analysis from ELO ratings",
        ]

        return PickAnalysis(
            sport=f'Soccer ({league})',
            match=f"{team_a} vs {team_b}",
            team_a=team_a,
            team_b=team_b,
            pick=pick,
            your_probability=prob_a_wins,
            implied_probability=implied_prob_a,
            expected_value=ev,
            odds=decimal_odds_a,
            recommended_bet_amount=recommended_bet,
            confidence=confidence,
            risk_level=risk_level,
            reasoning=reasoning,
            timestamp=datetime.now().isoformat()
        )

    def get_top_picks(self, min_ev: float = 0.05, top_n: int = 10) -> List[PickAnalysis]:
        """
        Get top picks filtered by minimum EV and sorted by confidence

        Args:
            min_ev: Minimum expected value threshold
            top_n: Number of top picks to return

        Returns:
            List of PickAnalysis objects
        """
        filtered = [p for p in self.picks if p.expected_value >= min_ev]
        sorted_picks = sorted(
            filtered,
            key=lambda x: (x.confidence, x.expected_value),
            reverse=True
        )
        return sorted_picks[:top_n]

    def generate_daily_report(self, min_ev: float = 0.05) -> Dict:
        """
        Generate daily betting report

        Returns:
            Dictionary with report data
        """
        top_picks = self.get_top_picks(min_ev=min_ev)

        total_recommended_bet = sum(p.recommended_bet_amount for p in top_picks)
        expected_return = sum(
            p.recommended_bet_amount * p.expected_value for p in top_picks
        )

        report = {
            'generated_at': datetime.now().isoformat(),
            'total_picks': len(self.picks),
            'qualified_picks': len(top_picks),
            'top_picks': [p.to_dict() for p in top_picks],
            'summary': {
                'total_bankroll': self.bankroll,
                'total_recommended_bet': total_recommended_bet,
                'expected_weekly_return': expected_return * 5,  # Rough estimate for 5 bets/week
                'average_confidence': np.mean([p.confidence for p in top_picks]) if top_picks else 0,
                'low_risk_picks': len([p for p in top_picks if p.risk_level == 'Low']),
                'medium_risk_picks': len([p for p in top_picks if p.risk_level == 'Medium']),
                'high_risk_picks': len([p for p in top_picks if p.risk_level == 'High']),
            },
            'bankroll_recommendations': {
                'daily_bet_limit': total_recommended_bet,
                'weekly_loss_limit': self.bankroll * 0.10,  # Stop after 10% loss
                'weekly_profit_target': 500.0,
                'bet_sizing': 'Kelly Criterion with 25% fractional adjustment',
            }
        }

        return report

    def save_picks_to_json(self, filepath: str, min_ev: float = 0.05) -> None:
        """Save picks to JSON file"""
        report = self.generate_daily_report(min_ev)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"Picks saved to {filepath}")

    def print_picks_summary(self, min_ev: float = 0.05) -> None:
        """Print summary of picks to console"""
        top_picks = self.get_top_picks(min_ev=min_ev)

        print("\n" + "=" * 80)
        print("DAILY BETTING PICKS SUMMARY")
        print("=" * 80)
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Bankroll: ${self.bankroll:.2f}")
        print()

        if not top_picks:
            print("No picks with sufficient EV found.")
            return

        for i, pick in enumerate(top_picks, 1):
            print(f"\n{i}. {pick.match} ({pick.sport})")
            print(f"   Pick: {pick.pick}")
            print(f"   Your Probability: {pick.your_probability:.1%}")
            print(f"   Implied Probability: {pick.implied_probability:.1%}")
            print(f"   Expected Value: {pick.expected_value:+.1%}")
            print(f"   Odds: {pick.odds:.2f} ({pick.odds - 1:+.2f})")
            print(f"   Recommended Bet: ${pick.recommended_bet_amount:.2f}")
            print(f"   Confidence: {pick.confidence}/10 ({pick.risk_level} Risk)")
            for reason in pick.reasoning:
                print(f"   - {reason}")

        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        total_bet = sum(p.recommended_bet_amount for p in top_picks)
        expected_ev = sum(p.recommended_bet_amount * p.expected_value for p in top_picks)

        print(f"Total Picks: {len(top_picks)}")
        print(f"Total Recommended Bet: ${total_bet:.2f}")
        print(f"Expected Value (Daily): ${expected_ev:.2f}")
        print(f"Expected Value (Weekly): ${expected_ev * 5:.2f}")
        print(f"Target Weekly Profit: $500.00")
        print(f"Coverage: {expected_ev * 5 / 500 * 100:.0f}% of weekly target")
        print("=" * 80 + "\n")


# ============================================================================
# EXAMPLE USAGE AND DEMONSTRATION
# ============================================================================

def main():
    """Example usage of the betting analysis engine"""

    print("Initializing Sports Betting Analysis Engine...")
    print("-" * 80)

    # Create engine with $1000 bankroll
    engine = BettingAnalysisEngine(bankroll=1000.0)

    # Load standings
    engine.load_all_standings()

    # Example: Add some sample picks for demonstration
    # In real usage, these would come from actual Kalshi odds

    print("\nAnalyzing sample matchups...")
    print("-" * 80)

    # Note: These are examples - replace with actual team names and odds from Kalshi
    sample_picks = []

    # Add NBA picks if teams available
    if len(engine.nba_standings) >= 2:
        teams = list(engine.nba_standings.keys())[:2]
        if len(teams) >= 2:
            pick = engine.analyze_nba_matchup(teams[0], teams[1], -110)
            if pick:
                sample_picks.append(pick)
                engine.picks.append(pick)

    # Add MLB picks if teams available
    if len(engine.mlb_standings) >= 2:
        teams = list(engine.mlb_standings.keys())[:2]
        if len(teams) >= 2:
            pick = engine.analyze_mlb_matchup(teams[0], teams[1], -110)
            if pick:
                sample_picks.append(pick)
                engine.picks.append(pick)

    # Add soccer picks if teams available
    if len(engine.epl_standings) >= 2:
        teams = list(engine.epl_standings.keys())[:2]
        if len(teams) >= 2:
            pick = engine.analyze_soccer_matchup(teams[0], teams[1], -110, 'EPL')
            if pick:
                sample_picks.append(pick)
                engine.picks.append(pick)

    # Print summary
    if sample_picks:
        engine.print_picks_summary()
    else:
        print("\nNo picks generated (web scraping may require updates)")

    print("\nEngine ready for use!")
    print("Call engine.analyze_nba_matchup(), analyze_mlb_matchup(), or analyze_soccer_matchup()")
    print("Use engine.save_picks_to_json(filepath) to export picks")


if __name__ == '__main__':
    main()
