"""Every tunable number for the live dip put credit spread bot, plus credential loading.

The strategy block is the frozen research finalist (tuning/signals/REPORT.md,
finalists.json `sig_dip_pcs_*`). Do not tune it here; change it in research first.

Credentials
-----------
* Trading (account, positions, orders) uses ONLY the dedicated options paper
  account: OPT_APCA_API_KEY_ID / OPT_APCA_API_SECRET_KEY / OPT_APCA_PAPER=true,
  read from "Options Bot/.env" (process env may override the same OPT_* names).
  There is no fallback for trading. The APCA_* variables belong to the live
  Stock Shorting Bot and are never read for trading.
* Market data (bars, option chain snapshots) uses the OPT keys when present,
  otherwise the read-only data keys from "Stock Shorting Bot/.env". Those keys
  are only ever sent to data.alpaca.markets (enforced in broker.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LIVE_DIR = Path(__file__).resolve().parent
ROOT = LIVE_DIR.parent                                   # "Options Bot/"
ENV_PATH = ROOT / ".env"
SHORTING_ENV_PATH = ROOT.parent / "Stock Shorting Bot" / ".env"
LOG_DIR = LIVE_DIR / "logs"
STATE_DIR = LIVE_DIR / "state"
CACHE_DIR = LIVE_DIR / ".cache"

BOT_ID = "options"
ORDER_PREFIX = BOT_ID + "-"     # every client_order_id the bot sends starts with this; only these are canceled
SLEEVE_DIP = "dip"              # spread orders: "options-dip-..." (dip put credit spread)
SLEEVE_CONDOR = "condor"        # ...            "options-condor-..." (SPY iron condor)
STRATEGY_ID = "dip_pcs_live"
MARKET_TZ = "America/New_York"

# ---------------------------------------------------------------- endpoints
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
PAPER_TRADING_HOST = "paper-api.alpaca.markets"
LIVE_TRADING_HOST = "api.alpaca.markets"                 # refused, always
DATA_URL = "https://data.alpaca.markets"
STOCK_FEED = os.environ.get("OPT_STOCK_FEED", "iex")    # live price/snapshot feed ("sip" needs a paid plan)
HIST_FEED = "sip"                                        # daily bars before today (free plans allow SIP > 15 min old)
OPTION_FEED = "indicative"                               # free option feed; "opra" needs the OPRA agreement
REQUEST_TIMEOUT = 20
HTTP_RETRIES = 4                # 429 / 5xx / network errors on idempotent calls
CBOE_HISTORY = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"
CBOE_QUOTE = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json"

# ---------------------------------------------------------------- universe
UNIVERSE = ["SPY", "IWM"]          # QQQ dropped 2026-10-22: Sharpe 0.37, DD -22.5% after the engine fixes
# Single names were less stable OOS; off by default. Earnings guard applies if enabled.
SINGLE_NAMES = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META"]
ENABLE_SINGLE_NAMES = False

# ---------------------------------------------------------------- frozen strategy
RSI_N = 2
RSI_MAX = 10.0                  # RSI(2) < 10
IBS_MAX = 0.20                  # IBS < 0.2 (today's high/low so far)
TREND_SMA = 200                 # close > 200DMA
EXIT_SMA = 5                    # exit when close > 5DMA (from the first day after entry)
SHORT_DELTA = 0.25
LONG_DELTA = 0.10
DTE_MIN, DTE_MAX, DTE_TARGET = 7, 14, 10
TAKE_PROFIT = 0.50              # buy back when 50% of the credit is captured
MAX_HOLD_DAYS = 5               # trading days
# Research semantics (strategies.credit_exit sl_mult): close when cost to close >= credit * (1 + STOP_MULT),
# e.g. 1.0 = the spread has doubled against us. Off in research.
STOP_MULT: float | None = None
CLOSE_DTE_TRADING_DAYS = 1      # close on the last trading day before expiry

# ---------------------------------------------------------------- guards
MAX_CREDIT_FRAC = 0.60          # bug #7 guard: credit > 60% of width is a bad quote
# Credit floor as a share of width. The brief asked for 15%, but 0.25/0.10-delta
# put spreads at 7-14 DTE collect ~12% of width: in the research backtest
# (SPY/QQQ/IWM, 2024-03..2026-10) 51 of 54 trades had credit < 15% of width
# (median 12.3%, min 6.8%). 15% would switch the strategy off, so the default
# sits just under the research minimum. Set 0.15 to apply the stricter rule.
MIN_CREDIT_FRAC = 0.065
MIN_CREDIT_ABS = 0.10           # $ per share
MAX_LEG_SPREAD_FRAC = 0.35      # each leg: (ask - bid) / mid
MAX_COMBO_SPREAD_ABS = 0.10     # combo natural-vs-far width allowed regardless of credit size
MAX_COMBO_SPREAD_FRAC = 0.40    # ... or up to this share of the mid credit
DELTA_TOLERANCE = 0.08          # chosen leg's |delta| must be within this of the target
MIN_WIDTH = 1.0                 # $; narrower spreads are rejected

# ---------------------------------------------------------------- condor sleeve (second, independent)
# Spreads finalist F1 (tuning/spreads/REPORT.md, frozen.json `F1_ic10_w3_60d_hold`): SPY iron
# condor, 0.10 delta shorts on both sides, wings 3% of spot, 50-70 DTE (target 60), held to
# expiry (no take-profit, no stop), max loss <= 20% of equity, one position at a time, skip when
# the credit > 60% of the width. OFF by default. `run.py --condor` forces it on for one run.
# Live differences: it is closed on the last trading day before expiry at the daily decision
# time (the dip sleeve's expiry_exit), the 09:45 sweep closes anything left on expiry day, and no
# condor order is ever sent on expiry day at or after CONDOR_EXPIRY_DAY_DEADLINE (Alpaca starts its
# own expiration handling at 15:30 ET). New entries need the kill switch to be clear.
CONDOR_ENABLED = True
CONDOR_UNDERLYING = "SPY"
CONDOR_SHORT_DELTA = 0.10       # |delta| of the short put and the short call
CONDOR_WING_PCT = 0.03          # long strikes at short -/+ 3% of spot
CONDOR_DTE_MIN, CONDOR_DTE_MAX, CONDOR_DTE_TARGET = 50, 70, 60
CONDOR_DELTA_TOLERANCE = 0.05   # chosen short leg's |delta| must be within this of 0.10
CONDOR_RISK_PER_POSITION = 0.20 # condor max loss <= 20% of equity
CONDOR_MAX_POSITIONS = 1        # one condor at a time
CONDOR_MAX_CREDIT_FRAC = 0.60   # bug #7 guard on the condor's (max) width
CONDOR_MIN_CREDIT_ABS = 0.10    # research: an iron condor needs > $0.10 credit
CONDOR_CLOSE_DTE_TRADING_DAYS = 1   # close at the daily run on the last trading day before expiry
CONDOR_EXPIRY_DAY_DEADLINE = "15:00"    # ET: no condor orders on expiry day at or after this
CONDOR_EXPIRY_DAY_MARGIN_MINUTES = 6    # ... and none started later than this before the deadline

# ---------------------------------------------------------------- sizing
RISK_PER_POSITION = 0.25        # max loss per spread position <= 25% of equity
# Total max loss across positions <= 50% of equity. This is the SHARED budget of both sleeves:
# open dip spreads + the open condor (its max loss is reserved) + anything planned this run.
RISK_TOTAL = 0.50
MAX_CONTRACTS = 50              # hard per-order cap, whatever the equity
DRY_RUN_EQUITY = 10_000.0       # used when no options account exists yet (dry run only)

# ---------------------------------------------------------------- kill switch
VIX_RATIO_MAX = 1.15            # no entries when VIX / VIX3M > 1.15
VIX_MAX = 40.0                  # ... or VIX > 40
KILL_ON_MISSING_VIX = True      # fail closed: no entries if VIX data is unavailable
VIX_MAX_AGE_MINUTES = 30        # entries need same-day, same-source VIX and VIX3M quotes at most this old

# ---------------------------------------------------------------- data freshness (fail closed)
MIN_HISTORY_BARS = TREND_SMA + 5    # daily bars needed before today
MAX_HISTORY_GAP_DAYS = 5            # last daily bar must be at most this many calendar days before today
MAX_STOCK_TRADE_AGE_SECONDS = 300   # live price older than this blocks entries (execute mode)
MAX_OPTION_QUOTE_AGE_SECONDS = 900  # option quotes older than this block entries / take-profits

# ---------------------------------------------------------------- timing (minutes before the close)
# DECIDE_MINUTES_BEFORE_CLOSE is the signal snapshot time. See live/README.md "Decision time" and
# tuning/live_timing/ for the re-validation at 15:45 / 15:50 / 15:55 / 15:56 / close.
START_MINUTES_BEFORE_CLOSE = 15     # cron 15:45: start, checks, startup sweep; then wait for the decision time
# 15:55 (was 15:50): tuning/live_timing re-validation. At 15:50 the live rule kept 35 of 53 research
# trades (IS Sharpe 0.52 vs 0.76 at the close); at 15:55 it keeps 45 (IS 0.74, OOS 1.89 vs 1.54).
DECIDE_MINUTES_BEFORE_CLOSE = 5     # signals + exits at 15:55 (12:55 on half days)
CUTOFF_MINUTES_BEFORE_CLOSE = 2     # cancel working orders by 15:58
INTRADAY_START_MINUTES_AFTER_OPEN = 30
LOCK_TIMEOUT_SECONDS = 240          # the daily run waits this long for an intraday run to release the lock
EXPIRY_SWEEP_MINUTES_AFTER_OPEN = 15    # --sweep: expiry-day morning sweep (Alpaca starts its own
                                        # expiration handling at 15:30 ET on expiration day)

# ---------------------------------------------------------------- order working
ENTRY_STEPS = 4                 # re-price up to N times (N+1 prices in total)
ENTRY_MAX_CONCESSION = 0.50     # walk at most this far from mid toward the natural price
EXIT_STEPS = 4
EXIT_MAX_CONCESSION = 1.00      # exits walk all the way to the natural price ...
EXIT_CUTOFF_EXTRA = 0.05        # ... and at the cutoff pay natural + $0.05 (aggressive fallback)
STEP_WAIT_SECONDS = 30          # max time each price stays working (5 prices fit 15:55-15:58; shrunk if needed)
POLL_SECONDS = 3
TICK = 0.01

# ---------------------------------------------------------------- round 3 (docs/ENGINEERING_ROUND3.md)
# Host-death resilience. Alpaca mleg orders fill as one unit (all legs or none), so a host that dies
# mid-run can never leave half a spread; what it can leave is (a) a DAY order still working until the
# close and (b) fills the bot never booked. Both are handled below.
#
# Write-ahead journal of in-flight orders (state/inflight.json): written before the first submit,
# cleared once every fill is booked. A run that finds one left by a dead run looks each order up by
# client_order_id, cancels any still open, and books the fills with the planned metadata (entry mid,
# signal, exit reason) instead of adopting the spread blind. Bookkeeping only; no order is sent.
RECOVER_INFLIGHT = True
# Resting ("exchange-side") GTC mleg limit orders, so a take-profit / an overdue expiry exit does not
# depend on this host being up. Alpaca: options TIF day|gtc; mleg orders only market|limit (no stop),
# so a stop cannot rest at Alpaca for a spread. Resting orders use client_order_id "options-rest-...",
# are left alone by the leftover-order cancel at the start of a run, and are canceled (and confirmed)
# before the bot works any order on the same spread.
STAGE_TP_ORDERS = False             # dip spreads: rest a GTC close at entry_mid * (1 - TAKE_PROFIT) after entry
REST_UNFILLED_EXPIRY_EXITS = False  # an expiry exit still unfilled at the close rests overnight as a GTC
                                    # close at its cutoff price, so it works at the next open (09:30)
                                    # even if the 09:45 sweep never runs
REST_ORDER_PREFIX = ORDER_PREFIX + "rest-"
# Dip sleeve: same expiry-day deadline as the condor (no order started or worked on expiry day at or
# after 15:00 ET; Alpaca sells out ITM positions without buying power "within 1 hour before expiry").
DIP_EXPIRY_DAY_DEADLINE_ENABLED = False

# Early-assignment guard for the condor's SHORT CALL around SPY's ex-dividend date. SPY goes ex on
# the third Friday of Mar/Jun/Sep/Dec (the previous session when that is a holiday), which is the
# monthly expiry the condor targets, so the condor's last day (expiry - 1 session) is the last
# cum-dividend session: the day a call holder exercises early to collect the dividend. The guard
# closes the condor at the daily run on the last cum-dividend session before an ex-date inside the
# position's life when the short call is in the money and its extrinsic value is below the expected
# dividend (rational early exercise). Safety fix, default ON; set False for the research behaviour.
CONDOR_EXDIV_GUARD = True
CONDOR_EXDIV_INTRADAY_FROM = "15:00"   # ... and from this time the intraday runs on that day too
EXDIV_QUARTER_MONTHS = {"SPY": (3, 6, 9, 12)}
DIVIDEND_ESTIMATE = {"SPY": 2.00}      # $/share, used when Alpaca has no recent dividend (2026: 1.80-1.90)
EXDIV_ITM_BUFFER = 0.0                 # $ of spot above the short call strike that already counts as ITM

# Execution quality: every order journal row (and options_live_orders, migration 009) records the
# planned mid and natural and the slippage of the fill vs the mid (per share, and $ per order).
# Monitoring parity with the shorting bot: alerts rows (bot_id 'options') + ntfy push.
ALERTS_ENABLED = True
ALERTS_PUSH = True                  # ntfy, when NTFY_TOPIC is found (process env, Options .env, Shorting .env)


@dataclass(frozen=True)
class Creds:
    key: str
    secret: str
    source: str                 # "options" | "shorting-readonly"


class ConfigError(RuntimeError):
    pass


def _read_env_file(path: Path) -> dict:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def options_env(path: Path | None = None) -> dict:
    """OPT_* and SUPABASE_* from Options Bot/.env, overridable by the process env
    (same names only). Never reads APCA_*."""
    env = _read_env_file(path or ENV_PATH)
    for k in ("OPT_APCA_API_KEY_ID", "OPT_APCA_API_SECRET_KEY", "OPT_APCA_PAPER",
              "OPT_APCA_BASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def _is_true(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes")


def trading_creds(env: dict | None = None) -> Creds:
    """The dedicated options paper account, or a refusal. No fallback."""
    env = options_env() if env is None else env
    key, secret = env.get("OPT_APCA_API_KEY_ID", ""), env.get("OPT_APCA_API_SECRET_KEY", "")
    if not key or not secret or "..." in key:
        raise ConfigError(
            "Options trading keys missing: set OPT_APCA_API_KEY_ID / OPT_APCA_API_SECRET_KEY in "
            f"{ENV_PATH} (a SEPARATE Alpaca paper account; see live/README.md). "
            "The Stock Shorting Bot keys are never used for trading.")
    if not _is_true(env.get("OPT_APCA_PAPER")):
        raise ConfigError("OPT_APCA_PAPER is not true. This bot is paper-only and refuses to run.")
    base = env.get("OPT_APCA_BASE_URL", PAPER_TRADING_URL)
    assert_paper_url(base)
    if key.startswith("AK"):
        raise ConfigError("OPT_APCA_API_KEY_ID looks like a LIVE key (AK...). Paper keys start with PK.")
    assert_not_shorting_key(key)
    return Creds(key, secret, "options")


def assert_paper_url(url: str) -> None:
    """Exact host + https. A substring match would accept e.g. paper-api.alpaca.markets.evil.com."""
    try:
        u = urlparse(str(url).strip())
        host, port = (u.hostname or "").lower(), u.port
    except ValueError:
        host, port, u = "", None, None
    if (u is None or u.scheme != "https" or host != PAPER_TRADING_HOST or port not in (None, 443)
            or u.username or u.password or (u.path or "").strip("/")):
        raise ConfigError(f"Trading base URL is not the paper endpoint ({url!r}); refusing.")


def shorting_identity(path: Path | None = None) -> dict:
    """The Stock Shorting Bot's key id and (optional) account id/number, for the
    'never the same account' check. Values are only compared, never logged.
    The account id comes from SHORTING_ACCOUNT_ID / SHORTING_ACCOUNT_NUMBER in
    Options Bot/.env (or the shorting .env); the shorting keys themselves are
    never sent to the trading host to look it up."""
    sh = _read_env_file(path or SHORTING_ENV_PATH)
    mine = _read_env_file(ENV_PATH)
    ids = set()
    for k in ("SHORTING_ACCOUNT_ID", "SHORTING_ACCOUNT_NUMBER", "APCA_ACCOUNT_ID", "APCA_ACCOUNT_NUMBER"):
        for src in (sh, mine, os.environ):
            v = str(src.get(k, "") or "").strip()
            if v:
                ids.add(v)
    return {"key": sh.get("APCA_API_KEY_ID", "").strip(), "account_ids": ids}


def assert_not_shorting_key(key: str, ident: dict | None = None) -> None:
    ident = shorting_identity() if ident is None else ident
    if ident.get("key") and key.strip() == ident["key"]:
        raise ConfigError("OPT_APCA_API_KEY_ID is the Stock Shorting Bot's key. The options bot needs its own "
                          "paper account; refusing.")


def assert_not_shorting_account(account: dict, ident: dict | None = None) -> None:
    ident = shorting_identity() if ident is None else ident
    mine = {str(account.get(k, "") or "").strip() for k in ("id", "account_number")} - {""}
    if mine & set(ident.get("account_ids") or ()):
        raise ConfigError("The OPT keys point at the Stock Shorting Bot's account; refusing.")


def data_creds(env: dict | None = None) -> Creds:
    """Market-data keys: OPT keys if present, else the shorting bot's keys for
    READ-ONLY data calls (broker.py only sends them to data.alpaca.markets)."""
    env = options_env() if env is None else env
    k, s = env.get("OPT_APCA_API_KEY_ID", ""), env.get("OPT_APCA_API_SECRET_KEY", "")
    if k and s and "..." not in k:
        return Creds(k, s, "options")
    sh = _read_env_file(SHORTING_ENV_PATH)
    k, s = sh.get("APCA_API_KEY_ID", ""), sh.get("APCA_API_SECRET_KEY", "")
    if k and s:
        return Creds(k, s, "shorting-readonly")
    raise ConfigError("No market-data keys: set OPT_APCA_* in Options Bot/.env.")


def ntfy_settings() -> tuple[str, str]:
    """(topic, server) for ntfy pushes. The process env wins when the name is set at all, even
    empty (the deploy kit blanks NTFY_TOPIC in shadow mode); then Options Bot/.env, then
    Stock Shorting Bot/.env, where the owner's topic lives. Only these two names are read from
    the shorting .env."""
    def get(name: str) -> str:
        if name in os.environ:
            return os.environ[name].strip()
        for path in (ENV_PATH, SHORTING_ENV_PATH):
            v = _read_env_file(path).get(name, "").strip()
            if v:
                return v
        return ""
    return get("NTFY_TOPIC"), (get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")


def universe() -> list[str]:
    return UNIVERSE + (SINGLE_NAMES if ENABLE_SINGLE_NAMES else [])
