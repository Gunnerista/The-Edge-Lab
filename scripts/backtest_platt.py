#!/usr/bin/env python3
"""
Platt Scaling + Fee-Adjusted ROI Analysis
==========================================
Prediction Market War Machine

1. Splits OOS data (Jan+) into train/test 50:50 by time
2. Fits sigmoid recalibration on train half
3. Evaluates on test half: Brier, calibration, edge ROI
4. Computes all ROI with Kalshi 7% fee deducted

Usage:
    python scripts/backtest_platt.py
"""

import sys
import json
import math
import sqlite3
import logging
import re
import time
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_DB = PROJECT_ROOT / "data" / "market_data.db"
NBA_DB = PROJECT_ROOT / "data" / "nba_data.db"
GAMELOG_CACHE = PROJECT_ROOT / "data" / "gamelogs_cache.json"
REPORT_FILE = PROJECT_ROOT / "data" / "backtest_platt_report.json"

# ============================================================================
# Model constants (from nba_model.py)
# ============================================================================
SEASON_WEIGHT = 0.35
RECENT_WEIGHT = 0.50
HOME_AWAY_WEIGHT = 0.15
MAX_MATCHUP_ADJUSTMENT = 0.20
MAX_B2B_PENALTY = 0.08
PACE_ADJUSTMENT_FACTOR = 0.5
MIN_STD = {"points": 4.0, "rebounds": 2.0, "assists": 1.5}
LEAGUE_AVG = {"points": 114.0, "rebounds": 44.0, "assists": 26.0}
LEAGUE_AVG_PACE = 100.0
MIN_GAMES = 5

KALSHI_FEE_RATE = 0.07  # 7% fee on profit

TEAM_ABBRS = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN",
    "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA",
    "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX",
    "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
}
PROP_PREFIX_MAP = {"KXNBAPTS": "points", "KXNBAREB": "rebounds", "KXNBAAST": "assists"}


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_over(projected, std_dev, line):
    if std_dev <= 0:
        return 1.0 if projected > line else 0.0
    z = (line - projected) / std_dev
    return 1.0 - _norm_cdf(z)


# ============================================================================
# Platt Scaling: fit sigmoid a, b so that calibrated_p = 1/(1+exp(a*logit+b))
# ============================================================================

def fit_platt(probs, labels, lr=0.01, epochs=5000):
    """
    Fit Platt scaling: P_cal = sigmoid(a * logit(p) + b)
    Vectorized with pre-computed logits for speed.
    """
    n = len(probs)
    # Pre-compute logits once
    logits = []
    for p in probs:
        p_c = max(0.001, min(0.999, p))
        logits.append(math.log(p_c / (1 - p_c)))

    a, b = 1.0, 0.0

    for epoch in range(epochs):
        grad_a = grad_b = 0.0

        for i in range(n):
            z = a * logits[i] + b
            # Numerically stable sigmoid
            if z >= 0:
                sig = 1.0 / (1.0 + math.exp(-z))
            else:
                ez = math.exp(z)
                sig = ez / (1.0 + ez)
            err = sig - labels[i]
            grad_a += err * logits[i]
            grad_b += err

        a -= lr * grad_a / n
        b -= lr * grad_b / n

    return a, b


def apply_platt(p, a, b):
    p_c = max(0.001, min(0.999, p))
    logit = math.log(p_c / (1 - p_c))
    z = a * logit + b
    return max(0.02, min(0.98, 1.0 / (1.0 + math.exp(-z))))


# ============================================================================
# Reuse rolling prediction from backtest_oos
# ============================================================================

def compute_rolling_stats(games, before_date, prop_type):
    stat_key = {"points": "PTS", "rebounds": "REB", "assists": "AST"}[prop_type]
    prior = [g for g in games if g["date"] < before_date]
    if len(prior) < MIN_GAMES:
        return None
    vals = [g[stat_key] for g in prior]
    season_avg = sum(vals) / len(vals)
    recent = prior[-10:]
    rv = [g[stat_key] for g in recent]
    recent_avg = sum(rv) / len(rv)
    if len(rv) > 1:
        recent_std = (sum((v - recent_avg)**2 for v in rv) / (len(rv)-1))**0.5
    else:
        recent_std = season_avg * 0.25
    home_g = [g for g in prior if g["is_home"]]
    away_g = [g for g in prior if not g["is_home"]]
    home_avg = sum(g[stat_key] for g in home_g)/len(home_g) if home_g else season_avg
    away_avg = sum(g[stat_key] for g in away_g)/len(away_g) if away_g else season_avg
    return {
        "season_avg": season_avg, "recent_avg": recent_avg, "recent_std": recent_std,
        "home_avg": home_avg, "away_avg": away_avg, "games_played": len(prior),
    }


