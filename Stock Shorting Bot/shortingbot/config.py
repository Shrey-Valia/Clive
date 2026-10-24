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

# ---------------------------------------------------------------- strategy
# Which jobs daily.py runs, in order. One entry per strategy, so a long side can be
# added later without touching the short side.
#   "short_intraday"   v2: scan at 15:45 on T, short at the T+1 open, cover at T+1 15:55
#   "short_overnight"  v1: scan at 15:45, cover yesterday's and short the new top 5 at 15:55,
#                      hold overnight. Off: the 2016-2026 backtest lost money in 10 of 11 years
#                      (research/PROPOSAL.md). To switch back see the README.
STRATEGIES = ["short_intraday"]
STRATEGY_VERSION = "intraday_v2"         # written to every short_runs row

# ---------------------------------------------------------------- selection gates (research/PROPOSAL.md)
MIN_SHORT_SCORE = 70.0           # trade only names scoring >= this; fewer than 5 (or none) is fine. v1: 0
MIN_PRICE_AT_SCAN = 10.00        # 15:45 price >= this. v1: 0 (MIN_PREV_CLOSE was the only price gate)
MIN_ADV20_USD = 10_000_000       # 20-day average dollar volume, Alpaca daily bars, today excluded. v1: 0
ADV20_DAYS = 20
ADV20_MIN_DAYS = 10              # fewer bars than this -> no ADV20 -> excluded (as in the backtest)

# ---------------------------------------------------------------- execution
N_POSITIONS = 5
SIZE_PCT_PER_NAME = 5.0          # equal size per name, % of equity. The one sizing knob in v2 (was 10.0).
                                 # None = linear from SIZE_PCT_MAX (rank 1) to SIZE_PCT_MIN (rank N), as v1
SIZE_PCT_MAX = SIZE_PCT_PER_NAME # v1: 19.0 (with SIZE_PCT_PER_NAME = None)
SIZE_PCT_MIN = SIZE_PCT_PER_NAME # v1: 15.0
MAX_GROSS_PCT = 50.0             # total short notional never above this % of equity. v1: 100
# Squeeze-cost cap: no short is sized so that a SQUEEZE_MOVE_PCT move against it costs more than
# MAX_SQUEEZE_LOSS_PCT of equity, i.e. size <= MAX_SQUEEZE_LOSS_PCT / (SQUEEZE_MOVE_PCT / 100).
# 5% / (100% / 100) = 5% of equity per name. With the +35% broker stop the expected worst case
# per name is ~5% x 35% = 1.75% of equity, plus gap risk (a gap through the stop fills at the gap).
# None = off.
MAX_SQUEEZE_LOSS_PCT = 5.0
SQUEEZE_MOVE_PCT = 100.0
# Diversification: at most this many picks per TradingView industry (sector when the industry is
# missing), in rank order, after the other gates. None or 0 = off. Applies to the long sleeve too
# (it buys the same picks).
MAX_PER_INDUSTRY = 2
# Earnings gate: exclude a name that reports after the close on the scan day or on either of the
# next EARNINGS_SESSIONS sessions (the trade date and the one after). Sources: Supabase
# market_events (kind EARNINGS, events/sync_events.py) and the Nasdaq earnings calendar, cached
# for the day in .cache/earnings/. Both sources failing excludes the name (fail closed).
EARNINGS_GATE = True
EARNINGS_SESSIONS = 2
EARNINGS_NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings"
EARNINGS_REQUIRE_NASDAQ = True   # True: a name is cleared only if Nasdaq answered or market_events covers
                                 # it (an EARNINGS row on or after the scan day). market_events only covers
                                 # names in recent picks/positions, so it can miss new gainers.
                                 # False: either source answering clears every name (the older rule)

# Broker-side protective stop on each 09:31 short: a DAY buy-stop at fill x (1 + pct/100),
# cancelled before the 15:55 cover. None = off. On since 2026-10-23 (owner-approved; the backtest
# found stops lowered Sharpe, but they cap a squeeze). DAY, not GTC, so a stop can never outlive
# the cover and open a long.
BROKER_STOP_PCT = 35

# Account-level halts. None = off.
DAILY_LOSS_HALT_PCT = None       # account down this % on the day (equity vs last_equity): flatten,
                                 # no new entries for the rest of the day. Checked at 09:31 and by monitor.py
