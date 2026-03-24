#!/usr/bin/env python3
"""
Example usage of the Sports Betting Analysis Engine
Shows practical workflows for daily betting analysis
"""

from scripts.analyzer import (
    BettingAnalysisEngine,
    american_to_probability,
    american_to_decimal,
    kelly_criterion,
    calculate_ev,
    calculate_parlay_ev,
    ELORating,
    SoccerAnalyzer,
)
import json
from datetime import datetime


# ============================================================================
# EXAMPLE 1: Basic Single Game Analysis
# ============================================================================

def example_single_game_analysis():
    """Analyze a single NBA game"""
    print("\n" + "=" * 80)
    print("EXAMPLE 1: Single Game Analysis")
    print("=" * 80)

    # Initialize engine
    engine = BettingAnalysisEngine(bankroll=1000.0)

    # Load standings
    print("\nLoading standings...")
    engine.load_all_standings()

    # Example: Analyze an NBA matchup
    # In real usage, get actual odds from Kalshi
    if len(engine.nba_standings) >= 2:
        teams = list(engine.nba_standings.keys())
        team_a = teams[0]
        team_b = teams[1]

        print(f"\nAnalyzing: {team_a} vs {team_b}")

        # American odds from Kalshi (example)
        # -110 is typical for "fair" odds (around 52.4% probability each side)
        american_odds = -110

        pick = engine.analyze_nba_matchup(team_a, team_b, american_odds)

        if pick:
            print(f"\nResult:")
            print(f"  Pick: {pick.pick}")
            print(f"  Your Win Probability: {pick.your_probability:.1%}")
            print(f"  Implied Probability: {pick.implied_probability:.1%}")
            print(f"  Expected Value: {pick.expected_value:+.2%}")
            print(f"  Decimal Odds: {pick.odds:.2f}")
            print(f"  Recommended Bet: ${pick.recommended_bet_amount:.2f}")
            print(f"  Confidence: {pick.confidence}/10")
            print(f"  Risk Level: {pick.risk_level}")
            print(f"\nReasoning:")
            for reason in pick.reasoning:
                print(f"  • {reason}")


# ============================================================================
# EXAMPLE 2: Odds Conversion Workflow
# ============================================================================

def example_odds_conversion():
    """Show odds conversion examples"""
    print("\n" + "=" * 80)
    print("EXAMPLE 2: Odds Conversion")
    print("=" * 80)

    print("\nConverting American Odds:")
    print("-" * 40)

    test_odds = [-110, -150, +150, +110, -200, +250]

    for american in test_odds:
        decimal = american_to_decimal(american)
        implied_prob = american_to_probability(american)
        print(f"  {american:>4} → Decimal: {decimal:.3f} → Prob: {implied_prob:.1%}")

    print("\n\nKalshi Price Conversion Example:")
    print("-" * 40)
    print("Kalshi shows prices from $0.00 to $1.00")
    print("$0.65 'Yes' price = 65% implied probability")

    kalshi_price = 0.65
    # Decimal odds where $1 bet returns $1/probability
    decimal_equivalent = 1 / kalshi_price
    # Convert to American odds
    american_equivalent = (decimal_equivalent - 1) * -100

    print(f"\n  Kalshi Yes Price: ${kalshi_price:.2f}")
    print(f"  Implied Probability: {kalshi_price:.1%}")
    print(f"  Decimal Odds Equivalent: {decimal_equivalent:.3f}")
    print(f"  American Odds Equivalent: {american_equivalent:.0f}")


# ============================================================================
# EXAMPLE 3: Kelly Criterion Demonstration
# ============================================================================

