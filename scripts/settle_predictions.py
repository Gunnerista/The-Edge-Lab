#!/usr/bin/env python3
"""
Settle Predictions — Compare forward test predictions against actual results
=============================================================================
Prediction Market War Machine

Reads prediction_log.jsonl, fetches actual box score stats via nba_api,
and updates each prediction with the actual result + accuracy metrics.

Usage:
    python scripts/settle_predictions.py                     # settle all unsettled
    python scripts/settle_predictions.py --date 2026-03-28   # settle specific date
    python scripts/settle_predictions.py --report            # just show report, no settle
"""

import sys
import json
import re
import time
import logging
import argparse
from datetime import datetime, timezone, date
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tz import ET

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "data" / "prediction_log.jsonl"

# Map prop_type to box score column
PROP_TO_STAT = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
}


def load_predictions() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def save_predictions(preds: list[dict]):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        for p in preds:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def fetch_box_scores(game_date: str) -> dict:
    """
    Fetch all player box scores for a given date.
    Returns: {player_name_lower: {PTS: x, REB: y, AST: z, ...}}
    """
    from nba_api.stats.endpoints import scoreboardv3
    from nba_api.live.nba.endpoints import boxscore as live_boxscore

    logger.info(f"[Settle] Fetching scoreboard for {game_date} (V3)")
    time.sleep(0.6)
    sb = scoreboardv3.ScoreboardV3(game_date=game_date)
    data = sb.get_dict()
    scoreboard = data.get("scoreboard", data)
    games = scoreboard.get("games", [])

    if not games:
        logger.warning(f"[Settle] No games found for {game_date}")
        return {}

    all_stats = {}

    for game in games:
        game_id = game.get("gameId", "")
        game_status = game.get("gameStatus", 0)

        away = game.get("awayTeam", {}).get("teamTricode", "")
        home = game.get("homeTeam", {}).get("teamTricode", "")

        # Only process completed games (status 3)
        if int(game_status) != 3:
            logger.info(f"[Settle] Game {game_id} {away}@{home} not final (status={game_status}), skipping")
            continue

        logger.info(f"[Settle] Fetching live box score for {game_id} ({away}@{home})")
        time.sleep(0.6)
        try:
            box = live_boxscore.BoxScore(game_id=game_id)
            box_data = box.get_dict()
            game_data = box_data.get("game", {})

            for team_key in ["homeTeam", "awayTeam"]:
                team = game_data.get(team_key, {})
                for p in team.get("players", []):
                    stats = p.get("statistics", {})
                    if not stats:
                        continue
                    name = p.get("name", p.get("nameI", ""))
                    if not name:
                        continue
                    all_stats[name.lower()] = {
                        "PTS": int(stats.get("points", 0)),
                        "REB": int(stats.get("reboundsTotal", 0)),
                        "AST": int(stats.get("assists", 0)),
                        "MIN": stats.get("minutesCalculated", ""),
                        "GAME_ID": game_id,
                    }
        except Exception as e:
            logger.warning(f"[Settle] Failed to fetch box score for {game_id}: {e}")

    return all_stats


def settle(target_date: str = None):
    """Settle unsettled predictions by fetching actual results."""
    preds = load_predictions()
    if not preds:
        print("No predictions found.")
        return

    # Group unsettled by date
    unsettled_dates = set()
    for p in preds:
        if not p.get("settled", False):
            if target_date and p["game_date"] != target_date:
                continue
            unsettled_dates.add(p["game_date"])

    if not unsettled_dates:
        print("All predictions already settled.")
        return

    logger.info(f"[Settle] Settling dates: {sorted(unsettled_dates)}")

    # Fetch box scores per date
    box_scores_by_date = {}
    for d in sorted(unsettled_dates):
        box_scores_by_date[d] = fetch_box_scores(d)

    # Settle each prediction
    settled_count = 0
    not_found = 0

    for p in preds:
        if p.get("settled", False):
            continue
        if target_date and p["game_date"] != target_date:
            continue

        box_scores = box_scores_by_date.get(p["game_date"], {})
        if not box_scores:
            continue

        player_key = p["player"].lower()
        stats = box_scores.get(player_key)

        if not stats:
            # Try fuzzy match
            for name, s in box_scores.items():
                if player_key.split()[-1] in name:
                    stats = s
                    break

        if not stats:
            not_found += 1
            logger.debug(f"[Settle] Player not in box score: {p['player']}")
            continue

        stat_col = PROP_TO_STAT.get(p["prop_type"])
        if not stat_col:
            continue

        actual_value = stats[stat_col]
        hit_over = actual_value > p["line"]

        p["actual_result"] = "yes" if hit_over else "no"
        p["actual_value"] = actual_value
        p["settled"] = True
        p["settled_at"] = datetime.now(timezone.utc).isoformat()

        # Model said over (prob > 0.5) or under (prob < 0.5)?
        model_said_over = p["model_prob"] > 0.5
        p["model_correct"] = model_said_over == hit_over

        settled_count += 1

    save_predictions(preds)
    logger.info(f"[Settle] Settled {settled_count}, not found {not_found}")

    # Print report
    print_report(preds)


