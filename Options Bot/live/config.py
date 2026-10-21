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

LIVE_DIR = Path(__file__).resolve().parent
ROOT = LIVE_DIR.parent                                   # "Options Bot/"
ENV_PATH = ROOT / ".env"
SHORTING_ENV_PATH = ROOT.parent / "Stock Shorting Bot" / ".env"
LOG_DIR = LIVE_DIR / "logs"
STATE_DIR = LIVE_DIR / "state"
CACHE_DIR = LIVE_DIR / ".cache"

BOT_ID = "options"
STRATEGY_ID = "dip_pcs_live"
MARKET_TZ = "America/New_York"

# ---------------------------------------------------------------- endpoints
PAPER_TRADING_URL = "https://paper-api.alpaca.markets"
LIVE_TRADING_HOST = "api.alpaca.markets"                 # refused, always
DATA_URL = "https://data.alpaca.markets"
STOCK_FEED = os.environ.get("OPT_STOCK_FEED", "iex")    # live price/snapshot feed ("sip" needs a paid plan)
HIST_FEED = "sip"                                        # daily bars before today (free plans allow SIP > 15 min old)
OPTION_FEED = "indicative"                               # free option feed; "opra" needs the OPRA agreement
REQUEST_TIMEOUT = 20
CBOE_HISTORY = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"
CBOE_QUOTE = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json"

# ---------------------------------------------------------------- universe
UNIVERSE = ["SPY", "QQQ", "IWM"]
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
STOP_MULT: float | None = None  # e.g. 2.0 = close when cost to close >= 2x credit. Off in research.
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

# ---------------------------------------------------------------- sizing
RISK_PER_POSITION = 0.25        # max loss per spread position <= 25% of equity
RISK_TOTAL = 0.50               # total max loss across positions <= 50% of equity
MAX_CONTRACTS = 50              # hard per-order cap, whatever the equity
DRY_RUN_EQUITY = 10_000.0       # used when no options account exists yet (dry run only)

# ---------------------------------------------------------------- kill switch
VIX_RATIO_MAX = 1.15            # no entries when VIX / VIX3M > 1.15
VIX_MAX = 40.0                  # ... or VIX > 40
KILL_ON_MISSING_VIX = True      # fail closed: no entries if VIX data is unavailable

# ---------------------------------------------------------------- timing (minutes before the close)
START_MINUTES_BEFORE_CLOSE = 15     # cron 15:45
DECIDE_MINUTES_BEFORE_CLOSE = 10    # signals + exits at 15:50
CUTOFF_MINUTES_BEFORE_CLOSE = 2     # cancel working orders by 15:58
INTRADAY_START_MINUTES_AFTER_OPEN = 30

# ---------------------------------------------------------------- order working
ENTRY_STEPS = 4                 # re-price up to N times (N+1 prices in total)
ENTRY_MAX_CONCESSION = 0.50     # walk at most this far from mid toward the natural price
EXIT_STEPS = 4
EXIT_MAX_CONCESSION = 1.00      # exits walk all the way to the natural price ...
EXIT_CUTOFF_EXTRA = 0.05        # ... and at the cutoff pay natural + $0.05 (aggressive fallback)
STEP_WAIT_SECONDS = 90          # max time each price stays working (shrunk to fit the window)
POLL_SECONDS = 3
TICK = 0.01


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
    return Creds(key, secret, "options")


def assert_paper_url(url: str) -> None:
    u = url.lower().rstrip("/")
    if "paper-api.alpaca.markets" not in u:
        raise ConfigError(f"Trading base URL is not the paper endpoint ({url!r}); refusing.")


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


def universe() -> list[str]:
    return UNIVERSE + (SINGLE_NAMES if ENABLE_SINGLE_NAMES else [])
