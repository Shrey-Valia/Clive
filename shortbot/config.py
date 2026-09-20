"""Tunable knobs. Everything you'd want to adjust lives here."""

# ---------------------------------------------------------------- Step 1
# Screen criteria for the TradingView gainers pull.
MIN_PRICE = 3.00
MIN_MARKET_CAP = 25_000_000
# Ceilings are off by default so the screen matches the original spec, but a
# floor-only screen is NOT a penny-stock screen - it happily returns $190
# mega-caps. Set these (or pass --max-price / --max-mcap) to bound it.
MAX_PRICE = None
MAX_MARKET_CAP = None
TOP_N = 10
EXCHANGES = ["NASDAQ", "NYSE", "AMEX"]

# ---------------------------------------------------------------- Step 4
# Confidence weights. Each block must sum to 100.

# HOLDING PERIOD: enter near the close, exit ~24h later near the next close.
# Every weight below is set for that horizon and nothing longer.
#
# This is why days-to-cover is nearly absent. DTC measures the SESSIONS of
# average volume shorts need to unwind - a DTC of 12 is a twelve-session
# thesis, and a 24h hold captures roughly a twelfth of it. Same for
# fails-to-deliver, which is 2-4 weeks stale and scored at zero here; it
# stays in the CSV as context only.
#
# What a one-day hold actually turns on is how the stock CLOSED. A +30% day
# that finishes at the high of its range and a +30% day that finishes near
# the low are opposite setups for tomorrow, and nothing else in the data
# separates them.

# WEIGHTS AFTER BACKTESTING (see backtest.py, 2,410 spike events, 3y).
#
# What the data actually said:
#   close-in-range        NO signal (pearson p=0.28, spearman p=0.49).
#                         It had 25% of both scores. Cut to 8 - kept only
#                         because it describes the setup, not because it
#                         predicts.
#   spike size            weak positive on next-day return, so it argues
#                         mildly FOR the long side and AGAINST the short.
#   extension above SMA20 same - more extended means HIGHER next-day
#                         return, the opposite of the classic fade thesis.
#
# Shorting non-extended spikes looked like a real edge (+0.74%, 60.7% win,
# n=537) until it was split by year: 2025 alone carried it (+2.82%, 73.6%)
# and the other three years were negative. That is one year of noise, not
# an edge, so nothing here is fitted to it.
#
# Bottom line: no feature here has demonstrated stable predictive power.
# These weights rank how well a name FITS a setup. They do not forecast.
# The measured base rate lives in expectancy.py and should outrank them.

# LONG - betting it continues tomorrow (day-2 runner).
LONG_WEIGHTS = {
    "short_pct_float": 22,   # day-2 squeeze fuel, gated on float + volume
    "rel_volume":      18,   # real participation behind the move
    "float_tightness": 18,   # low float makes day-2 moves violent
    "spike":           14,   # the one feature that leaned long, weakly
    "close_in_range":   8,   # tested FLAT - descriptive only
    "close_vs_vwap":    8,
    "days_to_cover":    7,   # multi-week metric; token weight at 24h
    "borrow_fee":       5,   # unavailable today
}

# SHORT - betting it gives back tomorrow.
#
# Nothing predicts the fade, and the loss tail is what actually decides
# whether shorting these makes money: 11.2% of historical shorts lost more
# than 10%, the worst 1% lost 40%. So this score leads with avoiding a
# squeeze rather than pretending to time a top.
SHORT_WEIGHTS = {
    "low_squeeze_risk":    30,   # the tail is what kills shorts - avoid it
    "spike_extension":     15,   # REDUCED: tested in the wrong direction
    "sma_extension":       15,   # REDUCED: tested in the wrong direction
    "rvol_blowoff":        12,
    "close_weak_in_range": 10,   # tested FLAT - descriptive only
    "rsi_hot":             10,
    "close_below_vwap":     8,
}

# Named bands, straight from the desk rules. (upper_bound_exclusive, label)
TIERS = {
    "short_pct_float": [(5, "NOT_SQUEEZE"), (10, "MILD"), (20, "REAL_CROWDING"),
                        (30, "SERIOUS_FUEL"), (float("inf"), "EXTREME")],
    "days_to_cover":   [(1, "EASY_EXIT"), (2, "LOW"), (5, "PAINFUL"),
                        (float("inf"), "MESSY_EXIT")],
    "borrow_fee_pct":  [(5, "CHEAP"), (20, "MODERATE"), (50, "BITING"),
                        (100, "PAINFUL"), (500, "SEVERE"),
                        (float("inf"), "NUCLEAR_HTB")],
    "short_vol_ratio": [(20, "LOW"), (40, "NORMAL"), (50, "ELEVATED"),
                        (70, "HEAVY"), (float("inf"), "DOMINANT")],
}

