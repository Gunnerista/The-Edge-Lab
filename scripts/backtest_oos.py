#!/usr/bin/env python3
"""
Out-of-Sample Rolling Window Backtest
=======================================
Prediction Market War Machine

True OOS backtest: for each market, only uses player stats from BEFORE the game date.

Steps:
  1. Fetch full-season game logs for all players appearing in settled markets
  2. For each settled market (Jan 1+), compute rolling stats up to game_date - 1
  3. Generate prediction using only historical data
  4. Compare all metrics vs in-sample results

Usage:
    python scripts/backtest_oos.py
    python scripts/backtest_oos.py --skip-fetch   # reuse cached game logs
"""

import sys
import json
import math
import sqlite3
import logging
import argparse
import time
import re
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_DB = PROJECT_ROOT / "data" / "market_data.db"
NBA_DB = PROJECT_ROOT / "data" / "nba_data.db"
GAMELOG_CACHE = PROJECT_ROOT / "data" / "gamelogs_cache.json"
REPORT_FILE = PROJECT_ROOT / "data" / "backtest_oos_report.json"
IS_REPORT = PROJECT_ROOT / "data" / "backtest_report.json"

# ============================================================================
# Model constants (mirroring nba_model.py)
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


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_over(projected: float, std_dev: float, line: float) -> float:
    if std_dev <= 0:
        return 1.0 if projected > line else 0.0
    z = (line - projected) / std_dev
    return 1.0 - _norm_cdf(z)


# ============================================================================
# Step 1: Fetch game logs
# ============================================================================

def resolve_players(db_path: Path) -> dict:
    """Map player_segment from tickers to (player_name, player_id, team_abbr)."""
    conn_market = sqlite3.connect(str(MARKET_DB))
    conn_nba = sqlite3.connect(str(db_path))
    conn_nba.row_factory = sqlite3.Row

    # Get unique player segments from settled tickers
    rows = conn_market.execute("""
        SELECT DISTINCT ticker FROM settled_markets
        WHERE (ticker LIKE 'KXNBAPTS%' OR ticker LIKE 'KXNBAREB%' OR ticker LIKE 'KXNBAAST%')
        AND close_time >= '2026-01-01'
    """).fetchall()

    segments = set()
    for (ticker,) in rows:
        parts = ticker.split("-")
        if len(parts) >= 3:
            segments.add(parts[2])

    # Parse each segment and match to DB
    player_map = {}  # segment -> {name, id, team}

    for seg in segments:
        # Pattern: {TEAM3}{INITIAL}{LASTNAME}{JERSEY}
        m = re.match(r'([A-Z]{3})([A-Z])([A-Z]+?)(\d+)$', seg.upper())
        if not m:
            # Try without jersey number (edge case like SACDSABONISC)
            m = re.match(r'([A-Z]{3})([A-Z])([A-Z]+?)([A-Z]?\d*)$', seg.upper())
            if not m:
                continue

        team = m.group(1)
        last_name = m.group(3).capitalize()

        # Find in nba_players
        row = conn_nba.execute(
            """SELECT player_id, player_name, team_abbr FROM nba_players
               WHERE player_name LIKE ? AND season = '2025-26' AND games_played >= 5
               LIMIT 1""",
            (f"% {last_name}%",)
        ).fetchone()

        if row:
            player_map[seg] = {
                "player_id": row["player_id"],
                "player_name": row["player_name"],
                "team_abbr": row["team_abbr"],
            }

    conn_market.close()
    conn_nba.close()

    logger.info(f"[OOS] Resolved {len(player_map)}/{len(segments)} player segments")
    return player_map


