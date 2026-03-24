---
name: sports-data-scout
description: "Automated sports data scout that gathers real-time stats, injuries, odds, and news for betting analysis. Use this skill whenever you need to: research today's games, check injury reports, look up team statistics, find current odds, get weather forecasts for games, check line movements, gather H2H records, or compile any sports data for betting decisions. Triggers on: today's games, injury report, team stats, standings, odds, line movement, weather, matchup, schedule, roster, starting lineup, pitcher, form guide, recent results, or any request for sports information that feeds into betting analysis. ALWAYS use this skill before making any betting recommendations."
---

# Sports Data Scout Skill

This skill provides a systematic protocol for gathering real-time sports data to inform betting decisions on Kalshi prediction markets. The focus is on NBA, MLB, and Soccer with an emphasis on finding free, reliable data sources and efficiently organizing information for analysis.

## Data Collection Protocol

For **EVERY** analysis session, gather data in this order. Each priority level must be completed before moving to the next.

### Priority 1 — Game Schedule & Odds (MUST HAVE)

These are non-negotiable. You cannot make a betting recommendation without knowing the matchup, timing, and current market prices.

**Web Search Queries:**
- `[Sport] games today [date] schedule`
- `[Sport] odds today Kalshi` OR `[Sport] odds today`
- `[Sport] spread movement today sharp money`

**What to Record:**
- Teams playing
- Start time (convert to ET for standardization)
- Current spread (and which team is favored)
- Moneyline odds for both teams
- Over/under total
- Kalshi market YES/NO price (if available)
- Public betting percentage (show which side is getting action)

**Example Output:**
```
GAME: Boston Celtics vs Miami Heat
DATE: 2026-03-23 | TIME: 7:30 PM ET
VENUE: FTX Arena (Miami)
SPREAD: Celtics -6.5
MONEYLINE: Celtics -250 / Heat +200
TOTAL: O/U 209.5
KALSHI: YES $0.68 / NO $0.32
PUBLIC: 62% on Celtics
```

### Priority 2 — Injury & Lineup (CRITICAL)

Missing key players dramatically shifts game outcomes. Flag any uncertainty.

**Web Search Queries:**
- `[Team name] injury report today`
- `[Sport] starting lineups today [date]`
- For MLB: `[Team] probable pitcher today`
- For NBA: `[Team] player status out questionable`
- For Soccer: `[Team] team news injuries squad`

**What to Verify:**
- Status of key players: OUT vs GTD (Game Time Decision) vs Probable
- Last confirmed report (many sources update throughout day)
- Cross-reference from at least 2 sources (ESPN + team official site)
- Note impact: Is this a bench rotation player or All-Star?
- Starting pitcher for MLB (impacts run total significantly)

**Flag Categories:**
- 🔴 OUT: Player confirmed unavailable
- 🟡 GTD: Game-time decision, wait for late confirmation
- 🟢 PROB: Likely to play pending late tests/warm-ups

### Priority 3 — Team Stats & Form

Context separates winning bets from losing ones. A 3-point favorite plays differently when up 8 games vs down 8 games.

**Web Search Queries:**
- `[Team] stats 2025-26 season standings`
- `[Team] last 10 games results record`
- `[Team] home/away split stats`
- For NBA: `[Team] net rating offensive rating`
- For MLB: `[Team] team batting average OPS ERA`
- For Soccer: `[Team] xG xGA goals conceded`

**NBA Key Stats to Find:**
- Record (overall, home, away)
- Net Rating (points per 100 possessions differential)
- Offensive Rating, Defensive Rating
- Pace (possessions per game)
- 3-point percentage
- Turnover rate
- Last 5 games record
- Streak (current win/loss streak)

**MLB Key Stats to Find:**
- Record (overall, home, away)
- Runs scored per game vs Runs allowed
- Team OPS (On-base Plus Slugging)
- Team ERA
- Bullpen ERA
- Team strikeout rate
- Last 5 games record
- Current pitcher's ERA, strikeout rate, walk rate

