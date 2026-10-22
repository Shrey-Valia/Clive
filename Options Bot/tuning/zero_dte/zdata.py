"""Read-only Alpaca data access for the 0DTE research harness.

GET only (asserted). ~60 req/min (1 worker; keys are shared with other agents).
Credentials come from "Stock Shorting Bot/.env" and are never printed or cached.
Everything is cached under tuning/zero_dte/.cache (gitignored).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from optionsbot.data import load_env  # noqa: E402  (reads the .env, prints nothing)

CACHE = HERE / ".cache"
CACHE.mkdir(exist_ok=True)
DATA_API = "https://data.alpaca.markets"
MIN_INTERVAL = float(os.environ.get("ZDTE_MIN_INTERVAL", "1.0"))  # 60 req/min


class Client:
    def __init__(self):
        env = load_env()
        self.h = {"APCA-API-KEY-ID": env.get("APCA_API_KEY_ID", ""),
                  "APCA-API-SECRET-KEY": env.get("APCA_API_SECRET_KEY", "")}
        self.s = requests.Session()
        self.last = 0.0
        self.n = 0

    def get(self, path: str, params: dict) -> dict:
        assert "/orders" not in path and "/positions" not in path and "/account" not in path
        url = DATA_API + path
        for attempt in range(8):
            w = MIN_INTERVAL - (time.time() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.time()
            try:
                r = self.s.get(url, params=params, headers=self.h, timeout=60)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            self.n += 1
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {path}: {r.text[:200]}")
            return r.json()
        raise RuntimeError(f"gave up on {path}")

    def paged(self, path: str, params: dict, key: str = "bars") -> dict:
        out: dict = {}
        p = dict(params)
        while True:
            js = self.get(path, p)
            for sym, bars in (js.get(key) or {}).items():
                out.setdefault(sym, []).extend(bars or [])
            tok = js.get("next_page_token")
            if not tok:
                return out
            p["page_token"] = tok


_C: Client | None = None


def client() -> Client:
    global _C
    if _C is None:
        _C = Client()
    return _C


def _bars_df(rows: list) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["o", "h", "l", "c", "v", "vw", "n"])
    df = pd.DataFrame(rows)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    return df.set_index("t").sort_index()


def spy_minutes(day: str) -> pd.DataFrame:
    """SPY 1-minute SIP bars for one session (regular + a little pre for gap)."""
    f = CACHE / "spy_min" / f"{day}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    f.parent.mkdir(exist_ok=True)
    js = client().paged("/v2/stocks/bars", {"symbols": "SPY", "timeframe": "1Min",
                                            "start": f"{day}T13:00:00Z", "end": f"{day}T21:30:00Z",
                                            "feed": "sip", "adjustment": "raw", "limit": 10000})
    df = _bars_df(js.get("SPY", []))
    df.to_parquet(f)
    return df


def spy_minutes_range(start: str, end: str) -> pd.DataFrame:
    """Bulk SPY minute bars, cached by month."""
    months = pd.period_range(start, end, freq="M")
    parts = []
    for m in months:
        f = CACHE / "spy_month" / f"{m}.parquet"
        if m == pd.Period(pd.Timestamp.now(), "M"):
            f = CACHE / "spy_month" / f"{m}_partial.parquet"   # current month: snapshot once
        if not f.exists():
            f.parent.mkdir(exist_ok=True)
            s = m.start_time.strftime("%Y-%m-%dT00:00:00Z")
            e_ts = min(m.end_time.tz_localize("UTC") + pd.Timedelta(seconds=1),
                       pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=20))
            e = e_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            js = client().paged("/v2/stocks/bars", {"symbols": "SPY", "timeframe": "1Min",
                                                    "start": s, "end": e, "feed": "sip",
                                                    "adjustment": "raw", "limit": 10000})
            df = _bars_df(js.get("SPY", []))
            df.to_parquet(f)
        parts.append(pd.read_parquet(f))
    return pd.concat(parts)


_SPY_ALL = None


def spy_all() -> pd.DataFrame:
    global _SPY_ALL
    if _SPY_ALL is None:
        a = spy_minutes_range("2023-09-01", "2026-10-21")
        _SPY_ALL = a[~a.index.duplicated()].sort_index()
    return _SPY_ALL


def occ(day: pd.Timestamp, cp: str, k: float) -> str:
    return f"SPY{day:%y%m%d}{cp}{int(round(k * 1000)):08d}"


def option_minutes(day: str, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """1-minute option bars for one session for the given contracts (cached per day)."""
    f = CACHE / "opt_min" / f"{day}.json"
    have: dict = {}
    if f.exists():
        have = json.loads(f.read_text())
    need = [s for s in symbols if s not in have]
    if need:
        f.parent.mkdir(exist_ok=True)
        for i in range(0, len(need), 100):
            chunk = need[i:i + 100]
            js = client().paged("/v1beta1/options/bars", {"symbols": ",".join(chunk), "timeframe": "1Min",
                                                          "start": f"{day}T13:25:00Z",
                                                          "end": f"{day}T21:15:00Z", "limit": 10000})
            for s in chunk:
                have[s] = js.get(s, [])
        f.write_text(json.dumps(have))
    return {s: _bars_df(have.get(s, [])) for s in symbols}
