# Sports Betting Formulas & Mathematical Reference

**Purpose**: Complete mathematical reference for the betting consultant skill. All formulas include derivations, worked examples, and practical applications.

---

## 1. Odds Conversion & Probability

### 1.1 American Odds to Implied Probability

**Formula (Negative Odds - Favorites):**
```
Probability = |Odds| / (|Odds| + 100)
```

**Example:**
```
Odds: -150 (Lakers favored)
Probability = 150 / (150 + 100) = 150 / 250 = 0.60 (60%)
```

**Formula (Positive Odds - Underdogs):**
```
Probability = 100 / (Odds + 100)
```

**Example:**
```
Odds: +150 (Nets underdog)
Probability = 100 / (150 + 100) = 100 / 250 = 0.40 (40%)
```

**Quick Reference Table:**
```
American Odds    Implied Probability
-110            52.4%
-120            54.5%
-150            60.0%
-200            66.7%
+100            50.0%
+150            40.0%
+200            33.3%
+250            28.6%
```

---

### 1.2 Decimal Odds to Implied Probability

**Formula:**
```
Probability = 1 / Decimal Odds
```

**Example:**
```
Decimal Odds: 2.50 (Lakers favored)
Probability = 1 / 2.50 = 0.40 (40%)

Decimal Odds: 1.667 (equivalent to -150 American)
Probability = 1 / 1.667 = 0.60 (60%)
```

**Relationship:**
```
Decimal Odds = (100 + Negative American Odds) / |American Odds|
Example: -150 → (100 + 150) / 150 = 250 / 150 = 1.667
```

---

### 1.3 Implied Probability to Odds

**To American Odds (from probability):**

**For Favorites (probability > 0.50):**
```
American Odds = -1 × Probability / (1 - Probability) × 100
```

**Example:**
```
Your probability: 65%
American Odds = -1 × 0.65 / 0.35 × 100 = -185.7 ≈ -185
(65% probability is roughly -185 odds)
```

**For Underdogs (probability < 0.50):**
```
American Odds = (1 - Probability) / Probability × 100
```

**Example:**
```
Your probability: 35%
American Odds = 0.65 / 0.35 × 100 = +185.7 ≈ +185
(35% probability is roughly +185 odds)
```

**To Decimal Odds (from probability):**
```
Decimal Odds = 1 / Probability
```

**Example:**
```
Your probability: 60%
Decimal Odds = 1 / 0.60 = 1.667
```

---

## 2. Expected Value (EV) Calculation

### 2.1 Core EV Formula

**Definition**: Expected Value is the average profit/loss per bet over infinite repetition.

**Formula (Using Probability and Payout):**
```
EV = (Probability of Win × Payout) - (Probability of Loss × Stake)
EV = (P_win × Payout) - (P_loss × Stake)

Where:
- P_win = your estimated probability of winning
- P_loss = 1 - P_win
- Payout = total return if bet wins (including original stake)
- Stake = amount wagered
```

**Example:**
```
Bet: $100 on Lakers at -150 odds (1.667 decimal)
Your probability: 65%

Payout if win = $100 × 1.667 = $166.70
Stake = $100

EV = (0.65 × $166.70) - (0.35 × $100)
EV = $108.36 - $35.00
EV = +$73.36 per $100 wagered

Interpretation: Over 100 identical $100 bets with +$73.36 EV each, you expect +$7,336 profit
```

---

### 2.2 Simplified EV Formula (Using Decimal Odds)

**Formula:**
```
EV (as percentage) = (Probability × Decimal Odds) - 1

EV (as profit) = (Probability × Decimal Odds - 1) × Stake
```

**Example:**
```
Bet: $100 on Lakers at -150 (1.667 decimal)
Your probability: 65%

EV% = (0.65 × 1.667) - 1 = 1.0836 - 1 = 0.0836 (8.36% EV)
EV$ = 0.0836 × $100 = $8.36 per $100 bet

This matches the previous formula (differs only by rounding)
```

---

### 2.3 Break-Even EV Threshold