**Soccer Key Stats to Find:**
- Points total, record (W-D-L)
- Goals for, Goals against, Goal differential
- Expected Goals (xG) and Expected Goals Against (xGA)
- Possession %
- Shots on target
- Last 5 games record
- Home/away form split

### Priority 4 — Contextual Factors

These meta-factors separate sharp analysis from basic picking.

**Schedule Situation:**
- Back-to-back games? (Performance typically drops 3-5%)
- Rest advantage? (Team A on 3 days rest vs Team B on 1 day)
- Travel? (Cross-country, international travel impacts performance)
- Home/away status

**Weather (Outdoor Sports — Baseball, Soccer):**
- Temperature (cold = fewer home runs in baseball)
- Wind (direction and speed for baseball)
- Rain probability
- Field conditions

**Motivation Factors:**
- Playoff implications (teams play harder when seeds are decided)
- Rivalry matchups (tend to be lower-scoring, more physical)
- Revenge spot (team playing after recent loss to same opponent)
- Trading deadline (focus shifts for sellers/buyers in MLB)

**Historical Context:**
- H2H last 5 games: Which team dominates?
- Line movement: Where is sharp money going?
- Public betting trends: Is the public wrong here?
- Situational splits: How does Team A perform specifically in this situation?

## Free Data Sources Guide

### Multi-Sport Sources

**ESPN.com**
- Schedules: espn.com/[sport]/schedule
- Scores & standings: espn.com/[sport]/standings
- Injury reports: Look for "Injury Report" tab on team/league pages
- Team pages with recent results and statistics

**Sports Reference Sites** (All free, no paywall)
- Basketball: basketball-reference.com
- Baseball: baseball-reference.com
- Soccer: fbref.com (Football Reference)
- Includes: Game logs, play-by-play, advanced stats, splits

**Covers.com**
- Current odds across multiple sportsbooks
- Line movement history (how odds have shifted)
- Public betting percentage
- Advanced analysis and sharp indicators

**Action Network** (actionnetwork.com)
- Sharp money indicators
- Line movement tracking
- Consensus picks
- Expert analysis

### NBA Specific

**basketball-reference.com**
- Team stats pages: stats, pace, efficiency, shooting %
- Player pages: season stats, game logs, splits
- Matchup center: H2H records, head-to-head stats
- Advanced stats: Net Rating, True Shooting %, Player Efficiency Rating

**NBA.com/stats** (official NBA statistics)
- Team stats: Offensive Rating, Defensive Rating, pace
- Shooting stats: 2P%, 3P%, FT%
- Advanced metrics: True Shooting %, Effective Field Goal %
- Player tracking data

**Search Templates for NBA:**
- "NBA standings 2025-26" — Current season records
- "NBA injury report today" — Daily official report
- "[Team name] vs [Team name] head to head last 5" — Historical matchup
- "[Team] stats this season" — Season overview
- "[Player] stats 2025-26 game log" — Individual performance trend

### MLB Specific

**baseball-reference.com**
- Team pitching: ERA, strikeouts, walks, home runs allowed
- Team batting: Batting average, OPS, home runs, strikeouts
- Player game logs: Performance trends
- Pitcher game logs: Individual pitcher performance trend
- Splits: Home/away, vs right/left-handed, in day/night games

**FanGraphs.com** (advanced baseball analytics)
- xFIP (Expected Fielding Independent Pitching)
- Barrel% (hard-hit balls)
- wRC+ (Weighted Runs Created Plus)
- Launch angle and exit velocity
- Pitcher velocity trends

**Search Templates for MLB:**
- "MLB schedule today [date]" — Today's games
- "[Team] probable pitchers this week" — Starting pitchers
- "[Team] injury report today" — Daily roster updates
- "[Pitcher name] 2026 stats game log" — Pitcher performance trend
- "[Pitcher] vs [Team] stats split" — Pitcher vs specific opponent
- "[City] weather forecast [date] game time" — Weather impact

