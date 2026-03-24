# Bankroll Management Tool - Usage Guide

A comprehensive Python bankroll management system for sports bettors using Kalshi.

## Features

1. **Kelly Criterion Calculator** - Optimal bet sizing based on probability and odds
2. **Bankroll Tracker** - Real-time tracking of bankroll, daily/weekly P&L
3. **Risk Management** - Enforces max bet sizes and daily loss limits
4. **Weekly Goal Tracking** - $500/week target with pace calculations
5. **Performance Analytics** - Win rate, streak tracking, ROI calculations
6. **Persistence** - Save/load bankroll state as JSON

## Quick Start

```python
from bankroll import BankrollTracker, KellyCriterion

# Initialize tracker
tracker = BankrollTracker(
    starting_bankroll=5000,
    max_bet_pct=0.05,           # 5% max bet per wager
    daily_loss_limit_pct=0.10,  # 10% daily loss limit
    weekly_target=500.0,        # $500/week target
    data_file="bankroll_data.json"
)

# Load existing data if available
tracker.load_from_file()

# Record a bet
result = tracker.add_bet(
    amount=75,
    win=True,
    odds=1.909,
    description="Kalshi bet on event X"
)
print(f"Bankroll: ${tracker.current_bankroll:.2f}")

# Get bet recommendation
rec = tracker.recommend_bet_size(
    probability=0.55,
    decimal_odds=1.909,
    kelly_fraction=0.25  # Quarter Kelly (conservative)
)
print(f"Recommended bet: ${rec['recommended_bet']:.2f}")

# Check weekly progress
weekly = tracker.get_weekly_report()
print(f"Weekly P&L: ${weekly['weekly_pnl']:.2f}")
print(f"Status: {weekly['status']}")

# Save progress
tracker.save_to_file()
```

## Class Reference

### BankrollTracker

Main class for bankroll management.

#### Constructor

```python
BankrollTracker(
    starting_bankroll: float,           # Initial bankroll
    max_bet_pct: float = 0.05,         # Max bet as % of bankroll
    daily_loss_limit_pct: float = 0.10, # Daily loss limit as % of bankroll
    weekly_target: float = 500.0,       # Weekly profit target
    data_file: Optional[str] = None     # JSON persistence file
)
```

#### Key Methods

**Bet Recording & Management**
- `add_bet(amount, win, odds, description)` - Record a bet result
- `validate_bet_size(bet_amount)` - Check if bet respects risk rules
- `recommend_bet_size(probability, decimal_odds, kelly_fraction)` - Get Kelly-based recommendation

**Data Persistence**
- `load_from_file()` - Load previous bankroll state from JSON
- `save_to_file()` - Save current state to JSON
- `close_day()` - Record daily statistics and reset daily tracking

**Performance Metrics**
- `get_daily_pnl()` - Today's profit/loss
- `get_session_pnl()` - Session profit/loss
- `get_week_pnl()` - Weekly profit/loss
- `get_win_rate()` - Win rate percentage
- `get_daily_roi()` - Daily ROI percentage
- `get_session_roi()` - Session ROI percentage

**Risk Management**
- `get_max_bet()` - Maximum bet size for today
- `get_daily_loss_limit()` - Daily loss threshold
- `get_remaining_daily_loss_capacity()` - Loss capacity remaining today

**Reporting**
- `get_weekly_report()` - Comprehensive weekly metrics
- `get_summary()` - Full bankroll summary

#### Properties

```python
tracker.current_bankroll      # Current account balance
tracker.starting_bankroll     # Initial bankroll
tracker.total_bets           # Total bets placed
tracker.total_wins           # Total wins
tracker.total_losses         # Total losses
tracker.current_streak       # Current win/loss streak count
tracker.streak_type          # 'win' or 'loss'
tracker.today_bets          # List of today's bets
```

### KellyCriterion

Static methods for Kelly Criterion calculations and odds conversion.

#### Methods

**Kelly Calculation**
```python
# Calculate optimal Kelly fraction
kelly_fraction = KellyCriterion.calculate_kelly(
    probability=0.55,       # 55% win probability
    decimal_odds=1.909,     # Decimal format odds
    kelly_fraction=0.25     # Quarter Kelly (safer)
)
# Returns: 0.0138 (1.38% of bankroll)

# Recommended bet size
bet_size = bankroll * kelly_fraction  # e.g., 5000 * 0.0138 = $69
```

**Odds Conversion**
```python
# American to Decimal
decimal = KellyCriterion.american_to_decimal(-110)
# Returns: 1.909

# Decimal to American
american = KellyCriterion.decimal_to_american(1.909)
# Returns: -110.0
```

