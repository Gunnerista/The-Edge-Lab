#!/usr/bin/env python3
"""
MLB Strikeout Feature Engineering
====================================
War Machine MLB

Builds feature vectors for pitcher strikeout over/under prediction.
All rolling calculations use only pre-game data (no leakage).

Usage:
    python mlb/scripts/mlb_features.py                # build and inspect dataset
    python mlb/scripts/mlb_features.py --save          # save to CSV
"""

import sys
import os
import io
import math
import logging
import argparse
import numpy as np
import pandas as pd
from datetime import date

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

from db import get_connection, put_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_pitcher_gamelogs(seasons):
    """Load pitcher gamelogs for given seasons from DB."""
    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()
    placeholders = ','.join(['%s'] * len(seasons))
    cur.execute(f"""
        SELECT player_name, bbref_id, date, team, opponent, home_away,
               innings_pitched, strikeouts, walks, hits_allowed,
               pitch_count, game_score
        FROM mlb_pitcher_gamelogs
        WHERE EXTRACT(YEAR FROM date) IN ({placeholders})
        ORDER BY bbref_id, date
    """, seasons)
    rows = cur.fetchall()
    put_connection(conn)
    return pd.DataFrame(rows)


def _load_league_k_rate(seasons):
    """Compute league-average K rate per season from batter gamelogs."""
    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()
    placeholders = ','.join(['%s'] * len(seasons))
    cur.execute(f"""
        SELECT EXTRACT(YEAR FROM date)::int as season,
               SUM(strikeouts) as total_k,
               SUM(at_bats) as total_ab
        FROM mlb_batter_gamelogs
        WHERE EXTRACT(YEAR FROM date) IN ({placeholders}) AND team != ''
        GROUP BY 1
    """, seasons)
    rows = cur.fetchall()
    put_connection(conn)
    result = {}
    for r in rows:
        if r['total_ab'] and r['total_ab'] > 0:
            result[r['season']] = r['total_k'] / r['total_ab']
    return result


def _rolling_stats(group, idx, n):
    """Compute rolling stats from rows [max(0, idx-n) : idx] (exclusive of idx)."""
    start = max(0, idx - n)
    window = group.iloc[start:idx]
    if len(window) == 0:
        return None
    return window