**Weather for Games:**
- Most stadiums can be found via search: "[City] weather [game time]"
- Key factors: Temperature (affects ball carry), wind (outfield impacts)
- Rain delays are rare but possible

### Soccer Specific

**fbref.com** (Football Reference)
- League tables: Points, goals, goal differential
- xG/xGA stats: Expected goals and defensive intensity
- Passing networks: Possession and distribution patterns
- Defensive actions: Tackles, interceptions, clearances
- Shooting stats: Shots, shots on target, conversion rate

**Transfermarkt.com**
- Squad depth: Market value and player positions
- Injury history: Current and recent injuries
- Transfer news: Recent signings/departures (affects form)
- Historical H2H records

**Search Templates for Soccer:**
- "Premier League table 2025-26" — Current season standings
- "La Liga standings 2025-26" — Current season standings
- "[Team] last 5 [League] results form" — Recent performance
- "[Team A] vs [Team B] head to head history" — Matchup record
- "[Team] injury report today [date]" — Squad news
- "[Team] xG stats this season" — Expected goals data
- "[Team] defensive record 2025-26" — Goals conceded trends

## API Integration Notes

For automation and real-time data pulls, these APIs offer free access:

**The Odds API** (the-odds-api.com)
- Live odds from multiple sportsbooks
- Free tier: 500 requests/month
- Covers: NFL, NBA, MLB, soccer
- Provides: Spreads, moneylines, totals, multiple book comparisons
- No API key needed for free tier

**BallDontLie API** (balldontlie.io)
- NBA stats (teams, players, games, seasons)
- Free tier: No rate limits
- Includes: Historical game logs, season stats
- No authentication required

**Kalshi API** (docs.kalshi.com)
- REST API for market data and order management
- WebSocket support for real-time price updates
- Demo environment: demo-api.kalshi.co
- Includes: Live market prices, volume, open interest
- Documentation: Comprehensive with code examples

## Data Collection Templates

### Game Analysis Card (Standard Format)

Use this structure for every game you analyze. It organizes data for quick reference and comparison:

```
GAME: [Team A] vs [Team B]
DATE: [Date] | TIME: [Time ET]
VENUE: [Stadium/Arena Name]

=== TEAM A ===
Record: [Overall W-L] (Home: [Home W-L], Away: [Away W-L])
Last 5: [L/W/W/L/W]
Key Stats:
  - [Stat 1: value]
  - [Stat 2: value]
  - [Stat 3: value]
Injuries:
  - [Player Name]: [Status - OUT/GTD/PROB]
  - [Player Name]: [Status - OUT/GTD/PROB]

=== TEAM B ===
Record: [Overall W-L] (Home: [Home W-L], Away: [Away W-L])
Last 5: [W/L/W/W/L]
Key Stats:
  - [Stat 1: value]
  - [Stat 2: value]
  - [Stat 3: value]
Injuries:
  - [Player Name]: [Status - OUT/GTD/PROB]
  - [Player Name]: [Status - OUT/GTD/PROB]

=== MARKET ===
Spread: [Team A] -X.X or [Team B] +X.X
Moneyline: [Team A] [-XXX] / [Team B] [+XXX]
Total: O/U [Number]
Kalshi YES: $X.XX | Kalshi NO: $X.XX
Public: [X]% on [Favorite]
Line Movement: [Movement description and direction]

=== CONTEXT ===
H2H Last 5: [Team A] X-X [Team B]
Schedule Notes: [B2B status, rest advantage, travel]
Weather: [Temperature, wind, precipitation if applicable]
Motivation: [Playoff seeding impact, rivalry, revenge spot]
Notable: [Any other context - back-up starter, trade deadline, etc.]
```

### NBA-Specific Game Card Example