def predict_rolling(stats, prop_type, line, is_home, opp_team, is_b2b):
    season_avg = stats["season_avg"]
    recent_avg = stats["recent_avg"]
    recent_std = stats["recent_std"]
    ha_avg = stats["home_avg"] if is_home else stats["away_avg"]
    base = SEASON_WEIGHT*season_avg + RECENT_WEIGHT*recent_avg + HOME_AWAY_WEIGHT*ha_avg

    matchup_adj = pace_adj = 0.0
    opp_col = {"points":"opp_pts_pg","rebounds":"opp_reb_pg","assists":"opp_ast_pg"}[prop_type]
    if opp_team:
        oa = float(opp_team.get(opp_col,0))
        if oa > 0:
            matchup_adj = max(-MAX_MATCHUP_ADJUSTMENT, min(MAX_MATCHUP_ADJUSTMENT, oa/LEAGUE_AVG[prop_type]-1))
        op = float(opp_team.get("pace",0))
        if op > 0:
            pace_adj = (op/LEAGUE_AVG_PACE - 1) * PACE_ADJUSTMENT_FACTOR

    b2b_adj = -MAX_B2B_PENALTY if is_b2b else 0.0
    projected = base * (1 + matchup_adj + b2b_adj + pace_adj)
    std_dev = max(recent_std, MIN_STD[prop_type])
    if is_b2b: std_dev *= 1.15
    if stats["games_played"] < 20: std_dev *= 1.10
    mp = prob_over(projected, std_dev, line)
    return max(0.02, min(0.98, mp))


def parse_ticker_light(ticker):
    parts = ticker.upper().split("-")
    if len(parts) < 4: return None
    prop_type = PROP_PREFIX_MAP.get(parts[0])
    if not prop_type: return None
    m = re.match(r'(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})', parts[1])
    if not m: return None
    try: line = float(parts[3]) - 0.5
    except ValueError: return None
    months = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"OCT":10,"NOV":11,"DEC":12}
    mon = months.get(m.group(2), 0)
    if not mon: return None
    game_date = f"{2000+int(m.group(1))}-{mon:02d}-{int(m.group(3)):02d}"
    player_seg = parts[2]
    pm = re.match(r'([A-Z]{3})', player_seg)
    player_team = pm.group(1) if pm else ""
    home_abbr = m.group(5)
    away_abbr = m.group(4)
    is_home = (player_team == home_abbr)
    opponent = away_abbr if is_home else home_abbr
    return {"prop_type": prop_type, "game_date": game_date, "player_seg": player_seg,
            "opponent": opponent, "is_home": is_home, "line": line}


# ============================================================================
# Main
# ============================================================================

