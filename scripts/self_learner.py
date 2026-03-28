#!/usr/bin/env python3
"""
Self-Learner — Automatic Parameter Tuning Based on Performance Data.
=====================================================================
Adjusts trading parameters based on calibration + risk decomposition results.
Only activates after 50+ settled trades to avoid premature tuning.

Tunable parameters:
  - MIN_EDGE: minimum edge to enter (default 0.05)
  - VPIN_CUTOFF: max VPIN for overreaction fade (default 0.70)
  - OVERREACTION_THRESHOLD: min price deviation to trigger (default 0.08)
  - KELLY_HIGH: Kelly fraction for high confidence (default 0.50)
  - KELLY_MEDIUM: Kelly fraction for medium confidence (default 0.25)

Adjustment rules:
  - Max 10% change per tuning cycle
  - Hard bounds prevent extreme values
  - Every change logged with reasoning to parameter_history.jsonl
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PARAMS_FILE = PROJECT_ROOT / "data" / "tuned_params.json"
HISTORY_FILE = PROJECT_ROOT / "data" / "parameter_history.jsonl"
MIN_SAMPLES = 50  # minimum settled trades before tuning activates

# Default parameter values
DEFAULTS = {
    "min_edge": 0.10,
    "vpin_cutoff": 0.70,
    "overreaction_threshold": 0.08,
    "overreaction_relative": 0.15,
    "kelly_high": 0.50,
    "kelly_medium": 0.25,
    "min_edge_market_order": 0.25,
    "max_spread_cents": 10,
}

# Hard bounds: parameters cannot go outside these ranges
BOUNDS = {
    "min_edge":                (0.08, 0.20),
    "vpin_cutoff":             (0.40, 0.90),
    "overreaction_threshold":  (0.04, 0.20),
    "overreaction_relative":   (0.08, 0.30),
    "kelly_high":              (0.15, 0.75),
    "kelly_medium":            (0.08, 0.50),
    "min_edge_market_order":   (0.10, 0.50),
    "max_spread_cents":        (3, 20),
}

MAX_CHANGE_PCT = 0.10  # max 10% change per tuning cycle


def _clamp(value: float, param_name: str) -> float:
    """Clamp value within hard bounds."""
    lo, hi = BOUNDS.get(param_name, (0, 1))
    return max(lo, min(hi, value))


def _adjust(current: float, target_direction: float, param_name: str) -> float:
    """
    Adjust a parameter by up to MAX_CHANGE_PCT in target_direction.

    target_direction: positive = increase, negative = decrease
    Returns new value clamped within bounds.
    """
    max_delta = abs(current) * MAX_CHANGE_PCT
    if max_delta < 0.001:
        max_delta = 0.005  # floor for very small values

    if target_direction > 0:
        new = current + max_delta
    elif target_direction < 0:
        new = current - max_delta
    else:
        return current

    return round(_clamp(new, param_name), 4)


class TunedParams:
    """
    Shared parameter store. auto_trader and signal_engine read from this.
    self_learner writes to this based on performance data.
    """

    def __init__(self, params_file: Path = PARAMS_FILE):
        self.file = params_file
        self.params = self._load()

    def _load(self) -> dict:
        if self.file.exists():
            try:
                with open(self.file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Merge with defaults (new params get default values)
                merged = dict(DEFAULTS)
                merged.update(saved)
                return merged
            except (json.JSONDecodeError, IOError):
                pass
        return dict(DEFAULTS)

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(self.params, f, indent=2)

    def get(self, name: str) -> float:
        return self.params.get(name, DEFAULTS.get(name, 0))

    def set(self, name: str, value: float, reason: str = ""):
        old = self.params.get(name)
        self.params[name] = value
        self.save()

        # Log change
        self._log_change(name, old, value, reason)

    def _log_change(self, name: str, old_val, new_val, reason: str):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "parameter": name,
            "old_value": old_val,
            "new_value": new_val,
            "reason": reason,
        }
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info(f"[SelfLearner] {name}: {old_val} -> {new_val} ({reason})")


class SelfLearner:
    """
    Reads calibration + risk decomposition data and tunes parameters.
    Only activates after MIN_SAMPLES settled trades.
    """

    def __init__(self):
        self.params = TunedParams()

    def should_tune(self) -> bool:
        """Check if we have enough data to tune."""
        from calibration import CalibrationTracker
        cal = CalibrationTracker()
        return cal.state.get("total_predictions", 0) >= MIN_SAMPLES

    def run_tuning_cycle(self) -> dict:
        """
        Run one tuning cycle. Reads performance data, adjusts parameters.

        Returns dict of changes made (empty if no changes or insufficient data).
        """
        if not self.should_tune():
            n = 0
            try:
                from calibration import CalibrationTracker
                n = CalibrationTracker().state.get("total_predictions", 0)
            except Exception:
                pass
            logger.info(f"[SelfLearner] Not enough data ({n}/{MIN_SAMPLES}). Skipping.")
            return {}

        changes = {}

        # Get calibration data
        from calibration import CalibrationTracker
        cal = CalibrationTracker()
        brier = cal.get_brier_score()
        bias = cal.get_bias()

        # Get risk decomposition data
        from risk_decomposition import generate_loss_report
        loss_report = generate_loss_report()
        loss_cats = loss_report.get("categories", {})
        total_losses = loss_report.get("total_losses", 0)

        # Calculate loss category percentages
        model_error_pct = loss_cats.get("model_error", {}).get("pct_amount", 0) / 100
        regime_change_pct = loss_cats.get("regime_change", {}).get("pct_amount", 0) / 100
        slippage_pct = loss_cats.get("execution_slippage", {}).get("pct_amount", 0) / 100

        # ================================================================
        # Rule 1: Calibration bias -> adjust Kelly fractions
        # ================================================================
        if bias["direction"] == "overconfident" and bias["magnitude"] > 0.05:
            # We're predicting too high -> reduce position sizes
            old_kh = self.params.get("kelly_high")
            old_km = self.params.get("kelly_medium")
            new_kh = _adjust(old_kh, -1, "kelly_high")
            new_km = _adjust(old_km, -1, "kelly_medium")
            if new_kh != old_kh:
                self.params.set("kelly_high", new_kh,
                    f"Overconfident bias ({bias['magnitude']*100:.1f}pp) -> reduce Kelly")
                changes["kelly_high"] = {"old": old_kh, "new": new_kh}
            if new_km != old_km:
                self.params.set("kelly_medium", new_km,
                    f"Overconfident bias ({bias['magnitude']*100:.1f}pp) -> reduce Kelly")
                changes["kelly_medium"] = {"old": old_km, "new": new_km}

        elif bias["direction"] == "underconfident" and bias["magnitude"] > 0.05:
            # We're predicting too low -> increase position sizes
            old_kh = self.params.get("kelly_high")
            old_km = self.params.get("kelly_medium")
            new_kh = _adjust(old_kh, +1, "kelly_high")
            new_km = _adjust(old_km, +1, "kelly_medium")
            if new_kh != old_kh:
                self.params.set("kelly_high", new_kh,
                    f"Underconfident bias ({bias['magnitude']*100:.1f}pp) -> increase Kelly")
                changes["kelly_high"] = {"old": old_kh, "new": new_kh}
            if new_km != old_km:
                self.params.set("kelly_medium", new_km,
                    f"Underconfident bias ({bias['magnitude']*100:.1f}pp) -> increase Kelly")
                changes["kelly_medium"] = {"old": old_km, "new": new_km}

        # ================================================================
        # Rule 2: Model Error dominant -> raise MIN_EDGE
        # ================================================================
        if total_losses >= 5 and model_error_pct > 0.40:
            old = self.params.get("min_edge")
            new = _adjust(old, +1, "min_edge")
            if new != old:
                self.params.set("min_edge", new,
                    f"Model Error={model_error_pct*100:.0f}% of losses -> raise MIN_EDGE")
                changes["min_edge"] = {"old": old, "new": new}

        # ================================================================
        # Rule 3: Regime Change dominant -> raise VPIN_CUTOFF
        # Priority: pre-loaded from test data showing 60.9% regime change.
        # If regime_change >= 40% of losses, tighten VPIN filter.
        # ================================================================
        if total_losses >= 5 and regime_change_pct >= 0.40:
            old = self.params.get("vpin_cutoff")
            new = _adjust(old, -1, "vpin_cutoff")  # lower cutoff = more conservative
            if new != old:
                self.params.set("vpin_cutoff", new,
                    f"Regime Change={regime_change_pct*100:.0f}% of losses -> lower VPIN cutoff (stricter)")
                changes["vpin_cutoff"] = {"old": old, "new": new}

        # Also raise overreaction threshold if regime change is high
        if total_losses >= 5 and regime_change_pct >= 0.50:
            old = self.params.get("overreaction_threshold")
            new = _adjust(old, +1, "overreaction_threshold")
            if new != old:
                self.params.set("overreaction_threshold", new,
                    f"Regime Change={regime_change_pct*100:.0f}% -> need bigger deviation to trigger")
                changes["overreaction_threshold"] = {"old": old, "new": new}

        # ================================================================
        # Rule 4: Execution Slippage dominant -> lower market order threshold
        # ================================================================
        if total_losses >= 5 and slippage_pct > 0.40:
            old = self.params.get("min_edge_market_order")
            new = _adjust(old, -1, "min_edge_market_order")
            if new != old:
                self.params.set("min_edge_market_order", new,
                    f"Slippage={slippage_pct*100:.0f}% of losses -> lower market order edge (faster fill)")
                changes["min_edge_market_order"] = {"old": old, "new": new}

        # ================================================================
        # Rule 5: Overall Brier score bad -> raise MIN_EDGE globally
        # ================================================================
        if brier > 0.25:
            old = self.params.get("min_edge")
            new = _adjust(old, +1, "min_edge")
            if new != old:
                self.params.set("min_edge", new,
                    f"Brier={brier:.3f} (>0.25) -> raise MIN_EDGE, be more selective")
                changes["min_edge"] = {"old": old, "new": new}

        if changes:
            logger.info(f"[SelfLearner] Adjusted {len(changes)} parameters: {list(changes.keys())}")
        else:
            logger.info("[SelfLearner] No adjustments needed this cycle.")

        return changes


# ============================================================================
# Integration: read tuned params from auto_trader / signal_engine
# ============================================================================

_cached_params: Optional[TunedParams] = None

def get_params() -> TunedParams:
    """Get the shared TunedParams instance (cached)."""
    global _cached_params
    if _cached_params is None:
        _cached_params = TunedParams()
    return _cached_params


# ============================================================================
# CLI
# ============================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Self-Learner Parameter Tuning")
    parser.add_argument("--tune", action="store_true", help="Run tuning cycle")
    parser.add_argument("--show", action="store_true", help="Show current params")
    parser.add_argument("--history", action="store_true", help="Show parameter history")
    parser.add_argument("--reset", action="store_true", help="Reset to defaults")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.reset:
        p = TunedParams()
        p.params = dict(DEFAULTS)
        p.save()
        print("Reset to defaults.")
        return

    if args.show or not any([args.tune, args.history, args.reset]):
        p = TunedParams()
        print("Current parameters:")
        for k, v in sorted(p.params.items()):
            default = DEFAULTS.get(k, "?")
            marker = " *" if v != default else ""
            print(f"  {k:30s} = {v}{marker}")
        return

    if args.tune:
        learner = SelfLearner()
        changes = learner.run_tuning_cycle()
        if changes:
            print(f"Adjusted {len(changes)} parameters:")
            for k, v in changes.items():
                print(f"  {k}: {v['old']} -> {v['new']}")
        else:
            print("No adjustments needed.")

    if args.history:
        if HISTORY_FILE.exists():
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    d = json.loads(line.strip())
                    print(f"  {d['timestamp'][:19]} | {d['parameter']:25s} | {d['old_value']} -> {d['new_value']} | {d['reason'][:50]}")
        else:
            print("No history yet.")


if __name__ == "__main__":
    main()
