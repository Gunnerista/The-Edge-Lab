#!/usr/bin/env python3
"""
MLB Strikeout Prediction Model
=================================
War Machine MLB

XGBoost classifier with Platt scaling for calibrated probabilities.
Train on 2024, validate/calibrate on 2025.

Usage:
    python mlb/scripts/mlb_model.py           # train + save
    python mlb/scripts/mlb_model.py --eval     # train + detailed eval (no save)
"""

import sys
import os
import io
import pickle
import logging
import argparse
import numpy as np
import pandas as pd
from scipy.optimize import minimize

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'shared'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

from mlb_features import build_training_dataset, FEATURE_COLS

MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


def platt_transform(raw_prob, A, B):
    """Apply Platt scaling: calibrated = 1 / (1 + exp(A * raw + B))"""
    return 1.0 / (1.0 + np.exp(A * raw_prob + B))


def fit_platt(raw_probs, labels):
    """Fit Platt scaling parameters A, B via MLE."""
    def neg_log_likelihood(params):
        A, B = params
        calibrated = platt_transform(raw_probs, A, B)
        calibrated = np.clip(calibrated, 1e-7, 1 - 1e-7)
        return -np.sum(labels * np.log(calibrated) + (1 - labels) * np.log(1 - calibrated))

    result = minimize(neg_log_likelihood, x0=[1.0, 0.0], method='Nelder-Mead')
    return result.x[0], result.x[1]


def brier_score(probs, labels):
    """Compute Brier score."""
    return np.mean((probs - labels) ** 2)