```
GAME: Golden State Warriors vs Los Angeles Lakers
DATE: 2026-03-23 | TIME: 10:30 PM ET
VENUE: Crypto.com Arena (Los Angeles)

=== GOLDEN STATE WARRIORS ===
Record: 42-28 (Home: 19-15, Away: 23-13)
Last 5: W-W-L-W-W
Key Stats:
  - 3P%: 38.2% (League leader)
  - Pace: 98.5 possessions/game (Fastest)
  - Net Rating: +3.8 (Top 5)
Injuries:
  - Klay Thompson: PROB (shoulder)
  - Andrew Wiggins: PROB

=== LOS ANGELES LAKERS ===
Record: 37-33 (Home: 21-12, Away: 16-21)
Last 5: L-W-L-L-W
Key Stats:
  - Defensive Rating: 109.2 (Bottom 10)
  - Offensive Rating: 113.4 (Top 15)
  - Rebounding: 10.2 off boards per game
Injuries:
  - Anthony Davis: OUT (ankle)
  - None listed

=== MARKET ===
Spread: Warriors -2.5
Moneyline: Warriors -140 / Lakers +120
Total: O/U 223.5
Kalshi YES: $0.62 | Kalshi NO: $0.38
Public: 58% on Lakers (getting the points)
Line Movement: Opened Warriors -3, moved to -2.5 (money on Lakers)

=== CONTEXT ===
H2H Last 5: Warriors 3-2 Lakers
Schedule Notes: Lakers on 2nd night of B2B (played yesterday), Warriors on 3 days rest
Weather: N/A (Indoor)
Motivation: Both teams fighting for playoff position; no significant playoff seeding locked
Notable: AD out = Lakers significantly weakened defensively; Warriors historically strong on road
```

### MLB-Specific Game Card Example

```
GAME: New York Yankees vs Boston Red Sox
DATE: 2026-03-23 | TIME: 1:05 PM ET
VENUE: Fenway Park (Boston)

=== NEW YORK YANKEES ===
Record: 15-10 (Home: 8-5, Away: 7-5)
Last 5: W-W-L-W-L
Probable Pitcher: Gerrit Cole
  - 2026 ERA: 2.84, K/9: 10.2, BB/9: 1.8
Key Stats:
  - Team OPS: .762 (Above league average)
  - Team ERA: 3.21
  - Strikeout rate: 9.1 K/9
Injuries:
  - Juan Soto: PROB (rest day possible)

=== BOSTON RED SOX ===
Record: 12-13 (Home: 7-6, Away: 5-7)
Last 5: L-L-W-L-L
Probable Pitcher: Tanner Houck
  - 2026 ERA: 4.15, K/9: 8.7, BB/9: 2.9
Key Stats:
  - Team OPS: .698 (Below average)
  - Team ERA: 3.98
  - Strikeout rate: 8.3 K/9
Injuries:
  - Rafael Devers: OUT (hamstring)
  - Jarren Duran: GTD

=== MARKET ===
Spread: Yankees -1.5
Moneyline: Yankees -160 / Red Sox +135
Total: O/U 8.5
Kalshi YES (Yankees): $0.65 | NO: $0.35
Public: 64% on OVER (runs)
Line Movement: Opened at -1, moved to -1.5 (sharp money on Yankees)

=== CONTEXT ===
H2H Last 5: Yankees 3-2 Red Sox
Schedule Notes: Both teams off yesterday (no advantage)
Weather: 42°F, Wind 12 mph from right field (reduces home runs)
Motivation: Divisional rivalry (always intense)
Notable: Cole vs Houck is significant talent gap; Red Sox missing Devers impacts power; Cold weather lowers run totals
```

### Soccer-Specific Game Card Example

