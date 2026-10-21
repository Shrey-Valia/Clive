"""Small, self-contained daily-bar fetcher for the charts/ research scripts.

* Stock bars: Alpaca market-data v2 (``/v2/stocks/bars``), read-only, adjusted
  (``adjustment=all``). Tries the SIP feed with ``end`` >15 min in the past and
  falls back to IEX if SIP is refused.
* VIX / VIX3M / VIX9D: CBOE daily history CSVs.

Everything is cached under ``Options Bot/.cache/charts/`` (parquet-free: plain
CSV so it works without pyarrow). Credentials are read at runtime from the
Stock Shorting Bot ``.env`` file and are never printed.

This module only ever issues GET requests to data endpoints. It never touches
any Alpaca trading / order endpoint.

Usage::

    python3 charts/fetch_bars.py            # refresh cache for default tickers
    from fetch_bars import load_bars, load_cboe
"""
from __future__ import annotations

import datetime as dt
import io
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]          # Options Bot/
CACHE = ROOT / ".cache" / "charts"
CACHE.mkdir(parents=True, exist_ok=True)
ENV_PATH = ROOT.parent / "Stock Shorting Bot" / ".env"

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
CBOE_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{sym}_History.csv"

TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "IWM"]
DEFAULT_START = "2016-01-01"   # Alpaca daily history begins 2016-01-04 (verified)
MAX_CACHE_AGE_H = 12


def _read_env(path: Path = ENV_PATH) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def _headers() -> dict[str, str]:
    env = _read_env()
    key, sec = env.get("APCA_API_KEY_ID"), env.get("APCA_API_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError(f"Alpaca credentials not found in {ENV_PATH}")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def _fetch_alpaca(symbols: list[str], start: str, feed: str) -> dict[str, pd.DataFrame]:
    end = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "end": end,
              "adjustment": "all", "feed": feed, "limit": 10000}
    rows: dict[str, list] = {s: [] for s in symbols}
    hdr = _headers()
    while True:
        r = requests.get(DATA_URL, headers=hdr, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Alpaca {feed} HTTP {r.status_code}: {r.text[:200]}")
        js = r.json()
        for sym, bars in (js.get("bars") or {}).items():
            rows.setdefault(sym, []).extend(bars)
        tok = js.get("next_page_token")
        if not tok:
            break
        params["page_token"] = tok
    out = {}
    for sym, bars in rows.items():
        if not bars:
            continue
        df = pd.DataFrame(bars).rename(columns={"t": "date", "o": "open", "h": "high", "l": "low",
                                                "c": "close", "v": "volume", "vw": "vwap", "n": "trades"})
        df["date"] = pd.to_datetime(df["date"]).dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
        out[sym] = df.set_index("date").sort_index()[["open", "high", "low", "close", "volume"]]
    return out


def _fresh(p: Path) -> bool:
    return p.exists() and (time.time() - p.stat().st_mtime) < MAX_CACHE_AGE_H * 3600


def load_bars(symbols: list[str] | None = None, start: str = DEFAULT_START,
              refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Return {symbol: OHLCV DataFrame indexed by date}. Cached per symbol."""
    symbols = symbols or TICKERS
    need = [s for s in symbols if refresh or not _fresh(CACHE / f"{s}.csv")]
    if need:
        try:
            got, feed = _fetch_alpaca(need, start, "sip"), "sip"
        except RuntimeError as e:
            print(f"[fetch_bars] SIP failed ({str(e)[:80]}); falling back to IEX", file=sys.stderr)
            got, feed = _fetch_alpaca(need, start, "iex"), "iex"
        for s, df in got.items():
            df.to_csv(CACHE / f"{s}.csv")
        (CACHE / "feed.txt").write_text(feed)
    out = {}
    for s in symbols:
        p = CACHE / f"{s}.csv"
        if p.exists():
            out[s] = pd.read_csv(p, index_col="date", parse_dates=True)
    return out


def load_cboe(sym: str = "VIX", refresh: bool = False) -> pd.DataFrame:
    """CBOE index daily history (VIX, VIX3M, VIX9D) as OHLC DataFrame."""
    p = CACHE / f"{sym}_History.csv"
    if refresh or not _fresh(p):
        r = requests.get(CBOE_URL.format(sym=sym), headers={"User-Agent": "Mozilla/5.0"},
                         timeout=30, allow_redirects=True)
        r.raise_for_status()
        p.write_bytes(r.content)
    df = pd.read_csv(p)
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y")
    return df.set_index("date").sort_index()


if __name__ == "__main__":
    bars = load_bars(refresh="--refresh" in sys.argv, start=sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else DEFAULT_START)
    for s, df in bars.items():
        print(f"{s:6s} {df.index[0].date()} -> {df.index[-1].date()}  {len(df)} bars")
    print("feed:", (CACHE / "feed.txt").read_text() if (CACHE / "feed.txt").exists() else "?")
    for v in ("VIX", "VIX3M", "VIX9D"):
        d = load_cboe(v)
        print(f"{v:6s} {d.index[0].date()} -> {d.index[-1].date()}  {len(d)} rows")