class MLBStrikeoutModel:
    def __init__(self):
        self.model = None
        self.platt_A = None
        self.platt_B = None
        self.feature_cols = FEATURE_COLS

    def train(self, df=None):
        """Train on 2024, validate on 2025, fit Platt scaling."""
        from xgboost import XGBClassifier
        from sklearn.metrics import roc_auc_score, accuracy_score

        if df is None:
            df = build_training_dataset(seasons=[2024, 2025], min_starts=3)

        if df.empty:
            logger.error("No training data!")
            return

        # Split by season
        df['_year'] = df['_date'].str[:4].astype(int)
        train = df[df['_year'] == 2024].copy()
        val = df[df['_year'] == 2025].copy()

        logger.info(f"Train: {len(train)} rows (2024), Val: {len(val)} rows (2025)")

        X_train = train[self.feature_cols].values
        y_train = train['over_line'].values
        X_val = val[self.feature_cols].values
        y_val = val['over_line'].values

        # Train XGBoost
        self.model = XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_weight=5,
            eval_metric='logloss',
            random_state=42,
        )
        self.model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)

        # Raw predictions
        train_probs = self.model.predict_proba(X_train)[:, 1]
        val_probs = self.model.predict_proba(X_val)[:, 1]

        # Metrics before Platt
        train_auc = roc_auc_score(y_train, train_probs)
        val_auc = roc_auc_score(y_val, val_probs)
        val_brier_raw = brier_score(val_probs, y_val)
        val_acc = accuracy_score(y_val, (val_probs >= 0.5).astype(int))

        # Fit Platt scaling on validation set
        self.platt_A, self.platt_B = fit_platt(val_probs, y_val)

        # Calibrated predictions
        val_calibrated = platt_transform(val_probs, self.platt_A, self.platt_B)
        val_brier_cal = brier_score(val_calibrated, y_val)

        # Feature importance
        importances = self.model.feature_importances_
        feat_imp = sorted(zip(self.feature_cols, importances), key=lambda x: -x[1])

        # Print Training Report
        print()
        print("=" * 60)
        print("  MLB STRIKEOUT MODEL — TRAINING REPORT")
        print("=" * 60)
        print(f"  Training set:   2024 season, {len(train):,} rows")
        print(f"  Validation set: 2025 season, {len(val):,} rows")
        print()
        print(f"  Train AUC:              {train_auc:.4f}")
        print(f"  Val AUC:                {val_auc:.4f}")
        print(f"  Val Brier Score (raw):  {val_brier_raw:.4f}")
        print(f"  Val Accuracy:           {val_acc*100:.2f}%")
        print()
        print(f"  Platt Scaling: A={self.platt_A:.4f}, B={self.platt_B:.4f}")
        print(f"  Calibrated Brier Score: {val_brier_cal:.4f}")
        print()
        print("  Feature Importance (top 10):")
        for i, (name, imp) in enumerate(feat_imp[:10]):
            print(f"    {i+1:2d}. {name:30s} {imp:.4f}")

        # Line-specific performance
        print()
        print("  === Line-specific Performance (validation) ===")
        for line_val in sorted(val['line'].unique()):
            mask = val['line'] == line_val
            if mask.sum() < 10:
                continue
            sub_probs = val_calibrated[mask.values]
            sub_labels = y_val[mask.values]
            sub_acc = accuracy_score(sub_labels, (sub_probs >= 0.5).astype(int))
            sub_brier = brier_score(sub_probs, sub_labels)
            print(f"    Line {line_val:4.1f}: {mask.sum():5d} predictions, accuracy {sub_acc*100:.2f}%, Brier {sub_brier:.4f}")

        # Calibration table
        print()
        print("  === Calibration Table (validation, 10 bins) ===")
        print(f"    {'Bin':>12s}  {'Count':>6s}  {'Avg Pred':>8s}  {'Actual':>8s}  {'Gap':>8s}")
        for low in np.arange(0, 1, 0.1):
            high = low + 0.1
            mask = (val_calibrated >= low) & (val_calibrated < high)
            if mask.sum() == 0:
                continue
            avg_pred = val_calibrated[mask].mean()
            actual = y_val[mask].mean()
            gap = avg_pred - actual
            print(f"    {low:.1f}-{high:.1f}  {mask.sum():6d}  {avg_pred:8.3f}  {actual:8.3f}  {gap:+8.3f}")

        print("=" * 60)

        return {
            'train_auc': train_auc,
            'val_auc': val_auc,
            'val_brier_raw': val_brier_raw,
            'val_brier_cal': val_brier_cal,
            'val_acc': val_acc,
            'platt_A': self.platt_A,
            'platt_B': self.platt_B,
        }

    def predict_proba(self, features_dict):
        """
        Predict calibrated probability of over the line.

        Args:
            features_dict: dict with feature values

        Returns:
            float: calibrated probability
        """
        if self.model is None:
            raise RuntimeError("Model not trained or loaded")

        X = np.array([[features_dict.get(col, 0) for col in self.feature_cols]])
        raw = self.model.predict_proba(X)[0, 1]
        return float(platt_transform(raw, self.platt_A, self.platt_B))

    def save(self, path=None):
        """Save model + Platt params to pickle."""
        if path is None:
            path = os.path.join(MODEL_DIR, 'mlb_strikeout_model.pkl')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {
            'model': self.model,
            'platt_A': self.platt_A,
            'platt_B': self.platt_B,
            'feature_cols': self.feature_cols,
        }
        with open(path, 'wb') as f:
            pickle.dump(data, f)
        logger.info(f"[Model] Saved to {path}")

    def load(self, path=None):
        """Load model from pickle."""
        if path is None:
            path = os.path.join(MODEL_DIR, 'mlb_strikeout_model.pkl')
        with open(path, 'rb') as f:
            data = pickle.load(f)
        self.model = data['model']
        self.platt_A = data['platt_A']
        self.platt_B = data['platt_B']
        self.feature_cols = data.get('feature_cols', FEATURE_COLS)
        logger.info(f"[Model] Loaded from {path}")


def main():
    parser = argparse.ArgumentParser(description="MLB Strikeout Model")
    parser.add_argument("--eval", action="store_true", help="Eval only (no save)")
    args = parser.parse_args()

    model = MLBStrikeoutModel()
    model.train()

    if not args.eval:
        model.save()
        print(f"\nModel saved to mlb/data/mlb_strikeout_model.pkl")


if __name__ == "__main__":
    main()
