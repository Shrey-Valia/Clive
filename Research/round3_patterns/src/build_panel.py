"""Build the liquid-stock daily panel from the shorting study's cached Alpaca bars (read-only).

Returns use adjustment=all prices (splits + dividends). Price and dollar-volume filters use raw prices.
All filter fields are lagged one day (known at the prior close) except the day-0 move itself.
Output: .cache/panel_liquid.parquet with names that ever had 20d ADV >= $10M and raw price >= $5.
"""
import glob
import os

import numpy as np
import pandas as pd

from common import CACHE, SCACHE

fs = sorted(glob.glob(os.path.join(SCACHE, "daily_all", "*.parquet")))
adj = pd.concat([pd.read_parquet(f, columns=["symbol", "date", "o", "h", "l", "c", "v"]) for f in fs])
raw = pd.concat([pd.read_parquet(f, columns=["symbol", "date", "c", "v"])
                 for f in sorted(glob.glob(os.path.join(SCACHE, "daily_raw", "*.parquet")))])
raw = raw.rename(columns={"c": "c_raw", "v": "v_raw"})
df = adj.merge(raw, on=["symbol", "date"], how="left")
df = df[df.date >= "2015-10-01"].sort_values(["symbol", "date"]).reset_index(drop=True)
g = df.groupby("symbol", sort=False)
df["dv"] = df.c_raw * df.v_raw
df["adv20"] = g.dv.transform(lambda x: x.rolling(20, min_periods=15).mean())
df["pc"] = g.c.shift(1)
df["r_cc"] = df.c / df.pc - 1
df["r_co"] = df.o / df.pc - 1          # overnight (prior close -> open)
df["r_oc"] = df.c / df.o - 1           # intraday
# bad-print flag: a >50% one-day move that reverses to within 20% of the prior level the next day,
# or any day with |move| > 90% / open-close ratio impossible. Rows flagged (and their neighbours) are
# excluded from signals and trades.
nxt = g.r_cc.shift(-1)
spike = (df.r_cc.abs() > 0.5) & (((1 + df.r_cc) * (1 + nxt)).between(0.8, 1.25))
bad = spike | (df.r_cc < -0.9) | (df.r_co < -0.9) | (df.r_co > 3) | (df.r_cc > 5)
df["bad"] = bad
g = df.groupby("symbol", sort=False)
bw = df.bad.copy()
for k in (1, -1, -2, -3, -4, -5):
    bw = bw | g.bad.shift(k, fill_value=False)
df["bad_win"] = bw   # bad print anywhere from T-1 to T+5
# lagged filters (known before day-0 trading)
df["adv20_l"] = g.adv20.shift(1)
df["px_l"] = g.c_raw.shift(1)
keep = df.groupby("symbol").apply(lambda x: ((x.adv20 >= 1e7) & (x.c_raw >= 5)).any())
df = df[df.symbol.isin(keep[keep].index)]
st = pd.read_csv(os.path.join(SCACHE, "universe.csv"))[["symbol", "status", "exchange"]]
df = df.merge(st, on="symbol", how="left")
for c in ["o", "h", "l", "c", "c_raw", "dv", "adv20", "pc", "r_cc", "r_co", "r_oc", "adv20_l", "px_l"]:
    df[c] = df[c].astype("float32")
df.to_parquet(os.path.join(CACHE, "panel_liquid.parquet"))
print(df.shape, df.symbol.nunique(), df.date.min(), df.date.max())
print(df.groupby(df.date.str[:4]).apply(lambda x: (x.adv20_l >= 2e7).sum() / x.date.nunique()))