def _compute_features_for_row(group, idx, league_k_rates, line):
    """Compute feature dict for a single pitcher-game row."""
    row = group.iloc[idx]
    game_date = row['date']
    season = game_date.year

    # All prior games this season (before this game)
    season_mask = (group['date'].dt.year == season) if hasattr(group['date'], 'dt') else (group['date'].apply(lambda d: d.year) == season)
    prior = group.iloc[:idx]
    prior_season = prior[prior['date'].apply(lambda d: d.year) == season] if len(prior) > 0 else prior

    # Also get prior season data for fallback
    prev_season = prior[prior['date'].apply(lambda d: d.year) == season - 1] if len(prior) > 0 else pd.DataFrame()

    # Use season data if enough, else fallback to prev season
    if len(prior_season) < 3 and len(prev_season) >= 3:
        base = pd.concat([prev_season, prior_season])
    else:
        base = prior_season

    if len(base) == 0:
        return None

    # Helper: K/9 from a set of rows
    def k_per_9(df):
        total_ip = df['innings_pitched'].sum()
        total_k = df['strikeouts'].sum()
        return (total_k / total_ip * 9) if total_ip > 0 else 0

    def k_per_start(df):
        return df['strikeouts'].mean() if len(df) > 0 else 0

    def ip_per_start(df):
        return df['innings_pitched'].mean() if len(df) > 0 else 0

    def pitch_count_avg(df):
        return df['pitch_count'].mean() if len(df) > 0 else 0

    def whip(df):
        total_ip = df['innings_pitched'].sum()
        total_wh = df['walks'].sum() + df['hits_allowed'].sum()
        return total_wh / total_ip if total_ip > 0 else 2.0

    # Last N games (from base, which is all prior data)
    last_3 = base.tail(3)
    last_5 = base.tail(5)
    last_10 = base.tail(10)

    # Season-only stats
    season_data = prior_season if len(prior_season) > 0 else base

    features = {}

    # Pitcher stats
    features['season_k_per_9'] = k_per_9(season_data)
    features['last_3_k_per_9'] = k_per_9(last_3)
    features['last_5_k_per_9'] = k_per_9(last_5)
    features['last_10_k_per_9'] = k_per_9(last_10)
    features['season_k_per_start'] = k_per_start(season_data)
    features['last_5_k_per_start'] = k_per_start(last_5)
    features['season_ip_per_start'] = ip_per_start(season_data)
    features['last_5_ip_per_start'] = ip_per_start(last_5)
    features['season_pitch_count_avg'] = pitch_count_avg(season_data)
    features['last_5_pitch_count_avg'] = pitch_count_avg(last_5)
    features['season_whip'] = whip(season_data)

    # K volatility
    if len(last_10) >= 3:
        features['k_volatility'] = last_10['strikeouts'].std()
    else:
        features['k_volatility'] = base['strikeouts'].std() if len(base) >= 2 else 2.0

    # Opponent team K rate (league average fallback since opponent field is empty)
    features['opp_team_k_pct_season'] = league_k_rates.get(season, 0.23)
    features['opp_team_k_pct_last_10'] = league_k_rates.get(season, 0.23)

    # Context
    features['home_away'] = 1 if row.get('home_away') == 'H' else 0

    # Rest days
    if idx > 0:
        prev_date = group.iloc[idx - 1]['date']
        rest = (game_date - prev_date).days
        features['rest_days'] = min(rest, 10)
    else:
        features['rest_days'] = 5  # default

    # Line
    features['line'] = line
    features['k_minus_line'] = features['season_k_per_start'] - line

    # Target
    actual_k = row['strikeouts']
    features['over_line'] = 1 if actual_k >= line else 0

    # Metadata (not used as features)
    features['_player_name'] = row['player_name']
    features['_bbref_id'] = row['bbref_id']
    features['_date'] = str(game_date)
    features['_actual_k'] = actual_k
    features['_team'] = row.get('team', '')

    return features


def build_training_dataset(seasons=[2024, 2025], min_starts=3):
    """
    Build training dataset from historical pitcher gamelogs.
    Generates synthetic lines around each pitcher's season K average.

    Returns: pd.DataFrame with features + 'over_line' target
    """
    logger.info(f"[Features] Building dataset for seasons {seasons}...")

    df = _load_pitcher_gamelogs(seasons)
    if df.empty:
        logger.error("[Features] No pitcher gamelogs found!")
        return pd.DataFrame()

    league_k_rates = _load_league_k_rate(seasons)
    logger.info(f"[Features] League K rates: {league_k_rates}")
    logger.info(f"[Features] Total pitcher gamelogs: {len(df)}")

    # Group by pitcher
    all_rows = []
    pitchers = df.groupby('bbref_id')
    pitcher_count = 0

    for pid, group in pitchers:
        group = group.sort_values('date').reset_index(drop=True)
        if len(group) < min_starts:
            continue

        pitcher_count += 1

        for idx in range(min_starts, len(group)):
            # Compute season average K up to this point for synthetic lines
            row = group.iloc[idx]
            season = row['date'].year
            prior_season = group.iloc[:idx]
            prior_this_yr = prior_season[prior_season['date'].apply(lambda d: d.year) == season]

            if len(prior_this_yr) < 2:
                avg_k = group.iloc[:idx]['strikeouts'].mean()
            else:
                avg_k = prior_this_yr['strikeouts'].mean()

            # Generate synthetic lines: floor(avg)-0.5, floor(avg)+0.5, floor(avg)+1.5
            base_line = math.floor(avg_k)
            lines = [base_line - 0.5, base_line + 0.5, base_line + 1.5]
            # Filter out unrealistic lines
            lines = [l for l in lines if 1.5 <= l <= 12.5]

            for line in lines:
                feat = _compute_features_for_row(group, idx, league_k_rates, line)
                if feat is not None:
                    all_rows.append(feat)

        if pitcher_count % 100 == 0:
            logger.info(f"[Features] Processed {pitcher_count} pitchers, {len(all_rows)} rows...")

    result = pd.DataFrame(all_rows)
    logger.info(f"[Features] Dataset complete: {len(result)} rows from {pitcher_count} pitchers")
    return result


