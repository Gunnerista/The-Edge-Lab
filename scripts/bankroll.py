"""
Sports Betting Bankroll Management Tool
========================================

A comprehensive bankroll management system for sports bettors using Kalshi.
Implements Kelly Criterion for optimal bet sizing, tracks daily/weekly performance,
and enforces risk management rules.

Author: Sports Betting System
Version: 1.0
"""

import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional, Dict, List, Tuple
from pathlib import Path
import math


@dataclass
class DailyRecord:
    """Records daily betting performance."""
    date: str
    starting_balance: float
    ending_balance: float
    pnl: float
    num_bets: int
    wins: int
    losses: int
    win_rate: float
    streaks: Dict[str, int]  # {'current_streak': X, 'streak_type': 'win'/'loss'}

    def to_dict(self):
        return asdict(self)


class KellyCriterion:
    """
    Kelly Criterion Calculator for optimal bet sizing.

    The Kelly Criterion formula: f* = (bp - q) / b
    where:
      - f* = optimal fraction of bankroll to bet
      - b = odds (decimal format) - 1
      - p = probability of winning
      - q = probability of losing (1 - p)
    """

    @staticmethod
    def calculate_kelly(
        probability: float,
        decimal_odds: float,
        kelly_fraction: float = 1.0
    ) -> float:
        """
        Calculate Kelly Criterion fraction.

        Args:
            probability: Probability of winning (0.0 to 1.0)
            decimal_odds: Decimal odds (e.g., 2.0 for -100 in American)
            kelly_fraction: Fraction of Kelly to use (1.0=full, 0.5=half, 0.25=quarter)

        Returns:
            Fraction of bankroll to bet (0.0 to 1.0)

        Raises:
            ValueError: If inputs are invalid
        """
        if not (0 < probability < 1):
            raise ValueError("Probability must be between 0 and 1")
        if decimal_odds <= 0:
            raise ValueError("Decimal odds must be positive")
        if not (0 < kelly_fraction <= 1):
            raise ValueError("Kelly fraction must be between 0 and 1")

        b = decimal_odds - 1  # Convert to betting odds
        p = probability
        q = 1 - probability

        # Kelly formula
        kelly = (b * p - q) / b

        # Apply kelly fraction for more conservative betting
        kelly_adjusted = kelly * kelly_fraction

        # Never bet more than 100% or go negative
        return max(0, min(kelly_adjusted, 1.0))

    @staticmethod
    def american_to_decimal(american_odds: float) -> float:
        """
        Convert American odds to decimal format.

        Args:
            american_odds: American odds (e.g., -110, +150)

        Returns:
            Decimal odds (e.g., 1.909)
        """
        if american_odds == 0:
            raise ValueError("American odds cannot be zero")

        if american_odds > 0:
            # Positive odds: decimal = (american + 100) / 100
            return (american_odds + 100) / 100
        else:
            # Negative odds: decimal = 100 / (-american) + 1
            return (100 / (-american_odds)) + 1

    @staticmethod
    def decimal_to_american(decimal_odds: float) -> float:
        """Convert decimal odds to American format."""
        if decimal_odds < 1:
            raise ValueError("Decimal odds must be >= 1")

        if decimal_odds >= 2:
            # Positive american odds
            return (decimal_odds - 1) * 100
        else:
            # Negative american odds
            return -100 / (decimal_odds - 1)


def kelly_bet_size(edge, odds, bankroll, kelly_fraction=0.25):
    """
    Fractional Kelly Criterion for Kalshi binary markets.

    Parameters:
        edge: calculated edge (0.20 = 20% edge)
        odds: payout ratio.
              YES bet: (1 - entry_price) / entry_price
              NO bet:  entry_price / (1 - entry_price)
        bankroll: current virtual bankroll ($)
        kelly_fraction: Kelly fraction (0.25 = quarter Kelly)

    Returns:
        bet_size in dollars
    """
    b = odds
    if b <= 0:
        return 0

    p = edge + (1 / (1 + b))
    q = 1 - p

    kelly_full = (b * p - q) / b

    if kelly_full <= 0:
        return 0

    kelly_adjusted = kelly_full * kelly_fraction

    max_bet_pct = 0.05
    bet_pct = min(kelly_adjusted, max_bet_pct)

    bet_size = bankroll * bet_pct

    min_bet = 1.0
    max_bet = 25.0

    return max(min_bet, min(bet_size, max_bet))


