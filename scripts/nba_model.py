#!/usr/bin/env python3
"""
NBA Prop Model - Phase 1: Matchup-Adjusted Player Prop Projections
=====================================================================
Prediction Market War Machine

REAL edge generation model for NBA player props.
Unlike the old _bayesian_estimate() which was self-referential (Kalshi prices only),
this model uses actual basketball data:

  1. Season average (baseline)
  2. Recent form weighted average (momentum)
  3. Matchup adjustment (opponent defense vs position)
  4. Home/Away adjustment
  5. Back-to-back fatigue adjustment
  6. Minutes projection adjustment
  7. Pace adjustment

Output: P(X > line) using normal distribution CDF

Supported props:
  - Points (over/under)
  - Rebounds (over/under)
  - Assists (over/under)

Usage:
    from nba_model import NBAModel

    model = NBAModel()
    result = model.get_prob("LeBron James", "points", 25.5, "LAC", is_home=True)
    # result = {"model_prob": 0.62, "confidence": "high", "projected": 27.3, ...}

    # CLI
    python scripts/nba_model.py --player "LeBron James" --prop points --line 25.5 --opponent LAC --home
"""

import sys
import math
import json
import logging
import argparse
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_connection, put_connection

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# Constants & Weights
# ============================================================================

# How much to weight season vs recent form
# v2: Shifted weight toward recent form. Even if season avg is high,
# a cold last-5 streak drags projection down — markets react to recency.
SEASON_WEIGHT = 0.20           # 20% season average (was 35%)
RECENT_10_WEIGHT = 0.30        # 30% recent 10-game form (was 50%)
RECENT_5_WEIGHT = 0.35         # 35% recent 5-game form (NEW — highest weight)
HOME_AWAY_WEIGHT = 0.15        # 15% home/away split (unchanged)

# Matchup adjustment caps (prevent extreme swings)
MAX_MATCHUP_ADJUSTMENT = 0.20  # ±20% max from matchup
MAX_B2B_PENALTY = 0.08         # 8% reduction on back-to-back
PACE_ADJUSTMENT_FACTOR = 0.5   # How much pace affects projection

# Blowout adjustment: when pre-game spread is large (≥10),
# starters get benched early in Q4 → reduced counting stats
BLOWOUT_SPREAD_THRESHOLD = 10.0   # points
BLOWOUT_REDUCTION_BASE = 0.07     # 7% reduction for starters in blowout-likely games
BLOWOUT_REDUCTION_PER_PT = 0.005  # additional 0.5% per point of spread beyond threshold

# Confidence thresholds
HIGH_CONFIDENCE_MIN_GAMES = 30
MEDIUM_CONFIDENCE_MIN_GAMES = 15
MIN_GAMES_FOR_MODEL = 5

# Standard deviation floor (prevent unrealistic tightness)
MIN_STD_DEV_POINTS = 4.0
MIN_STD_DEV_REBOUNDS = 2.0
MIN_STD_DEV_ASSISTS = 1.5

# League average points allowed per game (for normalization)
LEAGUE_AVG_OPP_PTS = 114.0
LEAGUE_AVG_OPP_REB = 44.0
LEAGUE_AVG_OPP_AST = 26.0
LEAGUE_AVG_PACE = 100.0

# Platt scaling parameters — v2 refit on 2,308 samples (2026-03-28 to 2026-04-14)
# Applies sigmoid recalibration: P_cal = sigmoid(PLATT_A * logit(P_raw) + PLATT_B)
#
# v2 vs v1: Applied ALWAYS (not gated by edge threshold).
#   v1 (A=0.8976, B=-0.4242, edge>20% gate): ECE=0.0728 on 2308 samples
#   v2 (A=0.6759, B=-0.3489, always apply):  ECE=0.0307 on same 2308 samples
#
# See docs/CALIBRATION_v2_2026-04-17.md for refit methodology and validation.
PLATT_A = 0.6759
PLATT_B = -0.3489
PLATT_EDGE_THRESHOLD = 0.0  # apply ALWAYS — refit is well-calibrated across full range

# Edge shrinkage: when model-vs-market divergence is large (≥20%),
# the model is more likely wrong than the market. Shrink model_prob
# toward market price by EDGE_SHRINK_FACTOR to reduce overconfidence.
EDGE_SHRINK_THRESHOLD = 0.20  # |model_prob - kalshi_price| threshold
EDGE_SHRINK_FACTOR = 0.05    # 5% shrink toward market price