def example_kelly_criterion():
    """Demonstrate Kelly Criterion for bet sizing"""
    print("\n" + "=" * 80)
    print("EXAMPLE 3: Kelly Criterion Bet Sizing")
    print("=" * 80)

    bankroll = 1000.0
    print(f"Bankroll: ${bankroll:.2f}\n")

    scenarios = [
        {"prob": 0.55, "american_odds": -110, "scenario": "Slight Edge"},
        {"prob": 0.60, "american_odds": -150, "scenario": "Good Edge"},
        {"prob": 0.70, "american_odds": -110, "scenario": "Strong Edge"},
        {"prob": 0.52, "american_odds": +150, "scenario": "Underdog Pick"},
    ]

    for scenario in scenarios:
        decimal_odds = american_to_decimal(scenario["american_odds"])
        kelly_pct = kelly_criterion(scenario["prob"], decimal_odds)
        kelly_full = kelly_criterion(scenario["prob"], decimal_odds, kelly_fraction=1.0)

        bet_25pct = bankroll * kelly_pct
        bet_full = bankroll * kelly_full

        implied_prob = american_to_probability(scenario["american_odds"])
        ev = calculate_ev(scenario["prob"], implied_prob, decimal_odds)

        print(f"{scenario['scenario']}:")
        print(f"  Your Prob: {scenario['prob']:.0%}  Odds: {scenario['american_odds']:>4}  " +
              f"Decimal: {decimal_odds:.2f}")
        print(f"  EV: {ev:+.2%}")
        print(f"  25% Kelly: {kelly_pct:.1%} = ${bet_25pct:.2f}")
        print(f"  Full Kelly: {kelly_full:.1%} = ${bet_full:.2f}")
        print()


# ============================================================================
# EXAMPLE 4: Parlay Analysis
# ============================================================================

def example_parlay_analysis():
    """Analyze parlay/combo bets"""
    print("\n" + "=" * 80)
    print("EXAMPLE 4: Parlay/Combo Bet Analysis")
    print("=" * 80)

    # Example 3-bet parlay
    picks = [
        (0.55, 1.909),  # (your_prob, decimal_odds) - American -110
        (0.60, 1.833),  # American -120
        (0.58, 1.923),  # American +100 (underdog)
    ]

    print("\nThree-Bet Parlay:")
    print("-" * 40)

    total_odds = 1.0
    parlay_prob = 1.0

    for i, (prob, odds) in enumerate(picks, 1):
        implied = 1 / odds
        ev = calculate_ev(prob, implied, odds)
        print(f"  Leg {i}: Your {prob:.0%} vs Implied {implied:.0%}")
        print(f"         Odds: {odds:.2f} ({odds - 1:+.2f})  EV: {ev:+.2%}")
        parlay_prob *= prob
        total_odds *= odds

    print(f"\nParlay Totals:")
    print(f"  Overall Probability: {parlay_prob:.1%}")
    print(f"  Total Odds: {total_odds:.2f}x")
    print(f"  Bet $100 to win: ${100 * (total_odds - 1):.2f}")

    # Calculate parlay EV
    parlay_ev, parlay_prob2, parlay_odds = calculate_parlay_ev(picks)
    print(f"  Parlay EV: {parlay_ev:+.2%}")

    # Compare to individual betting
    individual_ev_sum = sum(
        calculate_ev(prob, 1 / odds, odds) * 100
        for prob, odds in picks
    )
    print(f"\nComparison to Individual Bets:")
    print(f"  Parlay EV on $100: ${parlay_ev * 100:.2f}")
    print(f"  3x $33.33 individual: ~${individual_ev_sum:.2f}")
    print(f"  → {'Parlay Favored' if parlay_ev > individual_ev_sum / 100 else 'Individual Favored'}")


# ============================================================================
# EXAMPLE 5: ELO Rating System
# ============================================================================

