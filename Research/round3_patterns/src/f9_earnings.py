"""Family F9: earnings-announcement-return drift on large caps (equity, not options; the options event
strategies tested earlier are a different trade).

Hypothesis: Chan, Jegadeesh & Lakonishok (1996); Brandt, Kishore, Santa-Clara & Venkatachalam (2008):
the market-adjusted return on the announcement reaction day (EAR) predicts the next 1-3 months' returns,
more robustly than SUE. Counterpoint: Martineau (2022) finds PEAD has largely disappeared in large caps.

Data: SEC 8-K Item 2.02 acceptance times for the CURRENT S&P 500 (Pairs Research cache): survivorship
bias favours longs (names that later dropped out are missing). Reaction day = filing date if accepted
before 16:00 ET, else the next trading day. EAR = stock minus SPY on the reaction day.
Rules: long EAR in the top quintile, short the bottom quintile (thresholds = IS quintiles, fixed), enter at
the close of reaction day + lag, hold H trading days. Each position gets 1/N_SLOTS of sleeve capital
(N_SLOTS = 40), capped at 100% gross per side.
"""
import os

import numpy as np
import pandas as pd

from common import BORROW_GC, CACHE, IS_END, MAIN, RES, SELL_FEES, by_year, corr_block, register, split_perf
from lib import yahoo

E = pd.read_csv(os.path.join(MAIN, "Pairs Research", ".cache", "earnings_dates.csv"))
t = pd.to_datetime(E.accept_raw, utc=True).dt.tz_convert("America/New_York")
E["mins"] = t.dt.hour * 60 + t.dt.minute
E["fd"] = pd.to_datetime(E.filing_date)
P = pd.read_parquet(os.path.join(CACHE, "panel_liquid.parquet"), columns=["symbol", "date", "c", "r_cc", "adv20_l", "bad_win"])
P["date"] = pd.to_datetime(P.date)
P = P[P.symbol.isin(E.symbol.unique())]
C = P.pivot(index="date", columns="symbol", values="c")
R = P.pivot(index="date", columns="symbol", values="r_cc")
idx = C.index
SPY = yahoo("SPY").c.pct_change().reindex(idx).fillna(0)
pos = {d: i for i, d in enumerate(idx)}


def next_td(d):
    j = idx.searchsorted(d)
    return idx[j] if j < len(idx) else None


ev = []
for r in E.itertuples():
    d = next_td(r.fd)
    if d is None:
        continue
    if r.fd == d and r.mins >= 960:
        j = pos[d] + 1
    else:
        j = pos[d]
    if j >= len(idx) or r.symbol not in C.columns:
        continue
    rd = idx[j]
    x = R.at[rd, r.symbol]
    if pd.isna(x):
        continue
    ev.append((r.symbol, rd, j, x - SPY.iloc[j]))
ev = pd.DataFrame(ev, columns=["symbol", "rd", "j", "ear"]).drop_duplicates(["symbol", "rd"])
q_hi, q_lo = ev[ev.rd <= IS_END].ear.quantile([0.8, 0.2])
print("events", len(ev), "IS quintile thresholds", round(q_hi, 4), round(q_lo, 4))
N_SLOTS = 40
CS = 4e-4

rows, series = [], {}
Rv = R.fillna(0).values
cols = {s: i for i, s in enumerate(C.columns)}
for lag in (0, 1):
    for H in (20, 60):
        L = np.zeros((len(idx), C.shape[1])); S = np.zeros_like(L)
        for e in ev.itertuples():
            a = e.j + lag + 1; b = min(e.j + lag + H, len(idx) - 1)   # returns earned on days a..b
            if a > b:
                continue
            k = cols[e.symbol]
            if e.ear >= q_hi:
                L[a:b + 1, k] += 1
            elif e.ear <= q_lo:
                S[a:b + 1, k] += 1
        nl = L.sum(axis=1); ns = S.sum(axis=1)
        wl = L / np.maximum(N_SLOTS, nl)[:, None]; ws = S / np.maximum(N_SLOTS, ns)[:, None]
        gl = (wl * Rv).sum(axis=1); gs = (ws * Rv).sum(axis=1)
        # costs: each position entered and exited once (cost on 1/slot weight)
        tl = np.abs(np.diff(wl, axis=0, prepend=0)).sum(axis=1)
        ts = np.abs(np.diff(ws, axis=0, prepend=0)).sum(axis=1)
        netl = gl - tl * (CS + SELL_FEES / 2)
        nets = -gs - ts * (CS + SELL_FEES / 2) - ws.sum(axis=1) * BORROW_GC / 252
        spyw = SPY.values * wl.sum(axis=1)
        for name, rr in [("LS", netl + nets), ("LminusSPY", netl - spyw)]:
            s = pd.Series(rr, index=idx)
            s = s[s.index >= "2016-03-01"]
            key = f"F9_EAR_{name}_lag{lag}_H{H}"
            register(key, "F9_earnings", s, {"lag": lag, "H": H})
            series[key] = s
            rows.append(dict(name=key, avg_long_gross=wl.sum(axis=1).mean(), avg_short_gross=ws.sum(axis=1).mean(),
                             **split_perf(s), **corr_block(s)))

res = pd.DataFrame(rows)
res.to_csv(os.path.join(RES, "f9_earnings.csv"), index=False)
pd.DataFrame(series).to_parquet(os.path.join(RES, "f9_series.parquet"))
pd.DataFrame({k: by_year(v) for k, v in series.items()}).to_csv(os.path.join(RES, "f9_by_year.csv"))
pd.set_option("display.width", 250)
print(res[["name", "avg_long_gross", "avg_short_gross", "is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr", "all_maxdd",
           "corr_spy", "corr_shortbot"]].round(3).to_string())
