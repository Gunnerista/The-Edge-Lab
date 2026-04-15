#!/usr/bin/env python3
"""
MLB Strikeout Model Validation
=================================
War Machine MLB

Hold-out test on 2025 second half (July+).
Edge threshold simulations for profitability analysis.

Usage:
    python mlb/scripts/mlb_validate_model.py
"""

import sys
import os
import io
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, accuracy_score

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

from mlb_features import build_training_dataset, FEATURE_COLS
from mlb_model import MLBStrikeoutModel, brier_score, platt_transform

KALSHI_FEE = 0.07


def simulate_roi(probs, labels, prices, edge_threshold, side_filter=None):
    """
    Simulate ROI at a given edge threshold.

    Args:
        probs: calibrated model probabilities (P(over))
        labels: actual outcomes (1=over, 0=under)
        prices: market prices (kalshi_price approximation)
        edge_threshold: minimum edge to bet
        side_filter: 'yes', 'no', or None (both)

    Returns: dict with bets, wins, roi, pnl
    """
    bets = 0
    wins = 0
    total_pnl = 0.0
    total_cost = 0.0

    for i in range(len(probs)):
        p = probs[i]
        label = labels[i]
        price = prices[i]

        # YES side: model says over is likely
        yes_edge = p - price
        # NO side: model says under is likely
        no_edge = (1 - p) - (1 - price)

        if side_filter == 'yes' and yes_edge < edge_threshold:
            continue
        elif side_filter == 'no' and no_edge < edge_threshold:
            continue
        elif side_filter is None:
            if max(yes_edge, no_edge) < edge_threshold:
                continue

        if side_filter == 'yes' or (side_filter is None and yes_edge >= no_edge):
            # Bet YES (over)
            entry = price
            won = (label == 1)
        else:
            # Bet NO (under)
            entry = 1 - price
            won = (label == 0)

        bets += 1
        total_cost += entry

        if won:
            profit = (1 - entry) * (1 - KALSHI_FEE)
            total_pnl += profit
            wins += 1
        else:
            total_pnl -= entry

    roi = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    return {
        'bets': bets,
        'wins': wins,
        'win_rate': (wins / bets * 100) if bets > 0 else 0,
        'pnl': round(total_pnl, 2),
        'cost': round(total_cost, 2),
        'roi': round(roi, 1),
    }


def main():
    print("\n" + "=" * 70)
    print("  MLB STRIKEOUT MODEL — HOLD-OUT VALIDATION REPORT")
    print("=" * 70)

    # Load model
    model = MLBStrikeoutModel()
    model.load()
    print(f"\n  Model loaded. Platt A={model.platt_A:.4f}, B={model.platt_B:.4f}")

    # Build dataset — use 2025 only for hold-out
    df = build_training_dataset(seasons=[2025], min_starts=3)
    if df.empty:
        print("  No data!")
        return

    # Hold-out: July 2025 onwards
    df['_month'] = df['_date'].str[5:7].astype(int)
    holdout = df[df['_month'] >= 7].copy()

    print(f"\n  Full 2025 dataset:  {len(df):,} rows")
    print(f"  Hold-out (Jul+):    {len(holdout):,} rows")
    print(f"  Pitchers in holdout: {holdout['_bbref_id'].nunique()}")

    if len(holdout) < 100:
        print("  Not enough hold-out data!")
        return

    X = holdout[FEATURE_COLS].values
    y = holdout['over_line'].values

    # Raw predictions
    raw_probs = model.model.predict_proba(X)[:, 1]
    cal_probs = platt_transform(raw_probs, model.platt_A, model.platt_B)

    # Metrics
    auc = roc_auc_score(y, cal_probs)
    brier_raw = brier_score(raw_probs, y)
    brier_cal = brier_score(cal_probs, y)
    acc = accuracy_score(y, (cal_probs >= 0.5).astype(int))

    print(f"\n  === Hold-out Metrics ===")
    print(f"  AUC:                    {auc:.4f}")
    print(f"  Brier Score (raw):      {brier_raw:.4f}")
    print(f"  Brier Score (calibrated): {brier_cal:.4f}")
    print(f"  Accuracy:               {acc*100:.2f}%")

    # Calibration table
    print(f"\n  === Calibration Table (10 bins) ===")
    print(f"    {'Bin':>12s}  {'Count':>6s}  {'Avg Pred':>8s}  {'Actual':>8s}  {'Gap':>8s}")
    for low in np.arange(0, 1, 0.1):
        high = low + 0.1
        mask = (cal_probs >= low) & (cal_probs < high)
        if mask.sum() == 0:
            continue
        avg_pred = cal_probs[mask].mean()
        actual = y[mask].mean()
        gap = avg_pred - actual
        print(f"    {low:.1f}-{high:.1f}  {mask.sum():6d}  {avg_pred:8.3f}  {actual:8.3f}  {gap:+8.3f}")

    # Use line as proxy for market price (rough approximation)
    # In real Kalshi, price = implied probability. For simulation,
    # use the base rate for that line level as approximate price.
    lines = holdout['line'].values
    # Approximate market price: for each line, use the overall hit rate as price proxy
    line_prices = {}
    for line_val in holdout['line'].unique():
        mask = holdout['line'] == line_val
        line_prices[line_val] = y[mask.values].mean()

    prices = np.array([line_prices.get(l, 0.5) for l in lines])

    # Edge threshold simulations
    print(f"\n  === Edge Threshold ROI Simulation ===")
    print(f"  (Using line base rate as price proxy, 7% Kalshi fee)")
    print(f"    {'Edge':>6s}  {'Bets':>6s}  {'Wins':>5s}  {'Win%':>6s}  {'PnL':>8s}  {'ROI':>7s}")

    for edge in [0.05, 0.10, 0.15, 0.20, 0.25]:
        r = simulate_roi(cal_probs, y, prices, edge)
        print(f"    {edge*100:5.0f}%  {r['bets']:6d}  {r['wins']:5d}  {r['win_rate']:5.1f}%  ${r['pnl']:>7.2f}  {r['roi']:>6.1f}%")

    # YES vs NO side breakdown
    print(f"\n  === Side-specific Performance (edge >= 15%) ===")
    for side in ['yes', 'no']:
        r = simulate_roi(cal_probs, y, prices, 0.15, side_filter=side)
        print(f"    {side.upper():>4s} side: {r['bets']:5d} bets, {r['win_rate']:5.1f}% win, PnL ${r['pnl']:>7.2f}, ROI {r['roi']:>6.1f}%")

    # Line-specific performance
    print(f"\n  === Line-specific Performance ===")
    print(f"    {'Line':>6s}  {'Count':>6s}  {'Accuracy':>8s}  {'Brier':>8s}")
    for line_val in sorted(holdout['line'].unique()):
        mask = (holdout['line'] == line_val).values
        if mask.sum() < 20:
            continue
        sub_probs = cal_probs[mask]
        sub_labels = y[mask]
        sub_acc = accuracy_score(sub_labels, (sub_probs >= 0.5).astype(int))
        sub_brier = brier_score(sub_probs, sub_labels)
        print(f"    {line_val:5.1f}  {mask.sum():6d}  {sub_acc*100:7.2f}%  {sub_brier:8.4f}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