def fetch_game_logs(player_map: dict) -> dict:
    """Fetch full-season game logs for all players via nba_api."""
    from nba_api.stats.endpoints import playergamelog

    all_logs = {}  # player_id -> [{"date": ..., "PTS": ..., ...}, ...]
    total = len(set(p["player_id"] for p in player_map.values()))
    done_ids = set()
    i = 0

    for seg, info in player_map.items():
        pid = info["player_id"]
        if pid in done_ids:
            continue
        done_ids.add(pid)
        i += 1

        if i % 20 == 0:
            logger.info(f"[OOS] Fetching game logs: {i}/{total}")

        try:
            time.sleep(0.6)
            log = playergamelog.PlayerGameLog(player_id=pid, season="2025-26")
            df = log.get_data_frames()[0]

            games = []
            for _, row in df.iterrows():
                game_date = datetime.strptime(row["GAME_DATE"], "%b %d, %Y").strftime("%Y-%m-%d")
                matchup = str(row.get("MATCHUP", ""))
                is_home = "vs." in matchup
                games.append({
                    "date": game_date,
                    "PTS": int(row["PTS"]) if row["PTS"] is not None else 0,
                    "REB": int(row["REB"]) if row["REB"] is not None else 0,
                    "AST": int(row["AST"]) if row["AST"] is not None else 0,
                    "MIN": float(str(row["MIN"]).split(":")[0]) if row["MIN"] else 0,
                    "is_home": is_home,
                })

            all_logs[str(pid)] = sorted(games, key=lambda g: g["date"])

        except Exception as e:
            logger.warning(f"[OOS] Failed to fetch {info['player_name']}: {e}")

    logger.info(f"[OOS] Fetched game logs for {len(all_logs)} players")
    return all_logs


# ============================================================================
# Step 2: Rolling stats computation
# ============================================================================

def compute_rolling_stats(games: list[dict], before_date: str, prop_type: str) -> dict | None:
    """Compute player stats using only games before the given date."""
    stat_key = {"points": "PTS", "rebounds": "REB", "assists": "AST"}[prop_type]

    prior_games = [g for g in games if g["date"] < before_date]

    if len(prior_games) < MIN_GAMES:
        return None

    # Season averages
    vals = [g[stat_key] for g in prior_games]
    season_avg = sum(vals) / len(vals)

    # Recent 10 games
    recent = prior_games[-10:]
    recent_vals = [g[stat_key] for g in recent]
    recent_avg = sum(recent_vals) / len(recent_vals)
    if len(recent_vals) > 1:
        mean = recent_avg
        recent_std = (sum((v - mean) ** 2 for v in recent_vals) / (len(recent_vals) - 1)) ** 0.5
    else:
        recent_std = season_avg * 0.25

    # Home/away splits
    home_games = [g for g in prior_games if g["is_home"]]
    away_games = [g for g in prior_games if not g["is_home"]]
    home_avg = sum(g[stat_key] for g in home_games) / len(home_games) if home_games else season_avg
    away_avg = sum(g[stat_key] for g in away_games) / len(away_games) if away_games else season_avg

    return {
        "season_avg": season_avg,
        "recent_avg": recent_avg,
        "recent_std": recent_std,
        "home_avg": home_avg,
        "away_avg": away_avg,
        "games_played": len(prior_games),
    }


# ============================================================================
# Step 3: Prediction using rolling stats
# ============================================================================