## Usage Examples

### Example 1: Basic Daily Workflow

```python
from bankroll import BankrollTracker, KellyCriterion

# Load tracker
tracker = BankrollTracker(
    starting_bankroll=5000,
    weekly_target=500.0,
    data_file="bankroll_data.json"
)
tracker.load_from_file()

# Place a bet with Kelly recommendation
print("\n=== NEW BET ===")
american_odds = -110  # Standard odds on Kalshi
decimal_odds = KellyCriterion.american_to_decimal(american_odds)
win_probability = 0.55  # You estimate 55% win chance

# Get recommendation
rec = tracker.recommend_bet_size(win_probability, decimal_odds, kelly_fraction=0.25)
print(f"Kelly recommends: ${rec['recommended_bet']:.2f}")
print(f"Expected value: ${rec['expected_value']:.2f}")

# Verify it passes validation
is_valid, msg = tracker.validate_bet_size(rec['recommended_bet'])
print(f"Valid: {is_valid} - {msg}")

# Place the bet (assume you won)
tracker.add_bet(
    amount=rec['recommended_bet'],
    win=True,
    odds=decimal_odds,
    description="Kalshi event prediction bet"
)

# Check status
print(f"\nCurrent bankroll: ${tracker.current_bankroll:.2f}")
print(f"Daily P&L: ${tracker.get_daily_pnl():.2f}")
print(f"Win rate: {tracker.get_win_rate():.1f}%")

# Save progress
tracker.save_to_file()
```

### Example 2: Weekly Monitoring

```python
# Check if on track for weekly goal
weekly = tracker.get_weekly_report()

print(f"Weekly Target: ${weekly['weekly_target']:.2f}")
print(f"Current P&L: ${weekly['weekly_pnl']:.2f}")
print(f"Pace: {weekly['pace_percentage']}")
print(f"Status: {weekly['status']}")
print(f"Days remaining: {weekly['days_remaining']}")
print(f"Daily rate needed: ${weekly['needed_daily_average']:.2f}")
print(f"\n{weekly['recommendation']}")
```

### Example 3: Risk Management Checks

```python
# Calculate risk limits
print(f"Max single bet: ${tracker.get_max_bet():.2f}")
print(f"Daily loss limit: ${tracker.get_daily_loss_limit():.2f}")
print(f"Loss capacity remaining: ${tracker.get_remaining_daily_loss_capacity():.2f}")

# Check if a proposed bet is safe
proposed_bet = 150
is_valid, message = tracker.validate_bet_size(proposed_bet)
if is_valid:
    print(f"${proposed_bet} bet is within limits")
else:
    print(f"Cannot place ${proposed_bet} bet: {message}")
    # Fall back to max allowed
    max_safe_bet = tracker.get_max_bet()
    print(f"Max safe bet: ${max_safe_bet:.2f}")
```

### Example 4: Kelly Criterion Comparison

```python
from bankroll import KellyCriterion

# Compare Kelly fractions for the same bet
prob = 0.55
odds = 1.909
bankroll = 5000

print("Kelly Fraction Comparison (55% win, -110 odds):")
for name, fraction in [("Full", 1.0), ("Half", 0.5), ("Quarter", 0.25)]:
    kelly = KellyCriterion.calculate_kelly(prob, odds, fraction)
    bet_amount = bankroll * kelly
    print(f"  {name} Kelly ({kelly*100:.2f}%): ${bet_amount:.2f}")

# Quarter Kelly is recommended for sports betting (safer)
# Full Kelly can quickly bankrupt you with unlucky streaks
```

### Example 5: End-of-Day Reporting

```python
# At end of day, close out
daily_record = tracker.close_day()

print(f"Daily Summary for {daily_record.date}:")
print(f"  Starting: ${daily_record.starting_balance:.2f}")
print(f"  Ending: ${daily_record.ending_balance:.2f}")
print(f"  P&L: ${daily_record.pnl:.2f}")
print(f"  Bets: {daily_record.num_bets} ({daily_record.wins}W-{daily_record.losses}L)")
print(f"  Win rate: {daily_record.win_rate:.1f}%")

# Get comprehensive summary
summary = tracker.get_summary()
print(f"\nSession Summary:")
print(f"  Current bankroll: ${summary['bankroll']['current']:.2f}")
print(f"  Session P&L: ${summary['session']['pnl']:.2f}")
print(f"  Session ROI: {summary['session']['roi']}")
print(f"  Overall win rate: {summary['performance']['win_rate']}")

tracker.save_to_file()
```