def example_elo_rating():
    """Demonstrate ELO rating usage"""
    print("\n" + "=" * 80)
    print("EXAMPLE 5: ELO Rating System")
    print("=" * 80)

    # Example team ratings
    team_a_elo = 1650  # Strong team
    team_b_elo = 1550  # Average team

    print(f"\nTeam A ELO: {team_a_elo}")
    print(f"Team B ELO: {team_b_elo}")
    print(f"Difference: {team_a_elo - team_b_elo}")

    # Away game
    prob_a_away = ELORating.expected_win_probability(team_a_elo, team_b_elo, home=False)
    print(f"\nTeam A @ Team B (away): {prob_a_away:.1%}")

    # Home game
    prob_a_home = ELORating.expected_win_probability(team_a_elo, team_b_elo, home=True)
    print(f"Team B @ Team A (home): {prob_a_home:.1%}")

    home_advantage_impact = prob_a_home - prob_a_away
    print(f"Home Advantage Impact: +{home_advantage_impact:.1%}")

    # Update ELO after games
    print(f"\nELO Updates After Games:")
    print(f"  Team A wins: ", end="")
    new_a_win, new_b_win = ELORating.update_elo(team_a_elo, team_b_elo, 1)
    print(f"A {new_a_win:.0f} ({new_a_win - team_a_elo:+.0f}), " +
          f"B {new_b_win:.0f} ({new_b_win - team_b_elo:+.0f})")

    print(f"  Team B wins: ", end="")
    new_a_loss, new_b_loss = ELORating.update_elo(team_a_elo, team_b_elo, 0)
    print(f"A {new_a_loss:.0f} ({new_a_loss - team_a_elo:+.0f}), " +
          f"B {new_b_loss:.0f} ({new_b_loss - team_b_elo:+.0f})")


# ============================================================================
# EXAMPLE 6: Soccer Analysis with Poisson
# ============================================================================

def example_soccer_poisson():
    """Demonstrate soccer analysis with Poisson distribution"""
    print("\n" + "=" * 80)
    print("EXAMPLE 6: Soccer Analysis (Poisson Distribution)")
    print("=" * 80)

    # Example team ratings
    team_a_elo = 1700  # Strong team
    team_b_elo = 1600  # Weaker opponent

    print(f"\nTeam A ELO: {team_a_elo}")
    print(f"Team B ELO: {team_b_elo}")

    # Calculate match probabilities
    prob_a_wins, prob_draw, prob_b_wins = SoccerAnalyzer.calculate_match_probabilities(
        team_a_elo, team_b_elo
    )

    print(f"\nMatch Outcome Probabilities:")
    print(f"  Team A Wins: {prob_a_wins:.1%}")
    print(f"  Draw: {prob_draw:.1%}")
    print(f"  Team B Wins: {prob_b_wins:.1%}")

    # Expected goals
    exp_goals_a = SoccerAnalyzer.expected_goals_team_strength(
        team_a_elo + 30, team_b_elo  # +30 for home advantage
    )
    exp_goals_b = SoccerAnalyzer.expected_goals_team_strength(
        team_b_elo, team_a_elo + 30
    )

    print(f"\nExpected Goals:")
    print(f"  Team A (home): {exp_goals_a:.2f}")
    print(f"  Team B (away): {exp_goals_b:.2f}")

    # Goal probabilities
    print(f"\nGoal Probability Distribution (Team A):")
    for goals in range(0, 6):
        prob = SoccerAnalyzer.poisson_goal_probability(exp_goals_a, goals)
        bar = "█" * int(prob * 50)
        print(f"  {goals} goals: {prob:.3f}  {bar}")


# ============================================================================
# EXAMPLE 7: Daily Picks Report
# ============================================================================

def example_daily_picks_report():
    """Generate a daily picks report"""
    print("\n" + "=" * 80)
    print("EXAMPLE 7: Daily Picks Report")
    print("=" * 80)

    engine = BettingAnalysisEngine(bankroll=1000.0)
    engine.load_all_standings()

    # Add sample picks
    teams_nba = list(engine.nba_standings.keys())
    teams_mlb = list(engine.mlb_standings.keys())
    teams_epl = list(engine.epl_standings.keys())

    # Add picks at various odds for demonstration
    if len(teams_nba) >= 2:
        pick1 = engine.analyze_nba_matchup(teams_nba[0], teams_nba[1], -110)
        if pick1:
            engine.picks.append(pick1)

    if len(teams_mlb) >= 2:
        pick2 = engine.analyze_mlb_matchup(teams_mlb[0], teams_mlb[1], -115)
        if pick2:
            engine.picks.append(pick2)

    if len(teams_epl) >= 2:
        pick3 = engine.analyze_soccer_matchup(teams_epl[0], teams_epl[1], -110, 'EPL')
        if pick3:
            engine.picks.append(pick3)

    # Print summary
    if engine.picks:
        engine.print_picks_summary(min_ev=0.0)

        # Save to JSON
        engine.save_picks_to_json('/tmp/sample_picks.json', min_ev=0.0)
        print("\nPicks saved to /tmp/sample_picks.json")