DRAWDOWN_HALT_PCT = None         # equity this % under its peak (logs/equity_peak.json): no new entries
# State: logs/halt.json (today's halt) and logs/equity_peak.json. Delete the peak file to reset it.
MARKET_TZ = "America/New_York"
# Times are relative to the exchange calendar's close, so half days
# (13:00 close) shift automatically: scan 12:45, trade 12:55.
SCAN_MINUTES_BEFORE_CLOSE = 15   # scrape + score at 15:45
TRADE_MINUTES_BEFORE_CLOSE = 5   # flatten + enter at 15:55
CUTOFF_MINUTES_BEFORE_CLOSE = 2  # refuse to send orders after 15:58
FILL_WAIT_SECONDS = 30           # wait this long for covers to fill before shorting
SCAN_TIMEOUT_SECONDS = 25 * 60   # v2 scan runs beside the 15:55 cover; give up on it after this
COVER_EXTRA_PASSES = 2           # shorts still open after the first cover pass: cancel, re-read, re-send, up to this many times
LEFTOVER_FILL_WAIT_SECONDS = 150 # leftover covers (no close cutoff nearby): Alpaca paper took 1-3 min to fill opening market orders on 2026-10-23
CANCEL_WAIT_SECONDS = 30         # wait this long for a cancel to land; paper took ~19 s on 2026-10-23
ALPACA_TIMEOUT_SECONDS = 15      # every Alpaca HTTP call (alpaca-py sets none)

# Next-open entry (v2). cron starts `daily.py --open --execute` at 09:31.
PICKS_FILE = os.path.join(LOG_DIR, "next_open_picks.json")
ENTRY_MINUTES_AFTER_OPEN = 1     # short at 09:31
ENTRY_CUTOFF_MINUTES_AFTER_OPEN = 20   # a late start (Mac asleep) enters nothing after 09:50
# Catch-ups. Under v2 nothing is short overnight, so a short held at 09:31 that was not opened
# today is a leftover (a missed 15:40 job). The --open job covers leftovers first (purpose
# "leftover"), then sells the sleeve's longs, then enters. `daily.py --cover-leftovers --execute`
# does the same cover at any time in market hours (10:00, 12:00, 14:00 cron lines).
COVER_LEFTOVERS_AT_OPEN = True
MAX_GAP_UP_PCT = 40.0            # skip a pick that opens more than this % above its scan price

# Round 3 (research/round3/REPORT.md), OFF. How the v2 short is entered:
#   "market"  v2: DAY market sell at 09:31 (ENTRY_MINUTES_AFTER_OPEN)
#   "opg"     market-on-open: when the --open job starts before the OPG cutoff (09:28; move its cron
#             line to 09:20), each pick is sold short in the opening auction, sized on the latest
#             premarket print, with the same borrow / price / MAX_GAP_UP_PCT checks against that print.
#             A pick the overnight long sleeve still holds cannot be shorted in the same auction it is
#             sold in, so it (and any pick the auction pass skipped or Alpaca rejected) goes through the
#             09:31 market path as today. Not used when the PDT cap or a daily-loss / drawdown halt is
#             configured or applies (those are checked at 09:31). Broker stops (BROKER_STOP_PCT) are
#             placed at 09:31 on the auction fills.
# Backtest 2016-2026 (round 3): opening-auction entry + no broker stop + close exit: IS Sharpe 1.44,
# OOS 1.19 vs v2 live 1.14 / 0.81. With the 35% stop kept, auction entry is WORSE out of sample
# (OOS 0.60): the open print is stopped out by first-minute spikes. Use "opg" only with
# BROKER_STOP_PCT = None, an owner decision on tail risk (worst single short then -51% vs -37%).
ROUND3_SHORT_ENTRY_ORDER = "market"