def predict_rolling(
    stats: dict,
    prop_type: str,
    line: float,
    is_home: bool,
    opp_team: dict | None,
    is_b2b: bool,
) -> dict:
    """Generate prediction from rolling stats (mirrors nba_model._project)."""

    season_avg = stats["season_avg"]
    recent_avg = stats["recent_avg"]
    recent_std = stats["recent_std"]
    ha_avg = stats["home_avg"] if is_home else stats["away_avg"]

    # Weighted base
    base = SEASON_WEIGHT * season_avg + RECENT_WEIGHT * recent_avg + HOME_AWAY_WEIGHT * ha_avg

    # Matchup adjustment (use current team stats - slight leak but team stats are stable)
    matchup_adj = 0.0
    pace_adj = 0.0
    opp_col = {"points": "opp_pts_pg", "rebounds": "opp_reb_pg", "assists": "opp_ast_pg"}[prop_type]
    league_avg = LEAGUE_AVG[prop_type]

    if opp_team:
        opp_allowed = float(opp_team.get(opp_col, 0))
        if opp_allowed > 0:
            matchup_adj = max(-MAX_MATCHUP_ADJUSTMENT,
                              min(MAX_MATCHUP_ADJUSTMENT, opp_allowed / league_avg - 1.0))
        opp_pace = float(opp_team.get("pace", 0))
        if opp_pace > 0:
            pace_adj = (opp_pace / LEAGUE_AVG_PACE - 1.0) * PACE_ADJUSTMENT_FACTOR

    b2b_adj = -MAX_B2B_PENALTY if is_b2b else 0.0

    projected = base * (1.0 + matchup_adj + b2b_adj + pace_adj)

    std_dev = max(recent_std, MIN_STD[prop_type])
    if is_b2b:
        std_dev *= 1.15
    if stats["games_played"] < 20:
        std_dev *= 1.10

    model_prob = prob_over(projected, std_dev, line)
    model_prob = max(0.02, min(0.98, model_prob))

    gp = stats["games_played"]
    has_recent = recent_avg > 0
    has_matchup = opp_team is not None
    if gp >= 30 and has_recent and has_matchup:
        confidence = "high"
    elif gp >= 15 and (has_recent or has_matchup):
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "model_prob": round(model_prob, 4),
        "projected": round(projected, 2),
        "std_dev": round(std_dev, 2),
        "confidence": confidence,
        "games_used": gp,
    }


# ============================================================================
# Step 4: Parse tickers + run backtest
# ============================================================================

TEAM_ABBRS = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN",
    "DET", "GSW", "HOU", "IND", "LAC", "LAL", "MEM", "MIA",
    "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX",
    "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
}

PROP_PREFIX_MAP = {"KXNBAPTS": "points", "KXNBAREB": "rebounds", "KXNBAAST": "assists"}


def parse_ticker_light(ticker: str):
    """Fast ticker parser returning (prop_type, game_date, player_seg, away, home, line)."""
    parts = ticker.upper().split("-")
    if len(parts) < 4:
        return None

    prefix = parts[0]
    prop_type = PROP_PREFIX_MAP.get(prefix)
    if not prop_type:
        return None

    game_seg = parts[1]
    player_seg = parts[2]

    try:
        line = float(parts[3]) - 0.5
    except ValueError:
        return None

    # Parse game date and teams
    m = re.match(r'(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})', game_seg)
    if not m:
        return None

    yy, mon_str, dd = m.group(1), m.group(2), m.group(3)
    away_abbr, home_abbr = m.group(4), m.group(5)

    months = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5,
              "OCT": 10, "NOV": 11, "DEC": 12}
    mon = months.get(mon_str, 0)
    if mon == 0:
        return None

    year = 2000 + int(yy)
    game_date = f"{year}-{mon:02d}-{int(dd):02d}"

    # Parse player team from player segment
    pm = re.match(r'([A-Z]{3})', player_seg)
    player_team = pm.group(1) if pm else ""

    is_home = (player_team == home_abbr)
    opponent = away_abbr if is_home else home_abbr

    return {
        "prop_type": prop_type,
        "game_date": game_date,
        "player_seg": player_seg,
        "player_team": player_team,
        "opponent": opponent,
        "is_home": is_home,
        "line": line,
    }


