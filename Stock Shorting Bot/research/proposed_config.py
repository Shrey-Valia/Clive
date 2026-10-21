"""PROPOSAL ONLY - not imported by the live bot. Values to review against shortingbot/config.py.

Evidence: research/results/*.csv (see proposal_candidates.csv, dsr_and_finalists.csv,
leg_decomposition.csv, component_ic.csv). Backtest config "P7_intraday_top5_eq50_min70_strict".

Ordered by strength of evidence. Items 1-2 are the important ones; 3-5 are refinements.
"""

# ------------------------------------------------------------------ 1. timing (needs code, see below)
# Do NOT hold overnight. Scan at 15:45 on day T as today, but enter the shorts at the
# T+1 open and cover them at T+1 15:55 (or market-on-close).
#   overnight leg (T 15:55 -> T+1 open), short return, all eligible top-20: IS -0.49%, OOS -0.14%
#   intraday leg  (T+1 open -> T+1 15:55):                                   IS +0.40%, OOS +0.29%
# Also removes overnight gap risk and (on Alpaca) overnight borrow fees.
HOLD_MODE = "next_day_intraday"        # was: 15:55 -> next 15:55
ENTRY_TIME = "open"                    # T+1 09:30-09:35 ET, market order after the open print
EXIT_MINUTES_BEFORE_CLOSE = 5          # cover at T+1 15:55 (same cutoff logic as today)

# ------------------------------------------------------------------ 2. sizing
N_POSITIONS = 5
SIZE_PCT_MAX = 10.0                    # was 19.0; flat 10% each, 50% gross at most
SIZE_PCT_MIN = 10.0                    # was 15.0
# At 19% per name the baseline lost 98.7% of equity on 2021-01-26 (KOSS +499%) and
# 96.4% on 2023-04-27 (TOP +499%). One squeeze at 19% is an account-ending event.

# ------------------------------------------------------------------ 3. skip weak names / days
MIN_SHORT_SCORE = 70.0                 # new: trade only names scoring >= 70; fewer than 5 (or 0) is fine
# Baseline hold with this filter alone: IS Sharpe -1.58 -> -0.24, OOS -0.42 -> +0.48.

# ------------------------------------------------------------------ 4. liquidity / borrow proxy gate
MIN_PRICE_AT_SCAN = 10.00              # new: price at 15:45 >= $10 (MIN_PREV_CLOSE 3.00 can stay as the screen)
MIN_ADV20_USD = 10_000_000             # new: 20-day average dollar volume >= $10M
# Improved IS Sharpe for all 7 candidates and OOS Sharpe for 4 of 5 intraday ones, with smaller drawdowns
# everywhere (proposal_candidates.csv, stress=gate_strict_p10_adv10m). It worsened OOS for the overnight hold.
# Keep the Alpaca shortable / easy_to_borrow gate; this proxy sits on top of it.

# ------------------------------------------------------------------ 5. scoring
WEIGHTS = {
    "move_volume": 35,
    "crowding": 20,
    "ftd": 0,          # was 10: rank IC ~0 in both periods (IS +0.004, OOS -0.008)
    "catalyst": 35,
}
# Keep SQUEEZE_EXCLUDE and CATALYST_EXCLUDE (cheap; excluded names were no better shorts).
# Do NOT add a daily-bar stop loss: every stop level tested (10-40%) lowered Sharpe on the
# overnight hold. With the intraday hold a protective +40% stop is optional insurance: it cut the
# worst day roughly in half but also cut OOS Sharpe (see P2 vs P1).

# ------------------------------------------------------------------ code sketch for item 1
# daily.py today: 15:45 scan -> 15:55 flatten + short.
# Proposed:
#   15:45 T   scan + score as now; write picks (ticker, score, target_pct) to logs/next_open_picks.json
#   15:55 T   flatten anything still open (should be nothing - the book is covered at 15:55 daily)
#   09:31 T+1 new cron entry: re-check Alpaca shortable/ETB, short the saved picks at 10% each
#   15:55 T+1 cover all shorts (existing rotation step), then save the new picks for T+2
# The wash-trade resize logic in strategy.plan_orders is no longer needed, because the book is
# flat between 15:55 and the next open.