**When is EV > 0?**
```
EV > 0 when: Your Probability > Market Implied Probability

Example:
- Market implies 60% (from -150 odds)
- You calculate 65%
- You have +5% EV (65% - 60%)
```

**Minimum Edge for Profitable Betting:**
```
Recommended minimum edge: 3%

Why 3%?
- 0-2% edge: Too much variance, model error eats edge
- 3-5% edge: Good sweet spot, sustainable
- 5%+ edge: Excellent, but rare to find

The math:
- 3% edge with 100 bets at $50 each = $150 profit expected
- Variance might result in -$100 to +$400 actual, but +$150 is center
- With 2% edge, variance can easily wipe out expected profit
```

---

### 2.4 Worked Examples

**Example 1: Favorites**
```
Game: Lakers vs Celtics
Odds: Lakers -120 (54.5% implied)
Your probability: 58%
Edge: 58% - 54.5% = 3.5%
Decimal odds: 1.833

Stake: $100
EV = (0.58 × 1.833) - 1 = 1.063 - 1 = 0.063 (6.3%)
EV$ = 0.063 × $100 = $6.30

Over 100 such bets: $630 expected profit
```

**Example 2: Underdogs**
```
Game: Nets vs Lakers
Odds: Nets +180 (35.7% implied, 2.80 decimal)
Your probability: 40%
Edge: 40% - 35.7% = 4.3%

Stake: $100
EV = (0.40 × 2.80) - 1 = 1.12 - 1 = 0.12 (12%)
EV$ = 0.12 × $100 = $12.00

Over 100 such bets: $1,200 expected profit
```

**Example 3: Total (Over/Under)**
```
Game: Lakers vs Celtics Over 220.5 points
Odds: -110 (52.4% implied, 1.909 decimal)
Your probability: 54% (based on xG, pace, defenses)
Edge: 54% - 52.4% = 1.6%

Stake: $100
EV = (0.54 × 1.909) - 1 = 1.031 - 1 = 0.031 (3.1%)
EV$ = 0.031 × $100 = $3.10

This clears the 3% edge threshold (barely), so it's a PASS pick
```

---

## 3. Kelly Criterion: Optimal Bet Sizing

### 3.1 Full Kelly Formula & Derivation

**Formula:**
```
f* = (bp - q) / b

Where:
- f* = fraction of bankroll to wager
- b = odds received (decimal odds - 1, or payout ratio)
- p = probability of winning (your estimate)
- q = probability of losing (1 - p)
```

**Derivation (Conceptual):**

Kelly Criterion maximizes long-term bankroll growth by balancing risk and return. The formula comes from maximizing the expected logarithm of bankroll growth:

```
G = ln(Bankroll × (1 + f × b) if win, OR Bankroll × (1 - f) if lose)

Taking derivative with respect to f and setting to 0:
dG/df = p × b / (1 + fb) - q / (1 - f) = 0

Solving for f:
f* = (bp - q) / b
```

**Practical Interpretation:**
- f* = 0.10 means wager 10% of bankroll
- f* = 0.25 means wager 25% of bankroll
- f* > 0.50 means odds are very good and you're very confident

---

### 3.2 Full Kelly Examples

**Example 1: Favorite**
```
Bet: Lakers -150 (1.667 decimal)
Your probability: 65%

b = 1.667 - 1 = 0.667
p = 0.65
q = 0.35

f* = (0.667 × 0.65 - 0.35) / 0.667
f* = (0.433 - 0.35) / 0.667
f* = 0.083 / 0.667
f* = 0.125 (12.5% of bankroll)

With $5,000 bankroll: Wager $625
```

**Example 2: Underdog**
```
Bet: Nets +180 (2.80 decimal)
Your probability: 42%

b = 2.80 - 1 = 1.80
p = 0.42
q = 0.58

f* = (1.80 × 0.42 - 0.58) / 1.80
f* = (0.756 - 0.58) / 1.80
f* = 0.176 / 1.80
f* = 0.0978 (9.78% of bankroll)

With $5,000 bankroll: Wager $489
```

