"""Shared helpers for the round-3 pattern study: paths, data access, cost model, metrics, trial registry.

Read-only on the main checkout: caches and the Alpaca data key are read by absolute path. Nothing here
places orders; the only network calls are Alpaca *data* GETs and free public sources.
"""
from __future__ import annotations

import json
import os
import threading
import time

import numpy as np
import pandas as pd
import requests
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # Research/round3_patterns
CACHE = os.path.join(ROOT, ".cache")
OUT = os.path.join(ROOT, "out")
RES = os.path.join(ROOT, "results")
for _d in (CACHE, OUT, RES):
    os.makedirs(_d, exist_ok=True)

MAIN = "/Users/shreyvalia/Desktop/Clive-Trading Bot"
SCACHE = os.path.join(MAIN, "Stock Shorting Bot", "research", ".cache")
SRES = os.path.join(MAIN, "Stock Shorting Bot", "research", "results")
ENV = os.path.join(MAIN, "Stock Shorting Bot", ".env")

# In-sample / out-of-sample split shared with the shorting and penny studies.
IS_END = "2021-12-31"
OOS_START = "2022-01-01"
# Long-history ETF studies (yfinance) use an earlier split so OOS covers the bot era.
LH_IS_END = "2015-12-31"
LH_OOS_START = "2016-01-01"

UA = {"User-Agent": "Clive-Research/1.0 (academic research; no contact)"}


# --------------------------------------------------------------------------- Alpaca data API (GET only)
def _env() -> dict:
    out = {}
    for line in open(ENV):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_lock = threading.Lock()
_stamps: list[float] = []


def _throttle(max_per_min=180):
    with _lock:
        now = time.time()
        while _stamps and now - _stamps[0] > 60:
            _stamps.pop(0)
        if len(_stamps) >= max_per_min:
            time.sleep(60 - (now - _stamps[0]) + 0.05)
        _stamps.append(time.time())


def alpaca_get(url: str, params: dict, tries: int = 6) -> dict:
    if not url.startswith("https://data.alpaca.markets/"):
        raise ValueError("data API only")
    e = _env()
    h = {"APCA-API-KEY-ID": e["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": e["APCA_API_SECRET_KEY"]}
    for i in range(tries):
        _throttle()
        try:
            r = requests.get(url, params=params, headers=h, timeout=60)
        except requests.RequestException:
            time.sleep(2 * (i + 1)); continue
        if r.status_code in (429,) or r.status_code >= 500:
            time.sleep(3 * (i + 1)); continue
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code} {r.text[:200]}")
        return r.json()
    raise RuntimeError("alpaca_get failed")


def bars(symbols, start, end, timeframe="1Day", adjustment="all"):
    """Alpaca SIP bars for one or more symbols. Returns long frame with ts (UTC) and OHLCV."""
    url = "https://data.alpaca.markets/v2/stocks/bars"
    p = {"symbols": ",".join(symbols), "timeframe": timeframe, "start": start, "end": end,
         "feed": "sip", "adjustment": adjustment, "limit": 10000}
    rows = []
    while True:
        j = alpaca_get(url, p)
        for s, bs in (j.get("bars") or {}).items():
            for b in bs:
                rows.append((s, b["t"], b["o"], b["h"], b["l"], b["c"], b["v"], b.get("vw")))
        tok = j.get("next_page_token")
        if not tok:
            break
        p["page_token"] = tok
    return pd.DataFrame(rows, columns=["symbol", "ts", "o", "h", "l", "c", "v", "vw"])


# --------------------------------------------------------------------------- calendar
def trading_days() -> list[str]:
    cal = pd.read_csv(os.path.join(SCACHE, "calendar.csv"))
    return [d for d in cal.date if "2016-01-04" <= d <= "2026-10-20"]


# --------------------------------------------------------------------------- cost model
def stock_cost_side(adv_dollar) -> np.ndarray:
    """One-way cost (fraction) for a liquid US stock at the open/close auction or a marketable order.
    Half-spread + slippage: 4 bp for ADV >= $500M, 6 bp for $100-500M, 10 bp for $20-100M, 20 bp below.
    Conservative vs. institutional (Frazzini-Israel-Moskowitz) and in line with retail marketable fills."""
    a = np.asarray(adv_dollar, float)
    return np.select([a >= 5e8, a >= 1e8, a >= 2e7], [4e-4, 6e-4, 10e-4], 20e-4)


ETF_COST_SIDE = 1.5e-4      # SPY/QQQ/IWM and big sector ETFs: ~0.3-1 bp half-spread + slippage
ETF_COST_SIDE_THIN = 4e-4   # country / commodity / smaller ETFs
SELL_FEES = 0.3e-4          # SEC fee + FINRA TAF on sells, rounded up
BORROW_GC = 0.005           # annual borrow, easy-to-borrow liquid names (GC ~0.25-0.5% + Alpaca markup)
BORROW_HTB = 0.05           # annual borrow assumption for high-short-interest names


