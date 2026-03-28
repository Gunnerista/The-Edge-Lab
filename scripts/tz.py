"""
Timezone constants for War Machine.
All scheduling and game-date logic uses US/Eastern (North Carolina).
Internal timestamps (DB, logs, API) remain UTC.
"""

from zoneinfo import ZoneInfo

ET = ZoneInfo("US/Eastern")
UTC = ZoneInfo("UTC")
