"""Alpaca market-data access with an on-disk cache.

DATA ONLY. This module never touches any order / account-mutating endpoint.
Credentials are read at runtime from the Stock Shorting Bot .env file and are
never written anywhere or printed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

# macOS: a forked worker that lets requests/urllib consult the system proxy configuration
# (_scproxy -> SystemConfiguration/ObjC, not fork-safe) segfaults on its first request
# (tuning/spreads/BUGS.md #5). When no proxy is configured through the environment, pin
# urllib to the environment path with no_proxy=*. Opt out: OPTIONSBOT_KEEP_SYSTEM_PROXY=1.
PROXY_ENV_VARS = ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
                  "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
if sys.platform == "darwin" and not os.environ.get("OPTIONSBOT_KEEP_SYSTEM_PROXY") \
        and not any(os.environ.get(k) for k in PROXY_ENV_VARS):
    os.environ["no_proxy"] = "*"
    os.environ["NO_PROXY"] = "*"

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
ENV_PATH = ROOT.parent / "Stock Shorting Bot" / ".env"

TRADING_API = "https://paper-api.alpaca.markets"
DATA_API = "https://data.alpaca.markets"

_OCC_RE = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def load_env(path: Path = ENV_PATH) -> dict:
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


class AlpacaData:
    """Thin cached client for the read-only endpoints we need."""

    def __init__(self, cache_dir: Path = CACHE, offline: bool = False):
        env = load_env()
        self.headers = {
            "APCA-API-KEY-ID": env.get("APCA_API_KEY_ID", ""),
            "APCA-API-SECRET-KEY": env.get("APCA_API_SECRET_KEY", ""),
        }
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.session = requests.Session()
        self._last = 0.0
        self.n_requests = 0
        # seconds between requests; split the ~200 req/min budget across workers
        self.min_interval = float(os.environ.get("OPTIONSBOT_MIN_INTERVAL", "0.32"))

    # ------------------------------------------------------------------ http
    def _get(self, url: str, params: dict) -> dict:
        # GET only. Guard against accidental use of order endpoints.
        assert "/orders" not in url and "/positions" not in url
        for attempt in range(6):
            wait = self.min_interval - (time.time() - self._last)  # ~190 req/min total
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.session.get(url, params=params, headers=self.headers, timeout=60)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            self.n_requests += 1
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} for {url}: {r.text[:300]}")
            return r.json()
        raise RuntimeError(f"giving up on {url}")

    def _cache_path(self, kind: str, key: str) -> Path:
        h = hashlib.sha1(key.encode()).hexdigest()[:16]
        d = self.cache_dir / kind
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{h}.json"

    def _cached(self, kind: str, key: str, fetch):
        p = self._cache_path(kind, key)
        if p.exists():
            return json.loads(p.read_text())
        if self.offline:
            return None
        data = fetch()
        _atomic_write(p, json.dumps(data))
        return data

    # ---------------------------------------------------------------- stocks
    def stock_bars(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """Daily split/dividend-adjusted ('all') and raw bars. Returns columns
        close (raw), adj_close (total-return adjusted), open/high/low raw."""
        def fetch(adjustment):
            out, token = [], None
            while True:
                params = {"symbols": symbol, "timeframe": "1Day", "start": start, "end": end,
                          "adjustment": adjustment, "feed": "sip", "limit": 10000}
                if token:
                    params["page_token"] = token
                js = self._get(f"{DATA_API}/v2/stocks/bars", params)
                out.extend(js.get("bars", {}).get(symbol, []) or [])
                token = js.get("next_page_token")
                if not token:
                    return out

        frames = {}
        for adj in ("raw", "split", "all"):
            rows = self._cached("stock", f"{symbol}|{start}|{end}|{adj}", lambda a=adj: fetch(a)) or []
            if not rows:
                return pd.DataFrame()
            f = pd.DataFrame(rows)
            f["date"] = (pd.to_datetime(f["t"]).dt.tz_convert("America/New_York")
                         .dt.normalize().dt.tz_localize(None))
            frames[adj] = f.set_index("date")
        df = frames["raw"].rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df["split_close"] = frames["split"]["c"]
        df["adj_close"] = frames["all"]["c"]
        df = df[["open", "high", "low", "close", "volume", "split_close", "adj_close"]].copy()
        # split factor applied to share counts on date t (e.g. 10.0 on NVDA 2024-06-10)
        ratio = df["split_close"] / df["close"]
        fac = (ratio / ratio.shift(1)).fillna(1.0)
        df["split_factor"] = fac.where((fac - 1).abs() > 0.02, 1.0)
        # cash dividends (per raw share, keyed by ex-date) from corporate actions
        df["dividend"] = 0.0
        for ex, rate in self.dividends(symbol, start, end).items():
            ts = pd.Timestamp(ex)
            if ts in df.index:
                df.loc[ts, "dividend"] += rate
        return df

    def dividends(self, symbol: str, start: str, end: str) -> dict:
        def fetch():
            out, token = [], None
            while True:
                params = {"symbols": symbol, "types": "cash_dividend", "start": start, "end": end,
                          "limit": 1000}
                if token:
                    params["page_token"] = token
                js = self._get(f"{DATA_API}/v1/corporate-actions", params)
                for c in (js.get("corporate_actions") or {}).get("cash_dividends", []) or []:
                    if c.get("symbol") == symbol:
                        out.append({"ex_date": c["ex_date"], "rate": float(c["rate"])})
                token = js.get("next_page_token")
                if not token:
                    return out
        rows = self._cached("divs", f"{symbol}|{start}|{end}", fetch) or []
        res = {}
        for r in rows:
            res[r["ex_date"]] = res.get(r["ex_date"], 0.0) + r["rate"]
        return res

    # ------------------------------------------------------------- contracts
    def option_contracts(self, underlying: str, exp_gte: str, exp_lte: str) -> pd.DataFrame:
        """All standard-root contracts (active + inactive/expired) in a window."""
        def fetch(status):
            out, token = [], None
            while True:
                params = {"underlying_symbols": underlying, "expiration_date_gte": exp_gte,
                          "expiration_date_lte": exp_lte, "status": status, "limit": 10000}
                if token:
                    params["page_token"] = token
                js = self._get(f"{TRADING_API}/v2/options/contracts", params)
                for c in js.get("option_contracts", []) or []:
                    out.append({k: c.get(k) for k in ("symbol", "root_symbol", "underlying_symbol",
                                                         "expiration_date", "type", "strike_price",
                                                         "style", "size", "status")})
                token = js.get("next_page_token")
                if not token:
                    return out

        rows = []
        for status in ("inactive", "active"):
            rows += self._cached("contracts", f"{underlying}|{exp_gte}|{exp_lte}|{status}",
                                 lambda: fetch(status)) or []
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).drop_duplicates("symbol")
        # Keep only the standard root (drops adjusted roots like 1SPY / AAPL1)
        df = df[df["root_symbol"] == underlying]
        df = df[df["symbol"].str.match(rf"^{underlying}\d{{6}}[CP]\d{{8}}$")]
        df["expiration_date"] = pd.to_datetime(df["expiration_date"])
        df["strike_price"] = df["strike_price"].astype(float)
        df["size"] = pd.to_numeric(df["size"], errors="coerce").fillna(100)
        df = df[df["size"] == 100]
        return df.reset_index(drop=True)

    # ------------------------------------------------------------ opt bars
    def option_bars(self, symbols: list[str], start: str, end: str) -> dict[str, pd.Series]:
        """Daily close series per option symbol. Cached per (symbol,start,end).

        To keep the request count down we cache per symbol and fetch uncached
        symbols in batches of 100.
        """
        result: dict[str, pd.Series] = {}
        todo = []
        for s in symbols:
            p = self._cache_path("optbars", f"{s}|{start}|{end}")
            if p.exists():
                result[s] = _bars_to_series(json.loads(p.read_text()))
            else:
                todo.append(s)
        if self.offline:
            todo = []
        for i in range(0, len(todo), 100):
            batch = todo[i:i + 100]
            got: dict[str, list] = {s: [] for s in batch}
            token = None
            while True:
                params = {"symbols": ",".join(batch), "timeframe": "1Day", "start": start,
                          "end": end, "limit": 10000}
                if token:
                    params["page_token"] = token
                js = self._get(f"{DATA_API}/v1beta1/options/bars", params)
                for s, bars in (js.get("bars") or {}).items():
                    got.setdefault(s, []).extend(bars)
                token = js.get("next_page_token")
                if not token:
                    break
            for s in batch:
                slim = [{"t": b["t"][:10], "c": b["c"], "v": b.get("v", 0)} for b in got.get(s, [])]
                _atomic_write(self._cache_path("optbars", f"{s}|{start}|{end}"), json.dumps(slim))
                result[s] = _bars_to_series(slim)
        return result

    def option_bar_frames(self, symbols: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
        """Like option_bars but returns DataFrame[close, volume] per symbol (same
        cache; fetches whatever option_bars would fetch)."""
        self.option_bars(symbols, start, end)
        out = {}
        for s in symbols:
            p = self._cache_path("optbars", f"{s}|{start}|{end}")
            if p.exists():
                out[s] = _bars_to_frame(json.loads(p.read_text()))
        return out


def _atomic_write(path: Path, text: str):
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _bars_to_series(bars: list, field: str = "c") -> pd.Series:
    """Daily series of one bar field ('c' close, default; 'v' volume)."""
    if not bars:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([b["t"][:10] for b in bars])
    return pd.Series([float(b.get(field, 0) or 0) for b in bars], index=idx).sort_index()


def _bars_to_frame(bars: list) -> pd.DataFrame:
    """DataFrame[close, volume] indexed by date (volume 0 when not cached)."""
    if not bars:
        return pd.DataFrame(columns=["close", "volume"], dtype=float)
    idx = pd.to_datetime([b["t"][:10] for b in bars])
    df = pd.DataFrame({"close": [float(b["c"]) for b in bars],
                       "volume": [float(b.get("v", 0) or 0) for b in bars]}, index=idx)
    return df.sort_index()


def parse_occ(sym: str):
    m = _OCC_RE.match(sym)
    if not m:
        return None
    root, ymd, cp, k = m.groups()
    return root, datetime.strptime(ymd, "%y%m%d"), cp, int(k) / 1000.0


def occ_symbol(root: str, exp: date, cp: str, strike: float) -> str:
    return f"{root}{exp:%y%m%d}{cp}{int(round(strike * 1000)):08d}"