```
GAME: Manchester City vs Manchester United
DATE: 2026-03-23 | TIME: 3:00 PM ET (12:30 PM local)
VENUE: Etihad Stadium (Manchester)

=== MANCHESTER CITY ===
Record: 22W-3D-3L (69 points, 1st)
Last 5: W-W-D-W-W
Key Stats:
  - Goals For: 71, Goals Against: 20
  - xG: 64.8, xGA: 28.2 (Significant outplay)
  - Possession: 64.2% average
Injuries:
  - Ruben Dias: GTD (muscle)
  - None listed as OUT

=== MANCHESTER UNITED ===
Record: 17W-5D-6L (56 points, 5th)
Last 5: L-W-L-W-W
Key Stats:
  - Goals For: 52, Goals Against: 38
  - xG: 48.1, xGA: 45.3 (Competitive expected)
  - Possession: 52.1% average
Injuries:
  - Luke Shaw: OUT (hamstring)
  - Lisandro Martinez: OUT (muscle)

=== MARKET ===
Spread: City -2.0 goals
Moneyline: City -220 / United +500 / Draw +320
Total: O/U 3.5 goals
Kalshi (City to win): YES $0.72 | NO $0.28
Public: 71% backing City
Line Movement: Opened City -1.5, moved to -2.0 (professional money on City)

=== CONTEXT ===
H2H Last 5: City 3-1-1 United (City dominance continues)
Schedule Notes: City on 4 days rest, United on 3 days rest (no significant advantage)
Weather: 48°F, Overcast, No rain expected (good conditions)
Motivation: Manchester Derby always heated; City dominant this season; United fighting for top 4
Notable: City's expected goals significantly higher (outplay metric); United missing two key defenders (Shaw, Martinez); City unbeaten in 12 derbies
```

## Search Query Templates

Pre-built queries for efficiency. Copy and paste these, filling in brackets with specific teams/dates:

### Daily Setup (Run These First)

```
NBA games today [date] schedule odds
MLB games today [date] probable pitchers
Premier League fixtures this week [date]
La Liga fixtures this week [date]
```

### Per-Game Deep Dive

```
[Team] injury report [date]
[Team] recent form last 10 games
[Team A] vs [Team B] prediction stats
[Player] stats 2025-26 season
[Pitcher] 2026 stats game log
[Team] xG expected goals this season
```

### Market Research

```
Kalshi sports markets today
[Sport] odds movement today sharp money
[Game] public betting percentage
[Team] moneyline opening line movement
[Sport] consensus picks today
Action Network [Team] analysis
```

### Specific Sport Combinations

**NBA Slate Setup:**
```
NBA schedule today [date]
NBA odds today Kalshi
NBA injury report today
NBA starting lineups today
NBA games tonight public betting
```

**MLB Slate Setup:**
```
MLB schedule today [date]
MLB odds today
MLB probable pitchers today
MLB starting lineups today
[Team] injury report today
[City] weather forecast game time
```

**Soccer Slate Setup:**
```
Premier League fixtures [date] odds
La Liga fixtures [date] odds
Soccer injury news today
[Team] squad news injuries
[Team] last 5 results form
```

## Data Quality Rules

These rules ensure your analysis is built on solid ground:

**Rule 1: Always Check the Date**
- Stats must be from current season (2025-26 for most sports)
- Injury reports older than 24 hours should be refreshed
- Odds older than 2 hours should be re-searched
- Game results from previous day? Note which day when recording

**Rule 2: Cross-Reference Injuries**
- Check at least 2 independent sources
- Official team site is gold standard
- ESPN, Yahoo, The Athletic are solid second sources
- If sources conflict, flag it: "⚠️ Conflicting reports: Source A says OUT, Source B says PROB"

**Rule 3: Distinguish Injury Statuses**
- 🔴 OUT: Officially unavailable, confirmed by team
- 🟡 GTD (Game Time Decision): Will be determined day-of or at warm-ups
- 🟢 PROB: Probable to play, but not 100% certain
- If uncertain: Always note in output, "Status unclear from available sources"

