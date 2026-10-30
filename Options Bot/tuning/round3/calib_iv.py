"""Implied vols of every cached option print (2024-01..2026-10) + the vol-index term level.

WORK/iv_<SYM>.pkl: date, expiry, strike, cp, close, volume, spot, dte, T, r, q, F, iv, base,
z = ln(K/F) / (base*sqrt(T)), ratio = iv / base, plus VIX/TS/SKEW of the day.
base(T) = SPX vol-index term structure (VIX9D/VIX/VIX3M/VIX6M) at T, scaled for IWM/QQQ by
RVX/VIX or VXN/VIX (30-day index ratio) - the proxy can only use what exists back in history.
"""
import sys

import numpy as np
import pandas as pd

import r3lib as L
from common import WORK

for sym in sys.argv[1:] or ["SPY", "IWM", "QQQ"]:
    df = pd.read_pickle(WORK / f"chain_{sym}.pkl")
    df = df[(df.dte >= 2) & (df.dte <= 200) & (df.close >= 0.05) & (df.volume >= 1)].copy()
    vp = L.vol_panel(sorted(df.date.unique()))
    df = df.join(vp, on="date")
    df["T"] = df.dte / 365.0
    df["q"] = L.DIV[sym]
    df["F"] = df.spot * np.exp((df.r - df.q) * df["T"])
    cpn = np.where(df.cp == "C", 1.0, -1.0)
    # OTM (forward) only: puts K<F, calls K>=F
    otm = ((cpn < 0) & (df.strike < df.F)) | ((cpn > 0) & (df.strike >= df.F))
    df = df[otm].copy()
    cpn = np.where(df.cp == "C", 1.0, -1.0)
    df["iv"] = L.implied_vol(df.close.values, df.spot.values, df.strike.values, df["T"].values,
                             df.r.values, df.q.values, cpn)
    base = L.term_vol(df[["VIX9D", "VIX", "VIX3M", "VIX6M"]].values, df["T"].values)
    if sym == "IWM":
        base = base * df.RVX.values / df.VIX.values
    elif sym == "QQQ":
        base = base * df.VXN.values / df.VIX.values
    df["base"] = base
    df["z"] = np.log(df.strike / df.F) / (df.base * np.sqrt(df["T"]))
    df["ratio"] = df.iv / df.base
    df["delta"] = L.delta(df.spot.values, df.strike.values, df["T"].values, df.r.values, df.q.values,
                          df.iv.values, cpn)
    df = df[np.isfinite(df.iv) & (df.iv > 0.03) & (df.iv < 2.0)]
    df.to_pickle(WORK / f"iv_{sym}.pkl")
    print(sym, len(df), flush=True)
    df["zb"] = pd.cut(df.z, [-8, -4, -3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3])
    df["tb"] = pd.cut(df.dte, [1, 5, 10, 16, 25, 40, 55, 75, 100, 200])
    print((df.groupby(["zb", "tb"], observed=True).ratio.median().unstack().round(3)).to_string())