# --------------------------------------------------------------------------- metrics
def perf(r: pd.Series, periods=252) -> dict:
    """r: daily returns of the sleeve on its own capital (0 on flat days)."""
    r = pd.Series(r).dropna()
    if len(r) < 5 or r.std() == 0:
        return dict(n=len(r), cagr=np.nan, vol=np.nan, sharpe=np.nan, maxdd=np.nan, hit=np.nan,
                    skew=np.nan, kurt=np.nan, total=np.nan)
    eq = (1 + r).cumprod()
    yrs = len(r) / periods
    dd = eq / eq.cummax() - 1
    return dict(n=len(r), cagr=eq.iloc[-1] ** (1 / yrs) - 1, vol=r.std() * np.sqrt(periods),
                sharpe=r.mean() / r.std() * np.sqrt(periods), maxdd=dd.min(),
                hit=(r[r != 0] > 0).mean(), skew=stats.skew(r), kurt=stats.kurtosis(r, fisher=False),
                total=eq.iloc[-1] - 1)


def split_perf(r: pd.Series, is_end=IS_END, oos_start=OOS_START) -> dict:
    r = pd.Series(r).copy()
    r.index = pd.to_datetime(r.index)
    a = perf(r[r.index <= is_end]); b = perf(r[r.index >= oos_start]); c = perf(r)
    out = {}
    for k, d in (("is", a), ("oos", b), ("all", c)):
        for m, v in d.items():
            out[f"{k}_{m}"] = v
    return out


def by_year(r: pd.Series) -> pd.Series:
    r = pd.Series(r).copy(); r.index = pd.to_datetime(r.index)
    return r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)


def deflated_sharpe(sr_daily, n_obs, skew, kurt, var_trials_daily, n_trials):
    """Bailey & Lopez de Prado (2014) DSR. Sharpe inputs are per-period (daily, not annualised)."""
    emc = 0.5772156649
    sr0 = np.sqrt(var_trials_daily) * ((1 - emc) * stats.norm.ppf(1 - 1 / n_trials)
                                       + emc * stats.norm.ppf(1 - 1 / (n_trials * np.e)))
    z = (sr_daily - sr0) * np.sqrt(n_obs - 1) / np.sqrt(1 - skew * sr_daily + (kurt - 1) / 4 * sr_daily ** 2)
    return float(stats.norm.cdf(z)), float(sr0)


# --------------------------------------------------------------------------- trial registry
TRIALS = os.path.join(RES, "trials_daily.parquet")
TRIALS_META = os.path.join(RES, "trials_meta.json")


def register(name: str, family: str, r: pd.Series, params: dict | None = None):
    """Store every variant's daily return series (all trials count toward the DSR)."""
    r = pd.Series(r, dtype=float).copy(); r.index = pd.to_datetime(r.index).strftime("%Y-%m-%d")
    meta = json.load(open(TRIALS_META)) if os.path.exists(TRIALS_META) else {}
    meta[name] = {"family": family, "params": params or {}}
    json.dump(meta, open(TRIALS_META, "w"), indent=1, default=str)
    df = pd.read_parquet(TRIALS) if os.path.exists(TRIALS) else pd.DataFrame()
    df = df.drop(columns=[name], errors="ignore")
    df = df.join(r.rename(name), how="outer") if len(df) else r.rename(name).to_frame()
    df.sort_index().to_parquet(TRIALS)


# --------------------------------------------------------------------------- reference series
def shortbot_daily() -> pd.Series:
    """Daily account return of the shorting v2 backtest (P7 if present, else P1)."""
    for f in ("trades_P7_intraday_top5_eq50_min70_strict.csv", "trades_P1_intraday_top5_eq50.csv"):
        p = os.path.join(SRES, f)
        if os.path.exists(p):
            t = pd.read_csv(p)
            s = t.groupby("date")["contrib"].sum()
            s.index = pd.to_datetime(s.index)
            return s.rename(f.split("_")[1])
    raise FileNotFoundError


def spy_daily() -> pd.Series:
    e = pd.read_parquet(os.path.join(SCACHE, "etf_daily.parquet"))
    s = e[e.symbol == "SPY"].set_index("date")["c"].sort_index()
    s.index = pd.to_datetime(s.index)
    return s.pct_change().rename("SPY")


def corr_block(r: pd.Series) -> dict:
    """Correlation of a sleeve's daily returns to the short bot (on bot-active days, and with 0-filled
    calendar) and to SPY; also the sleeve's return on the short bot's 20 worst days."""
    r = pd.Series(r).copy(); r.index = pd.to_datetime(r.index)
    sb = shortbot_daily(); spy = spy_daily()
    idx = spy.dropna().index
    rr = r.reindex(idx).fillna(0); sbb = sb.reindex(idx).fillna(0)
    worst = sbb.nsmallest(20).index
    spy_worst = spy.reindex(idx).nsmallest(20).index
    return dict(corr_shortbot=rr.corr(sbb), corr_shortbot_active=rr[sbb != 0].corr(sbb[sbb != 0]),
                corr_spy=rr.corr(spy.reindex(idx)),
                ret_on_bot_worst20=rr[worst].sum(), bot_on_bot_worst20=sbb[worst].sum(),
                ret_on_spy_worst20=rr[spy_worst].sum())