class BankrollTracker:
    """
    Main bankroll management and tracking system.

    Manages:
    - Bankroll state and history
    - Daily/weekly performance tracking
    - Win rate and streak calculations
    - Risk management enforcement
    """

    def __init__(
        self,
        starting_bankroll: float,
        max_bet_pct: float = 0.05,
        daily_loss_limit_pct: float = 0.10,
        weekly_target: float = 500.0,
        data_file: Optional[str] = None
    ):
        """
        Initialize bankroll tracker.

        Args:
            starting_bankroll: Initial bankroll amount
            max_bet_pct: Max bet as % of current bankroll (default 5%)
            daily_loss_limit_pct: Daily loss limit as % of bankroll (default 10%)
            weekly_target: Weekly profit target in dollars
            data_file: Path to JSON file for persistence
        """
        self.starting_bankroll = starting_bankroll
        self.current_bankroll = starting_bankroll
        self.max_bet_pct = max_bet_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.weekly_target = weekly_target
        self.data_file = data_file

        # Session tracking
        self.session_start_balance = starting_bankroll
        self.session_start_date = datetime.now().date()
        self.week_start_date = self._get_week_start()

        # Daily tracking
        self.daily_records: List[DailyRecord] = []
        self.today_bets: List[Dict] = []
        self.today_start_balance = starting_bankroll

        # Streak tracking
        self.current_streak = 0  # Positive for wins, negative for losses
        self.streak_type = None  # 'win' or 'loss'

        # Performance metrics
        self.total_bets = 0
        self.total_wins = 0
        self.total_losses = 0

    @staticmethod
    def _get_week_start() -> datetime.date:
        """Get the start of current week (Monday)."""
        today = datetime.now().date()
        return today - timedelta(days=today.weekday())

    def load_from_file(self) -> bool:
        """
        Load bankroll state from JSON file.

        Returns:
            True if loaded successfully, False otherwise
        """
        if not self.data_file or not os.path.exists(self.data_file):
            return False

        try:
            with open(self.data_file, 'r') as f:
                data = json.load(f)

            self.starting_bankroll = data['starting_bankroll']
            self.current_bankroll = data['current_bankroll']
            self.session_start_balance = data['session_start_balance']
            self.session_start_date = datetime.fromisoformat(
                data['session_start_date']
            ).date()
            self.week_start_date = datetime.fromisoformat(
                data['week_start_date']
            ).date()
            self.today_start_balance = data['today_start_balance']
            self.current_streak = data['current_streak']
            self.streak_type = data['streak_type']
            self.total_bets = data['total_bets']
            self.total_wins = data['total_wins']
            self.total_losses = data['total_losses']

            # Reconstruct daily records
            self.daily_records = [
                DailyRecord(**record) for record in data.get('daily_records', [])
            ]

            return True
        except Exception as e:
            print(f"Error loading bankroll data: {e}")
            return False

    def save_to_file(self) -> bool:
        """
        Save bankroll state to JSON file.

        Returns:
            True if saved successfully, False otherwise
        """
        if not self.data_file:
            return False

        try:
            # Create parent directories if needed
            Path(self.data_file).parent.mkdir(parents=True, exist_ok=True)

            data = {
                'starting_bankroll': self.starting_bankroll,
                'current_bankroll': self.current_bankroll,
                'session_start_balance': self.session_start_balance,
                'session_start_date': self.session_start_date.isoformat(),
                'week_start_date': self.week_start_date.isoformat(),
                'today_start_balance': self.today_start_balance,
                'current_streak': self.current_streak,
                'streak_type': self.streak_type,
                'total_bets': self.total_bets,
                'total_wins': self.total_wins,
                'total_losses': self.total_losses,
                'daily_records': [record.to_dict() for record in self.daily_records],
                'last_updated': datetime.now().isoformat()
            }

            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)

            return True
        except Exception as e:
            print(f"Error saving bankroll data: {e}")
            return False

    def add_bet(
        self,
        amount: float,
        win: bool,
        odds: float,
        description: str = ""
    ) -> Dict:
        """
        Record a bet result.

        Args:
            amount: Amount bet
            win: Whether the bet won
            odds: Decimal odds (for payout calculation)
            description: Optional description of the bet

        Returns:
            Dictionary with bet details and updated bankroll info
        """
        if amount <= 0:
            raise ValueError("Bet amount must be positive")

        # Calculate payout
        if win:
            payout = amount * odds
            pnl = payout - amount
            self.total_wins += 1
            self._update_streak(True)
        else:
            payout = 0
            pnl = -amount
            self.total_losses += 1
            self._update_streak(False)

        # Update bankroll
        self.current_bankroll += pnl
        self.total_bets += 1

        # Record bet
        bet_record = {
            'timestamp': datetime.now().isoformat(),
            'amount': amount,
            'win': win,
            'odds': odds,
            'payout': payout,
            'pnl': pnl,
            'bankroll_after': self.current_bankroll,
            'description': description
        }

        self.today_bets.append(bet_record)

        return {
            'success': True,
            'bet': bet_record,
            'bankroll': self.current_bankroll,
            'daily_pnl': self.get_daily_pnl(),
            'streak': f"{abs(self.current_streak)} {'wins' if self.streak_type == 'win' else 'losses'}"
        }

    def _update_streak(self, won: bool):
        """Update win/loss streak."""
        if self.streak_type is None:
            self.current_streak = 1
            self.streak_type = 'win' if won else 'loss'
        elif (won and self.streak_type == 'win') or (not won and self.streak_type == 'loss'):
            self.current_streak += 1
        else:
            self.current_streak = 1
            self.streak_type = 'win' if won else 'loss'

    def get_daily_pnl(self) -> float:
        """Get today's profit/loss."""
        return self.current_bankroll - self.today_start_balance

    def get_daily_roi(self) -> float:
        """Get today's ROI as percentage."""
        if self.today_start_balance == 0:
            return 0
        return (self.get_daily_pnl() / self.today_start_balance) * 100

    def get_session_pnl(self) -> float:
        """Get session profit/loss."""
        return self.current_bankroll - self.session_start_balance

    def get_session_roi(self) -> float:
        """Get session ROI as percentage."""
        if self.session_start_balance == 0:
            return 0
        return (self.get_session_pnl() / self.session_start_balance) * 100

    def get_week_pnl(self) -> float:
        """Get weekly profit/loss."""
        week_bets = [r for r in self.daily_records
                     if datetime.fromisoformat(r.date).date() >= self.week_start_date]
        if not week_bets:
            return self.get_daily_pnl()  # Use today if no previous records

        total_pnl = sum(r.pnl for r in week_bets)
        # Add today's P&L
        return total_pnl + self.get_daily_pnl()

    def get_win_rate(self) -> float:
        """Get win rate as percentage."""
        if self.total_bets == 0:
            return 0
        return (self.total_wins / self.total_bets) * 100

    def get_max_bet(self) -> float:
        """Get maximum allowed bet size based on bankroll."""
        return self.current_bankroll * self.max_bet_pct

    def get_daily_loss_limit(self) -> float:
        """Get daily loss limit."""
        return self.starting_bankroll * self.daily_loss_limit_pct

    def get_remaining_daily_loss_capacity(self) -> float:
        """Get remaining loss capacity for today."""
        daily_loss = -self.get_daily_pnl()  # Convert to positive
        return max(0, self.get_daily_loss_limit() - daily_loss)

    def validate_bet_size(self, bet_amount: float) -> Tuple[bool, str]:
        """
        Validate if bet size is within risk management rules.

        Returns:
            Tuple of (is_valid, message)
        """
        max_bet = self.get_max_bet()
        if bet_amount > max_bet:
            return False, f"Bet exceeds max bet of ${max_bet:.2f}"

        daily_pnl = self.get_daily_pnl()
        if daily_pnl < 0:  # Already losing today
            remaining_capacity = self.get_remaining_daily_loss_capacity()
            if abs(daily_pnl) + bet_amount > self.get_daily_loss_limit():
                return False, (
                    f"Bet would exceed daily loss limit. "
                    f"Remaining capacity: ${remaining_capacity:.2f}"
                )

        return True, "Bet size is valid"

    def recommend_bet_size(
        self,
        probability: float,
        decimal_odds: float,
        kelly_fraction: float = 0.25
    ) -> Dict:
        """
        Recommend bet size using Kelly Criterion.

        Args:
            probability: Probability of winning (0 to 1)
            decimal_odds: Decimal odds
            kelly_fraction: Kelly fraction to use (0.25=quarter kelly, default)

        Returns:
            Dictionary with recommendation details
        """
        kelly_fraction_result = KellyCriterion.calculate_kelly(
            probability, decimal_odds, kelly_fraction
        )

        kelly_bet = self.current_bankroll * kelly_fraction_result
        max_bet = self.get_max_bet()

        # Use the smaller of kelly bet and max bet
        recommended_bet = min(kelly_bet, max_bet)

        # Round to nearest $5 for ease of betting
        recommended_bet = round(recommended_bet / 5) * 5

        # Ensure bet is within $50-100 range if possible
        if recommended_bet < 50:
            recommended_bet = 50
        elif recommended_bet > 100 and recommended_bet < 100:
            recommended_bet = 100

        # Validate the recommended bet
        is_valid, message = self.validate_bet_size(recommended_bet)

        return {
            'recommended_bet': recommended_bet,
            'kelly_fraction': kelly_fraction_result,
            'kelly_bet': kelly_bet,
            'max_bet': max_bet,
            'expected_value': recommended_bet * (decimal_odds - 1) * probability - recommended_bet * (1 - probability),
            'is_valid': is_valid,
            'validation_message': message,
            'probability': probability,
            'odds': decimal_odds
        }

    def close_day(self) -> DailyRecord:
        """
        Close out the trading day and record daily statistics.

        Returns:
            DailyRecord for the day
        """
        daily_pnl = self.get_daily_pnl()
        daily_wins = sum(1 for b in self.today_bets if b['win'])
        daily_losses = len(self.today_bets) - daily_wins
        daily_win_rate = (daily_wins / len(self.today_bets) * 100) if self.today_bets else 0

        record = DailyRecord(
            date=datetime.now().date().isoformat(),
            starting_balance=self.today_start_balance,
            ending_balance=self.current_bankroll,
            pnl=daily_pnl,
            num_bets=len(self.today_bets),
            wins=daily_wins,
            losses=daily_losses,
            win_rate=daily_win_rate,
            streaks={
                'current_streak': abs(self.current_streak),
                'streak_type': self.streak_type or 'none'
            }
        )

        self.daily_records.append(record)

        # Reset daily tracking
        self.today_bets = []
        self.today_start_balance = self.current_bankroll

        return record

    def get_weekly_report(self) -> Dict:
        """
        Generate comprehensive weekly report.

        Returns:
            Dictionary with weekly performance metrics
        """
        week_pnl = self.get_week_pnl()
        pace = week_pnl / self.weekly_target if self.weekly_target > 0 else 0

        days_in_week = (datetime.now().date() - self.week_start_date).days + 1
        daily_rate = week_pnl / days_in_week if days_in_week > 0 else 0

        days_remaining = 7 - days_in_week
        projected_week_pnl = week_pnl + (daily_rate * days_remaining)

        # Calculate needed daily average to hit target
        remaining_to_target = self.weekly_target - week_pnl
        needed_daily = remaining_to_target / days_remaining if days_remaining > 0 else 0

        return {
            'week_start_date': self.week_start_date.isoformat(),
            'days_in_week': days_in_week,
            'days_remaining': days_remaining,
            'weekly_target': self.weekly_target,
            'weekly_pnl': week_pnl,
            'pace_to_target': pace,
            'pace_percentage': f"{pace * 100:.1f}%",
            'daily_average': daily_rate,
            'projected_weekly_pnl': projected_week_pnl,
            'needed_daily_average': needed_daily,
            'is_on_track': pace >= (days_in_week / 7),
            'status': 'ON TRACK' if pace >= (days_in_week / 7) else 'BEHIND',
            'recommendation': self._get_weekly_recommendation(pace, days_remaining)
        }

    def _get_weekly_recommendation(self, pace: float, days_remaining: int) -> str:
        """Generate recommendation based on weekly pace."""
        if pace >= 1.0:
            return f"Target achieved! Maintain current strategy. {days_remaining} days remaining."
        elif pace >= 0.75:
            return "On pace. Maintain current bet sizing and strategy."
        elif pace >= 0.5:
            return "Slightly behind pace. Consider slightly larger bet sizes or increase frequency."
        else:
            return "Significantly behind pace. Increase bet frequency or sizing, or reassess strategy."

    def get_summary(self) -> Dict:
        """
        Get comprehensive bankroll summary.

        Returns:
            Dictionary with all key metrics
        """
        return {
            'bankroll': {
                'starting': self.starting_bankroll,
                'current': self.current_bankroll,
                'profit_loss': self.current_bankroll - self.starting_bankroll
            },
            'session': {
                'start_date': self.session_start_date.isoformat(),
                'start_balance': self.session_start_balance,
                'pnl': self.get_session_pnl(),
                'roi': f"{self.get_session_roi():.2f}%"
            },
            'daily': {
                'start_balance': self.today_start_balance,
                'pnl': self.get_daily_pnl(),
                'roi': f"{self.get_daily_roi():.2f}%",
                'bets_placed': len(self.today_bets)
            },
            'performance': {
                'total_bets': self.total_bets,
                'wins': self.total_wins,
                'losses': self.total_losses,
                'win_rate': f"{self.get_win_rate():.2f}%",
                'current_streak': f"{abs(self.current_streak)} {'wins' if self.streak_type == 'win' else 'losses'}"
            },
            'risk_management': {
                'max_bet': self.get_max_bet(),
                'daily_loss_limit': self.get_daily_loss_limit(),
                'remaining_daily_loss_capacity': self.get_remaining_daily_loss_capacity()
            },
            'weekly': self.get_weekly_report()
        }


