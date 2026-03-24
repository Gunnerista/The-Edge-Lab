---
name: betting-consultant
description: "Elite sports betting consultant AI modeled after Tony Bloom's Starlizard system. Use this skill for ANY sports betting analysis, pick generation, game analysis, odds evaluation, value bet identification, parlay/combo construction, bankroll management advice, or pre-game research. Triggers on: bet, betting, pick, odds, spread, moneyline, over/under, parlay, combo, Kalshi, value bet, EV, edge, handicap, NBA picks, MLB picks, soccer picks, today's games, bankroll, Kelly criterion, or any sports event analysis. ALWAYS use this skill when the user asks about placing bets, analyzing matchups, or wants daily/weekly betting recommendations."
---

# The Starlizard Philosophy: Elite Sports Betting Framework

## Core Principles

The Starlizard approach, pioneered by Tony Bloom, is built on **information advantage and mathematical rigor**, not intuition. Success comes from identifying edges that the market has mispriced, which requires discipline and a data-driven mindset.

### The Three Pillars

1. **Value Betting**: Only place a wager when YOUR calculated probability is higher than the market's implied probability. If you believe a team has a 55% chance to win but the market prices them at 50%, you have a +5% edge. Don't bet on "who you think will win" — bet on "where the market is wrong."

2. **Expected Value (EV) is King**: A single bet's profitability isn't determined by the outcome, but by whether it has positive EV. Losing a +5% EV bet is correct. Winning a -5% EV bet is getting lucky. Over 100 bets, positive EV bets will be profitable.

3. **Information Advantage**: The edge comes from knowing something the market doesn't (injuries, weather, team form, tactical changes) or modeling something better than the market (player minutes allocation, bullpen impact, xG in soccer). Gut feeling provides ZERO edge.

4. **Discipline Over Emotion**: Your model says fade this favorite? Fade it. Your model says pass because edge is only 2%? Pass. Never chase losses, never revenge bet, never let a losing streak break your betting discipline.

---

## Pre-Analysis Checklist: What to Research for EVERY Game

### 1. Team Form & Trends
- **Last 5-10 games**: Win-loss record, scoring trends, defensive efficiency, pace
- **Home vs Away splits**: Many teams perform drastically different at home
- **Matchup history**: How do these specific teams match up stylistically?
- **Momentum**: Is one team improving or declining? Be skeptical of streaks (small sample)

### 2. Head-to-Head Record
- **Last 5 meetings**: What was the trend? (e.g., underdog has won last 3)
- **Historical tendency**: Does one team own the other?
- **Recent vs historical**: Prioritize recent matchups (teams evolve)

### 3. Injury & Suspension Report (CRITICAL)
- **Star players**: Is the MVP, leading scorer, or ace pitcher available?
- **Key role players**: Depth matters more than fans realize
- **Confirmation**: Check LATEST news (within 2 hours of game time)
- **Replacement quality**: A backup starter can shift win probability by 5-10%
- **Return date**: Is an injured player actually returning or just listed as questionable?

### 4. Rest & Schedule Density
- **NBA back-to-backs**: Teams on 2nd night of B2B win ~45% vs 55% expected (major fade opportunity)
- **Travel fatigue**: Cross-country road trips, especially time zone changes
- **Games in last 3 days**: Fatigue compounds; check each player's minutes
- **Upcoming schedule**: Team might be resting players if they have a crucial game in 2 days

### 5. Motivation Factors
- **Playoff positioning**: Desperation or already locked in?
- **Rivalry games**: Can increase edge by 2-5% either direction (know the history)
- **Resting stars**: Contending teams rest players; tanking teams play bench players
- **Coaching changes**: New coach = new system = old data is less useful
- **Revenge factor**: Did this team lose badly to this opponent recently?

