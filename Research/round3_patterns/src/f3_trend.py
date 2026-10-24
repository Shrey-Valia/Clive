"""Family F3: trend / time-series momentum and cross-sectional momentum on ETFs (the diversifier family).

Hypotheses
- Time-series momentum (Moskowitz, Ooi & Pedersen 2012 JFE; Hurst, Ooi & Pedersen 2017): an asset's own
  past 1-12 month excess return predicts its next-month return; trend portfolios are positively convex
  ("long straddle", Fung & Hsieh 2001) and paid off in most equity bear markets.
- Industry momentum (Moskowitz & Grinblatt 1999) and country momentum (Asness, Moskowitz & Pedersen 2013;
  Richards 1997): cross-sectional winners among industries/countries keep winning for months.
- Absolute + relative ("dual") momentum (Antonacci 2014, practitioner).

Data: Yahoo adjusted daily (total return), assets enter once they have 13 months of history.
Rules: signal at the close of the second-to-last trading day of each month, trade at the last day's close
(MOC). IS = through 2015, OOS = 2016-2026 (covers the shorting-bot era). No interest on cash (Alpaca paper).
Costs: 1.5 bp/side for SPY/QQQ/IWM/EFA/EEM/TLT/IEF/GLD/LQD/HYG/sector SPDRs, 4 bp/side for others;
borrow 0.5%/yr on short ETF legs.
"""
import itertools
import os

import numpy as np
import pandas as pd

from common import BORROW_GC, LH_IS_END, LH_OOS_START, RES, by_year, corr_block, register, split_perf
from lib import yahoo

MAJOR = {"SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "LQD", "HYG", "SHY", "AGG",
         "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"}
UNIV = {
    "xasset": ["SPY", "IWM", "EFA", "EEM", "EWJ", "TLT", "IEF", "LQD", "HYG", "TIP", "GLD", "SLV", "DBC", "USO",
               "UNG", "DBA", "UUP", "FXE", "FXY", "VNQ"],
    "sector": ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLRE", "XLC"],
    "country": ["SPY", "EWJ", "EWG", "EWU", "EWC", "EWA", "EWZ", "FXI", "EWY", "EWT", "EWH", "EWS", "EWW",
                "EWQ", "EWL", "EWP", "EWI", "INDA"],
}
Y = yahoo()
C = Y.pivot(index="date", columns="symbol", values="c").sort_index()
C = C[C.index >= "1996-01-01"]
R = C.pct_change()
SHY = R["SHY"].fillna(0)  # T-bill proxy for excess-return signals (pre-2002: 0)
idx = C.index
me = pd.Series(idx, index=idx).groupby(idx.to_period("M")).last()
reb_days = pd.DatetimeIndex(me.values)          # trade at these closes
sig_days = pd.DatetimeIndex([idx[idx.get_loc(d) - 1] for d in reb_days[1:]])
reb_days = reb_days[1:]


COST_MULT = [1.0]


def cost_side(sym):
    return (1.5e-4 if sym in MAJOR else 4e-4) * COST_MULT[0]


def run(univ, mode, lb_months, kind="ts", top_k=None, gross_cap=1.5, vol_lb=63):
    """mode: 'ls' long/short, 'lf' long/flat. kind: 'ts' time-series, 'xs' cross-sectional top_k (long only,
    optionally with absolute filter when mode=='dual')."""
    syms = [s for s in UNIV[univ] if s in C.columns]
    c = C[syms]; r = R[syms]
    vol = r.rolling(vol_lb, min_periods=int(vol_lb * 0.7)).std() * np.sqrt(252)
    W = pd.DataFrame(0.0, index=idx, columns=syms)
    for sd, rd in zip(sig_days, reb_days):
        sig = 0
        for L in lb_months:
            n = 21 * L
            loc = idx.get_loc(sd)
            if loc - n < 0:
                continue
            past = c.iloc[loc] / c.iloc[loc - n] - 1
            rf = (1 + SHY.iloc[loc - n + 1: loc + 1]).prod() - 1
            sig = sig + np.sign(past - rf)
        hist_ok = c.iloc[: idx.get_loc(sd) + 1].notna().sum() >= 21 * 13
        v = vol.loc[sd]
        ok = hist_ok & v.notna() & (v > 0)
        if ok.sum() < 3:
            continue
        if kind == "ts":
            s = pd.Series(sig, index=syms).where(ok, 0) / len(lb_months)
            if mode == "lf":
                s = s.clip(lower=0)
            w = s * (0.10 / v) / ok.sum() * np.sqrt(ok.sum())  # ~10% vol per asset / sqrt(N) -> ~10% port. if uncorrelated
        else:
            loc = idx.get_loc(sd)
            n = 21 * lb_months[0]
            skip = 21 if lb_months[0] >= 12 else 0
            past = (c.iloc[loc - skip] / c.iloc[loc - n] - 1).where(ok)
            rank = past.dropna().sort_values(ascending=False)
            pick = rank.index[:top_k]
            w = pd.Series(0.0, index=syms)
            if mode == "dual":
                rf = (1 + SHY.iloc[loc - n + 1: loc + 1]).prod() - 1
                pick = [p for p in pick if rank[p] > rf]
            w[pick] = 1.0 / top_k
        w = w.fillna(0)
        g = w.abs().sum()
        if g > gross_cap:
            w = w * gross_cap / g
        W.loc[rd] = w
        W.loc[rd, "_set"] = 1 if "_set" in W else None
    W = W.drop(columns="_set", errors="ignore")
    setmask = pd.Series(False, index=idx); setmask[reb_days] = True
    W = W.where(setmask, np.nan)
    W.iloc[0] = W.iloc[0].fillna(0)
    W = W.ffill()
    held = W.shift(1).fillna(0)            # weights set at close rd earn from rd+1
    gross = (held * r.fillna(0)).sum(axis=1)
    # drift-free approximation: costs on weight changes at rebalance closes
    dw = W.diff().abs().fillna(0)
    cs = pd.Series({s: cost_side(s) for s in syms})
    cost = (dw * cs).sum(axis=1)
    borrow = (held.clip(upper=0).abs().sum(axis=1)) * BORROW_GC / 252
    net = gross - cost.shift(0) - borrow
    first = W.abs().sum(axis=1).gt(0).idxmax()
    return net[net.index > first], held


