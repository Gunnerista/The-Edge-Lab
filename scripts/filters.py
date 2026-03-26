#!/usr/bin/env python3
"""
Filters - Market filtering utilities for the War Machine.
==========================================================
Shared by signal_engine, active_scanner, and main.py.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional


def is_within_settlement_window(
    expiration_time: str,
    close_time: str = "",
    max_hours: int = 72,
) -> bool:
    """
    Check if a market settles within the given time window.

    Args:
        expiration_time: ISO 8601 or Kalshi format expiration time
        close_time: ISO 8601 close time (alternative)
        max_hours: Maximum hours until settlement (default 72)

    Returns True if market settles within max_hours from now.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=max_hours)

    for ts_str in [expiration_time, close_time]:
        if not ts_str:
            continue
        try:
            # Handle various formats
            ts_str = ts_str.replace("Z", "+00:00")
            if "+" not in ts_str and "-" not in ts_str[10:]:
                ts_str += "+00:00"
            dt = datetime.fromisoformat(ts_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            # Must be in the future AND within the window
            if dt > now and dt <= cutoff:
                return True
            elif dt > cutoff:
                return False
        except (ValueError, TypeError):
            continue

    # If no valid timestamp, reject (don't trade what we can't verify)
    return False


def filter_markets_by_settlement(
    markets: list,
    max_hours: int = 72,
    expiration_key: str = "expiration_time",
    close_key: str = "close_time",
) -> list:
    """Filter a list of market dicts to only those settling within max_hours."""
    return [
        m for m in markets
        if is_within_settlement_window(
            m.get(expiration_key, ""),
            m.get(close_key, ""),
            max_hours,
        )
    ]