**Example 3: High-Edge Underdog**
```
Bet: Nets +150 (2.50 decimal)
Your probability: 48% (sharp analysis)

b = 2.50 - 1 = 1.50
p = 0.48
q = 0.52

f* = (1.50 × 0.48 - 0.52) / 1.50
f* = (0.72 - 0.52) / 1.50
f* = 0.20 / 1.50
f* = 0.133 (13.3% of bankroll)

With $5,000 bankroll: Wager $667
```

---

### 3.3 Quarter Kelly: Safe Variant (RECOMMENDED)

**Formula:**
```
f* (Quarter Kelly) = f* (Full Kelly) / 4
```

**Rationale:**
- Full Kelly maximizes growth but has extreme variance
- Drawdowns can reach 40-60% with Full Kelly
- Quarter Kelly reduces variance by 75% while capturing 95% of long-term growth
- Quarter Kelly also buffers against model error (your 65% estimate might be 58%)

---

### 3.4 Quarter Kelly Examples

**Example 1: Favorite (Revisited)**
```
Full Kelly: 12.5% of bankroll
Quarter Kelly: 12.5% / 4 = 3.125% of bankroll

With $5,000 bankroll: Wager $156 (vs $625 with Full Kelly)
Much safer, reduces risk of ruin, still captures edge
```

**Example 2: Underdog (Revisited)**
```
Full Kelly: 9.78% of bankroll
Quarter Kelly: 9.78% / 4 = 2.45% of bankroll

With $5,000 bankroll: Wager $123 (vs $489 with Full Kelly)
```

---

### 3.5 Kelly Criterion Caveats

**Problems with Extreme Kelly:**
```
Example: Full Kelly on good-probability bet
- Bankroll: $10,000
- Edge: High (+8%)
- Full Kelly: 20% of bankroll = $2,000 per bet
- If you lose 2 in a row: $10,000 - $2,000 - $1,600 = $6,400 (36% drawdown)
- Psychologically brutal, may lead to panic decisions
```

**Solution: Use Fractional Kelly**
```
Betting in practice:
- 1/4 Kelly (recommended): Smooth, sustainable, safe
- 1/2 Kelly: More aggressive than 1/4, still conservative vs Full Kelly
- Full Kelly: Only for professional bettors with large bankrolls and emotional discipline
```