def print_report(preds: list[dict] = None):
    """Print accuracy report from prediction log."""
    if preds is None:
        preds = load_predictions()

    settled = [p for p in preds if p.get("settled", False)]
    unsettled = [p for p in preds if not p.get("settled", False)]

    print()
    print("=" * 85)
    print(f"  FORWARD TEST REPORT - {len(settled)} settled / {len(unsettled)} pending")
    print("=" * 85)

    if not settled:
        print("  No settled predictions yet.")
        print()
        return

    # Overall accuracy
    correct = [p for p in settled if p.get("model_correct")]
    accuracy = len(correct) / len(settled) * 100

    print(f"  Overall accuracy: {len(correct)}/{len(settled)} = {accuracy:.1f}%")
    print()

    # By player
    by_player = defaultdict(list)
    for p in settled:
        by_player[p["player"]].append(p)

    for player, entries in sorted(by_player.items()):
        correct_n = sum(1 for e in entries if e.get("model_correct"))
        edges = [e["edge"] for e in entries if e.get("edge") is not None]
        avg_edge = sum(edges) / len(edges) if edges else 0
        print(f"  {player:20s} | {correct_n}/{len(entries)} correct | avg edge: {avg_edge:+.1%}")

        for e in entries:
            marker = "OK" if e.get("model_correct") else "XX"
            actual = e.get("actual_value", "?")
            kalshi_str = f"{e['kalshi_price']:.1%}" if e.get("kalshi_price") is not None else "n/a"
            print(
                f"    [{marker}] {e['prop_type']:8s} {e['line']:5.1f} "
                f"| model={e['model_prob']:.1%} kalshi={kalshi_str} "
                f"| actual={actual} ({'HIT' if e.get('actual_result') == 'yes' else 'MISS'})"
            )
        print()

    # Calibration buckets
    print("  --- Calibration ---")
    buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in buckets:
        bucket = [p for p in settled if lo <= p["model_prob"] < hi]
        if not bucket:
            continue
        hit_rate = sum(1 for p in bucket if p.get("actual_result") == "yes") / len(bucket)
        avg_prob = sum(p["model_prob"] for p in bucket) / len(bucket)
        print(f"  model_prob [{lo:.0%}-{hi:.0%}): n={len(bucket):2d}, hit_rate={hit_rate:.1%}, avg_model={avg_prob:.1%}")

    # Edge-based: did positive edge props outperform?
    print()
    print("  --- Edge Performance ---")
    pos_edge = [p for p in settled if (p.get("edge") or 0) > 0.05]
    neg_edge = [p for p in settled if (p.get("edge") or 0) < -0.05]
    neutral = [p for p in settled if -0.05 <= (p.get("edge") or 0) <= 0.05]

    for label, group in [("Edge > +5%", pos_edge), ("|Edge| <= 5%", neutral), ("Edge < -5%", neg_edge)]:
        if not group:
            continue
        acc = sum(1 for p in group if p.get("model_correct")) / len(group) * 100
        print(f"  {label:15s}: {len(group):2d} props, accuracy={acc:.0f}%")

    print("=" * 85)
    print()


def main():
    parser = argparse.ArgumentParser(description="Settle NBA Prop Predictions")
    parser.add_argument("--date", type=str, help="Settle specific date (YYYY-MM-DD)")
    parser.add_argument("--report", action="store_true", help="Show report only (no settle)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.report:
        print_report()
    else:
        settle(target_date=args.date)


if __name__ == "__main__":
    main()