# ============================================================================
# EXAMPLE 8: Bankroll Management
# ============================================================================

def example_bankroll_management():
    """Show bankroll management concepts"""
    print("\n" + "=" * 80)
    print("EXAMPLE 8: Bankroll Management")
    print("=" * 80)

    bankroll = 1000.0
    target_weekly_profit = 500.0
    bets_per_week = 8

    print(f"Starting Bankroll: ${bankroll:.2f}")
    print(f"Target Weekly Profit: ${target_weekly_profit:.2f}")
    print(f"Estimated Bets/Week: {bets_per_week}")
    print()

    # Calculate required EV
    required_avg_ev = target_weekly_profit / (bankroll * bets_per_week)
    print(f"Required Average EV: +{required_avg_ev:.1%}")
    print(f"  (To hit target: ${target_weekly_profit:.2f} / ${bankroll:.2f} / {bets_per_week} bets)")

    # Bet sizing guidelines
    print(f"\nBet Sizing Guidelines:")
    print(f"  Maximum per bet (2% rule): ${bankroll * 0.02:.2f}")
    print(f"  Recommended per bet: ${50:.2f} - ${100:.2f}")
    print(f"  Daily loss stop: ${bankroll * 0.10:.2f}")
    print(f"  Weekly loss stop: ${bankroll * 0.20:.2f}")

    # Risk levels
    print(f"\nRecommended Exposure by Risk Level:")
    print(f"  Low Risk (Conf 8-10): ${bankroll * 0.40:.2f} (40%)")
    print(f"  Medium Risk (Conf 6-7): ${bankroll * 0.35:.2f} (35%)")
    print(f"  High Risk (Conf 1-5): ${bankroll * 0.25:.2f} (25%)")

    # Scenario analysis
    print(f"\nSample Week Scenario:")
    print(f"  8 bets × $75 average = $600 wagered")
    print(f"  +8% average EV = $48 expected profit per bet")
    print(f"  8 × $48 = ${8 * 48:.2f} expected weekly profit")


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run all examples"""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + " " * 20 + "SPORTS BETTING ANALYSIS ENGINE" + " " * 28 + "║")
    print("║" + " " * 25 + "Usage Examples & Demonstrations" + " " * 23 + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")

    try:
        example_odds_conversion()
    except Exception as e:
        print(f"Error in odds conversion example: {e}")

    try:
        example_kelly_criterion()
    except Exception as e:
        print(f"Error in Kelly Criterion example: {e}")

    try:
        example_parlay_analysis()
    except Exception as e:
        print(f"Error in parlay analysis: {e}")

    try:
        example_elo_rating()
    except Exception as e:
        print(f"Error in ELO rating example: {e}")

    try:
        example_soccer_poisson()
    except Exception as e:
        print(f"Error in soccer Poisson example: {e}")

    try:
        example_bankroll_management()
    except Exception as e:
        print(f"Error in bankroll management example: {e}")

    try:
        example_single_game_analysis()
    except Exception as e:
        print(f"Error in single game analysis: {e}")

    try:
        example_daily_picks_report()
    except Exception as e:
        print(f"Error in daily picks report: {e}")

    print("\n" + "=" * 80)
    print("Examples completed!")
    print("=" * 80 + "\n")


if __name__ == '__main__':
    main()