def main():
    """Example usage of the bankroll tracker."""
    print("Sports Betting Bankroll Management Tool")
    print("=" * 50)

    # Initialize tracker
    tracker = BankrollTracker(
        starting_bankroll=5000,
        max_bet_pct=0.05,
        daily_loss_limit_pct=0.10,
        weekly_target=500.0,
        data_file="bankroll_data.json"
    )

    # Load existing data if available
    if tracker.load_from_file():
        print("Loaded previous bankroll data")
    else:
        print("Starting fresh bankroll")

    print("\n" + "=" * 50)
    print("CURRENT SUMMARY")
    print("=" * 50)
    summary = tracker.get_summary()
    print(f"Starting Bankroll: ${summary['bankroll']['starting']:.2f}")
    print(f"Current Bankroll: ${summary['bankroll']['current']:.2f}")
    print(f"Total Profit/Loss: ${summary['bankroll']['profit_loss']:.2f}")
    print(f"Win Rate: {summary['performance']['win_rate']}")
    print(f"Total Bets: {summary['performance']['total_bets']}")

    print("\n" + "=" * 50)
    print("WEEKLY REPORT")
    print("=" * 50)
    weekly = summary['weekly']
    print(f"Weekly Target: ${weekly['weekly_target']:.2f}")
    print(f"Weekly P&L: ${weekly['weekly_pnl']:.2f}")
    print(f"Pace: {weekly['pace_percentage']}")
    print(f"Status: {weekly['status']}")
    print(f"Recommendation: {weekly['recommendation']}")

    print("\n" + "=" * 50)
    print("RISK MANAGEMENT")
    print("=" * 50)
    risk = summary['risk_management']
    print(f"Max Bet Size: ${risk['max_bet']:.2f}")
    print(f"Daily Loss Limit: ${risk['daily_loss_limit']:.2f}")
    print(f"Remaining Daily Loss Capacity: ${risk['remaining_daily_loss_capacity']:.2f}")

    # Example: Recommend bet size
    print("\n" + "=" * 50)
    print("BET RECOMMENDATION EXAMPLE")
    print("=" * 50)
    print("Scenario: 55% win probability, -110 American odds")

    decimal_odds = KellyCriterion.american_to_decimal(-110)
    recommendation = tracker.recommend_bet_size(
        probability=0.55,
        decimal_odds=decimal_odds,
        kelly_fraction=0.25
    )

    print(f"Decimal Odds: {decimal_odds:.3f}")
    print(f"Recommended Bet Size: ${recommendation['recommended_bet']:.2f}")
    print(f"Expected Value: ${recommendation['expected_value']:.2f}")
    print(f"Valid: {recommendation['is_valid']}")
    if not recommendation['is_valid']:
        print(f"Message: {recommendation['validation_message']}")


if __name__ == "__main__":
    main()