# Ramp endpoints: (value_scoring_0, value_scoring_100).
# A ramp is just a clamped linear interpolation between these two points.
RAMPS = {
    # long side
    "close_in_range":      (40.0, 95.0),    # % of day's range at the close
    "rel_volume":          (1.5, 10.0),
    "float_tightness":     (50e6, 5e6),     # inverted
    "short_pct_float":     (5.0, 30.0),
    "close_vs_vwap":       (-2.0, 8.0),     # % above VWAP
    "spike":               (5.0, 40.0),
    "days_to_cover":       (1.0, 10.0),
    "borrow_fee":          (5.0, 200.0),
    # short side
    "close_weak_in_range": (80.0, 20.0),    # inverted: closed weak scores high
    "spike_extension":     (15.0, 80.0),
    "sma_extension":       (0.15, 1.00),
    "low_squeeze_risk":    (25.0, 3.0),     # inverted
    "rvol_blowoff":        (3.0, 20.0),
    "rsi_hot":             (65.0, 90.0),
    "close_below_vwap":    (5.0, -5.0),     # inverted
    # gating / context
    "si_gate":             (3.0, 10.0),
    "ftd_pct_float":       (0.10, 2.0),     # context column, unweighted at 24h
}

# ---------------------------------------------------------------- Blocks 3/4
SHORT_VOLUME_LOOKBACK_DAYS = 10   # FINRA sessions to average the ratio over
FTD_LOOKBACK_MONTHS = 3           # how far back to hunt for SEC FTD files

# ---------------------------------------------------------------- Quality
# Short interest is reported bi-monthly. Past this many days it's stale.
STALE_SHORT_DATA_DAYS = 30

# Missing short data caps how much we trust a squeeze score.
NO_SHORT_DATA_CONFIDENCE_CAP = 40.0

# Every name gets a LONG/SHORT call - direction is always computable from
# the two scores. Conviction is what separates a real signal from a coin
# flip, and it needs BOTH a decent winning score and a decent margin over
# the other side. A 52-vs-50 split is noise no matter how high the 52 is.
#
# Tuned against live boards: these put roughly 2 HIGH / 3 MEDIUM / 5 LOW on
# a typical top-10. Raise them if you want a quieter board.
CONVICTION = {
    "HIGH":   {"min_score": 50, "min_margin": 25},
    "MEDIUM": {"min_score": 35, "min_margin": 10},
}

# Short interest measured before a huge run describes a different stock.
# INDP reached HIGH conviction SHORT on 0.2% float short that was measured
# before a +216% move - the number was meaningless and there was no base
# rate to check it against. Past this, the short blocks are not evidence.
SI_MEANINGLESS_MOVE_PCT = 75.0

# A call with no measured base rate, or one resting on a thin sample, is the
# weights talking with nothing to check them. Cap those.
MIN_EXPECTANCY_SAMPLE = 60

# With no short interest, a LONG call is untestable - the squeeze thesis
# rests entirely on data we do not have. Cap those at LOW no matter what
# price action says.
CAP_LONG_CONVICTION_WITHOUT_SI = True

# A big run since the SI measurement date means shorts may have already
# covered into it - the fuel the squeeze score counts could be spent.
SI_LIKELY_COVERED_MOVE_PCT = 25.0

# Warn when this share of the board sits in one sector.
SECTOR_CONCENTRATION_WARN = 0.5

# ---------------------------------------------------------------- Execution
# Only this conviction level gets an order.
EXECUTE_CONVICTION = "HIGH"

# Percent of account equity per position, as NOTIONAL. This strategy has no
# stop - the exit is time-based the next afternoon - so stop-based "risk per
# trade" does not apply. Between entry and exit the downside is whatever the
# overnight gap does, and backtesting put the worst 1% of short outcomes at
# about -40%.
RISK_PER_TRADE_PCT = 5.0

# Hard ceiling on total deployment, so a board full of HIGH conviction names
# cannot quietly become an all-in bet.
MAX_TOTAL_EXPOSURE_PCT = 25.0

# Order type. "cls" = market-on-close: the order is queued during the
# session and fills at the OFFICIAL CLOSING PRICE. That is exactly what this
# strategy wants and exactly what the backtest measured (close to close).
#
# A plain market order sent at 15:59 does not do this - it either misses the
# bell entirely and sits in NEW until the next OPEN, filling hours later at
# the wrong price, or slips against you in the closing scramble.
ORDER_TIF = "cls"

# Alpaca stops accepting market-on-close orders at 15:50 ET, which drives
# the whole schedule earlier than the naive 15:50/15:55 plan.
MOC_CUTOFF = "15:50"

# Schedule (US/Eastern).
SCAN_TIME = "15:40"      # scrape + score
TRADE_TIME = "15:45"     # submit MOC orders, safely inside the 15:50 cutoff
MARKET_TZ = "America/New_York"

# ---------------------------------------------------------------- Network
REQUEST_TIMEOUT = 20
MAX_WORKERS = 4          # yfinance rate-limits above this
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