def main():
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # --- Load game logs cache ---
    with open(GAMELOG_CACHE, "r", encoding="utf-8") as f:
        all_logs = json.load(f)
    logger.info(f"[Platt] Loaded {len(all_logs)} player game logs")

    # --- Resolve players ---
    conn_nba = sqlite3.connect(str(NBA_DB))
    conn_nba.row_factory = sqlite3.Row
    conn_market = sqlite3.connect(str(MARKET_DB))

    rows = conn_market.execute("""
        SELECT DISTINCT ticker FROM settled_markets
        WHERE (ticker LIKE 'KXNBAPTS%' OR ticker LIKE 'KXNBAREB%' OR ticker LIKE 'KXNBAAST%')
        AND close_time >= '2026-01-01'
    """).fetchall()

    segments = set()
    for (t,) in rows:
        parts = t.split("-")
        if len(parts) >= 3: segments.add(parts[2])

    seg_to_pid = {}
    for seg in segments:
        m = re.match(r'([A-Z]{3})([A-Z])([A-Z]+?)(\d*)$', seg.upper())
        if not m: continue
        last_name = m.group(3).capitalize()
        row = conn_nba.execute(
            "SELECT player_id FROM nba_players WHERE player_name LIKE ? AND season='2025-26' AND games_played>=5 LIMIT 1",
            (f"% {last_name}%",)
        ).fetchone()
        if row:
            seg_to_pid[seg] = str(row["player_id"])

    # Load teams
    team_rows = conn_nba.execute("SELECT * FROM nba_teams WHERE season='2025-26'").fetchall()
    teams = {r["team_abbr"]: dict(r) for r in team_rows}
    conn_nba.close()

    # --- Load markets ---
    conn_market.row_factory = sqlite3.Row
    markets = conn_market.execute("""
        SELECT s.ticker, s.result, s.close_time,
               p.yes_price as kalshi_price
        FROM settled_markets s
        LEFT JOIN (
            SELECT ticker, yes_price,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY id DESC) as rn
            FROM price_snapshots
        ) p ON s.ticker = p.ticker AND p.rn = 1
        WHERE (s.ticker LIKE 'KXNBAPTS%' OR s.ticker LIKE 'KXNBAREB%' OR s.ticker LIKE 'KXNBAAST%')
        AND s.close_time >= '2026-01-01'
        ORDER BY s.close_time
    """).fetchall()
    conn_market.close()
    markets = [dict(r) for r in markets]

    logger.info(f"[Platt] {len(markets)} markets loaded")

    # --- Generate raw predictions ---
    all_preds = []
    skipped = 0
    t0 = time.time()

    for i, mkt in enumerate(markets):
        parsed = parse_ticker_light(mkt["ticker"])
        if not parsed:
            skipped += 1; continue

        pid = seg_to_pid.get(parsed["player_seg"])
        if not pid or pid not in all_logs:
            skipped += 1; continue

        stats = compute_rolling_stats(all_logs[pid], parsed["game_date"], parsed["prop_type"])
        if not stats:
            skipped += 1; continue

        opp_team = teams.get(parsed["opponent"])
        # B2B check
        prev_date = (date.fromisoformat(parsed["game_date"]) - timedelta(days=1)).isoformat()
        is_b2b = any(g["date"] == prev_date for g in all_logs[pid])

        raw_prob = predict_rolling(stats, parsed["prop_type"], parsed["line"],
                                   parsed["is_home"], opp_team, is_b2b)

        prop_label = {"points":"PTS","rebounds":"REB","assists":"AST"}[parsed["prop_type"]]
        all_preds.append({
            "ticker": mkt["ticker"],
            "prop_type": prop_label,
            "raw_prob": raw_prob,
            "actual": 1 if mkt["result"] == "yes" else 0,
            "kalshi_price": mkt.get("kalshi_price"),
            "close_time": mkt["close_time"],
            "game_date": parsed["game_date"],
        })

    elapsed = time.time() - t0
    logger.info(f"[Platt] {len(all_preds)} predictions in {elapsed:.1f}s, {skipped} skipped")

    # --- Sort by time and split 50/50 ---
    all_preds.sort(key=lambda x: x["close_time"])
    mid = len(all_preds) // 2
    train = all_preds[:mid]
    test = all_preds[mid:]

    logger.info(f"[Platt] Train: {len(train)} (up to {train[-1]['game_date']})")
    logger.info(f"[Platt] Test:  {len(test)} (from {test[0]['game_date']})")

    # --- Fit Platt on train ---
    train_probs = [p["raw_prob"] for p in train]
    train_labels = [p["actual"] for p in train]

    logger.info("[Platt] Fitting Platt scaling...")
    a, b = fit_platt(train_probs, train_labels, lr=0.1, epochs=500)
    logger.info(f"[Platt] Fitted: a={a:.4f}, b={b:.4f}")

    # --- Apply to test set ---
    for p in test:
        p["platt_prob"] = apply_platt(p["raw_prob"], a, b)

    # Also apply to train for reference
    for p in train:
        p["platt_prob"] = apply_platt(p["raw_prob"], a, b)

    # ================================================================
    # METRICS
    # ================================================================

    def brier(preds, key="raw_prob"):
        if not preds: return float("nan")
        return sum((p[key] - p["actual"])**2 for p in preds) / len(preds)

    def calibration(preds, key="raw_prob"):
        buckets = []
        for lo in range(0, 100, 10):
            lo_f, hi_f = lo/100, (lo+10)/100
            bucket = [p for p in preds if lo_f <= p[key] < hi_f]
            if not bucket:
                buckets.append({"range": f"{lo}-{lo+10}%", "n": 0,
                    "avg_prob": None, "hit_rate": None, "gap": None})
                continue
            avg = sum(p[key] for p in bucket)/len(bucket)
            hr = sum(p["actual"] for p in bucket)/len(bucket)
            buckets.append({"range": f"{lo}-{lo+10}%", "n": len(bucket),
                "avg_prob": round(avg,4), "hit_rate": round(hr,4), "gap": round(hr-avg,4)})
        return buckets

    def edge_roi(preds, min_edge, prob_key, with_fee=False):
        """
        Flat $1 bet simulation.
        Fee model: Kalshi charges 7% on PROFIT only (not on loss).
        """
        bets = pnl_gross = pnl_net = wins = 0
        priced = [p for p in preds if p["kalshi_price"] and p["kalshi_price"] > 0]

        for p in priced:
            edge = p[prob_key] - p["kalshi_price"]

            if edge > min_edge:
                # Buy YES at kalshi_price
                bets += 1
                if p["actual"] == 1:
                    gross_profit = 1.0 - p["kalshi_price"]
                    fee = gross_profit * KALSHI_FEE_RATE if with_fee else 0
                    pnl_gross += gross_profit
                    pnl_net += gross_profit - fee
                    wins += 1
                else:
                    pnl_gross -= p["kalshi_price"]
                    pnl_net -= p["kalshi_price"]  # no fee on loss

            elif edge < -min_edge:
                # Buy NO at (1 - kalshi_price)
                bets += 1
                if p["actual"] == 0:
                    gross_profit = p["kalshi_price"]
                    fee = gross_profit * KALSHI_FEE_RATE if with_fee else 0
                    pnl_gross += gross_profit
                    pnl_net += gross_profit - fee
                    wins += 1
                else:
                    pnl_gross -= (1.0 - p["kalshi_price"])
                    pnl_net -= (1.0 - p["kalshi_price"])

        roi_gross = (pnl_gross / bets * 100) if bets else 0
        roi_net = (pnl_net / bets * 100) if bets else 0
        return {
            "bets": bets, "wins": wins,
            "pnl_gross": round(pnl_gross, 2), "roi_gross": round(roi_gross, 2),
            "pnl_net": round(pnl_net, 2), "roi_net": round(roi_net, 2),
        }

    # --- Test set metrics ---
    test_brier_raw = brier(test, "raw_prob")
    test_brier_platt = brier(test, "platt_prob")
    train_brier_raw = brier(train, "raw_prob")
    train_brier_platt = brier(train, "platt_prob")

    test_cal_raw = calibration(test, "raw_prob")
    test_cal_platt = calibration(test, "platt_prob")

    # By prop
    brier_by_prop = {}
    for prop in ["PTS", "REB", "AST"]:
        sub = [p for p in test if p["prop_type"] == prop]
        brier_by_prop[prop] = {
            "n": len(sub),
            "raw": round(brier(sub, "raw_prob"), 4),
            "platt": round(brier(sub, "platt_prob"), 4),
        }

    # Edge ROI — raw vs platt, gross vs net
    thresholds = [0.05, 0.10, 0.15, 0.20]
    roi_table = {}
    for t in thresholds:
        key = f">{t:.0%}"
        roi_table[key] = {
            "raw_gross": edge_roi(test, t, "raw_prob", with_fee=False),
            "raw_net": edge_roi(test, t, "raw_prob", with_fee=True),
            "platt_gross": edge_roi(test, t, "platt_prob", with_fee=False),
            "platt_net": edge_roi(test, t, "platt_prob", with_fee=True),
        }

    # ================================================================
    # Save report
    # ================================================================
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "platt_params": {"a": round(a, 4), "b": round(b, 4)},
        "train_size": len(train),
        "test_size": len(test),
        "train_date_range": f"{train[0]['game_date']} to {train[-1]['game_date']}",
        "test_date_range": f"{test[0]['game_date']} to {test[-1]['game_date']}",
        "test_brier_raw": round(test_brier_raw, 4),
        "test_brier_platt": round(test_brier_platt, 4),
        "brier_by_prop": brier_by_prop,
        "test_calibration_raw": test_cal_raw,
        "test_calibration_platt": test_cal_platt,
        "roi_table": roi_table,
        "kalshi_fee_rate": KALSHI_FEE_RATE,
    }
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # ================================================================
    # PRINT
    # ================================================================

    print()
    print("=" * 90)
    print(f"  PLATT SCALING BACKTEST: train={len(train)} / test={len(test)}")
    print("=" * 90)
    print()
    print(f"  Platt params:    a={a:.4f}, b={b:.4f}")
    print(f"  Train period:    {train[0]['game_date']} to {train[-1]['game_date']}")
    print(f"  Test period:     {test[0]['game_date']} to {test[-1]['game_date']}")
    print()

    # Brier
    print(f"  {'':30s} {'Raw':>10s} {'Platt':>10s} {'Delta':>10s}")
    print(f"  {'-'*60}")
    d = test_brier_platt - test_brier_raw
    status = "IMPROVED" if d < 0 else "WORSE"
    print(f"  {'TEST Brier':<30s} {test_brier_raw:>10.4f} {test_brier_platt:>10.4f} {d:>+10.4f}  [{status}]")
    print(f"  {'TRAIN Brier (ref)':<30s} {train_brier_raw:>10.4f} {train_brier_platt:>10.4f}")
    print()

    for prop, s in brier_by_prop.items():
        d = s["platt"] - s["raw"]
        print(f"  {prop+' (n='+str(s['n'])+')':<30s} {s['raw']:>10.4f} {s['platt']:>10.4f} {d:>+10.4f}")
    print()

    # Calibration comparison
    print("  CALIBRATION: TEST SET")
    print(f"  {'Bucket':>10s} | {'N':>5s} | {'Raw':>7s} | {'Actual':>7s} | {'RawGap':>7s} | {'Platt':>7s} | {'Actual':>7s} | {'PlattGap':>8s}")
    print("  " + "-" * 80)
    for j in range(10):
        r = test_cal_raw[j]
        p = test_cal_platt[j]
        if r["n"] == 0 and p["n"] == 0:
            continue
        rn = r["n"] or 0
        line = f"  {r['range']:>10s} | {rn:>5d} |"
        if r["n"]:
            line += f" {r['avg_prob']:>6.1%} | {r['hit_rate']:>6.1%} | {r['gap']:>+6.1%} |"
        else:
            line += f"    n/a |    n/a |    n/a |"
        if p["n"]:
            line += f" {p['avg_prob']:>6.1%} | {p['hit_rate']:>6.1%} | {p['gap']:>+7.1%}"
        else:
            line += f"    n/a |    n/a |      n/a"
        print(line)
    print("  " + "-" * 80)
    print()

    # Edge ROI
    priced_test = sum(1 for p in test if p.get("kalshi_price") and p["kalshi_price"] > 0)
    print(f"  EDGE ROI: TEST SET (n={priced_test} with prices, fee={KALSHI_FEE_RATE:.0%})")
    print(f"  {'Thresh':>8s} | {'Model':>6s} | {'Bets':>5s} | {'Wins':>5s} | {'Gross ROI':>10s} | {'Net ROI':>10s} | {'Net PnL':>9s}")
    print("  " + "-" * 75)
    for thresh, data in roi_table.items():
        for label, key_g, key_n in [("Raw", "raw_gross", "raw_net"), ("Platt", "platt_gross", "platt_net")]:
            g = data[key_g]
            n = data[key_n]
            if g["bets"] == 0:
                continue
            wr = g["wins"]/g["bets"]*100 if g["bets"] else 0
            print(
                f"  {thresh:>8s} | {label:>6s} | {g['bets']:>5d} | {g['wins']:>5d} | "
                f"{g['roi_gross']:>+9.1f}% | {n['roi_net']:>+9.1f}% | ${n['pnl_net']:>+8.2f}"
            )
    print("  " + "-" * 75)
    print()
    print(f"  Report: {REPORT_FILE}")
    print("=" * 90)


if __name__ == "__main__":
    main()
