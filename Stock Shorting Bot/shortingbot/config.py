"""Every tunable number lives here. Nothing else in the package hardcodes a threshold."""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT, ".cache")
LOG_DIR = os.path.join(ROOT, "logs")

# ---------------------------------------------------------------- screen
MIN_MARKET_CAP = 25_000_000      # market cap must be above this
MIN_PREV_CLOSE = 3.00            # previous day's close must be above this
SCAN_POOL = 100                  # rows pulled from TradingView before filtering
TOP_N = 20                       # gainers that get analysed
EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]

# ---------------------------------------------------------------- execution
N_POSITIONS = 5
SIZE_PCT_MAX = 19.0              # rank 1 gets this % of equity
SIZE_PCT_MIN = 15.0              # rank N gets this %, linear in between
MARKET_TZ = "America/New_York"
# Times are relative to the exchange calendar's close, so half days
# (13:00 close) shift automatically: scan 12:45, trade 12:55.
SCAN_MINUTES_BEFORE_CLOSE = 15   # scrape + score at 15:45
TRADE_MINUTES_BEFORE_CLOSE = 5   # flatten + enter at 15:55
CUTOFF_MINUTES_BEFORE_CLOSE = 2  # refuse to send orders after 15:58
FILL_WAIT_SECONDS = 30           # wait this long for covers to fill before shorting
# Fresh start: fills before this date (the first bot's) are ignored.
TRACK_FROM = "2026-10-21"

# ---------------------------------------------------------------- scoring weights
# One block per checklist item. Weights renormalise when a block has no data.
WEIGHTS = {
    "move_volume": 35,   # 1. blow-off: big % up on heavy volume, upper wick, weak close
    "crowding": 20,      # 2. moderate SI / low DTC - shorts can exit, no squeeze fuel
    "ftd": 10,           # 4. no repeated giant fails-to-deliver
    "catalyst": 35,      # 5. hype > substance
}
# 3. borrow is a hard gate (Alpaca shortable + easy_to_borrow), not a weight.

# 1. Move & volume
CHANGE_RAMP = (10.0, 60.0)       # % up on the day: 10% -> 0, 60%+ -> 1
CHANGE_CAP = 100.0               # beyond this the fade is still good, no extra credit
RVOL_RAMP = (2.0, 8.0)           # relative volume: 2x -> 0, 8x+ -> 1
UPPER_WICK_RAMP = (0.05, 0.40)   # (high-close)/(high-low)
CLOSE_IN_RANGE_RAMP = (90.0, 40.0)   # inverted: closing at the high is bad for a fade
MOVE_SUBWEIGHTS = {"change": 40, "rvol": 25, "wick": 20, "close_in_range": 15}

# 2. Crowding
SQUEEZE_EXCLUDE_SI = 20.0        # SI % float above this AND
SQUEEZE_EXCLUDE_DTC = 10.0       # DTC above this -> excluded (squeeze trap)
SI_IDEAL = (5.0, 15.0)
DTC_IDEAL = (1.0, 5.0)
SI_DANGER = 30.0                 # SI score reaches 0 here
DTC_DANGER = 12.0                # DTC score reaches 0 here
TINY_SI_SCORE = 0.6              # SI below the ideal band: safe, but no forced-seller help

# 4. FTD - relative to float
FTD_MAX_PCT_FLOAT_RAMP = (0.2, 2.0)   # biggest single day: 0.2% -> fine, 2%+ -> bad
FTD_DAYS_RAMP = (3, 15)               # days with FTDs in the window

# 5. Catalyst
CATALYST_EXCLUDE = True          # strong de-risking news excludes the name outright
NEWS_LOOKBACK_HOURS = 36

# ---------------------------------------------------------------- data
SHORT_VOLUME_LOOKBACK_DAYS = 10
FTD_LOOKBACK_MONTHS = 2
REQUEST_TIMEOUT = 20
MAX_WORKERS = 4                  # yfinance throttles above this
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SEC_USER_AGENT = "StockShortingBot/1.0 (personal research)"