## JSON Data Format

The saved bankroll data looks like:

```json
{
  "starting_bankroll": 5000,
  "current_bankroll": 5140.89,
  "session_start_balance": 5000,
  "session_start_date": "2026-03-23",
  "week_start_date": "2026-03-23",
  "today_start_balance": 5000,
  "current_streak": 1,
  "streak_type": "win",
  "total_bets": 4,
  "total_wins": 3,
  "total_losses": 1,
  "daily_records": [
    {
      "date": "2026-03-23",
      "starting_balance": 5000,
      "ending_balance": 5140.89,
      "pnl": 140.89,
      "num_bets": 4,
      "wins": 3,
      "losses": 1,
      "win_rate": 75.0,
      "streaks": {
        "current_streak": 1,
        "streak_type": "win"
      }
    }
  ],
  "last_updated": "2026-03-23T19:45:30.123456"
}
```

## Risk Management Rules

The tool enforces three critical risk management rules:

### 1. Max Bet Size (Default: 5% of bankroll)
- No single bet should exceed 5% of current bankroll
- For $5,000 bankroll: max bet is $250
- Protects against catastrophic losses

### 2. Daily Loss Limit (Default: 10% of bankroll)
- Daily losses capped at 10% of starting bankroll
- For $5,000 bankroll: max daily loss is $500
- If you've lost $400 today, only $100 loss capacity remains
- Prevents emotional revenge trading

### 3. Unit-Based Betting
- Kelly Criterion recommends bets as fraction of bankroll
- Scale bets based on confidence level (Kelly fraction)
- Quarter Kelly (0.25) recommended for sports betting
- Allows profitable strategy even with streak losses

## Kalshi-Specific Notes

Kalshi markets use binary outcomes (YES/NO). Typical setup:

```python
# Kalshi bet example
# Market: "Will Team X score > 20 points?"
# Probability estimate: 60%
# Market price (odds): -110 (you bet $110 to win $100)

decimal_odds = KellyCriterion.american_to_decimal(-110)  # 1.909

rec = tracker.recommend_bet_size(
    probability=0.60,
    decimal_odds=decimal_odds,
    kelly_fraction=0.25
)

print(f"Kelly recommends: ${rec['recommended_bet']:.2f}")

# If bet wins
tracker.add_bet(
    amount=rec['recommended_bet'],
    win=True,
    odds=decimal_odds,
    description="Kalshi: Team X > 20 points"
)

# If bet loses
tracker.add_bet(
    amount=rec['recommended_bet'],
    win=False,
    odds=decimal_odds,
    description="Kalshi: Team X > 20 points"
)
```

## Tips for Success

1. **Use Quarter Kelly or Less** - Full Kelly will cause larger swings. Quarter Kelly is safer.

2. **Honest Probability Estimates** - If your estimates are off, no bet sizing strategy will save you. Spend time calibrating.

3. **Track Everything** - Record every bet. Review misses weekly to improve estimates.

4. **Don't Chase Losses** - The daily loss limit exists for a reason. Stop trading if you hit it.

5. **Long-Term Perspective** - Expect variance. A 55% win rate can still see 5-loss streaks. One week behind doesn't mean you're off track.

6. **Review Weekly** - Every Sunday, review the week. Adjust strategy if needed.

7. **Maintain Discipline** - Don't adjust bet sizing based on emotion. Let Kelly/risk rules guide you.

## Troubleshooting

**Q: My Kelly recommendation is too high**
A: You have high bankroll relative to confidence. Try:
   - Lower kelly_fraction (use 0.125 or 0.1 instead of 0.25)
   - Re-estimate your win probability (might be overconfident)
   - Increase your starting bankroll

**Q: I keep hitting daily loss limits**
A: Your win rate is too low. Either:
   - Improve your probability estimates
   - Increase your bankroll
   - Reduce bet sizes (Kelly fraction)
   - Take more time between bets (avoid tilt)

**Q: My win rate is high but I'm not profitable**
A: Your odds might be bad, or you're taking too many small-edge bets:
   - Focus only on +EV bets (expected value > 0)
   - Avoid betting just because you can
   - Improve your market analysis

**Q: Data file won't load**
A: Check that:
   - File path exists and is readable
   - JSON is valid (no manual edits introducing syntax errors)
   - File permissions allow reading
   - Try deleting the file and starting fresh

## Requirements

- Python 3.7+
- No external dependencies (uses only standard library)

## License

This tool is provided as-is for personal use in sports betting management.