# ============================================================================
# Normal Distribution CDF (no scipy dependency)
# ============================================================================

def _norm_cdf(x: float) -> float:
    """Standard normal CDF using error function approximation."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def platt_calibrate(raw_prob: float) -> float:
    """Apply Platt sigmoid recalibration to raw model probability."""
    p_c = max(0.001, min(0.999, raw_prob))
    logit = math.log(p_c / (1.0 - p_c))
    z = PLATT_A * logit + PLATT_B
    if z >= 0:
        sig = 1.0 / (1.0 + math.exp(-z))
    else:
        ez = math.exp(z)
        sig = ez / (1.0 + ez)
    return max(0.02, min(0.98, sig))


def prob_over(projected: float, std_dev: float, line: float) -> float:
    """
    P(X > line) assuming normal distribution.

    Args:
        projected: expected value (mean)
        std_dev: standard deviation
        line: the over/under line

    Returns:
        Probability between 0 and 1.
    """
    if std_dev <= 0:
        return 1.0 if projected > line else 0.0

    # P(X > line) = 1 - Φ((line - projected) / σ)
    z = (line - projected) / std_dev
    return 1.0 - _norm_cdf(z)


# ============================================================================
# Model Result
# ============================================================================

@dataclass
class PropPrediction:
    """Result of a prop prediction."""
    player_name: str
    prop_type: str              # points, rebounds, assists
    line: float
    opponent: str
    is_home: bool

    projected_value: float      # our projected stat line
    std_dev: float              # uncertainty (std dev)
    model_prob: float           # P(X > line) — raw model probability
    platt_prob: float           # Platt-calibrated probability
    confidence: str             # high, medium, low

    # Breakdown
    season_component: float     # season avg contribution
    recent_component: float     # recent form contribution
    ha_component: float         # home/away contribution
    matchup_adjustment: float   # % adjustment from matchup
    b2b_adjustment: float       # % adjustment from B2B
    pace_adjustment: float      # % adjustment from pace
    blowout_adjustment: float   # % adjustment from blowout (spread ≥10)
    injury_status: str          # injury report status

    reasoning: str              # human-readable explanation
    data_quality: dict          # metadata about inputs

    def effective_prob(self, kalshi_price: float = 0.5) -> float:
        """
        Returns the probability to use for trading decisions.

        v2 Pipeline (2026-04-17 refit):
          1. Always use Platt-calibrated prob (v2 constants fit on 2308 samples,
             ECE=0.0307 across full probability range — no need for edge gate).
          2. Apply edge shrinkage: pull 5% toward market when divergence ≥20%.
             → Bayesian hedge: when model diverges heavily from market,
               there's usually information the model doesn't have.

        NOTE: self.platt_prob is already computed using v2 constants at
        prediction time (see _project method line ~485). We use it directly.
        """
        # Step 1: Always use Platt (v2 constants are well-calibrated across range)
        base_prob = self.platt_prob

        # Step 2: Edge shrinkage — pull toward market when divergence is large
        effective_edge = abs(base_prob - kalshi_price)
        if effective_edge >= EDGE_SHRINK_THRESHOLD:
            # Shrink 5% toward market price
            # e.g., model=0.75, market=0.50 → 0.75 + 0.05*(0.50-0.75) = 0.7375
            base_prob = base_prob + EDGE_SHRINK_FACTOR * (kalshi_price - base_prob)

        return max(0.02, min(0.98, base_prob))


# ============================================================================
# NBA Prop Model
# ============================================================================

class NBAModel:
    """
    Matchup-adjusted NBA player prop model.

    Unlike the old Bayesian estimator that only used Kalshi prices,
    this model generates probabilities from REAL basketball data.
    """

    def __init__(self):
        self.conn = get_connection(dict_cursor=True)
        self._season = self._get_current_season()

    def _get_current_season(self) -> str:
        today = datetime.now(ZoneInfo("US/Eastern")).date()
        if today.month >= 10:
            return f"{today.year}-{str(today.year + 1)[-2:]}"
        else:
            return f"{today.year - 1}-{str(today.year)[-2:]}"

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cur = self.conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    # ----------------------------------------------------------------
    # Main Interface
    # ----------------------------------------------------------------

    def get_prob(
        self,
        player_name: str,
        prop_type: str,
        line: float,
        opponent: str,
        is_home: bool = True,
        game_date: str = None,
        spread: float = 0.0,
    ) -> Optional[PropPrediction]:
        """
        Generate P(X > line) for a player prop.

        Args:
            player_name: Full player name (e.g. "LeBron James")
            prop_type: "points", "rebounds", or "assists"
            line: The over/under line (e.g. 25.5)
            opponent: Team abbreviation (e.g. "LAC") or team name
            is_home: True if player is at home
            game_date: Optional date string (default: today)
            spread: Pre-game point spread (positive = player's team favored).
                     If abs(spread) >= 10, blowout adjustment kicks in.

        Returns:
            PropPrediction with model_prob, or None if insufficient data.
        """
        prop_type = prop_type.lower().strip()
        if prop_type not in ("points", "rebounds", "assists"):
            logger.error(f"[NBAModel] Unsupported prop type: {prop_type}")
            return None

        # 1. Get player data
        player = self._get_player(player_name)
        if not player:
            logger.warning(f"[NBAModel] Player not found: {player_name}")
            return None

        # 2. Get opponent team data
        opp_team = self._get_team(opponent)
        # opp_team can be None — we'll skip matchup adjustment

        # 3. Check injury status
        injury_status = self._get_injury_status(player_name)

        # 4. Check B2B
        team_abbr = player["team_abbr"]
        b2b = self._is_b2b(team_abbr, game_date)

        # 5. Calculate projection
        return self._project(
            player, opp_team, prop_type, line, is_home, b2b,
            injury_status, spread=spread,
        )

    # ----------------------------------------------------------------
    # Projection Engine
    # ----------------------------------------------------------------

    def _project(
        self,
        player: dict,
        opp_team: Optional[dict],
        prop_type: str,
        line: float,
        is_home: bool,
        is_b2b: bool,
        injury_status: str,
        spread: float = 0.0,
    ) -> Optional[PropPrediction]:
        """Core projection logic."""

        # Map prop type to column prefixes
        # recent_col = last 10 games, recent5_col = last 5 games
        stat_map = {
            "points": ("pts", "points_pg", "recent_pts_avg", "recent_pts_std",
                        "recent5_pts_avg",
                        "home_pts_avg", "away_pts_avg", "opp_pts_pg",
                        MIN_STD_DEV_POINTS, LEAGUE_AVG_OPP_PTS),
            "rebounds": ("reb", "rebounds_pg", "recent_reb_avg", "recent_reb_std",
                          "recent5_reb_avg",
                          "home_reb_avg", "away_reb_avg", "opp_reb_pg",
                          MIN_STD_DEV_REBOUNDS, LEAGUE_AVG_OPP_REB),
            "assists": ("ast", "assists_pg", "recent_ast_avg", "recent_ast_std",
                         "recent5_ast_avg",
                         "home_ast_avg", "away_ast_avg", "opp_ast_pg",
                         MIN_STD_DEV_ASSISTS, LEAGUE_AVG_OPP_AST),
        }

        (prefix, season_col, recent_col, std_col, recent5_col,
         home_col, away_col, opp_col,
         min_std, league_avg_opp) = stat_map[prop_type]

        # ------ Component 1: Season Average ------
        season_avg = float(player.get(season_col, 0))
        games_played = int(player.get("games_played", 0))

        if games_played < MIN_GAMES_FOR_MODEL or season_avg <= 0:
            logger.debug(f"[NBAModel] Insufficient data for {player['player_name']}: {games_played} GP")
            return None

        # ------ Component 2: Recent Form (10-game + 5-game) ------
        recent_avg = float(player.get(recent_col, 0))
        recent_std = float(player.get(std_col, 0))
        recent5_avg = float(player.get(recent5_col, 0))

        # Fallback chain: recent5 → recent10 → season
        if recent_avg <= 0:
            recent_avg = season_avg
            recent_std = season_avg * 0.25  # assume 25% CV
        if recent5_avg <= 0:
            recent5_avg = recent_avg  # fall back to 10-game if 5-game unavailable

        # ------ Component 3: Home/Away Split ------
        if is_home:
            ha_avg = float(player.get(home_col, 0))
        else:
            ha_avg = float(player.get(away_col, 0))

        # Fall back to season if no split data
        if ha_avg <= 0:
            ha_avg = season_avg

        # ------ Weighted Base Projection ------
        # v2: Season 20% + Recent10 30% + Recent5 35% + H/A 15%
        # This ensures a hot/cold last-5 streak outweighs a season average
        base_projection = (
            SEASON_WEIGHT * season_avg
            + RECENT_10_WEIGHT * recent_avg
            + RECENT_5_WEIGHT * recent5_avg
            + HOME_AWAY_WEIGHT * ha_avg
        )

        # ------ Matchup Adjustment ------
        matchup_adj = 0.0
        if opp_team:
            opp_allowed = float(opp_team.get(opp_col, 0))
            if opp_allowed > 0 and league_avg_opp > 0:
                # How much does this team allow vs league average?
                # e.g., if team allows 120 pts/g and league avg is 114,
                #        ratio = 120/114 = 1.053 → +5.3% boost
                opp_ratio = opp_allowed / league_avg_opp
                matchup_adj = opp_ratio - 1.0

                # Cap the adjustment
                matchup_adj = max(-MAX_MATCHUP_ADJUSTMENT,
                                  min(MAX_MATCHUP_ADJUSTMENT, matchup_adj))

        # ------ Back-to-Back Penalty ------
        b2b_adj = 0.0
        if is_b2b:
            b2b_adj = -MAX_B2B_PENALTY  # reduce projection by 8%

        # ------ Pace Adjustment ------
        pace_adj = 0.0
        if opp_team:
            opp_pace = float(opp_team.get("pace", 0))
            if opp_pace > 0 and LEAGUE_AVG_PACE > 0:
                pace_ratio = opp_pace / LEAGUE_AVG_PACE
                pace_adj = (pace_ratio - 1.0) * PACE_ADJUSTMENT_FACTOR

        # ------ Blowout Adjustment ------
        # When spread is large, starters get benched in Q4 garbage time
        # → counting stats (pts/reb/ast) are systematically lower
        blowout_adj = 0.0
        abs_spread = abs(spread)
        if abs_spread >= BLOWOUT_SPREAD_THRESHOLD:
            # Base 7% reduction + 0.5% per point beyond threshold
            blowout_adj = -(
                BLOWOUT_REDUCTION_BASE
                + BLOWOUT_REDUCTION_PER_PT * (abs_spread - BLOWOUT_SPREAD_THRESHOLD)
            )
            # Cap at -15% max reduction
            blowout_adj = max(blowout_adj, -0.15)

        # ------ Final Projection ------
        total_adjustment = 1.0 + matchup_adj + b2b_adj + pace_adj + blowout_adj
        projected = base_projection * total_adjustment

        # ------ Standard Deviation ------
        # Use recent game std dev, with a floor
        std_dev = max(recent_std, min_std)

        # Increase uncertainty for B2B (more variance in fatigue games)
        if is_b2b:
            std_dev *= 1.15

        # Increase uncertainty if limited data
        if games_played < 20:
            std_dev *= 1.10

        # ------ Probability Calculation ------
        model_prob = prob_over(projected, std_dev, line)

        # Clamp to avoid extreme probabilities
        model_prob = max(0.02, min(0.98, model_prob))

        # ------ Confidence Assessment ------
        confidence = self._assess_confidence(player, opp_team, is_b2b, injury_status)

        # ------ Data Quality ------
        data_quality = {
            "games_played": games_played,
            "has_recent_form": recent_avg > 0 and recent_avg != season_avg,
            "has_matchup_data": opp_team is not None,
            "has_home_away_split": ha_avg != season_avg,
            "is_b2b": is_b2b,
            "injury_status": injury_status or "healthy",
            "season": self._season,
        }

        # ------ Reasoning ------
        reasoning_parts = [
            f"Season={season_avg:.1f}(w{SEASON_WEIGHT})",
            f"R10={recent_avg:.1f}(w{RECENT_10_WEIGHT})",
            f"R5={recent5_avg:.1f}(w{RECENT_5_WEIGHT})",
            f"H/A={'Home' if is_home else 'Away'}={ha_avg:.1f}",
            f"Base={base_projection:.1f}",
        ]
        if matchup_adj != 0:
            reasoning_parts.append(
                f"Matchup={'+'if matchup_adj>0 else ''}{matchup_adj*100:.1f}%"
            )
        if b2b_adj != 0:
            reasoning_parts.append(f"B2B={b2b_adj*100:.0f}%")
        if pace_adj != 0:
            reasoning_parts.append(
                f"Pace={'+'if pace_adj>0 else ''}{pace_adj*100:.1f}%"
            )
        if blowout_adj != 0:
            reasoning_parts.append(
                f"Blowout(spd={abs_spread:.0f})={blowout_adj*100:.1f}%"
            )
        reasoning_parts.append(f"Proj={projected:.1f}±{std_dev:.1f}")
        reasoning_parts.append(f"P(>{line})={model_prob:.2%}")

        if injury_status:
            reasoning_parts.append(f"INJURY: {injury_status}")

        # Platt calibrated probability
        platt_prob_val = round(platt_calibrate(model_prob), 4)

        return PropPrediction(
            player_name=player["player_name"],
            prop_type=prop_type,
            line=line,
            opponent=player.get("team_abbr", ""),
            is_home=is_home,
            projected_value=round(projected, 2),
            std_dev=round(std_dev, 2),
            model_prob=round(model_prob, 4),
            platt_prob=platt_prob_val,
            confidence=confidence,
            season_component=round(season_avg, 2),
            recent_component=round(recent_avg, 2),
            ha_component=round(ha_avg, 2),
            matchup_adjustment=round(matchup_adj, 4),
            b2b_adjustment=round(b2b_adj, 4),
            pace_adjustment=round(pace_adj, 4),
            blowout_adjustment=round(blowout_adj, 4),
            injury_status=injury_status or "healthy",
            reasoning=" | ".join(reasoning_parts),
            data_quality=data_quality,
        )

    # ----------------------------------------------------------------
    # Confidence Assessment
    # ----------------------------------------------------------------

    def _assess_confidence(
        self,
        player: dict,
        opp_team: Optional[dict],
        is_b2b: bool,
        injury_status: str,
    ) -> str:
        """
        Assess confidence level based on data quality.

        high: 30+ GP, has recent form + matchup data, no injury concerns
        medium: 15+ GP, has some data
        low: limited data, injured, or missing matchup info
        """
        gp = int(player.get("games_played", 0))
        has_recent = float(player.get("recent_pts_avg", 0)) > 0
        has_matchup = opp_team is not None

        # Immediate downgrades
        if injury_status and any(s in injury_status.lower() for s in ["out", "doubtful"]):
            return "low"  # injured players = unreliable

        if gp >= HIGH_CONFIDENCE_MIN_GAMES and has_recent and has_matchup:
            return "high"
        elif gp >= MEDIUM_CONFIDENCE_MIN_GAMES and (has_recent or has_matchup):
            return "medium"
        else:
            return "low"

    # ----------------------------------------------------------------
    # Data Lookups
    # ----------------------------------------------------------------

    def _get_player(self, name: str) -> Optional[dict]:
        """Find player in database."""
        # Exact match
        row = self._query_one(
            "SELECT * FROM nba_players WHERE player_name = %s AND season = %s",
            (name, self._season)
        )
        if row:
            return row

        # Fuzzy match
        row = self._query_one(
            "SELECT * FROM nba_players WHERE player_name LIKE %s AND season = %s LIMIT 1",
            (f"%{name}%", self._season)
        )
        return row

    def _get_team(self, team_input: str) -> Optional[dict]:
        """Find team in database."""
        from nba_data_collector import TEAM_NAME_TO_ABBR

        abbr = team_input.upper().strip()

        # Direct abbreviation
        if len(abbr) <= 3:
            row = self._query_one(
                "SELECT * FROM nba_teams WHERE team_abbr = %s AND season = %s",
                (abbr, self._season)
            )
            if row:
                return row

        # Name lookup
        lookup = TEAM_NAME_TO_ABBR.get(team_input.lower().strip(), "")
        if lookup:
            return self._query_one(
                "SELECT * FROM nba_teams WHERE team_abbr = %s AND season = %s",
                (lookup, self._season)
            )

        # Fuzzy
        return self._query_one(
            "SELECT * FROM nba_teams WHERE team_name LIKE %s AND season = %s",
            (f"%{team_input}%", self._season)
        )

    def _get_injury_status(self, player_name: str) -> str:
        """Get injury status for player."""
        cur = self.conn.cursor()
        cur.execute(
            "SELECT status, injury_detail FROM nba_injuries WHERE player_name LIKE %s",
            (f"%{player_name}%",)
        )
        row = cur.fetchone()
        if row:
            return f"{row['status']} ({row['injury_detail']})" if row["injury_detail"] else str(row["status"])
        return ""

    def _is_b2b(self, team_abbr: str, game_date: str = None) -> bool:
        """Check if team is on back-to-back."""
        if game_date is None:
            game_date = datetime.now(ZoneInfo("US/Eastern")).date().isoformat()

        cur = self.conn.cursor()
        cur.execute("""
            SELECT is_back_to_back_home, is_back_to_back_away,
                   home_team_abbr
            FROM nba_games
            WHERE game_date = %s AND (home_team_abbr = %s OR away_team_abbr = %s)
        """, (game_date, team_abbr, team_abbr))
        row = cur.fetchone()

        if not row:
            return False
        return bool(row["is_back_to_back_home"]) if row["home_team_abbr"] == team_abbr else bool(row["is_back_to_back_away"])

    # ----------------------------------------------------------------
    # Batch Processing (for signal_engine integration)
    # ----------------------------------------------------------------

    def process_kalshi_nba_ticker(self, ticker: str, title: str,
                                   market_price: float) -> Optional[PropPrediction]:
        """
        Parse a Kalshi NBA player prop ticker/title and generate a prediction.

        Kalshi NBA tickers typically look like:
          KXNBA-LEBRON-JAMES-PTS-25-5-2026-03-28
          or title: "LeBron James 25+ Points"

        Args:
            ticker: Kalshi market ticker
            title: Market title
            market_price: Current YES price (0-1)

        Returns:
            PropPrediction or None
        """
        parsed = self._parse_nba_ticker(ticker, title)
        if not parsed:
            return None

        player_name, prop_type, line, opponent, is_home = parsed

        return self.get_prob(
            player_name=player_name,
            prop_type=prop_type,
            line=line,
            opponent=opponent or "",
            is_home=is_home,
        )

    # ================================================================
    # Kalshi NBA Ticker Format (verified from learning_log.jsonl):
    #
    #   KXNBA{PROP}-{YY}{MON}{DD}{AWAY}{HOME}-{TEAM}{INIT}{LAST}{JERSEY}-{LINE}
    #
    # Examples:
    #   KXNBAPTS-26MAR25BKNGSW-GSWKPORZINGIS7-15     → GSW Porzingis 15+ pts
    #   KXNBAREB-26MAR25CHIPHI-CHIJSMITH25-4          → CHI J.Smith 4+ reb
    #   KXNBAAST-26MAR27CHIOKC-CHIJGIDDEY3-6          → CHI Giddey 6+ ast
    #   KXNBA3PT-26MAR25TORLAC-TORSBARNES4-4          → TOR Barnes 4+ 3pt
    #
    # Game segment: {YY}{MON}{DD}{AWAY_ABBR}{HOME_ABBR}
    #   26MAR25BKNGSW → 2026-Mar-25, BKN @ GSW
    #
    # Player segment: {TEAM_ABBR}{FIRST_INITIAL}{LASTNAME}{JERSEY}
    #   GSWKPORZINGIS7 → GSW, K(ristaps) Porzingis, #7
    #
    # Line segment: integer (the "X+" line)
    #   15 → 15+ (i.e., over 14.5)
    # ================================================================

    TICKER_PROP_MAP = {
        "KXNBAPTS": "points",
        "KXNBAREB": "rebounds",
        "KXNBAAST": "assists",
        "KXNBA3PT": "threes",      # not modeled yet
        "KXNBASTL": "steals",      # not modeled yet
        "KXNBABLK": "blocks",      # not modeled yet
        "KXNBATOV": "turnovers",   # not modeled yet
    }

    SUPPORTED_PROPS = {"points", "rebounds", "assists"}

    # 3-letter team abbreviations used in tickers
    TEAM_ABBRS = {
        "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN",
        "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA",
        "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX",
        "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
    }

    def _parse_nba_ticker(self, ticker: str, title: str) -> Optional[Tuple]:
        """
        Parse Kalshi NBA prop ticker into components using verified format.

        Ticker format: KXNBA{PROP}-{YY}{MON}{DD}{AWAY}{HOME}-{TEAM}{INIT}{LAST}{JERSEY}-{LINE}

        Returns: (player_name, prop_type, line, opponent_abbr, is_home) or None
        """
        import re

        ticker_upper = ticker.upper() if ticker else ""
        title_lower = title.lower() if title else ""

        # ---- Step 1: Prop type from ticker prefix ----
        prop_type = None
        for prefix, ptype in self.TICKER_PROP_MAP.items():
            if ticker_upper.startswith(prefix):
                prop_type = ptype
                break

        if not prop_type:
            # Fallback: title keywords
            if any(kw in title_lower for kw in ["point", "pts"]):
                prop_type = "points"
            elif any(kw in title_lower for kw in ["rebound", "reb"]):
                prop_type = "rebounds"
            elif any(kw in title_lower for kw in ["assist", "ast"]):
                prop_type = "assists"

        if not prop_type or prop_type not in self.SUPPORTED_PROPS:
            return None

        # ---- Step 2: Parse ticker structure ----
        parts = ticker_upper.split("-")
        # Expected: [PREFIX, GAME_SEGMENT, PLAYER_SEGMENT, LINE]
        # e.g. ["KXNBAPTS", "26MAR25BKNGSW", "GSWKPORZINGIS7", "15"]

        line = None
        player_team = ""
        away_abbr = ""
        home_abbr = ""
        player_last_name = ""

        if len(parts) >= 4:
            game_seg = parts[1]    # 26MAR25BKNGSW
            player_seg = parts[2]  # GSWKPORZINGIS7
            line_seg = parts[3]    # 15

            # Extract line
            try:
                line = float(line_seg)
                # Kalshi uses integer lines meaning "X+" → over (X - 0.5)
                line = line - 0.5
            except ValueError:
                pass

            # Parse game segment: {YY}{MON}{DD}{AWAY3}{HOME3}
            # Pattern: 2 digits + 3 alpha + 2 digits + 3 alpha + 3 alpha
            game_match = re.match(
                r'(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})',
                game_seg
            )
            if game_match:
                away_abbr = game_match.group(4)
                home_abbr = game_match.group(5)

            # Parse player segment: {TEAM3}{FIRST_INITIAL}{LASTNAME}{JERSEY}
            # Pattern: 3 alpha (team) + 1 alpha (initial) + alpha (name) + digits (jersey)
            player_match = re.match(
                r'([A-Z]{3})([A-Z])([A-Z]+?)(\d+)$',
                player_seg
            )
            if player_match:
                player_team = player_match.group(1)
                first_initial = player_match.group(2)
                player_last_name = player_match.group(3).capitalize()

        # ---- Step 3: Determine line from title if ticker parse failed ----
        if line is None:
            # Try title: "15+ points", "Over 14.5", etc.
            line_match = re.search(
                r'(\d+\.?\d*)\+?\s*(?:point|pts|rebound|reb|assist|ast)',
                title_lower
            )
            if not line_match:
                line_match = re.search(r'(?:over|under)\s*(\d+\.?\d*)', title_lower)
            if not line_match:
                line_match = re.search(r'(\d+\.5)', title_lower)
            if line_match:
                line = float(line_match.group(1))
                if "+" in title and line == int(line):
                    line = line - 0.5
            else:
                return None

        # ---- Step 4: Find player in DB ----
        # Primary: match by last name from ticker
        player_name = None
        if player_last_name:
            player_name = self._find_player_by_ticker_info(
                player_last_name, player_team
            )
        # Fallback: match from title
        if not player_name:
            player_name = self._extract_player_name(title)
        if not player_name:
            return None

        # ---- Step 5: Determine opponent and home/away ----
        if player_team and away_abbr and home_abbr:
            if player_team == home_abbr:
                opponent = away_abbr
                is_home = True
            elif player_team == away_abbr:
                opponent = home_abbr
                is_home = False
            else:
                # Player team doesn't match either side (shouldn't happen)
                opponent = home_abbr if player_team != home_abbr else away_abbr
                is_home = player_team == home_abbr
        else:
            opponent = self._extract_opponent(title, ticker)
            is_home = " vs " in title_lower or " vs. " in title_lower

        return (player_name, prop_type, line, opponent, is_home)

    def _find_player_by_ticker_info(
        self, last_name: str, team_abbr: str
    ) -> Optional[str]:
        """
        Find player in DB by last name and team from ticker.

        Ticker gives us: team abbreviation + first initial + last name
        e.g. GSWKPORZINGIS7 → team=GSW, initial=K, last=Porzingis
        """
        # Exact team + last name match
        cur = self.conn.cursor()
        cur.execute(
            """SELECT player_name FROM nba_players
               WHERE team_abbr = %s AND player_name LIKE %s
               AND season = %s AND games_played >= 5
               LIMIT 1""",
            (team_abbr, f"% {last_name}%", self._season)
        )
        row = cur.fetchone()
        if row:
            return row["player_name"]

        # Try without team (player might have been traded)
        cur.execute(
            """SELECT player_name FROM nba_players
               WHERE player_name LIKE %s
               AND season = %s AND games_played >= 5
               LIMIT 1""",
            (f"% {last_name}%", self._season)
        )
        row = cur.fetchone()
        if row:
            return row["player_name"]

        # Case-insensitive broader search
        cur.execute(
            """SELECT player_name FROM nba_players
               WHERE LOWER(player_name) LIKE LOWER(%s)
               AND season = %s AND games_played >= 5
               LIMIT 1""",
            (f"%{last_name}%", self._season)
        )
        row = cur.fetchone()
        return row["player_name"] if row else None

    def _extract_player_name(self, title: str) -> Optional[str]:
        """Extract player name from market title by matching against DB."""
        if not title:
            return None

        # Try to match known players
        # Get all active player names
        cur = self.conn.cursor()
        cur.execute(
            "SELECT player_name FROM nba_players WHERE season = %s AND games_played >= 10",
            (self._season,)
        )
        players = cur.fetchall()

        title_lower = title.lower()
        best_match = None
        best_len = 0

        for row in players:
            name = row["player_name"]
            if name.lower() in title_lower and len(name) > best_len:
                best_match = name
                best_len = len(name)

        return best_match

    def _extract_opponent(self, title: str, ticker: str) -> str:
        """Try to extract opponent team from title or ticker."""
        from nba_data_collector import TEAM_NAME_TO_ABBR

        title_lower = (title or "").lower()
        # Common patterns: "vs LAC", "@ BOS", "against the Lakers"
        import re

        # Pattern: "vs. LAC" or "vs LAC" or "@ BOS"
        match = re.search(r'(?:vs\.?\s*|@\s*|against\s+(?:the\s+)?)(\w+)', title_lower)
        if match:
            team_str = match.group(1)
            abbr = TEAM_NAME_TO_ABBR.get(team_str, "")
            if abbr:
                return abbr
            if team_str.upper() in [v for v in TEAM_NAME_TO_ABBR.values()]:
                return team_str.upper()

        # Try ticker parsing: KXNBA-... might have team info
        parts = ticker.upper().split("-") if ticker else []
        for part in parts:
            if part in [v for v in TEAM_NAME_TO_ABBR.values()] and len(part) == 3:
                return part

        return ""

    def close(self):
        put_connection(self.conn)
        self.conn = None


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="NBA Prop Model - Matchup-Adjusted Projections"
    )
    parser.add_argument("--player", type=str, required=True, help="Player name")
    parser.add_argument("--prop", type=str, required=True,
                        choices=["points", "rebounds", "assists"], help="Prop type")
    parser.add_argument("--line", type=float, required=True, help="Over/under line")
    parser.add_argument("--opponent", type=str, default="", help="Opponent team")
    parser.add_argument("--home", action="store_true", help="Player is at home")
    parser.add_argument("--away", action="store_true", help="Player is away")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    model = NBAModel()
    is_home = not args.away  # default to home unless --away

    result = model.get_prob(
        player_name=args.player,
        prop_type=args.prop,
        line=args.line,
        opponent=args.opponent,
        is_home=is_home,
    )

    if not result:
        print(f"ERROR: Could not generate prediction for {args.player}")
        sys.exit(1)

    if args.json:
        print(json.dumps(asdict(result), indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"  {result.player_name} — {result.prop_type.upper()} O/U {result.line}")
        print(f"{'='*60}")
        print(f"  Projected:  {result.projected_value:.1f} ± {result.std_dev:.1f}")
        print(f"  Model P:    {result.model_prob:.2%}")
        print(f"  Platt P:    {result.platt_prob:.2%}")
        print(f"  Confidence: {result.confidence}")
        print(f"  Reasoning:  {result.reasoning}")
        print(f"{'='*60}\n")

    model.close()


if __name__ == "__main__":
    main()