def build_live_features(pitcher_bbref_id, game_date, opponent, line):
    """
    Build feature vector for a single live prediction.

    Args:
        pitcher_bbref_id: str, the pitcher's bbref_id (mlb_api_id as string)
        game_date: date object
        opponent: str team abbreviation (unused for now, league avg)
        line: float, the K line (e.g., 5.5)

    Returns: dict of features (without target/metadata)
    """
    conn = get_connection(dict_cursor=True)
    cur = conn.cursor()

    cur.execute("""
        SELECT player_name, bbref_id, date, team, opponent, home_away,
               innings_pitched, strikeouts, walks, hits_allowed,
               pitch_count, game_score
        FROM mlb_pitcher_gamelogs
        WHERE bbref_id = %s AND date < %s
        ORDER BY date
    """, (pitcher_bbref_id, game_date))
    rows = cur.fetchall()
    put_connection(conn)

    if not rows:
        return None

    df = pd.DataFrame(rows)
    # Add a dummy row for the "current" game
    dummy = dict(rows[-1])
    dummy['date'] = game_date
    dummy['strikeouts'] = 0
    df = pd.concat([df, pd.DataFrame([dummy])], ignore_index=True)

    league_k_rates = _load_league_k_rate([game_date.year, game_date.year - 1])
    idx = len(df) - 1

    feat = _compute_features_for_row(df, idx, league_k_rates, line)
    if feat is None:
        return None

    # Remove metadata and target for live prediction
    return {k: v for k, v in feat.items() if not k.startswith('_') and k != 'over_line'}


# Feature columns used by the model (exclude metadata and target)
FEATURE_COLS = [
    'season_k_per_9', 'last_3_k_per_9', 'last_5_k_per_9', 'last_10_k_per_9',
    'season_k_per_start', 'last_5_k_per_start',
    'season_ip_per_start', 'last_5_ip_per_start',
    'season_pitch_count_avg', 'last_5_pitch_count_avg',
    'season_whip', 'k_volatility',
    'opp_team_k_pct_season', 'opp_team_k_pct_last_10',
    'home_away', 'rest_days',
    'line', 'k_minus_line',
]


def main():
    parser = argparse.ArgumentParser(description="MLB Feature Engineering")
    parser.add_argument("--save", action="store_true", help="Save dataset to CSV")
    args = parser.parse_args()

    df = build_training_dataset(seasons=[2024, 2025], min_starts=3)

    if df.empty:
        print("No data generated!")
        return

    print(f"\nDataset shape: {df.shape}")
    print(f"Seasons: {sorted(df['_date'].str[:4].unique())}")
    print(f"Pitchers: {df['_bbref_id'].nunique()}")
    print(f"Target distribution: over={df['over_line'].sum():.0f} ({df['over_line'].mean()*100:.1f}%), under={len(df)-df['over_line'].sum():.0f}")

    print(f"\nFeature stats:")
    for col in FEATURE_COLS:
        if col in df.columns:
            print(f"  {col:30s} mean={df[col].mean():8.3f}  std={df[col].std():8.3f}  min={df[col].min():8.3f}  max={df[col].max():8.3f}")

    if args.save:
        out_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'mlb_training_data.csv')
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        df.to_csv(out_path, index=False)
        print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
