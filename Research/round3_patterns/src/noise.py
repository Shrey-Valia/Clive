"""Noise-area intraday momentum (Zarattini, Barbon & Aziz 2024), reusable implementation.

Day i, minute j (0 = 09:30 bar). open_i = first regular-session print.
sigma_{i,j} = mean over the previous `lb` sessions of |close_{d,j} / open_d - 1|.
UB = max(open_i, prevclose_i) * (1 + band_mult*sigma), LB = min(open_i, prevclose_i) * (1 - band_mult*sigma).
At each check minute (every `step` minutes from 10:00 to 15:30, using the bar close):
  flat  -> long if px > UB, short if px < LB
  long  -> exit if px < max(UB, VWAP); short -> exit if px > min(LB, VWAP)   (then may re-enter same check)
Exit any open position at the official close (MOC). Fill = the NEXT minute's open when fill_next=True
(one-minute latency), else the signal bar's close.
Sizing: 'lev1' = 100% notional; 'voltgt' = min(cap, target_daily_vol / trailing 14d daily vol of the ETF).
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

from common import CACHE
from lib import alpaca_etf

_CACHE = {}


def load(sym):
    if sym in _CACHE:
        return _CACHE[sym]
    fs = sorted(glob.glob(os.path.join(CACHE, "minute_etf", f"{sym}_*.parquet")))
    m = pd.concat([pd.read_parquet(f) for f in fs])
    t = pd.to_datetime(m.ts, utc=True).dt.tz_convert("America/New_York")
    m["day"] = t.dt.tz_localize(None).dt.normalize()
    m["mod"] = t.dt.hour * 60 + t.dt.minute - 570
    m = m[(m["mod"] >= 0) & (m["mod"] < 390)]
    piv = lambda col, f: m.pivot_table(index="day", columns="mod", values=col, aggfunc=f).reindex(columns=range(390))
    Cl = piv("c", "last").ffill(axis=1)
    Op = piv("o", "first")
    V = piv("v", "sum").fillna(0)
    PV = m.assign(pv=m.vw * m.v).pivot_table(index="day", columns="mod", values="pv", aggfunc="sum").reindex(columns=range(390)).fillna(0)
    d = alpaca_etf(sym)
    keep = Cl.index.intersection(d.index)
    out = dict(Cl=Cl.loc[keep], Op=Op.loc[keep].fillna(Cl.loc[keep]), V=V.loc[keep], PV=PV.loc[keep], D=d.loc[keep])
    _CACHE[sym] = out
    return out


def run(sym, lb=14, step=30, band_mult=1.0, cost_side=0.5e-4, sell_fee=0.3e-4, lev_mode="voltgt",
        target=0.02, cap=2.0, fill_next=True, first_check=29, return_trades=False):
    x = load(sym)
    Cl, Op, V, PV, D = x["Cl"], x["Op"], x["V"], x["PV"], x["D"]
    o = Op[0]; pc = D.c.shift(1); close = D.c
    half = Cl.notna().sum(axis=1) < 300
    half = half | (Cl[389].isna())
    move = (Cl.div(o, axis=0) - 1).abs()
    sigma = move.rolling(lb, min_periods=max(5, int(lb * 0.7))).mean().shift(1).values * band_mult
    ub = np.maximum(o, pc).values[:, None] * (1 + sigma)
    lbd = np.minimum(o, pc).values[:, None] * (1 - sigma)
    vwap = (PV.cumsum(axis=1) / V.cumsum(axis=1).replace(0, np.nan)).values
    px = Cl.values; opx = Op.values
    checks = list(range(first_check, 389, step))
    dvol = D.c.pct_change().rolling(14).std().shift(1).values
    n = len(Cl)
    out = np.zeros(n); sides = np.zeros(n); expo = np.zeros(n)
    trades = []
    for i in range(n):
        if half.iloc[i] or np.isnan(sigma[i, first_check]) or np.isnan(pc.iloc[i]):
            continue
        pos = 0; entry = np.nan; pnl = 0.0; k = 0; tin = 0
        for j in checks:
            p = px[i, j]
            if np.isnan(p):
                continue
            fill = opx[i, j + 1] if fill_next and j + 1 < 390 and not np.isnan(opx[i, j + 1]) else p
            if pos == 1 and p < max(ub[i, j], vwap[i, j]):
                pnl += fill / entry - 1; pos = 0; k += 1
                trades.append((Cl.index[i], 1, tj, j, entry, fill))
            elif pos == -1 and p > min(lbd[i, j], vwap[i, j]):
                pnl += -(fill / entry - 1); pos = 0; k += 1
                trades.append((Cl.index[i], -1, tj, j, entry, fill))
            if pos == 0:
                if p > ub[i, j]:
                    pos = 1; entry = fill; k += 1; tj = j
                elif p < lbd[i, j]:
                    pos = -1; entry = fill; k += 1; tj = j
            if pos != 0:
                tin += step
        if pos != 0:
            pnl += pos * (close.iloc[i] / entry - 1); k += 1
            trades.append((Cl.index[i], pos, tj, 390, entry, close.iloc[i]))
        if lev_mode == "lev1":
            lev = 1.0
        else:
            lev = float(np.clip(target / dvol[i], 0, cap)) if dvol[i] > 0 else 0.0
        out[i] = lev * (pnl - k * cost_side - (k / 2) * sell_fee)
        sides[i] = k; expo[i] = lev * tin / 390
    r = pd.Series(out, index=Cl.index)
    info = pd.DataFrame({"sides": sides, "time_in_mkt_x_lev": expo}, index=Cl.index)
    if return_trades:
        return r, info, pd.DataFrame(trades, columns=["day", "dir", "j_in", "j_out", "px_in", "px_out"])
    return r, info
