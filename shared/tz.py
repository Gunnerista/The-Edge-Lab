"""
Timezone constants and date utilities for War Machine.
All scheduling and game-date logic uses US/Eastern (North Carolina).
Internal timestamps (DB, logs, API) remain UTC.
"""

from zoneinfo import ZoneInfo

ET = ZoneInfo("US/Eastern")
UTC = ZoneInfo("UTC")


def ticker_date_pattern(d):
    """
    Convert date to Kalshi ticker date segment: '26APR05' for 2026-04-05.
    ALWAYS zero-pads the day. Used by NBA and MLB ticker matching.
    """
    yy = str(d.year)[-2:]
    mon = d.strftime("%b").upper()
    dd = f"{d.day:02d}"
    return f"{yy}{mon}{dd}"