# ---------------------------------------------------------------- overnight long sleeve (on)
# Penny Research "LS1 flip": buy v2's own picks at T 15:55 (after the cover-all), sell them at
# the T+1 open, then the 09:31 v2 short of the same names goes ahead. OOS 2022-2026 with v2:
# Sharpe 1.25, CAGR 16.4%, max DD -11.1% (v2 alone 1.21 / 10.7% / -8.3%). The long leg alone has
# a median trade of ~0 and a +2.1 skew; its worst overnight gap was -71%.
# On since 2026-10-23 (owner-approved). The real environment can force it off for a run:
# SHORTBOT_OVERNIGHT_LONG=0 (.env cannot, it is read after this module).
OVERNIGHT_LONG_ENABLED = os.environ.get("SHORTBOT_OVERNIGHT_LONG", "1").strip().lower() in ("1", "true", "yes")
OVERNIGHT_LONG_UNIVERSE = "v2_picks"     # exactly the names saved to next_open_picks.json at 15:45
OVERNIGHT_LONG_SIZE_PCT = 5.0            # % of equity per name
OVERNIGHT_LONG_MAX_GROSS = 25.0          # total long notional cap, % of equity (open longs included)
MIN_PRICE_FOR_ANY_TRADE = 10.0           # a long is skipped below this price at entry
OVERNIGHT_LONG_SKIP_IF_NEWS = ["dilution", "offering", "pricing", "424B", "S-3"]   # vs catalyst + headlines
# Entry: "limit" = DAY limit at ask x (1 + OVERNIGHT_LONG_LIMIT_PCT/100), sent at 15:55 after the
# cover-all; whatever is unfilled at the close expires. "moc" = market-on-close sent right after
# the scan: Alpaca rejects MOC orders after 15:50, so it cannot wait for the 15:55 cover, and any
# pick held (either side) or entered as a short today is skipped.
OVERNIGHT_LONG_ENTRY_ORDER = "limit"
OVERNIGHT_LONG_LIMIT_PCT = 0.5
MOC_CUTOFF_MINUTES_BEFORE_CLOSE = 10     # Alpaca: no MOC orders after 15:50
# Exit at T+1: "market" = DAY market sell as soon as the --open job runs with the market open
# (the 09:31 cron line). "opg" = market-on-open; it only works when the --open job starts before
# 09:28 (move its cron line to 09:20). Later than that it falls back to "market".
OVERNIGHT_LONG_EXIT_ORDER = "market"
OPG_CUTOFF_MINUTES_BEFORE_OPEN = 2       # Alpaca: no OPG orders after 09:28
OVERNIGHT_LONG_FILL_WAIT_SECONDS = 30    # wait for each sell pass before re-reading positions
OVERNIGHT_LONG_EXIT_EXTRA_PASSES = 2     # longs still open after the first sell: cancel, re-read, re-sell
OVERNIGHT_LONG_EXIT_DEADLINE_MINUTES_AFTER_OPEN = 20   # 09:50: a long still open then is a critical alert
OVERNIGHT_LONG_ALERT_LOSS_PCT = 25.0     # monitor.py: a long down this much

# ETB history for research: every scanned name's Alpaca flags, appended after each 15:45 scan
# of an executed run (dry runs write etb_history.dry_run.csv). In LOG_DIR.
ETB_HISTORY_FILE = "etb_history.csv"

# Pattern day trader rule. FINRA retired it (Rule 4210 amendments, effective 2026-06-04) and
# Alpaca replaced it with intraday margin; the paper account no longer reports
# pattern_day_trader / daytrade_count / daytrading_buying_power. Each v2 round trip is a day
# trade, so the old cap is kept as a fallback:
#   "auto"    apply the cap only if the account reports PDT restrictions (pattern_day_trader true,
#             a daytrade_count under PDT_MIN_EQUITY, daytrading_buying_power at 0), or once an
#             order has been rejected for a PDT reason (logs/pdt_detected.json; delete to reset)
#   "always"  always apply it      "never"  never apply it
# With the cap on and equity under PDT_MIN_EQUITY, the 09:31 job enters at most
# PDT_MAX_DAY_TRADES - (day trades in the last 5 sessions) names, in rank order, so every short
# can be covered the same day; unknown count -> nothing. At ~$10k that is ~3 trades per 5 sessions.
PDT_ENFORCE = "auto"
PDT_MIN_EQUITY = 25_000
PDT_MAX_DAY_TRADES = 3           # day trades allowed in 5 sessions under PDT_MIN_EQUITY
PDT_OVERFLOW = "skip"            # picks over the allowance are skipped. ("hold_overnight" is not built)
# Fresh start: fills before this date (the first bot's) are ignored.
TRACK_FROM = "2026-10-21"

# ---------------------------------------------------------------- scoring weights
# One block per checklist item. Weights renormalise when a block has no data.
WEIGHTS = {
    "move_volume": 35,   # 1. blow-off: big % up on heavy volume, upper wick, weak close
    "crowding": 20,      # 2. moderate SI / low DTC - shorts can exit, no squeeze fuel
    "ftd": 0,            # 4. no repeated giant fails-to-deliver. 0: no signal in the backtest (v1: 10)
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

# ---------------------------------------------------------------- monitor.py (alerts only, never trades)
ALERT_POSITION_LOSS_PCT = 25.0   # a position this % against us
ALERT_BOOK_LOSS_PCT = 5.0        # open positions down this % of equity in total
MONITOR_START = (9, 45)          # ET window monitor.py acts in; cron fires */15 9-15
MONITOR_END = (15, 45)

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