def load_team_data() -> dict:
    """Load current team stats from nba_data.db."""
    conn = sqlite3.connect(str(NBA_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM nba_teams WHERE season = '2025-26'").fetchall()
    teams = {row["team_abbr"]: dict(row) for row in rows}
    conn.close()
    return teams


def run_oos_backtest(skip_fetch: bool = False):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Resolve players
    player_map = resolve_players(NBA_DB)

    # Fetch or load game logs
    if skip_fetch and GAMELOG_CACHE.exists():
        logger.info("[OOS] Loading cached game logs...")
        with open(GAMELOG_CACHE, "r", encoding="utf-8") as f:
            all_logs = json.load(f)
        logger.info(f"[OOS] Loaded {len(all_logs)} player game logs from cache")
    else:
        logger.info("[OOS] Fetching game logs from nba_api...")
        all_logs = fetch_game_logs(player_map)
        GAMELOG_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(GAMELOG_CACHE, "w", encoding="utf-8") as f:
            json.dump(all_logs, f)
        logger.info(f"[OOS] Cached game logs to {GAMELOG_CACHE}")

    # Build player_seg -> player_id lookup
    seg_to_pid = {seg: str(info["player_id"]) for seg, info in player_map.items()}

    # Load team data (current - slight leak for matchup, acceptable)
    teams = load_team_data()

    # Load settled markets (Jan 1+)
    conn = sqlite3.connect(str(MARKET_DB))
    conn.row_factory = sqlite3.Row
    markets = conn.execute("""
        SELECT s.ticker, s.title, s.result, s.close_time,
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
    conn.close()
    markets = [dict(r) for r in markets]

    logger.info(f"[OOS] Processing {len(markets)} markets (Jan 1+)")

    # B2B detection from game logs
    def check_b2b(player_seg: str, game_date: str) -> bool:
        pid = seg_to_pid.get(player_seg)
        if not pid or pid not in all_logs:
            return False
        prev_date = (date.fromisoformat(game_date) - timedelta(days=1)).isoformat()
        return any(g["date"] == prev_date for g in all_logs[pid])

    # Run predictions
    results = []
    skipped = 0
    skip_reasons = defaultdict(int)
    t0 = time.time()

    for i, mkt in enumerate(markets):
        if (i + 1) % 5000 == 0:
            elapsed = time.time() - t0
            logger.info(
                f"[OOS] {i+1}/{len(markets)} ({len(results)} predictions, {skipped} skipped, "
                f"{elapsed:.0f}s)"
            )

        parsed = parse_ticker_light(mkt["ticker"])
        if not parsed:
            skipped += 1
            skip_reasons["parse_fail"] += 1
            continue

        pid = seg_to_pid.get(parsed["player_seg"])
        if not pid or pid not in all_logs:
            skipped += 1
            skip_reasons["no_gamelog"] += 1
            continue

        games = all_logs[pid]
        stats = compute_rolling_stats(games, parsed["game_date"], parsed["prop_type"])

        if not stats:
            skipped += 1
            skip_reasons["insufficient_games"] += 1
            continue

        opp_team = teams.get(parsed["opponent"])
        is_b2b = check_b2b(parsed["player_seg"], parsed["game_date"])

        pred = predict_rolling(
            stats, parsed["prop_type"], parsed["line"],
            parsed["is_home"], opp_team, is_b2b,
        )

        actual_binary = 1 if mkt["result"] == "yes" else 0
        prop_label = {"points": "PTS", "rebounds": "REB", "assists": "AST"}[parsed["prop_type"]]

        results.append({
            "ticker": mkt["ticker"],
            "prop_type": prop_label,
            "model_prob": pred["model_prob"],
            "actual": actual_binary,
            "kalshi_price": mkt.get("kalshi_price"),
            "confidence": pred["confidence"],
            "line": parsed["line"],
            "projected": pred["projected"],
            "games_used": pred["games_used"],
            "game_date": parsed["game_date"],
        })

    elapsed = time.time() - t0
    logger.info(f"[OOS] Done in {elapsed:.1f}s: {len(results)} predictions, {skipped} skipped")

    # ================================================================
    # METRICS (identical to backtest.py)
    # ================================================================

    def brier(preds):
        if not preds:
            return float("nan")
        return sum((p["model_prob"] - p["actual"]) ** 2 for p in preds) / len(preds)

    overall_brier = brier(results)

    brier_by_prop = {}
    for prop in ["PTS", "REB", "AST"]:
        subset = [r for r in results if r["prop_type"] == prop]
        brier_by_prop[prop] = {"brier": round(brier(subset), 4), "n": len(subset)}

    # Calibration
    buckets = []
    for lo in range(0, 100, 10):
        hi = lo + 10
        lo_f, hi_f = lo / 100, hi / 100
        bucket = [r for r in results if lo_f <= r["model_prob"] < hi_f]
        if not bucket:
            buckets.append({"range": f"{lo}-{hi}%", "n": 0,
                            "avg_model_prob": None, "actual_hit_rate": None, "gap": None})
            continue
        avg_prob = sum(r["model_prob"] for r in bucket) / len(bucket)
        hit_rate = sum(r["actual"] for r in bucket) / len(bucket)
        buckets.append({
            "range": f"{lo}-{hi}%", "n": len(bucket),
            "avg_model_prob": round(avg_prob, 4),
            "actual_hit_rate": round(hit_rate, 4),
            "gap": round(hit_rate - avg_prob, 4),
        })

    # Edge ROI
    priced = [r for r in results if r["kalshi_price"] is not None and r["kalshi_price"] > 0]

    def edge_roi(preds, min_edge):
        bets = pnl = wins = 0
        for p in preds:
            edge = p["model_prob"] - p["kalshi_price"]
            if edge > min_edge:
                bets += 1
                if p["actual"] == 1:
                    pnl += (1.0 - p["kalshi_price"]); wins += 1
                else:
                    pnl -= p["kalshi_price"]
            elif edge < -min_edge:
                bets += 1
                if p["actual"] == 0:
                    pnl += p["kalshi_price"]; wins += 1
                else:
                    pnl -= (1.0 - p["kalshi_price"])
        roi = (pnl / bets * 100) if bets > 0 else 0
        return {"bets": bets, "wins": wins, "pnl": round(pnl, 2), "roi": round(roi, 2)}

    edge_results = {}
    for t in [0.05, 0.10, 0.15, 0.20]:
        edge_results[f">{t:.0%}"] = edge_roi(priced, t)

    # Confidence breakdown
    conf_stats = {}
    for conf in ["high", "medium", "low"]:
        subset = [r for r in results if r["confidence"] == conf]
        if subset:
            conf_stats[conf] = {
                "n": len(subset),
                "brier": round(brier(subset), 4),
            }

    # Monthly Brier
    monthly = defaultdict(list)
    for r in results:
        m = r["game_date"][:7]
        monthly[m].append(r)
    monthly_brier = {m: round(brier(preds), 4) for m, preds in sorted(monthly.items())}

    # Save report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "out_of_sample_rolling",
        "total_markets": len(markets),
        "predictions": len(results),
        "skipped": skipped,
        "skip_reasons": dict(skip_reasons),
        "elapsed_seconds": round(elapsed, 1),
        "overall_brier": round(overall_brier, 4),
        "brier_by_prop": brier_by_prop,
        "calibration": buckets,
        "edge_roi": edge_results,
        "priced_markets": len(priced),
        "confidence_breakdown": conf_stats,
        "monthly_brier": monthly_brier,
    }

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # ================================================================
    # Load in-sample for comparison
    # ================================================================
    is_data = None
    if IS_REPORT.exists():
        with open(IS_REPORT, "r", encoding="utf-8") as f:
            is_data = json.load(f)

    # ================================================================
    # PRINT
    # ================================================================
    print()
    print("=" * 85)
    print(f"  OUT-OF-SAMPLE ROLLING BACKTEST: {len(results)} predictions")
    print("=" * 85)
    print()
    print(f"  Markets loaded:  {len(markets)} (Jan 1+ only)")
    print(f"  Predictions:     {len(results)}")
    print(f"  Skipped:         {skipped} ({', '.join(f'{k}={v}' for k,v in skip_reasons.items())})")
    print(f"  Time:            {elapsed:.1f}s")
    print()

    # Brier comparison
    is_brier = is_data["overall_brier"] if is_data else "n/a"
    target = "PASS" if overall_brier < 0.20 else "FAIL"
    print(f"  {'Metric':<25s} {'OOS':>10s} {'In-Sample':>10s} {'Delta':>10s}")
    print(f"  {'-'*55}")
    if is_data:
        delta = overall_brier - is_data["overall_brier"]
        print(f"  {'OVERALL BRIER':<25s} {overall_brier:>10.4f} {is_data['overall_brier']:>10.4f} {delta:>+10.4f}  [{target}]")
    else:
        print(f"  {'OVERALL BRIER':<25s} {overall_brier:>10.4f} {'n/a':>10s}  [{target}]")

    for prop in ["PTS", "REB", "AST"]:
        oos_b = brier_by_prop[prop]["brier"]
        oos_n = brier_by_prop[prop]["n"]
        if is_data and prop in is_data.get("brier_by_prop", {}):
            is_b = is_data["brier_by_prop"][prop]["brier"]
            d = oos_b - is_b
            print(f"  {prop + ' (n=' + str(oos_n) + ')':<25s} {oos_b:>10.4f} {is_b:>10.4f} {d:>+10.4f}")
        else:
            print(f"  {prop + ' (n=' + str(oos_n) + ')':<25s} {oos_b:>10.4f}")
    print()

    # Calibration
    print("  CALIBRATION TABLE")
    print(f"  {'Bucket':>10s} | {'N':>6s} | {'Model':>7s} | {'Actual':>7s} | {'Gap':>7s} |", end="")
    if is_data:
        print(f" {'IS Gap':>7s}")
    else:
        print()
    print("  " + "-" * 70)
    for j, b in enumerate(buckets):
        if b["n"] == 0:
            continue
        line = (
            f"  {b['range']:>10s} | {b['n']:>6d} | "
            f"{b['avg_model_prob']:>6.1%} | "
            f"{b['actual_hit_rate']:>6.1%} | "
            f"{b['gap']:>+6.1%} |"
        )
        if is_data and j < len(is_data.get("calibration", [])):
            is_b = is_data["calibration"][j]
            if is_b.get("gap") is not None:
                line += f" {is_b['gap']:>+6.1%}"
        print(line)
    print("  " + "-" * 70)
    print()

    # Edge ROI comparison
    print(f"  EDGE ROI (n={len(priced)} priced markets)")
    print(f"  {'Threshold':>12s} | {'Bets':>5s} | {'Wins':>5s} | {'PnL':>8s} | {'ROI':>7s} |", end="")
    if is_data:
        print(f" {'IS ROI':>7s}")
    else:
        print()
    print("  " + "-" * 65)
    for thresh, stats in edge_results.items():
        pnl_str = f"${stats['pnl']:+.2f}"
        line = (
            f"  {thresh:>12s} | {stats['bets']:>5d} | {stats['wins']:>5d} | "
            f"{pnl_str:>8s} | {stats['roi']:>+6.1f}% |"
        )
        if is_data and thresh in is_data.get("edge_roi", {}):
            is_roi = is_data["edge_roi"][thresh]["roi"]
            line += f" {is_roi:>+6.1f}%"
        print(line)
    print("  " + "-" * 65)
    print()

    # Monthly Brier
    print("  MONTHLY BRIER SCORE")
    for m, b in monthly_brier.items():
        n = len(monthly[m])
        print(f"    {m}: {b:.4f}  (n={n})")
    print()

    # Confidence
    print("  CONFIDENCE BREAKDOWN")
    for conf, stats in conf_stats.items():
        print(f"    {conf:>6s}: n={stats['n']:>5d}, brier={stats['brier']:.4f}")

    print()
    print(f"  Report: {REPORT_FILE}")
    print("=" * 85)


def main():
    parser = argparse.ArgumentParser(description="OOS Rolling Window Backtest")
    parser.add_argument("--skip-fetch", action="store_true", help="Reuse cached game logs")
    args = parser.parse_args()
    run_oos_backtest(skip_fetch=args.skip_fetch)


if __name__ == "__main__":
    main()