def main():
    rows, series, weights = [], {}, {}
    LBS = {"1m": [1], "3m": [3], "12m": [12], "blend": [1, 3, 12]}
    for univ, mode, lbn in itertools.product(["xasset", "sector", "country"], ["ls", "lf"], LBS):
        key = f"F3_TS_{univ}_{mode}_{lbn}"
        r, w = run(univ, mode, LBS[lbn])
        register(key, "F3_trend", r, {"univ": univ, "mode": mode, "lb": LBS[lbn]})
        series[key] = r; weights[key] = w
        rows.append(dict(name=key, start=r.index[0].date(), avg_gross=w.abs().sum(axis=1).mean(),
                         **split_perf(r, LH_IS_END, LH_OOS_START), **corr_block(r[r.index >= "2016"])))
    for univ, lb, k, mode in [("sector", 6, 3, "xs"), ("sector", 12, 3, "xs"), ("country", 6, 4, "xs"),
                              ("country", 12, 4, "xs"), ("sector", 12, 3, "dual"), ("country", 12, 4, "dual"),
                              ("xasset", 12, 5, "dual")]:
        key = f"F3_XS_{univ}_{mode}_{lb}m_top{k}"
        r, w = run(univ, mode, [lb], kind="xs", top_k=k)
        register(key, "F3_trend", r, {"univ": univ, "mode": mode, "lb": lb, "k": k})
        series[key] = r; weights[key] = w
        rows.append(dict(name=key, start=r.index[0].date(), avg_gross=w.abs().sum(axis=1).mean(),
                         **split_perf(r, LH_IS_END, LH_OOS_START), **corr_block(r[r.index >= "2016"])))
    for b in ["SPY", "EFA", "TLT"]:
        r = R[b].dropna()
        series[f"BH_{b}"] = r
        rows.append(dict(name=f"BH_{b}", start=r.index[0].date(), avg_gross=1, **split_perf(r, LH_IS_END, LH_OOS_START),
                         **corr_block(r[r.index >= "2016"])))
    # equal-weight sector benchmark
    ew = R[UNIV["sector"][:9]].mean(axis=1).dropna()
    series["BH_EW_sectors"] = ew
    rows.append(dict(name="BH_EW_sectors", start=ew.index[0].date(), avg_gross=1, **split_perf(ew, LH_IS_END, LH_OOS_START),
                     **corr_block(ew[ew.index >= "2016"])))

    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(RES, "f3_trend.csv"), index=False)
    pd.DataFrame(series).to_parquet(os.path.join(RES, "f3_series.parquet"))
    pd.DataFrame({k: by_year(v) for k, v in series.items()}).to_csv(os.path.join(RES, "f3_by_year.csv"))
    pd.to_pickle(weights, os.path.join(RES, "f3_weights.pkl"))
    pd.set_option("display.width", 250)
    cols = ["name", "start", "avg_gross", "is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr", "is_maxdd", "oos_maxdd",
            "oos_vol", "corr_spy", "corr_shortbot", "ret_on_bot_worst20", "ret_on_spy_worst20"]
    print(res[cols].round(3).to_string())


if __name__ == "__main__":
    main()