### 6. Weather (Outdoor Sports Critical)
- **Soccer**: Wind affects through-balls and long passes; rain = fewer goals
- **MLB**: Wind direction at the ballpark (Wrigley's ivy wall), humidity, temperature
- **Extreme conditions**: Humidity makes the ball travel further; cold reduces it

### 7. Line Movement
- **Opening line**: Where did the line open at opening sportsbooks?
- **Current line**: Where is it now?
- **Direction**: Which way did it move? (Line movement ≠ sharp money, but it's a signal)
- **Significance**: -2.5 to -3.5 is a small move; -2.5 to -6.5 is a massive move
- **Interpretation**: If everyone agrees a team should be -7, why did it open at -5? Look for the why.

### 8. Public vs Sharp Money
- **Public betting %**: Sportsbooks show public % betting (e.g., 65% on Lakers)
- **Sharp signal**: If a line moved against public money, sharps likely bet the opposite
- **Reverse indicator**: Public often gets it wrong (e.g., public chases favorites)
- **Kalshi insight**: Low-liquidity contracts on Kalshi show mispricings due to retail bias

---

## Mathematical Framework

### Odds Conversion Reference

**American Odds to Implied Probability:**
- Negative odds (Favorites): Probability = |Odds| / (|Odds| + 100)
  - Example: -150 odds → 150 / 250 = 60% implied
- Positive odds (Underdogs): Probability = 100 / (Odds + 100)
  - Example: +150 odds → 100 / 250 = 40% implied

**Decimal Odds to Implied Probability:**
- Probability = 1 / Decimal Odds
- Example: 2.50 decimal → 1 / 2.50 = 40% implied

**Implied Probability to Decimal Odds:**
- Decimal = 1 / Probability
- Example: 60% → 1 / 0.60 = 1.67 decimal

### Expected Value (EV) Calculation

**Formula:**
```
EV = (Your Probability × Payout) - (1 - Your Probability) × Stake
```

**Alternative (using decimal odds):**
```
EV = (Your Probability × Decimal Odds) - 1
If EV > 0, the bet has positive expected value
```

**Example:**
- Bet: Lakers -150 (60% implied, 1.67 decimal)
- You believe: Lakers 65% to win
- Stake: $100
- Payout if win: $100 / 1.5 = $66.67 total return (including original stake)
- EV = (0.65 × 166.67) - 35 = $108.33 - $35 = **+$73.33 (7.3% EV)**
- **Interpretation**: Over 100 identical $100 bets, you expect to profit $73.30

### Kelly Criterion: Optimal Bet Sizing

**Full Kelly Formula:**
```
f* = (bp - q) / b

Where:
- f* = fraction of bankroll to wager
- b = odds received (decimal odds - 1)
- p = your probability of winning
- q = probability of losing (1 - p)
```

**Example:**
- Decimal odds: 2.50 (so b = 1.50)
- Your probability: 65% (p = 0.65, q = 0.35)
- f* = (1.50 × 0.65 - 0.35) / 1.50
- f* = (0.975 - 0.35) / 1.50 = 0.625 / 1.50 = **41.7% of bankroll**

**CRITICAL**: Full Kelly is too aggressive for real-world betting due to variance and drawdowns.
**USE QUARTER KELLY INSTEAD**: f* / 4 = 10.4% of bankroll

**Quarter Kelly Benefits:**
- Reduces bankroll variance by 75% (smoother equity curve)
- Cuts maximum drawdown in half
- Still captures ~95% of long-term growth
- Provides cushion for model error (your 65% estimate might actually be 58%)

### Poisson Distribution for Soccer Goals

Soccer follows a Poisson distribution reasonably well. Use it to forecast goal totals and win probabilities.

**Poisson Probability:**
```
P(X = k) = (e^-λ × λ^k) / k!

Where:
- λ (lambda) = expected goals (from xG or team averages)
- k = number of goals
- e ≈ 2.71828
```

**Example: Manchester City expected to score 2.5 goals**
- P(City scores exactly 1) = (e^-2.5 × 2.5^1) / 1! ≈ 0.206 (20.6%)
- P(City scores exactly 2) = (e^-2.5 × 2.5^2) / 2! ≈ 0.257 (25.7%)
- P(City scores exactly 3) = (e^-2.5 × 2.5^3) / 3! ≈ 0.214 (21.4%)
- P(City scores 3+) = sum remaining probabilities ≈ 0.455 (45.5%)

**Over/Under Application:**
- City xG: 2.5, Arsenal xG: 1.2
- Total xG: 3.7
- Using Poisson, P(Under 2.5) ≈ 31%, P(Over 2.5) ≈ 69%
- If market prices Over 2.5 at -120 (54.5% implied), you have a +14.5% edge on Over

### Parlay & Combo Mathematics

**Two Independent Bets (Correlated = 0):**
```
Combined Probability = P(Bet A) × P(Bet B)

Example:
- Bet A: 55% (Lakers win)
- Bet B: 60% (Yankees win)
- Combined: 0.55 × 0.60 = 0.33 (33%)
```

**Parlay Payout:**
```
Payout = Stake × Decimal Odds A × Decimal Odds B

Example:
- Stake: $100
- Decimal odds A: 1.91 (-110)
- Decimal odds B: 1.91 (-110)
- Payout: $100 × 1.91 × 1.91 = $364.81 (profit: $264.81)
```

**Parlay EV:**
```
EV = (Your Combined Probability × Payout) - Stake

Example:
- Your probability: 33%
- Payout: $364.81
- Stake: $100
- EV = (0.33 × 364.81) - 100 = $120.39 - $100 = +$20.39

But market probability was: 0.55 × 0.60 = 33%
So EV ≈ 0% (no edge, this is a fair parlay)
```

**Key Rule**: Do NOT combine bets with correlation. If Game A outcome affects Game B (same team, weather impact on both games, etc.), the correlation reduces the parlay EV and increases risk. Multiply correlations in: **Combined Prob = P(A) × P(B) × (1 - Correlation)**

### Minimum Edge Threshold

**Rule**: ONLY place bets when Edge ≥ 3%

**Why?**
- Below 3% edge, you need a very large sample to differentiate luck from skill
- Model error, data quality issues, and line movement all consume small edges
- With 3%+ edge, you see consistent profit over 50-100 bets

**Edge Calculation:**
```
Edge % = (Your Probability - Market Implied Probability) × 100

Example:
- Your probability: 62%
- Market implied (from -150 odds): 60%
- Edge: (0.62 - 0.60) × 100 = +2%
DECISION: Pass (below 3% threshold)

Example 2:
- Your probability: 66%
- Market implied: 55%
- Edge: (0.66 - 0.55) × 100 = +11%
DECISION: Bet (exceeds 3% threshold)
```

---

## Sport-Specific Analysis Guides

### NBA: Basketball

**Key Statistical Metrics:**
- **Net Rating (OffRtg - DefRtg)**: Single best predictor of team strength; playoff teams average +2 or higher
- **Offensive Rating (ORtg)**: Points per 100 possessions; elite teams: 110+
- **Defensive Rating (DefRtg)**: Points allowed per 100 possessions; elite teams: 105 or lower
- **3-Point Shooting %**: Massively impacts winning; teams shooting 35%+ are strong
- **Free Throw Rate (FTA/FGA)**: Indicates how much a team attacks the basket; higher = more offensive flow
- **Pace**: Possessions per game; impacts total scoring (faster = higher totals)

**Situational Edges:**
- **Back-to-Back (2nd night)**: Teams on 2nd night of B2B win at ~45% rate. FADE the team on 2nd night of B2B, especially if they're favored
- **Home vs Away**: Some teams have 5+ point splits between home and away
- **vs .500+ Teams**: Better indicator than overall record; teams can pad records vs weak teams
- **Bench scoring**: Deep benches beat shallow ones in close games and during fatigue periods

**Player Props vs Defense Ranking:**
- Cross-reference player's season average vs opponent's defensive ranking at that position
- Example: Luka averages 28 PPG; opponent's PG defense allows 26 PPG. Expect Luka closer to 26-28, not 32
- Factor in: Rest days, foul trouble, game script (team up/down = more/fewer shots)

**Common Mispricings:**
- Public overvalues popular players' props (LeBron, Luka over the line)
- Underdogs on 2nd night of B2B are mispriced upward (adjust -2 to -3 points)
- Total points in NBA games are often overpriced due to public chasing high-scoring trends

### MLB: Baseball

**The Pitcher is 70% of the Analysis:**
- **ERA (Earned Run Average)**: Starter's runs per 9 innings; context matters (park factor, bullpen)
- **WHIP (Walks + Hits per Innings Pitched)**: Lower is better; elite pitchers: sub-1.10
- **K/9 (Strikeouts per 9 IP)**: High strikeout pitchers (9+) are harder to score on
- **FIP (Fielding Independent Pitching)**: ERA adjusted for park and defense; better than raw ERA
- **xFIP (Expected FIP)**: Estimates future ERA; less noise than current FIP
- **Recent Form**: Last 3 starts matter more than season average (injury trends, mechanical changes)

**Bullpen Fatigue (Critical for Over/Under):**
- Check innings pitched by relievers in last 3 games
- Fresh bullpen = more strikeouts, fewer walks = Under
- Tired bullpen (combined 15+ IP in last 2 games) = more hits, walks, runs = Over

**Batting Splits (Crucial for Prop Bets):**
- **vs LHP/RHP**: Lefties often struggle vs LHP; righties vs RHP
- **Home vs Away**: Some parks inflate stats (Yankee Stadium for HRs, Coors for hits)
- **Day vs Night**: Some teams perform worse in day games (travel, focus)
- **Recent Streak**: Hot hitter on 5-game hit streak should have higher prop prices

**Weather Impact (Often Overlooked):**
- **Wind Direction at Wrigley**: Out to left = inflated HR totals
- **Humidity & Temperature**: Hot and humid = ball carries further, more HRs
- **Rain**: Reduces scoring; wet field favors pitcher
- **Check**: Weather.gov for park-specific conditions 2 hours before game

**Umpire Home Plate Tendencies:**
- Some umps call a wide strike zone (more strikeouts, fewer walks)
- Some call narrow zone (more walks, more balls in play)
- Check: Ump's zone profile for the day's pitcher matchup
- Tight zone + high K/9 pitcher = strong Under candidate

### Soccer (EPL/La Liga)

**The #1 Metric: Expected Goals (xG)**
- **xG definition**: Sum of shot quality; 0.8 xG = typically 0.8 goals expected
- **Why?**: Actual goals are noisy (shots miss, keepers make saves); xG is more predictive
- **Team xG**: Manchester City 2.4, Arsenal 1.8 = City likely to win, but not guaranteed
- **Comparison**: xG > actual goals (team under-performing), xG < actual goals (team getting lucky)
- **Long-term**: Over 10+ games, xG predicts future goals better than recent goals

**Possession & Shot Quality:**
- **Possession %**: Indicates tempo and control, but doesn't guarantee goals (Barcelona problem)
- **Shots on Target (SoT)**: High SoT % (shots on target / total shots) = clinical finishing
- **Pressing Intensity**: Teams that press high force more mistakes (Gegenpressing teams like Liverpool)
- **Pass Completion %**: Indicates control; 85%+ suggests dominant team

**Head-to-Head Bogey Teams:**
- Some teams have inexplicable poor records vs certain opponents (tactical mismatch, personnel)
- Example: Chelsea has won only 3 of last 10 vs Brighton (bad matchup)
- Weight recent H2H more than overall form for soccer

**Asian Handicap for Better Value:**
- European 1X2 odds price 3 outcomes; Asian Handicap prices 2 (win or loss, ties are refunded at even money)
- Cleaner pricing = better edges
- Example: Instead of betting Liverpool at -200 (1X2), bet Liverpool -1 (AH) which might be -110 with better odds

**The Undervalued Draw:**
- Approximately 25% of EPL games end in draws
- Markets often price draws at lower probability than they occur
- Draw at +300 (25% implied) is fair; Draw at +400 (20% implied) is value
- Tendency: Teams are more likely to draw than bettors expect

**Team-Specific Tendencies:**
- **Slow Starters**: Some teams concede early; exploit with early goal markets
- **Weak Finishes**: Some teams fade in final 15 minutes; use for Under bets late
- **Set Piece Dominance**: Teams strong at corners/free kicks; check attacking corner % and defensive corner %; xG from set pieces
- **Counter-Attack Strength**: Look for teams fast on the break; exploit against possession-dominant teams

**Key Stats to Gather:**
- Last 5 xG and goals (both for and against)
- Last 5 SoT and conversion rate
- Season home/away splits
- Recent injuries to key players (strikers and fullbacks especially matter)
- Manager's tactics and recent lineup changes

---

## Kalshi-Specific Strategy

**What is Kalshi?**
- Prediction market where you trade YES/NO contracts
- Each contract priced $0.01 to $0.99 (represents probability)
- If YES contract is at $0.60, market implies 60% probability
- Profit = (1.00 - entry price) × shares if YES
- Loss = entry price × shares if NO

**Your Edge on Kalshi:**
```
Edge = Your Estimated Probability - Kalshi Market Price

Example:
- Kalshi prices "Lakers win tonight" at $0.55
- You calculate 62% probability (0.62)
- Edge = 0.62 - 0.55 = +0.07 (7%)
- BET YES at $0.55
```

**Finding Mispriced Contracts:**
- **Public sentiment ≠ statistical reality**: Retail traders love recency bias
- Example: A team lost last game badly; public bids down their next game contract despite strength of schedule
- **Low-liquidity markets**: Few traders = wider mispricings
- **Compare to sportsbooks**: Kalshi YES at $0.65 vs sportsbooks pricing team at -200 (66.7%)?
  - If Kalshi < sportsbook probability, consider value on YES
  - If Kalshi > sportsbook probability, consider fade

**Combo Bets on Kalshi:**
```
Multiple YES Contracts (Independent):
- Contract A (Lakers Win): Your probability 62%, Kalshi 55% = Edge +7%
- Contract B (Over 220): Your probability 58%, Kalshi 52% = Edge +6%
- Combo probability = 0.62 × 0.58 = 0.3596 (36%)
- Combo payout = $1 (if both YES hit)
- Combo cost to gain $1 at 36% prob = $0.36 entry (but if both contracts are $0.55 and $0.52, cost = $0.55 × $0.52 ≈ $0.286)
- Buy combo for $0.286, sell for ~$0.36 = 26% profit upside if edge holds

CRITICAL: Only combine contracts if they're uncorrelated. Same team playing game = 100% correlated; reduce combined probability accordingly.
```

**Arbitrage Signal:**
- Kalshi YES at $0.70 implies 70% probability
- Sportsbook prices same event at +150 (40% implied on YES)
- Buy Kalshi YES at $0.70, bet sportsbook NO at +150
- Risk: $70 to make $40 profit (lock in 36% ROI if you get fills)
- Requirement: Fast capital between platforms, tight timing

**Early Market Advantage:**
- When new contracts open on Kalshi, liquidity is thin
- Early movers can move prices; use limit orders
- Advantage: Wider spreads = more edge available for skilled traders

**Liquidity Matters:**
- High-volume contracts (presidential elections): Tight pricing, low edge
- Low-volume niche contracts: Wide pricing, high edge but hard to exit
- Goldilocks: Medium liquidity (enough volume to trade, enough spread to find edges)

---

## Bankroll Management Rules (STRICT DISCIPLINE REQUIRED)

### Starting Bankroll & Tracking
- **Segregate**: Your betting bankroll is NOT your emergency fund or regular spending money
- **Track separately**: Use a spreadsheet (Bankroll, Total Staked, Total Won, Current Balance)
- **Audit weekly**: Reconcile all bets and verify balance

### Single Bet Max (5% of Bankroll)

**Formula:**
```
Single Bet Size = Bankroll × 0.05 × Kelly Fraction

Example (starting bankroll $5,000):
- Single bet max = $5,000 × 0.05 × 0.25 (Quarter Kelly) = $62.50 per bet
- If multiple bets with +5% EV, adjust down to $50 to stay under max
```

**Why 5% Max?**
- Limits catastrophic loss if a single bet goes wrong
- Prevents emotion-driven oversizing when you "feel strongly"
- Reduces sequence risk (if you lose 3 in a row, you can still play)

### Daily Loss Limit (10% of Bankroll)

**Rule**: If you lose 10% of bankroll in a day, STOP betting for the day

**Example** (bankroll $5,000):
- Daily loss limit = $500
- Place 3 bets: -$100, -$120, -$300 = -$520 total
- STOP. Do not place any more bets today. Close the sportsbook.

**Purpose:**
- Prevents tilt/revenge betting (the fastest way to lose bankroll)
- Gives you time to review what went wrong
- Forces discipline during downswings

### Weekly Loss Limit (20% of Bankroll)

**Rule**: If you lose 20% of bankroll in a week, reduce unit size 25% for the following week

**Example** (bankroll $5,000):
- Weekly loss limit = $1,000
- Week 1 result: -$1,100 in losses
- Week 2: Reduce unit size to $37.50 (was $50) and reduce single bet count to 3-5 bets instead of 5-10
- Resume normal sizing only after recovering losses + achieving +10% gain

**Purpose:**
- Protects bankroll during downswings (variance happens)
- Forces humility; bad weeks are learning opportunities, not time to double down

### Winning Streak Discipline

**DO NOT increase bet size during winning streaks**
- Many bettors increase unit size after 3-4 wins (WRONG)
- Variance will correct; you'll lose with bigger units
- Stick to 5% max rule ALWAYS

**Example (Wrong):**
- Win 4 in a row with $50 bets; up $350
- Bettor thinks "I'm hot" and places $150 bet on next game
- Loses the next 4 games: -$600
- Back to square one

**Example (Right):**
- Win 4 in a row with $50 bets; up $350
- Continue placing $50 bets (same size)
- After 10 more bets, if ROI is still +10%, THEN consider slight increase to $55
- Increase gradually, not dramatically

### Parlay & Combo Max (3% of Bankroll)

**Rule**: High-variance bets (parlays, combos) max at 3% due to higher variance

**Example** (bankroll $5,000):
- Parlay/combo max = $150
- Even if two 4-leg parlays are available, place max $150 total across both

**Why Lower Limit?**
- Parlay variance is 3-5x higher than single bets
- Correlated bets amplify variance
- If 5 parlays lose in a week, you can recover easier with 3% max than 5% max

### Target: 5-10 Bets Per Week

**Rule**: Do NOT feel obligated to bet every day or on every game

**Quality over Quantity:**
- 5 high-edge bets > 20 low-edge bets
- Better to miss a game than bet weak edge (negative EV)
- Rest days are part of the system

**Weekly Bet Allocation:**
- NBA: 2-3 bets per week
- MLB: 1-2 bets per week
- Soccer: 1-2 bets per week
- Parlay/Combo: 1 max per week
- Total: 5-8 bets, not 20+

**Example Ideal Week:**
- Monday: NBA under 2.5 edge, $50 ✓
- Tuesday: Soccer over 2.5 edge, $45 ✓
- Wednesday: Rest (no edge games available)
- Thursday: MLB +EV pick on underdog, $55 ✓
- Friday: Two-leg parlay 4% EV, $50 ✓
- Saturday: NBA & Soccer: 2 picks, $50 each ✓
- Sunday: Review week, identify best edges for next week
- Total: 7 bets, $300 staked, target +$30-50 profit

---

## Daily Workflow (Step-by-Step)

### Step 1: Find Today's Games
```
DAILY TASK:
- Search: "NBA games today" / "MLB games today" / "Premier League fixtures today"
- Gather: Game times, opening odds from 2-3 sportsbooks
- Note: Any breaking news (injuries announced overnight)
- Kalshi check: Compare opening prices for same games
```

### Step 2: Injury & Lineup Checks
```
CRITICAL VERIFICATION:
- NBA: Check official team injury reports (updated daily at 9 AM PT / 12 PM ET)
- MLB: Check DFS sites for lineup confirmation (released ~1 hour before game)
- Soccer: Check team news (starting XI confirmed ~1 hour before kickoff)
- Action: If star player unexpectedly ruled out, re-analyze or pass bet
```

### Step 3: Gather Statistical Context
```
FOR EACH GAME UNDER ANALYSIS:
NBA:
- Both teams' last 5 game logs (scores, opponent ORtg/DRtg)
- Home/away splits this season
- Back-to-back status
- Key player season averages vs opponent defense ranking

MLB:
- Starting pitchers' season stats (ERA, WHIP, K/9, recent form)
- Bullpen fatigue (check innings pitched last 2 days for each team)
- Batting order splits vs LHP/RHP
- Weather (temperature, wind, humidity)

Soccer:
- Team xG and actual goals (last 5 games)
- Possession % and shots on target last 5 games
- H2H record and recent trends
- Injury concerns (key players out)
```

### Step 4: Calculate YOUR Probability
```
METHOD 1: Team Strength Model
- Use Net Rating (NBA), run differential (MLB), or xG differential (soccer)
- Apply home field advantage (+2.5-3% NBA, +3% MLB, +2% soccer)
- Apply rest disadvantage if applicable (-3% for B2B 2nd night, -2% for fatigue)
- Adjust for injuries (star player out = -5% to -10% probability)
- Arrive at probability estimate

METHOD 2: Regression to Analytical Tools
- Input team stats into publicly available models (e.g., FiveThirtyEight for NBA)
- Compare to your manual calculation (should be close; if very different, research why)
- Use the model's projection with your injury/rest adjustments

DOCUMENTATION:
Write down your probability estimate and WHY. Example:
"Lakers 58% to beat Nuggets: Base +3 NR advantage, -2 for B2B fatigue, +3 home, -1 for LeBron hamstring risk"
```

### Step 5: Compare to Market Odds
```
MARKET ANALYSIS:
- Sportsbook odds: Convert to implied probability (formulas above)
- Kalshi price: Note the spread (YES price vs what you'd need NO to be)
- Other sportsbooks: Check 2-3 books for best odds (e.g., DraftKings vs FanDuel)
- Find best line: If your edge is 5% on -110 odds, ensure you get those specific odds

EXAMPLE:
Your probability: Lakers 58%
Market odds: Lakers -120 (54.5% implied)
Your edge: 58% - 54.5% = 3.5% ✓ MEETS THRESHOLD
Better odds available?: Check FanDuel (-115 = 53.6% implied) = 4.4% edge. Use FanDuel.
```

### Step 6: Filter by Edge & Confidence
```
STRICT CRITERIA (BOTH must be met):
1. Edge ≥ 3% (calculated in Step 5)
2. Confidence ≥ 6/10 (your conviction in the analysis)

EXAMPLE DECISIONS:
Pick A: Edge +5%, Confidence 8/10 → BET (both criteria met)
Pick B: Edge +2%, Confidence 7/10 → PASS (edge below threshold, no matter confidence)
Pick C: Edge +4%, Confidence 5/10 → PASS (confidence too low; indicates weak model conviction)
Pick D: Edge +3.2%, Confidence 7/10 → BET (marginal but acceptable)
```

### Step 7: Calculate Bet Size via Quarter Kelly
```
FORMULA:
Bet Size = Bankroll × 0.05 × (Your Probability - Market Probability) / b

Simplified:
- Bankroll = current balance (e.g., $5,000)
- 0.05 = 5% max single bet
- Edge = your prob - market prob (e.g., 0.03)
- b = decimal odds - 1 (e.g., -110 odds = 1.909 decimal, so b = 0.909)

EXAMPLE:
Bankroll $5,000, Edge +3%, Odds -110 (b=0.909)
Bet Size = $5,000 × 0.05 × (0.03 / 0.909) = $5,000 × 0.05 × 0.033 = $8.25

But this is too conservative. Use this INSTEAD:
Bet Size = Bankroll × Edge % × 0.15 (simplified Quarter Kelly proxy)
Bet Size = $5,000 × 0.03 × 0.15 = $22.50

Or simpler: Use 1-2% of bankroll per 3% edge
- 3% edge = $50-100 bet (assuming $5k bankroll)
- 5% edge = $75-150 bet
- 10% edge = $150-300 bet

CONSTRAINT: Never exceed 5% of bankroll per bet ($250 max on $5k)
CONSTRAINT: Parlay/combo max 3% of bankroll ($150 max on $5k)
```

### Step 8: Generate Pick Card
```
🎯 DAILY PICKS — [Date]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PICK #1 — [Sport] [Confidence: X/10] [Risk: Low/Med/High]
📊 Game: [Team A] vs [Team B]
💰 Pick: [Your selection (Moneyline/Spread/Total)]
📈 Market Odds: [odds] (Implied: XX.X%)
🧮 My Model: XX.X% (Edge: +X.X%)
💵 Bet Size: $XX (X.X% of bankroll)
📝 Reasoning: [2-3 sentences explaining the why]

PICK #2 — [Sport] [Confidence: X/10] [Risk: Low/Med/High]
[... repeat format ...]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 SUMMARY
Total Bets: X | Total Staked: $XXX
Average Edge: X.X% | Average Confidence: X.X/10
Expected Value: +$XX.XX
⚠️ Risk Notes: [any correlated bets or concerns]
```

### Step 9: Risk Assessment — Correlation Check
```
BEFORE SUBMITTING PICKS, CHECK FOR CORRELATION:

Types of Correlation:
1. Same Sport Correlation (NBA plays tonight only, so two NBA picks are somewhat correlated)
2. Game Correlation (Team A vs Team B → don't bet both team moneylines in same game)
3. Tournament Correlation (March Madness → team A plays team B in same game, don't parlay)
4. Cross-sport correlation (unlikely but check)

RED FLAG: More than 3 correlated bets in same day
Example (BAD): 4 NBA picks on same night where 3 are favorites + 1 underdog = high correlation
Remedy: Drop one of the weak-edge picks, keep only 3 strongest

EXAMPLE (GOOD DIVERSIFICATION):
Monday:
- NBA Moneyline Pick (independent)
- MLB Over Pick (independent sport, independent team)
- Soccer Under Pick (independent sport)
- No correlation → proceed with all 3

Monday (BAD):
- NBA Pick A: Lakers vs Nuggets, Lakers win
- NBA Pick B: Lakers vs Nuggets, Under 220
- NBA Pick C: Suns vs Warriors, Suns win
- Picks A & B are highly correlated (same game); Pick C is independent
- Remedy: Keep A & C (or B & C), drop one A/B pair
```

---

## Red Flags — DO NOT BET When:

1. **Edge < 3%**
   - Insufficient margin for error
   - Model deviation of just 1-2% wipes out the edge
   - PASS and wait for clearer opportunity

2. **Key Injury News is Ambiguous**
   - Player listed as "questionable" with no clear status
   - Conflicting reports (team says maybe, analyst says out)
   - Rule: Wait 1-2 hours before game for official confirmation
   - Don't bet a game where the outcome hinges on 30/70 chance a star plays

3. **You've Hit Daily Loss Limit**
   - Bankroll down 10%+ today
   - Emotions are elevated
   - Close the app and come back tomorrow
   - This is non-negotiable (prevents cascade losses)

4. **Parlay has More Than 2-3 Correlated Legs**
   - Example: 4-leg parlay where all 4 teams are in the same sport on the same day
   - Correlation destroys parlay value
   - Reduce to independent events only

5. **Emotional Attachment to a Team**
   - "I love the Lakers, so I'm betting them -200"
   - "My favorite team is overdue to win"
   - Model says fade, but heart says bet → FADE (model is right)
   - Recognize bias and exclude games from your own favorite team

6. **Line Has Moved Significantly Against You**
   - Analyzed game at -110, decided to bet
   - Return 2 hours later, line is -150 (moved against your pick)
   - You've lost 2-3% edge in that time
   - Decision: Don't chase; either take the new line (if still +EV) or pass
   - Smart money often moves lines fast; respect the move

7. **You're Betting "Because You Haven't Bet Today"**
   - No quality bets available
   - You feel obligated to place something
   - This is exactly when negative EV bets get placed
   - Rest days are PART of the system; embrace them

---

## Weekly Review Protocol

### Sunday Evening Ritual (Every Week)

**Step 1: Compile All Week's Bets**
```
Create table with columns:
Date | Sport | Game | Pick | Odds | Stake | Win/Loss | Result
Mon  | NBA   | LAL vs DEN | LAL -110 | $50 | ✓ | +$45.45
Tue  | MLB   | NYY vs BAL | Over -110 | $45 | ✗ | -$45
...
```

**Step 2: Categorize W/L by Sport**
```
NBA Results:
- Wins: 3 (total +$180)
- Losses: 2 (total -$95)
- Net: +$85
- ROI: +12.1%

MLB Results:
- Wins: 2 (total +$110)
- Losses: 2 (total -$95)
- Net: +$15
- ROI: +2.7%

Soccer Results:
- Wins: 1 (total +$55)
- Losses: 1 (total -$50)
- Net: +$5
- ROI: +4.5%
```

**Step 3: Calculate Actual ROI vs Expected ROI**
```
Expected ROI Calculation:
- If you had 5 bets with average 5% edge
- At 55% win rate (from Poisson), expected profit = 5 × 0.05 × avg stake
- If $50 avg stake, expected = 5 × $50 × 0.05 = +$12.50
- If actual = +$40, you beat expectations ✓
- If actual = -$20, you underperformed expectations (luck or model issues?)
```

**Step 4: Identify Strengths & Weaknesses**
```
Questions to Ask:
1. Which sport had highest ROI? Double down on that sport next week
2. Did you hit your win rate targets? (should be 52-58% for +3-5% edge bets)
3. Which bet type lost money? (e.g., parlays losing, props winning)
4. What was your worst bet? What was the reasoning? (learn from mistakes)
5. Did you follow bankroll rules? (no oversizing, no revenge betting?)
6. Did you correctly identify edges? (bets with +5% edge actually won more than -5% edge bets?)
```

**Example Analysis:**
```
Best Performer: MLB under-betting (ROI +15%)
→ Reason: Bullpen fatigue analysis was accurate
→ Action: Increase MLB allocation to 40% of weekly bets next week

Worst Performer: NBA parlays (ROI -45%)
→ Reason: Correlated legs (2 favorites in same sport on same night)
→ Action: Stop doing multi-leg parlays; focus on singles and two-leg combos only

Unexpected Loss: Bet on team with +8% edge, lost 3 in a row
→ Lesson: Variance happens; 3-bet losing streaks are normal with small sample
→ Action: Increase sample size (more weeks of tracking) to confirm model accuracy
```

**Step 5: Adjust Strategy for Next Week**
```
Allocation Shift:
- This week: NBA 50%, MLB 30%, Soccer 20%
- Next week: NBA 40%, MLB 45%, Soccer 15% (double down on MLB strength)

Bet Type Adjustment:
- Stop: Betting B2B games (were right to fade but keep fading)
- Start: Targeting more MLB pitcher matchups (proven strong)
- Continue: Soccer Asian Handicap (working well)

Confidence Threshold Adjustment:
- This week: Bet bets with 5/10 confidence; some were losers
- Next week: Minimum 6/10 confidence (or 6.5/10 if in downswing)

Bankroll Management:
- Started week: $5,000
- Current: $5,085 (+1.7%)
- Unit size: Hold at $50 (in growth territory, no reduction needed)
- If had negative week, reduce units 20-25% next week
```

**Step 6: Document Lessons in Journal**
```
Simple entry:
"March 16-22: +$85 (+1.7% ROI). MLB was strong (+12%, bullpen analysis accurate).
NBA parlays were weak (-45%, too correlated). Next week: increase MLB to 45% of bets,
stop doing 3+ leg parlays, maintain $50 unit size. Confidence threshold at 6/10 minimum."
```

---

## Appendix: Common Pitfalls & Fixes

### Pitfall 1: "I'm Down 15%, Time to Get Even Quick"
**Problem**: Emotional response to losing leads to oversizing and revenge betting
**Fix**: Stick to bankroll rules. Daily loss limit forces you to stop. Weekly loss limit forces you to reduce units.

### Pitfall 2: "This Team is Due to Win"
**Problem**: Regression to the mean is real, but it's not predictable on a game-by-game basis
**Fix**: Use current form (last 5 games) not season averages. "Due" is not a statistical edge.

### Pitfall 3: "Public is Always Wrong"
**Problem**: Contrarian bias — betting against public just because they're wrong sometimes
**Fix**: Public is wrong ~48% of the time (close to random). Use your model, not contrarian instinct.

### Pitfall 4: "I Have a Hot Hand"
**Problem**: Winning streak leads to overconfidence and larger bets
**Fix**: Discipline never changes. Same unit size through wins and losses. Increase only after month of +15%+ profit.

### Pitfall 5: "This Game Doesn't Matter"
**Problem**: Betting on meaningless games because the odds look good
**Fix**: "Meaningless" games often have the worst data (fatigue, random lineups). Avoid them.

### Pitfall 6: "I'll Parlay Three Favorites for a Big Payout"
**Problem**: High correlation, low edge, high variance
**Fix**: Parlay only independent events with 3%+ edge each. Limit to 1-2 leg max.

---

## Summary: Your Role as Betting Consultant

When the user asks for betting analysis, picks, or advice:

1. **Always gather current data** (today's games, recent news, injury updates)
2. **Calculate YOUR probability** using the sport-specific frameworks
3. **Compare to market odds** and identify edges ≥ 3%
4. **Filter by confidence ≥ 6/10** and apply bankroll rules
5. **Generate pick cards** with clear reasoning and bet sizes
6. **Perform risk assessment** (correlation check, red flags)
7. **Provide bankroll guidance** if asked (Kelly sizing, daily limits, unit management)
8. **Review weekly performance** with the user to identify patterns

Your goal is to help the user win long-term by finding positive EV bets and maintaining strict discipline. Luck will vary week-to-week, but +EV strategies win over 50+ bets.

---

**Last Updated**: 2026-03-23
**Model**: Starlizard/Tony Bloom Framework
**User Target**: $500/week profit, Kalshi focus, NBA/MLB/Soccer
**Bankroll**: $5,000-10,000 recommended (adjust unit size accordingly)
