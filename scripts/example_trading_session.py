#!/usr/bin/env python3
"""
Example Trading Session
=======================

Demonstrates a realistic day of sports betting with the bankroll tool.
Shows how to integrate Kelly Criterion for bet sizing with daily monitoring.

Run this to see example output for a complete trading session.
"""

from bankroll import BankrollTracker, KellyCriterion
from datetime import datetime
import json


def print_section(title):
    """Print a formatted section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print('=' * 70)


def print_bet_details(bet_num, market, odds_american, probability_est, result):
    """Print formatted bet details."""
    decimal_odds = KellyCriterion.american_to_decimal(odds_american)
    print(f"\n[BET {bet_num}] {market}")
    print(f"  Odds: {odds_american} (decimal: {decimal_odds:.3f})")
    print(f"  Your estimate: {probability_est*100:.0f}% win probability")
    print(f"  Result: {'WON' if result else 'LOST'}")
    return decimal_odds


def main():
    """Run example trading session."""

    print_section("BANKROLL MANAGEMENT SYSTEM - EXAMPLE TRADING SESSION")

    # Initialize tracker
    print("\nInitializing tracker with:")
    print("  Starting bankroll: $5,000")
    print("  Target: $500/week profit")
    print("  Max bet: 5% of bankroll")
    print("  Daily loss limit: 10%")

    tracker = BankrollTracker(
        starting_bankroll=5000,
        max_bet_pct=0.05,
        daily_loss_limit_pct=0.10,
        weekly_target=500.0,
        data_file="/tmp/example_bankroll.json"
    )

    # Load previous data if exists
    if tracker.load_from_file():
        print("✓ Loaded previous session data")
    else:
        print("✓ Starting fresh session")

    # Show opening status
    print_section("OPENING STATUS")
    summary = tracker.get_summary()
    print(f"Current bankroll: ${summary['bankroll']['current']:.2f}")
    print(f"Daily P&L: ${summary['daily']['pnl']:.2f}")
    print(f"Win rate: {summary['performance']['win_rate']}")
    print(f"Max bet allowed: ${tracker.get_max_bet():.2f}")
    print(f"Daily loss capacity: ${tracker.get_remaining_daily_loss_capacity():.2f}")

    # MORNING - Check daily status
    print_section("MORNING MARKET CHECK")
    weekly = tracker.get_weekly_report()
    print(f"Weekly target: ${weekly['weekly_target']:.2f}")
    print(f"Weekly P&L: ${weekly['weekly_pnl']:.2f}")
    print(f"Pace: {weekly['pace_percentage']}")
    print(f"Status: {weekly['status']}")
    print(f"\nRecommendation:\n  {weekly['recommendation']}")

    # BET 1 - High confidence
    print_section("BET 1: TEAM A PLAYOFFS")
    decimal_odds = print_bet_details(
        1,
        "Will Team A make the playoffs?",
        -110,
        0.60,  # 60% estimate
        True   # We won
    )

    rec = tracker.recommend_bet_size(0.60, decimal_odds, kelly_fraction=0.25)
    print(f"\n  Kelly recommends: ${rec['recommended_bet']:.2f}")
    print(f"  Expected value: ${rec['expected_value']:.2f}")

    is_valid, msg = tracker.validate_bet_size(rec['recommended_bet'])
    print(f"  Validation: {'PASS' if is_valid else 'FAIL'} - {msg}")

    result = tracker.add_bet(rec['recommended_bet'], True, decimal_odds, "Team A playoffs")
    print(f"\n  ✓ Bet placed: ${rec['recommended_bet']:.2f}")
    print(f"  Bankroll after: ${result['bankroll']:.2f}")
    print(f"  Streak: {result['streak']}")

    # BET 2 - Medium confidence
    print_section("BET 2: PLAYER OVER POINTS")
    decimal_odds = print_bet_details(
        2,
        "Player X over 18.5 points?",
        -110,
        0.55,  # 55% estimate
        True   # We won
    )

    rec = tracker.recommend_bet_size(0.55, decimal_odds, kelly_fraction=0.25)
    print(f"\n  Kelly recommends: ${rec['recommended_bet']:.2f}")
    print(f"  Expected value: ${rec['expected_value']:.2f}")

    result = tracker.add_bet(rec['recommended_bet'], True, decimal_odds, "Player X over")
    print(f"\n  ✓ Bet placed: ${rec['recommended_bet']:.2f}")
    print(f"  Bankroll after: ${result['bankroll']:.2f}")
    print(f"  Streak: {result['streak']}")

    # Check daily progress
    print_section("MIDDAY CHECK")
    print(f"Bets today: {len(tracker.today_bets)}")
    print(f"Daily P&L: ${tracker.get_daily_pnl():.2f}")
    print(f"Daily ROI: {tracker.get_daily_roi():.2f}%")
    print(f"Loss capacity remaining: ${tracker.get_remaining_daily_loss_capacity():.2f}")

    # BET 3 - Low edge bet (but positive EV)
    print_section("BET 3: GAME TOTAL")
    decimal_odds = print_bet_details(
        3,
        "Game total over 210?",
        -110,
        0.52,  # 52% estimate - small edge
        False  # We lost
    )

    rec = tracker.recommend_bet_size(0.52, decimal_odds, kelly_fraction=0.25)
    print(f"\n  Kelly recommends: ${rec['recommended_bet']:.2f}")
    print(f"  Expected value: ${rec['expected_value']:.2f}")
    print(f"  Note: Very small edge. Consider skipping low-EV bets.")

    result = tracker.add_bet(rec['recommended_bet'], False, decimal_odds, "Game total")
    print(f"\n  ✗ Bet lost: ${rec['recommended_bet']:.2f}")
    print(f"  Bankroll after: ${result['bankroll']:.2f}")
    print(f"  Streak: {result['streak']}")

    # BET 4 - Higher confidence after loss (don't chase!)
    print_section("BET 4: DIVISION WINNER")
    decimal_odds = print_bet_details(
        4,
        "Team B wins division?",
        -110,
        0.58,  # 58% estimate
        True   # We won
    )

    rec = tracker.recommend_bet_size(0.58, decimal_odds, kelly_fraction=0.25)
    print(f"\n  Kelly recommends: ${rec['recommended_bet']:.2f}")
    print(f"  Expected value: ${rec['expected_value']:.2f}")
    print(f"  Note: Increasing size based on confidence, not chasing loss.")

    result = tracker.add_bet(rec['recommended_bet'], True, decimal_odds, "Team B division")
    print(f"\n  ✓ Bet placed: ${rec['recommended_bet']:.2f}")
    print(f"  Bankroll after: ${result['bankroll']:.2f}")
    print(f"  Streak: {result['streak']}")

    # AFTERNOON - Temptation to over-trade
    print_section("AFTERNOON - CONSIDERING MORE BETS")
    print(f"Current bankroll: ${tracker.current_bankroll:.2f}")
    print(f"Daily P&L: ${tracker.get_daily_pnl():.2f} (UP ${abs(tracker.get_daily_pnl()):.2f})")
    print(f"\n⚠ Feeling good, tempted to place more bets")
    print(f"Remember: Quality > Quantity")
    print(f"Only place bets where you have genuine edge")
    print(f"You already have {len(tracker.today_bets)} bets placed")

    # BET 5 - Marginal decision
    print_section("BET 5: PLAYER A vs PLAYER B")
    decimal_odds = print_bet_details(
        5,
        "Player A beats Player B in head-to-head?",
        +100,  # Plus odds
        0.51,  # 51% estimate - very marginal
        False  # We lost
    )

    rec = tracker.recommend_bet_size(0.51, decimal_odds, kelly_fraction=0.25)
    print(f"\n  Kelly recommends: ${rec['recommended_bet']:.2f}")
    print(f"  Expected value: ${rec['expected_value']:.2f}")
    print(f"  ⚠ Very thin edge (51% vs 50% breakeven)")
    print(f"  Consider skipping this one.")

    # But let's say we placed it anyway
    result = tracker.add_bet(rec['recommended_bet'], False, decimal_odds, "Player A vs B")
    print(f"\n  ✗ Bet lost: ${rec['recommended_bet']:.2f}")
    print(f"  Bankroll after: ${result['bankroll']:.2f}")
    print(f"  Streak: {result['streak']}")

    # EOD Summary
    print_section("END OF DAY SUMMARY")
    print(f"Total bets placed: {len(tracker.today_bets)}")

    wins = sum(1 for b in tracker.today_bets if b['win'])
    losses = len(tracker.today_bets) - wins

    print(f"Record: {wins}W - {losses}L")
    print(f"Win rate: {(wins/len(tracker.today_bets)*100):.1f}%")
    print(f"\nStarting bankroll: ${tracker.today_start_balance:.2f}")
    print(f"Ending bankroll: ${tracker.current_bankroll:.2f}")
    print(f"Daily P&L: ${tracker.get_daily_pnl():.2f}")
    print(f"Daily ROI: {tracker.get_daily_roi():.2f}%")

    # Close the day
    daily_record = tracker.close_day()
    print(f"\n✓ Day closed and recorded")

    # Weekly performance
    print_section("WEEKLY PERFORMANCE")
    summary = tracker.get_summary()
    weekly = summary['weekly']

    print(f"Week start: {weekly['week_start_date']}")
    print(f"Days in week: {weekly['days_in_week']}")
    print(f"Days remaining: {weekly['days_remaining']}")
    print(f"\nWeekly target: ${weekly['weekly_target']:.2f}")
    print(f"Weekly P&L: ${weekly['weekly_pnl']:.2f}")
    print(f"Pace: {weekly['pace_percentage']}")
    print(f"Status: {weekly['status']}")
    print(f"Projected weekly P&L: ${weekly['projected_weekly_pnl']:.2f}")
    print(f"\nDaily average needed: ${weekly['needed_daily_average']:.2f}")
    print(f"\n{weekly['recommendation']}")

    # Overall stats
    print_section("OVERALL PERFORMANCE STATS")
    perf = summary['performance']
    print(f"Total bets (all time): {perf['total_bets']}")
    print(f"Wins: {perf['wins']}")
    print(f"Losses: {perf['losses']}")
    print(f"Win rate: {perf['win_rate']}")
    print(f"Current streak: {perf['current_streak']}")

    bankroll = summary['bankroll']
    print(f"\nStarting bankroll: ${bankroll['starting']:.2f}")
    print(f"Current bankroll: ${bankroll['current']:.2f}")
    print(f"Total profit: ${bankroll['profit_loss']:.2f}")
    print(f"Total return: {(bankroll['profit_loss']/bankroll['starting']*100):.2f}%")

    # Save data
    print_section("SAVING SESSION DATA")
    tracker.save_to_file()
    print(f"✓ Bankroll data saved to {tracker.data_file}")

    # Final lessons
    print_section("KEY LESSONS FROM TODAY")
    print("""
1. Kelly Criterion helps right-size bets based on edge
   - Higher confidence = larger bet
   - Lower confidence = smaller bet
   - Protects you during downswings

2. Risk management prevents ruin
   - Max bet limits control single-bet risk
   - Daily loss limits prevent panic trading
   - Without limits, one bad streak can wipe you out

3. Don't chase losses
   - Made two losing bets late in the day
   - After a big win, easy to get overconfident
   - Discipline > emotion

4. Quality over quantity
   - Only bet when you have genuine edge
   - Marginal bets (51% vs 50% breakeven) add cost
   - Bet frequency matters less than accuracy

5. Track everything
   - Accurate records let you identify patterns
   - Find which markets you're strong in
   - Debug where you're losing money

6. Adapt to the week
   - Early in week: can be patient for high-EV bets
   - Late in week: might need higher volume to hit target
   - But NEVER sacrifice quality for quantity
    """)


if __name__ == "__main__":
    main()