**Rule 4: Flag Incomplete Data**
- If odds aren't available: "⚠️ Kalshi market not yet listed"
- If injury report missing: "⚠️ Could not verify [Team] starting lineup"
- If stats are week-old: "⚠️ Stats current as of [date], may be outdated"
- Missing data doesn't stop analysis but should be flagged

**Rule 5: Recent is Better Than Complete**
- A 30-minute-old search with current odds beats a comprehensive search from 2 hours ago
- Odds shift fast; market prices change continuously
- If analyzing multiple games, keep searches within a 1-hour window

## Output: Compiled Scouting Report

After gathering all data for a slate of games (single game or multiple games), compile final output in this format:

```
📋 SCOUTING REPORT — [Date]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Slate: [Sport] | Games Analyzed: [X]
Data Freshness: [HH:MM ET, timestamp of last search]
Confidence: [High/Medium/Low based on data completeness]
Missing Data: [List any data you couldn't find]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[Game Analysis Card 1]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[Game Analysis Card 2]

[... repeat for each game ...]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔍 KEY FINDINGS:
- [Notable injury that could swing a game]
- [Team on unusual rest/schedule situation]
- [Significant line movement detected]
- [Weather concern for outdoor game]
- [Statistical anomaly or red flag]
- [Public vs sharp money divergence]
- [Matchup-specific edge identified]

🎯 BETTING TARGETS:
- [Game]: [Potential edge identified]
- [Game]: [Potential edge identified]

⚠️ CAUTION FLAGS:
- [Uncertain data point that needs confirmation]
- [Public heavily favored (potential trap game)]
- [Line movement against public opinion]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

### Example Compiled Report

```
📋 SCOUTING REPORT — March 23, 2026
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Slate: NBA | Games Analyzed: 3
Data Freshness: 3:45 PM ET
Confidence: High (all data verified within 2 hours)
Missing Data: None

[Game Analysis Card 1: Warriors vs Lakers]
[Game Analysis Card 2: Celtics vs Heat]
[Game Analysis Card 3: 76ers vs Knicks]

🔍 KEY FINDINGS:
- Lakers missing Anthony Davis — 4-point net rating swing
- Warriors on 3-day rest vs Lakers on back-to-back (rest advantage +2.5%)
- Sharp money moving on Warriors (opened -3, now -2.5) but public on Lakers (58%)
- All three games have moving lines in same direction (professionals vs public divergence)

🎯 BETTING TARGETS:
- Warriors vs Lakers: Warriors -2.5 appears undervalued given rest advantage + AD out
- 76ers vs Knicks: Line movement to Sixers despite public on Knicks suggests sharp value

⚠️ CAUTION FLAGS:
- Celtics vs Heat: Heat injuries not fully confirmed, GTD status on two players
- Public heavily favored on Warriors — reverse this into sharp positioning

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Integration with Betting Workflow

**When to Use This Skill:**
- Before placing any Kalshi bet on sports markets
- When researching a new sport or team you're unfamiliar with
- When odds seem out of line with fundamentals
- When building a slate of multiple game analysis
- When you need to validate assumptions about a game

**What This Skill Produces:**
- Raw data cards ready for analysis
- Compiled scouting reports highlighting edges
- Flagged data gaps you need to fill yourself
- Structured information for risk assessment

**What You Do With the Output:**
- Compare market odds to probability assessments
- Identify sharp vs public divergence
- Assess whether edge justifies Kalshi position sizing
- Document your thesis for each bet

---

## Quick Reference: Sport-Specific Stats to Always Gather

### NBA
- Record, Net Rating, Offensive/Defensive Rating, 3P%, Pace, Last 5, Injury status

### MLB
- Record, ERA, Strikeout rate, Batting avg/OPS, Pitcher performance, Weather, Probable pitcher splits

### Soccer
- Record (Points), xG/xGA, Goals for/against, Possession %, Last 5, Injury status, Squad depth

---

**Last Updated:** 2026-03-23
**Skill Version:** 1.0
**Status:** Active for NBA, MLB, Soccer betting analysis
