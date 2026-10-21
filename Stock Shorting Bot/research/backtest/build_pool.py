"""Daily panel -> candidate pool (top daily gainers per day) plus forward-return fields.

Prices: adjustment=all for every return (a short pays dividends, so total-return
adjusted prices are the right basis); adjustment=raw for price-level filters
(prev close > $3, price > $5) and dollar volume, because back-adjusted prices
of reverse-split names would otherwise look like $30 stocks when they were $1.
"""
from __future__ import annotations

import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, alpaca_get  # noqa: E402

POOL_PER_DAY = 40


def load(adj):
    fs = sorted(glob.glob(os.path.join(CACHE, f"daily_{adj}", "*.parquet")))
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)


def etfs():
    path = os.path.join(CACHE, "etf_daily.parquet")
    if os.path.exists(path):
        return
    out = []
    params = {"symbols": "SPY,IWM,QQQ", "timeframe": "1Day", "start": "2015-10-01",
              "end": "2026-10-20T23:59:00Z", "feed": "sip", "adjustment": "all", "limit": 10000}
    while True:
        j = alpaca_get("https://data.alpaca.markets/v2/stocks/bars", params)
        for s, bars in j["bars"].items():
            out += [(s, b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars]
        if not j.get("next_page_token"):
            break
        params["page_token"] = j["next_page_token"]
    pd.DataFrame(out, columns=["symbol", "date", "o", "h", "l", "c", "v"]).to_parquet(path)


def main():
    etfs()
    a = load("all")
    r = load("raw")[["symbol", "date", "c", "v"]].rename(columns={"c": "c_raw", "v": "v_raw"})
    df = a.merge(r, on=["symbol", "date"], how="left")
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    cal = pd.read_csv(os.path.join(CACHE, "calendar.csv"))
    tdays = list(cal.date)
    tidx = {d: i for i, d in enumerate(tdays)}
    df["ti"] = df.date.map(tidx)
    df = df[df.ti.notna()].copy()
    df["ti"] = df.ti.astype(int)
    g = df.groupby("symbol", sort=False)
    df["pc"] = g.c.shift(1)
    df["pc_raw"] = g.c_raw.shift(1)
    df["pti"] = g.ti.shift(1)
    df["chg"] = df.c / df.pc - 1
    df["hchg"] = df.h / df.pc - 1
    df["nhist"] = g.cumcount()
    df["dv_raw"] = df.c_raw * df.v_raw
    df["avgv10"] = g.v.transform(lambda s: s.shift(1).rolling(10, min_periods=5).mean())
    df["adv20"] = g.dv_raw.transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    tr = np.maximum(df.h, df.pc) - np.minimum(df.l, df.pc)
    df["tr_pct"] = tr / df.pc
    df["atr14"] = df.groupby("symbol", sort=False).tr_pct.transform(lambda s: s.shift(1).rolling(14, min_periods=7).mean())
    df["sma20"] = g.c.transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean())
    df["ret5_prior"] = df.pc / g.c.shift(6) - 1
    # forward fields (adjusted). k = 1..5 trading rows ahead
    for k in range(1, 6):
        df[f"ti{k}"] = g.ti.shift(-k)
        df[f"o{k}"] = g.o.shift(-k)
        df[f"h{k}"] = g.h.shift(-k)
        df[f"l{k}"] = g.l.shift(-k)
        df[f"c{k}"] = g.c.shift(-k)
    df["missing_prev"] = (df.ti - df.pti) != 1
    df.to_parquet(os.path.join(CACHE, "panel.parquet"))

    u = pd.read_csv(os.path.join(CACHE, "universe.csv"))
    ok = (df.date >= "2016-01-04") & (df.pc_raw > 3) & (df.chg > 0.0) & (df.nhist >= 20) \
        & (~df.missing_prev) & (df.adv20 >= 100_000) & df.symbol.isin(u.symbol)
    p = df[ok].copy()
    p["rk_close"] = p.groupby("date").chg.rank(ascending=False, method="first")
    p = p[p.rk_close <= POOL_PER_DAY]
    p.to_parquet(os.path.join(CACHE, "pool.parquet"))
    print("panel", len(df), "pool", len(p), "days", p.date.nunique())
    per_day = df[df.date >= "2016-01-04"].groupby(df.date.str[:4]).symbol.nunique()
    print("symbols with bars per year:\n", per_day.to_string())


if __name__ == "__main__":
    main()
