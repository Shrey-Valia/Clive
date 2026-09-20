#!/usr/bin/env python3
"""Backtest the 24-hour hold: enter at the close, exit at the next close.

    python3 backtest.py                 # 600 small caps, 3y, +15% spikes
    python3 backtest.py --min-spike 25 --years 5

Answers the only question that matters before sizing this strategy: after a
big up day, does anything in the scanner's feature set predict tomorrow?

Results are cached in .cache/backtest_px.pkl; delete it to re-download.
"""
from __future__ import annotations

import argparse
import json
import os
import warnings

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")
CACHE = ".cache"


def universe(n: int = 600) -> list[str]:
    r = requests.post(
        "https://scanner.tradingview.com/america/scan",
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
        json={
            "filter": [
                {"left": "close", "operation": "greater", "right": 3},
                {"left": "market_cap_basic", "operation": "in_range",
                 "right": [25_000_000, 3_000_000_000]},
                {"left": "exchange", "operation": "in_range",
                 "right": ["NASDAQ", "NYSE", "AMEX"]},
                {"left": "is_primary", "operation": "equal", "right": True},
                {"left": "average_volume_10d_calc", "operation": "greater",
                 "right": 300_000},
            ],
            "options": {"lang": "en"}, "markets": ["america"],
            "symbols": {"query": {"types": ["stock"]}, "tickers": []},
            "columns": ["name"],
            "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
            "range": [0, n],
        },
        timeout=30,
    )
    r.raise_for_status()
    return [x["d"][0] for x in r.json()["data"]]


def prices(tickers: list[str], years: int) -> pd.DataFrame:
    path = os.path.join(CACHE, f"backtest_px_{years}y.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    import yfinance as yf

    df = yf.download(tickers, period=f"{years}y", interval="1d",
                     group_by="ticker", auto_adjust=False, threads=True,
                     progress=False)
    os.makedirs(CACHE, exist_ok=True)
    df.to_pickle(path)
    return df


def events(px: pd.DataFrame, tickers: list[str], min_spike: float) -> pd.DataFrame:
    out = []
    for t in tickers:
        try:
            d = px[t].dropna(subset=["Close", "High", "Low"])
        except Exception:
            continue
        if len(d) < 60:
            continue
        c, h, l = d["Close"], d["High"], d["Low"]
        chg = c.pct_change() * 100
        cir = 100 * (c - l) / (h - l).replace(0, np.nan)
        ext = 100 * (c / c.rolling(20).mean() - 1)
        nxt = c.shift(-1) / c - 1          # enter close T, exit close T+1
        m = (chg >= min_spike) & nxt.notna() & cir.notna() & ext.notna()
        if m.sum():
            out.append(pd.DataFrame({"ticker": t, "date": d.index[m],
                                     "chg": chg[m], "cir": cir[m],
                                     "ext": ext[m], "nxt": 100 * nxt[m]}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def report(e: pd.DataFrame) -> None:
    from scipy import stats

    n = len(e)
    print(f"\n{n:,} spike events\n")
    print("LONG the spike (buy close, sell next close)")
    print(f"  mean {e.nxt.mean():+.2f}%   median {e.nxt.median():+.2f}%   "
          f"win {100*(e.nxt>0).mean():.1f}%   skew {e.nxt.skew():.1f}")

    s = -e.nxt
    print("\nSHORT the spike")
    print(f"  mean {s.mean():+.2f}%   median {s.median():+.2f}%   "
          f"win {100*(s>0).mean():.1f}%")
    print(f"  worst 1% {s.quantile(.01):.1f}%   "
          f"trades losing >10%: {100*(s<-10).mean():.1f}%   "
          f">25%: {100*(s<-25).mean():.1f}%")
    print(f"  cumulative over {n:,} trades: {s.sum():+,.0f}%")

    print("\nDoes any feature predict tomorrow?")
    print(f"  {'feature':<24}{'pearson':>9}{'p':>8}{'spearman':>10}{'p':>8}  verdict")
    for name, col in [("close-in-range", e.cir), ("spike size", e.chg),
                      ("extension above SMA20", e.ext)]:
        pr, pp = stats.pearsonr(col, e.nxt)
        sr, sp = stats.spearmanr(col, e.nxt)
        verdict = "signal" if min(pp, sp) < 0.05 else "NO SIGNAL"
        print(f"  {name:<24}{pr:>9.3f}{pp:>8.3f}{sr:>10.3f}{sp:>8.3f}  {verdict}")

    print("\nNext-day return by close-in-range")
    b = pd.cut(e.cir, [0, 20, 40, 60, 80, 101],
               labels=["0-20 (closed LOW)", "20-40", "40-60", "60-80",
                       "80-100 (closed HIGH)"])
    print(f"  {'bucket':<22}{'n':>6}{'mean':>9}{'median':>9}{'short win%':>12}")
    for k, v in e.groupby(b, observed=True):
        if len(v) < 25:
            continue
        print(f"  {k:<22}{len(v):>6}{v.nxt.mean():>8.2f}%{v.nxt.median():>8.2f}%"
              f"{100*(-v.nxt>0).mean():>11.1f}%")

    print("\nShort P&L by how extended the name already was")
    b2 = pd.cut(e.ext, [-100, 10, 25, 50, 100, 1e4],
                labels=["<10%", "10-25%", "25-50%", "50-100%", "100%+"])
    print(f"  {'ext above SMA20':<22}{'n':>6}{'avg short':>11}{'win%':>8}")
    for k, v in e.groupby(b2, observed=True):
        if len(v) < 25:
            continue
        print(f"  {k:<22}{len(v):>6}{-v.nxt.mean():>10.2f}%"
              f"{100*(-v.nxt>0).mean():>7.1f}%")


def main() -> int:
    p = argparse.ArgumentParser(description="Backtest the 24-hour hold.")
    p.add_argument("--min-spike", type=float, default=15.0)
    p.add_argument("--years", type=int, default=3)
    p.add_argument("--universe", type=int, default=600)
    a = p.parse_args()

    print(f"Universe: top {a.universe} small caps | {a.years}y daily | "
          f"spikes >= +{a.min_spike:.0f}%")
    tick = universe(a.universe)
    px = prices(tick, a.years)
    e = events(px, tick, a.min_spike)
    if e.empty:
        print("No events found.")
        return 1
    report(e)

    os.makedirs(os.path.join("output", "backtest"), exist_ok=True)
    out = os.path.join("output", "backtest", "events.csv")
    e.to_csv(out, index=False)
    print(f"\nEvents written to {out} ({len(e):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
