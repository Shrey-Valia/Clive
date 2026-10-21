"""Live indicator computation: prior daily bars + today's partial bar.

Indicators come from optionsbot/indicators.py (the same code the research
used), on split-adjusted OHLC. Today's bar uses the live price as the close
proxy and today's high/low so far, so IBS and RSI(2) are what they would be
if the session closed at the current price.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import math
import sys

import numpy as np
import pandas as pd
import requests

from . import config as C
from .strategy import Signal

if str(C.ROOT) not in sys.path:
    sys.path.insert(0, str(C.ROOT))
from optionsbot import indicators as ind  # noqa: E402

log = logging.getLogger("optionslive")
HISTORY_DAYS = 420          # calendar days of daily bars (>= 200 sessions + warm-up)


def with_today(hist: pd.DataFrame, today: dt.date, o: float, h: float, l: float, c: float) -> pd.DataFrame:
    """Append (or replace) today's partial bar."""
    ts = pd.Timestamp(today)
    hist = hist[hist.index < ts]
    row = pd.DataFrame({"open": [o], "high": [max(h, c)], "low": [min(l, c)], "close": [c]}, index=[ts])
    return pd.concat([hist[["open", "high", "low", "close"]], row])


def compute(symbol: str, bars: pd.DataFrame) -> Signal:
    """Signal values on the last row of ``bars`` (which includes today)."""
    c = bars["close"].astype(float)
    rsi2 = ind.rsi(c, C.RSI_N)
    ibs = ind.ibs(bars)
    s200 = ind.sma(c, C.TREND_SMA)
    s5 = ind.sma(c, C.EXIT_SMA)
    last = bars.index[-1]
    f = lambda s: float(s.iloc[-1]) if pd.notna(s.iloc[-1]) else math.nan
    return Signal(symbol, float(c.iloc[-1]), f(s200), f(s5), f(rsi2), f(ibs), last.date())


def live_bar(broker, symbol: str, today: dt.date) -> dict:
    """Today's open/high/low so far and the live price. Combines the live
    snapshot (IEX on the free plan) with delayed SIP minute bars so the range
    is not narrower than the consolidated tape."""
    snap = broker.stock_snapshot(symbol)
    if snap["bar_date"] != today or snap["price"] is None:
        raise RuntimeError(f"{symbol}: no live bar for {today} (snapshot bar {snap['bar_date']})")
    o, h, l, px = snap["open"], snap["high"], snap["low"], snap["price"]
    src = snap["feed"]
    sip = broker.sip_intraday_range(symbol, today)
    if sip:
        o = sip["open"]
        h, l = max(h, sip["high"]), min(l, sip["low"])
        src += "+sip(delayed)"
    return {"open": o, "high": max(h, px), "low": min(l, px), "close": px, "source": src,
            "trade_time": snap["trade_time"]}


def signal_for(broker, symbol: str, today: dt.date, hist: pd.DataFrame | None = None) -> tuple[Signal, pd.DataFrame, dict]:
    if hist is None:
        hist = broker.daily_bars(symbol, today - dt.timedelta(days=HISTORY_DAYS), today - dt.timedelta(days=1))
    lb = live_bar(broker, symbol, today)
    bars = with_today(hist, today, lb["open"], lb["high"], lb["low"], lb["close"])
    return compute(symbol, bars), bars, lb


# ---------------------------------------------------------------- VIX
def _cboe_get(url: str):
    r = requests.get(url, timeout=C.REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
    r.raise_for_status()
    return r


def _history_last(sym: str) -> tuple[float, dt.date] | None:
    """Last close from the Cboe history CSV (cached per day under live/.cache)."""
    C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = C.CACHE_DIR / f"{sym}_History_{dt.date.today():%Y%m%d}.csv"
    try:
        if not f.exists():
            f.write_text(_cboe_get(C.CBOE_HISTORY.format(sym=sym)).text)
        text = f.read_text()
    except Exception as e:                                       # fall back to the research cache
        log.warning("Cboe %s history download failed: %s", sym, e)
        alt = C.ROOT / ".cache" / "charts" / f"{sym}_History.csv"
        if not alt.exists():
            return None
        text = alt.read_text()
    df = pd.read_csv(io.StringIO(text))
    df.columns = [x.strip().upper() for x in df.columns]
    if df.empty:
        return None
    d = pd.to_datetime(df["DATE"].iloc[-1], format="%m/%d/%Y").date()
    return float(df["CLOSE"].iloc[-1]), d


def _delayed_quote(sym: str) -> tuple[float, dt.datetime] | None:
    try:
        js = _cboe_get(C.CBOE_QUOTE.format(sym=sym)).json()
        d = js["data"]
        px = float(d["current_price"])
        ts = dt.datetime.fromisoformat(d.get("last_trade_time") or js["timestamp"].replace(" ", "T"))
        return (px, ts) if px > 0 else None
    except Exception as e:
        log.warning("Cboe delayed quote %s failed: %s", sym, e)
        return None


def vix_levels(today: dt.date) -> dict:
    """VIX and VIX3M: today's delayed quote if it is from today, else the most
    recent daily close (no older than 5 days, as in the backtest ffill)."""
    out = {}
    for sym in ("VIX", "VIX3M"):
        q = _delayed_quote(sym)
        if q and q[1].date() == today:
            out[sym] = (q[0], f"cboe delayed {q[1]:%Y-%m-%d %H:%M}")
            continue
        h = _history_last(sym)
        if h and (today - h[1]).days <= 7:
            out[sym] = (h[0], f"cboe close {h[1]}")
        else:
            out[sym] = (None, "unavailable")
    vix, v3 = out["VIX"][0], out["VIX3M"][0]
    return {"vix": vix, "vix3m": v3, "ratio": (vix / v3) if vix and v3 else None,
            "source": f"VIX {out['VIX'][1]}; VIX3M {out['VIX3M'][1]}"}


def sessions_from_bars(index: pd.DatetimeIndex, today: dt.date, ahead_days: int = 30,
                       calendar: list[dict] | None = None) -> list[dt.date]:
    """Trading sessions: past ones from the bar index, future ones from the
    broker calendar when available, else weekdays (holidays not excluded)."""
    past = sorted({d.date() for d in index if d.date() <= today} | {today})
    if calendar:
        fut = sorted(dt.date.fromisoformat(c["date"]) for c in calendar if dt.date.fromisoformat(c["date"]) > today)
    else:
        fut = [d.date() for d in pd.bdate_range(today + dt.timedelta(days=1), periods=ahead_days)]
    return past + fut


def isfinite(x) -> bool:
    return x is not None and isinstance(x, (int, float, np.floating)) and math.isfinite(x)