**Kelly's Assumption:**
- Assumes odds don't change (Kalshi prices may move against you)
- Assumes no reinvestment lag (real money takes time to settle)
- Assumes your probability estimate is perfect (it's not; model error is real)

---

## 4. Poisson Distribution for Soccer Goals

### 4.1 Poisson Probability Formula

**Formula:**
```
P(X = k) = (e^-λ × λ^k) / k!

Where:
- λ (lambda) = expected goals (mean)
- k = number of goals (0, 1, 2, 3, ...)
- e ≈ 2.71828 (Euler's number)
- k! = k factorial (k × (k-1) × (k-2) × ... × 1)
```

**Why Poisson for Soccer?**
- Soccer goals follow near-Poisson distribution (independently rare events)
- Team xG maps directly to λ parameter
- More accurate than assuming normal distribution

---

### 4.2 Poisson Calculation (Step-by-Step)

**Example: Manchester City Expected Goals = 2.5**

**Step 1: Calculate e^-λ**
```
λ = 2.5
e^-2.5 = 0.08208 (use calculator or log tables)
```

**Step 2: Calculate probability for each outcome**

**P(City scores 0 goals):**
```
P(X=0) = (0.08208 × 2.5^0) / 0!
P(X=0) = (0.08208 × 1) / 1
P(X=0) = 0.0821 (8.21%)
```

**P(City scores 1 goal):**
```
P(X=1) = (0.08208 × 2.5^1) / 1!
P(X=1) = (0.08208 × 2.5) / 1
P(X=1) = 0.2052 (20.52%)
```

**P(City scores 2 goals):**
```
P(X=2) = (0.08208 × 2.5^2) / 2!
P(X=2) = (0.08208 × 6.25) / 2
P(X=2) = 0.5130 / 2
P(X=2) = 0.2565 (25.65%)
```

**P(City scores 3 goals):**
```
P(X=3) = (0.08208 × 2.5^3) / 3!
P(X=3) = (0.08208 × 15.625) / 6
P(X=3) = 1.2825 / 6
P(X=3) = 0.2137 (21.37%)
```

**P(City scores 4+ goals):**
```
P(X≥4) = 1 - [P(0) + P(1) + P(2) + P(3)]
P(X≥4) = 1 - [0.0821 + 0.2052 + 0.2565 + 0.2137]
P(X≥4) = 1 - 0.7575
P(X≥4) = 0.2425 (24.25%)
```

**Summary for Manchester City (λ=2.5):**
```
0 goals: 8.2%
1 goal: 20.5%
2 goals: 25.7%
3 goals: 21.4%
4+ goals: 24.3%

Total: 100% ✓
```

---

### 4.3 Poisson for Match Totals

**Example: Over/Under 2.5 Goals**

**Two Independent Teams:**
```
Manchester City xG: 2.5 goals
Arsenal xG: 1.2 goals

Poisson distribution for Arsenal (λ=1.2):
0 goals: 30.1%
1 goal: 36.1%
2 goals: 21.7%
3+ goals: 12.1%
```

**Total Goals Distribution:**
```
To calculate total, sum all combinations:
- City 0 + Arsenal 0 = 0 total: 8.2% × 30.1% = 2.5%
- City 0 + Arsenal 1 = 1 total: 8.2% × 36.1% = 3.0%
- City 0 + Arsenal 2 = 2 total: 8.2% × 21.7% = 1.8%
- City 1 + Arsenal 0 = 1 total: 20.5% × 30.1% = 6.2%
- City 1 + Arsenal 1 = 2 total: 20.5% × 36.1% = 7.4%
- City 1 + Arsenal 2 = 3 total: 20.5% × 21.7% = 4.5%
- City 2 + Arsenal 0 = 2 total: 25.7% × 30.1% = 7.7%
- City 2 + Arsenal 1 = 3 total: 25.7% × 36.1% = 9.3%
- City 2 + Arsenal 2 = 4 total: 25.7% × 21.7% = 5.6%
... and so on for higher totals

Under 2.5 (0, 1, or 2 goals):
= 2.5% + 3.0% + 6.2% + 1.8% + 7.4% + 7.7% = 28.6%

Over 2.5 (3+ goals):
= 100% - 28.6% = 71.4%
```

**Market Comparison:**
```
If Over 2.5 is priced at -120 (54.5% implied)
Your calculation: 71.4% probability
Edge: 71.4% - 54.5% = +16.9%
STRONG BET (far exceeds 3% threshold)
```

---

### 4.4 Poisson Reference Table (Common λ Values)

**λ = 1.5 (Low-scoring expected):**
```
0 goals: 22.3%
1 goal: 33.5%
2 goals: 25.1%
3+ goals: 19.1%
```

**λ = 2.0 (Average xG):**
```
0 goals: 13.5%
1 goal: 27.1%
2 goals: 27.1%
3 goals: 18.0%
4+ goals: 14.3%
```

**λ = 2.5 (Above-average xG):**
```
0 goals: 8.2%
1 goal: 20.5%
2 goals: 25.7%
3 goals: 21.4%
4+ goals: 24.3%
```

**λ = 3.0 (High xG):**
```
0 goals: 5.0%
1 goal: 15.0%
2 goals: 22.5%
3 goals: 22.5%
4+ goals: 35.0%
```

---

## 5. Parlay & Combo Mathematics

### 5.1 Independent Bets (No Correlation)

**Combined Probability:**
```
P(both) = P(Bet A) × P(Bet B)

Example:
Bet A: Lakers win 55%
Bet B: Yankees win 60%
Combined: 0.55 × 0.60 = 0.33 (33%)
```

**Parlay Payout (American Odds):**
```
Payout = Stake × [(Odds A / 100) + 1] × [(Odds B / 100) + 1]

Example (both at -110):
Stake: $100
Odds A: -110 → decimal 1.909
Odds B: -110 → decimal 1.909
Payout: $100 × 1.909 × 1.909 = $364.81
Profit: $264.81
```

**Parlay EV Calculation:**
```
EV = (Your Combined Probability × Payout) - Stake

Example:
Your probability: 33%
Payout: $364.81
Stake: $100
EV = (0.33 × $364.81) - $100 = $120.39 - $100 = +$20.39

BUT, if the market probability is also 33% (fair odds):
EV ≈ 0 (no edge, this is a fair bet)
```

---

### 5.2 Correlated Bets (Same Event Risk)

**Problem with Correlation:**
```
Bet A: Lakers Moneyline (55%)
Bet B: Lakers vs Celtics Under 220.5 (58%)

These are CORRELATED because both depend on Lakers' performance.
If Lakers lose (45% chance), both bets lose.
Combined probability ≠ 0.55 × 0.58 = 0.319

Instead, use correlation factor:
P(both) = P(A) × P(B) × (1 - Correlation Coefficient)

If correlation = 0.40:
P(both) = 0.55 × 0.58 × (1 - 0.40) = 0.3190 × 0.60 = 0.191 (19.1%)

This is much lower than uncorrelated 31.9%!
Correlated bets have hidden variance risk.
```

**Red Flags for Correlation:**
- Same team in both bets
- Both bets in same game (moneyline + spread + total)
- Both bets dependent on weather (soccer & baseball same day)
- Tournament with same pool (March Madness team A and team A's potential opponent)

---

### 5.3 Combo Betting on Kalshi (Practical Example)

**Scenario: Two Independent Kalshi Contracts**
```
Contract A: "Lakers Win Today" priced at $0.55
- Your probability: 62%
- Edge: +7%

Contract B: "Over 220.5 Points" priced at $0.52
- Your probability: 58%
- Edge: +6%

These are independent (different outcomes, minimal correlation)
```

**Combo Calculation:**
```
Individual buys:
- Contract A cost: $0.55 per share
- Contract B cost: $0.52 per share

Option 1: Buy both individually for $100
- Spend $55 on A, $52 on B = $107 total (oops, $100 max)
- Adjust: $55 on A, $45 on B = $100

Expected value:
A: (0.62 - 0.55) × $55 = 0.07 × $55 = +$3.85
B: (0.58 - 0.52) × $45 = 0.06 × $45 = +$2.70
Total EV: +$6.55

Option 2: Create combo on Kalshi (if available)
- Combo payout: $1.00 if both YES
- Combo cost: $0.55 × $0.52 ≈ $0.29 per share
- Buy 300 shares at $0.29 = $87 spend
- Payout if both hit: $300
- Profit if both hit: $213

Expected payout:
- Both hit (0.62 × 0.58 = 0.36): Win $213
- At least one fails (0.64): Lose $87
- EV = (0.36 × $213) - (0.64 × $87) = $76.68 - $55.68 = +$21.00

Better than individual bets (+$6.55 vs +$21.00)!
BUT higher variance (all or nothing)
```

---

### 5.4 Parlay Expected Value Formula

**For N legs (independent):**
```
P(all win) = P(Bet 1) × P(Bet 2) × ... × P(Bet N)

Example: 3-leg parlay
P = 0.55 × 0.55 × 0.60 = 0.1815 (18.15%)

Payout calculation (all -110):
Payout = Stake × 1.909 × 1.909 × 1.909 = Stake × 6.969
Example: $100 parlay pays $696.90 if all 3 win

EV = (0.1815 × $696.90) - $100 = $126.51 - $100 = +$26.51
```

**When to Parlay vs Individual Bets:**
```
Single bets: Use when each leg has solid edge (3%+)
Parlay: Use when you want concentrated risk on multiple edges
- Example: Two games with +8% edge each
  - Individuals: Expected +$16 profit (safer)
  - Parlay: Expected higher variance, higher potential payout

Rule: Parlay only if:
1. Each leg has ≥ 3% edge
2. Legs are independent (no correlation)
3. Combo odds are actually better than individual
4. You have bankroll to absorb loss (3% max of bankroll)
```

---

## 6. ROI & Closing Line Value (CLV)

### 6.1 Return on Investment (ROI)

**Formula:**
```
ROI% = (Profit / Total Amount Wagered) × 100

Example:
Total wagered in week: $500 (5 bets × $100)
Result: +$45 profit
ROI = ($45 / $500) × 100 = 9%
```

**Interpreting ROI:**
```
ROI 0-3%: Barely profitable; variance might dominate
ROI 3-5%: Good performance; sustainable edge
ROI 5-10%: Excellent; likely sharp bettor
ROI 10%+: Exceptional; usually small sample or very sharp model
```

**ROI vs Win Rate:**
```
You can be profitable without 55% win rate if edges are right.

Example: 50% win rate, but better odds selection
- 100 bets @ $100 each = $10,000 wagered
- 50 wins (average +$105 payout per bet) = $5,250 profit
- 50 losses (average -$100 per bet) = -$5,000 loss
- Net: +$250 profit
- ROI: 2.5% (marginal, but profitable!)

This works only if you consistently pick +3-5% edge bets.
```

---

### 6.2 Closing Line Value (CLV)

**Definition:**
CLV measures whether you got better or worse odds than market did at bet close. It's a proxy for your edge quality.

**Formula:**
```
CLV$ = (Closing Odds - Opening Odds) × Stake (if favorable)
CLV% = (Closing Odds - Opening Odds) / Opening Odds × 100

Example:
- Opened bet at -110 (54.5% implied)
- Game time, line moved to -130 (56.6% implied)
- You bet at the opener (better odds)
- CLV = You got +1.1% edge by line movement
```

**Why CLV Matters:**
```
Your EV comes from two sources:
1. Model edge (your 65% vs market 60% = +5%)
2. Line movement (line moved in your favor after you bet)

Long-term, CLV ≥ 0 indicates sharp betting.
CLV < 0 indicates you're betting against sharp money.

Example:
- You bet Lakers at -110, convinced 60% to win
- Line moves to -150 (66.7% implied)
- You still won the bet, but CLV says sharp money disagreed with you
- Your CLV was NEGATIVE (bet worse odds than closing)
```

**Tracking CLV (Weekly):**
```
Bet 1: Opened -110, closed -120, you bet at -110 → CLV = -1% (slightly negative)
Bet 2: Opened -110, closed -100, you bet at -110 → CLV = +1% (slightly positive)
Bet 3: Opened +150, closed +140, you bet at +150 → CLV = -1% (slightly negative)

Average CLV: -0.3% (slightly negative, suggests you're betting when sharp money disagrees)
```

---

## 7. Break-Even Win Rate for Different Odds

**Formula:**
```
Break-Even Win Rate = 1 / (Decimal Odds)

Or equivalently: |American Odds| / (|American Odds| + 100) for favorites
                 100 / (American Odds + 100) for underdogs
```

**Examples:**

**-110 Odds (1.909 Decimal):**
```
Break-even: 1 / 1.909 = 52.4%
Meaning: Win 52.4% and break even
Win 55% and profit ~2.6%
```

**-150 Odds (1.667 Decimal):**
```
Break-even: 1 / 1.667 = 60%
Meaning: Must win 60% to break even
This is why favorites are harder to profit on
```

**+150 Odds (2.50 Decimal):**
```
Break-even: 1 / 2.50 = 40%
Meaning: Win just 40% and break even
This is why underdogs can be profitable with <50% win rate
```

**Reference Table:**

```
American    Decimal   Break-Even Win Rate   Notes
-200        1.50      66.7%                 High bar for favorites
-150        1.67      60.0%                 Standard favorite
-120        1.83      54.5%                 Close favorite
-110        1.91      52.4%                 Slight favorite
+100        2.00      50.0%                 Even odds
+150        2.50      40.0%                 Underdog value
+200        3.00      33.3%                 Strong underdog
```

---

## 8. Bankroll Growth & Compound Returns

### 8.1 Linear Growth Model (Worst Case)

**Formula:**
```
Final Bankroll = Starting Bankroll + (Weekly Profit × Number of Weeks)

Example:
Start: $5,000
Weekly profit: +$100 (2% ROI)
After 10 weeks: $5,000 + ($100 × 10) = $6,000 (20% growth)
After 50 weeks: $5,000 + ($100 × 50) = $10,000 (100% growth, 1 year)
```

**Problem:** Ignores re-investment. In reality, you bet with your growing bankroll.

---

### 8.2 Compound Growth Model (More Realistic)

**Formula:**
```
Final Bankroll = Starting Bankroll × (1 + Weekly ROI)^(Number of Weeks)

Example:
Start: $5,000
Weekly ROI: +2% (1.02 multiplier)
After 10 weeks: $5,000 × (1.02)^10 = $5,000 × 1.2190 = $6,095
After 26 weeks (6 months): $5,000 × (1.02)^26 = $5,000 × 1.6734 = $8,367
After 52 weeks (1 year): $5,000 × (1.02)^52 = $5,000 × 2.6928 = $13,464
```

**Comparison:**
```
Linear: $5,000 → $10,000 in 50 weeks (100% growth)
Compound 2% ROI: $5,000 → $10,000 in ~35 weeks (100% growth)

Compound growth accelerates over time (more bankroll = larger units = faster compounding)
```

---

### 8.3 Growth with Variance (Realistic Model)

**Assumption:** Average ROI 2%, but variance causes ±1-2% swings weekly

**Simulation Scenario:**
```
Week 1: +1.5% → $5,075
Week 2: -0.5% → $5,050
Week 3: +2.0% → $5,155
Week 4: +2.5% → $5,283
Week 5: -1.0% → $5,225
...after 26 weeks (conservative 1.5% avg ROI)
Bankroll: $6,750 (35% growth, vs 34.8% compound)

...after 52 weeks (maintaining 1.5% avg ROI)
Bankroll: $9,125 (82.5% growth, vs 85% perfect compound)
```

**Key Insight:**
- Variance (losing weeks) slows compounding slightly
- But discipline and size management mitigate variance
- Quarter Kelly strategy captures high growth with low drawdown

---

### 8.4 Break-Even Analysis: When Do You Profit?

**Scenario: $5,000 Starting Bankroll, $50 Unit Size, 3% Average Edge**

**Expected Results Per 100 Bets:**
```
Bets: 100
Win rate with +3% edge: ~56%
Wins: 56 bets
Losses: 44 bets

Average payout on wins (mixed odds): 1.95
Total wagered: 100 × $50 = $5,000

Profit:
- Win side: 56 × $50 × 0.95 = $2,660
- Loss side: 44 × $50 × -1.00 = -$2,200
- Net: +$460 (9.2% ROI)

Timeline:
- 5 bets per week = 20 weeks for 100 bets
- Expected profit: +$460 in 5 months
```

**Variance Impact (Realistic):**
```
Best case (70% win rate in 100 bets): +$1,400 profit (28% ROI)
Expected case (56% win rate): +$460 profit (9.2% ROI)
Worst case (45% win rate): -$250 loss (-5% ROI)

50 bets (10 weeks):
- Win rate can swing 45-65% with just 50 bets
- Expected profit: +$230
- Worst case: -$125 loss
- Best case: +$700 profit

Implication: You need 50-100+ bets to be confident in your edge
```

---

### 8.5 Multi-Year Projection (Conservative)

**Assumptions:**
```
- Starting bankroll: $5,000
- Weekly ROI: 1.5% (conservative, achievable with 3-4 bets @ 3%+ edge)
- Variance: ±1% weekly swings
- Unit size: Adjust quarterly based on bankroll growth
```

**Projection:**
```
After 3 months (13 weeks):
Expected: $5,000 × (1.015)^13 = $6,079
Realistic range: $5,500 - $6,600

After 6 months (26 weeks):
Expected: $5,000 × (1.015)^26 = $7,373
Realistic range: $6,200 - $8,700

After 1 year (52 weeks):
Expected: $5,000 × (1.015)^52 = $10,854
Realistic range: $8,000 - $13,500

After 2 years (104 weeks):
Expected: $5,000 × (1.015)^104 = $23,505
Realistic range: $15,000 - $35,000
```

**Key Insights:**
1. Compounding accelerates year 2 (larger bankroll, larger units)
2. Variance has bigger percentage impact early, smaller impact later
3. Discipline and consistent 1.5%+ weekly ROI is achievable (5-7 quality bets per week at 3%+ edge)
4. Doubling bankroll in ~18-24 months is realistic

---

## 9. Advanced: Correlation in Parlays

### 9.1 Correlation Coefficient Estimation

**Independent Events:** Correlation = 0
```
Example: Lakers game and Yankees game (different sports, times)
P(both) = P(Lakers) × P(Yankees) with no adjustment
```

**Partially Correlated:** Correlation = 0.20 to 0.60
```
Example: Two games same day, weather-dependent
- Rain affects both soccer games' goal totals
- Correlation ~0.30
- P(both) = P(A) × P(B) × (1 - 0.30)
```

**Highly Correlated:** Correlation = 0.60 to 0.90
```
Example: Moneyline and spread of same team in same game
- If Lakers lose, both bets lose
- Correlation ~0.85
- P(both) = P(A) × P(B) × (1 - 0.85) = significant reduction
```

**Perfect Correlation:** Correlation = 1.0
```
Example: Same bet twice (Lakers to win + Lakers to win)
- Obviously, P(both) = P(Lakers once), not squared
```

### 9.2 Practical Correlation Table

```
Scenario                                      Correlation
Same sport, different games, same day         0.15
Same sport, same day, different teams         0.10
Same game, moneyline + total                  0.60
Same game, team prop + team win               0.70
Different sports, different days              0.00
Weather-dependent (two soccer games)          0.30
Both dependent on same team's performance     0.75+
Tournament: A and B if A wins                 0.50+
```

---

## 10. Reference: Common Profit Targets

**Weekly Profit Targets (With $5,000 Bankroll):**
```
Conservative: $75-100/week (1.5-2% ROI)
- Requires: 5-7 bets at 3%+ edge, 56%+ win rate
- Risk: Low to moderate
- Achievability: High (sustainable long-term)

Moderate: $150-200/week (3-4% ROI)
- Requires: 5-7 bets at 4%+ edge, 57%+ win rate
- Risk: Moderate
- Achievability: Medium (harder to find enough edges)

Aggressive: $250-300/week (5-6% ROI)
- Requires: 5-7 bets at 5%+ edge, 58%+ win rate
- Risk: High (variance can cause weeks of losses)
- Achievability: Low (very sharp analysis required)
```

**Time Horizon:**
```
$500/week target (10% ROI on $5k)
- Requires: Exceptional edge finding (5%+ average)
- Realistic: 1 in 100 bettors achieves this consistently
- Danger: Over-leveraging bankroll to chase this goal
```

---

## Summary: Which Formulas to Use

| Task | Formula |
|------|---------|
| Convert odds to probability | Probability = 1 / Decimal Odds |
| Calculate EV | EV = (Probability × Decimal Odds) - 1 |
| Optimal bet size | f* (Quarter Kelly) / 4 |
| Soccer goal probabilities | Poisson: P(X=k) = (e^-λ × λ^k) / k! |
| Parlay probability | P(Both) = P(A) × P(B) if independent |
| Expected win rate | Break-even = 1 / Decimal Odds |
| Bankroll growth | Final = Start × (1 + ROI)^weeks |
| ROI | ROI% = (Profit / Wagered) × 100 |

---

**Last Updated:** 2026-03-23
**Version:** 1.0
**Audience:** Betting consultants, sharp bettors, quantitative analysts
