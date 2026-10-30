"""Q4: tail hedges for the whole book.

Book A (2016-10..2026-10, real SPY/IWM bars, $15k): live options book (condor F1 + dip SPY/IWM).
Book B (1990-03..2026-10, SPX/10): condor F1 + dip (RSI2 only) on the index, $15k-equivalent.
Shorting bot: P7 backtest at 5% per name with the +35% stop, on its own $10k account (2016-2026).

Hedges (premium at the ask, sold back at the bid, marked on the proxy; budget = % of equity per
year; `frac` = fractional contracts = the economics at any account size; `1-lot` = what $15k can
actually hold):
  ps10/20   SPY put spread 10% / 20% OTM, 50-100 DTE (target 75), rolled at 30 DTE
  ps5/15    5% / 15% OTM, 40-80 DTE (target 60), rolled at 21 DTE
  d10/d05   0.10-delta / 0.05-delta put spread, 30-90 DTE (target 60), rolled at 21 DTE
  ps3/8m    3% / 8% OTM, 25-45 DTE (target 35), rolled at 7 DTE (monthly, gap-sensitive)
  put15     single 15% OTM put, 60-120 DTE (target 90), rolled at 30 DTE
  vix       VIX 1-month 30-delta call (VXTH-style), modelled post hoc (vixopt.py); NOT tradable on
            Alpaca (no index options) - benchmark only
Writes out/q4_hedges.csv and out/q4_events.csv.
"""
import math
import multiprocessing as mp

import numpy as np
import pandas as pd

import book as BK
import psim as P
import r3lib as L
import rlib as R
import vixopt as V
from common import OUT, WORK

HEDGES = {
    "ps10/20": dict(long_otm=0.10, short_otm=0.20, dte_min=50, dte_max=100, dte_target=75, roll_dte=30),
    "ps5/15": dict(long_otm=0.05, short_otm=0.15, dte_min=40, dte_max=80, dte_target=60, roll_dte=21),
    "d10/d05": dict(long_delta=0.10, short_delta=0.05, dte_min=30, dte_max=90, dte_target=60, roll_dte=21),
    "ps3/8m": dict(long_otm=0.03, short_otm=0.08, dte_min=25, dte_max=45, dte_target=35, roll_dte=7),
    "put15": dict(long_otm=0.15, kind="put", dte_min=60, dte_max=120, dte_target=90, roll_dte=30),
}
BUDGETS = [0.0025, 0.005, 0.01, 0.02]
EV_A = ["2018-02 Volmageddon", "2018-Q4", "2020-03 COVID", "2022 bear", "2024-08 yen carry", "2025-04 tariffs"]


def job(args):
    which, hname, mode, budget = args
    if which == "A":
        kw = dict(start="2016-10-20", end="2026-10-20")
    else:
        kw = dict(start="1990-11-01", end="2026-10-20", spy="SPX10", iwm=None, dip_kw=dict(ibs_lo=None))
    hedge = None
    if hname != "none":
        hedge = dict(HEDGES[hname], budget_pa=budget)
        if mode == "frac":
            hedge["frac"] = True
        elif mode == "1lot":
            hedge["n_fixed"] = 1
    bk = BK.run_book(15_000, hedge=hedge, **kw)
    eq = bk.equity_series()
    tr = bk.trades()
    h = tr[tr.sleeve == "hedge"]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    m = P.metrics(eq)
    ev = R.event_table(eq)
    row = {"book": which, "hedge": hname, "mode": mode, "budget": budget, **m,
           "hedge_pnl_pa_pct": float(h.pnl.sum() / 15_000 / yrs) if len(h) else 0.0,
           "hedge_premium_pa_pct": float(-h.entry_cash.sum() / 15_000 / yrs) if len(h) else 0.0,
           "n_hedge_trades": len(h)}
    for k, v in ev["max_dd"].items():
        row[f"dd {k}"] = v
    return row, eq


def vix_overlay(eq: pd.Series, budget_pa: float, delta=0.30, rule="always") -> pd.Series:
    """Post-hoc VIX-call hedge: at each VIX expiry spend budget_pa/12 x equity on 1-month calls,
    mark daily with the model, settle at the VIX open. Returns the hedge P&L path ($)."""
    m = V.VixModel(vv_mult=0.9, conv=1.0)
    vopen = L.cboe_col("VIX", "OPEN")
    idx = eq.index
    tset = set(m.vp.index)
    ex = [e for e in V.vix_expiries(str(idx[0].date()), str(idx[-1].date()), tset) if e in tset]
    pnl = pd.Series(0.0, index=idx)
    cum = 0.0
    for e0, e1 in zip(ex[:-1], ex[1:]):
        if e0 not in idx:
            continue
        v0 = m.vp.at[e0, "VIX"]
        if rule == "vxth" and not (15 < v0 <= 50):
            continue
        K = m.strike_for_delta(e0, e1, delta)
        prem, _ = m.call(e0, e1, K)
        ask = prem + max(0.05, 0.05 * prem)
        n = eq.at[e0] * budget_pa / 12 / (ask * 100)
        days = idx[(idx > e0) & (idx <= e1)]
        last = 0.0
        for d in days:
            if d == e1:
                val = max(vopen.get(d, m.vp.at[d, "VIX"]) - K, 0.0)
            else:
                c, _ = m.call(d, e1, K)
                val = max(c - max(0.05, 0.05 * c), 0.0)
            pnl.at[d] += n * 100 * (val - last) - (n * 100 * ask if last == 0.0 and d == days[0] else 0.0)
            last = val
    return pnl.cumsum()


if __name__ == "__main__":
    jobs = [(w, "none", "-", 0.0) for w in ("A", "B")]
    for w in ("A", "B"):
        for h in HEDGES:
            for b in BUDGETS:
                jobs.append((w, h, "frac", b))
    for h in HEDGES:
        jobs.append(("A", h, "1lot", 0.0))
    with mp.get_context("fork").Pool(7) as pool:
        res = pool.map(job, jobs)
    rows = [r for r, _ in res]
    eqs = {(r["book"], r["hedge"], r["mode"], r["budget"]): e for r, e in res}
    # VIX overlays on the unhedged books
    for w in ("A", "B"):
        base = eqs[(w, "none", "-", 0.0)]
        if w == "B":
            base = base["2006-03-01":]
        for b in (0.005, 0.01, 0.02):
            for rule in ("always", "vxth"):
                hp = vix_overlay(base, b, rule=rule)
                eq = base + hp
                m = P.metrics(eq)
                ev = R.event_table(eq)
                yrs = (eq.index[-1] - eq.index[0]).days / 365.25
                row = {"book": w + ("(2006-)" if w == "B" else ""), "hedge": f"vix30d_{rule}", "mode": "frac",
                       "budget": b, **m, "hedge_pnl_pa_pct": float(hp.iloc[-1] / 15_000 / yrs)}
                for k, v in ev["max_dd"].items():
                    row[f"dd {k}"] = v
                rows.append(row)
        if w == "B":
            m = P.metrics(base)
            ev = R.event_table(base)
            row = {"book": "B(2006-)", "hedge": "none", "mode": "-", "budget": 0.0, **m}
            for k, v in ev["max_dd"].items():
                row[f"dd {k}"] = v
            rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "q4_hedges.csv", index=False)
    pd.to_pickle(eqs, WORK / "q4_eqs.pkl")
    pd.set_option("display.width", 300)
    cols = ["book", "hedge", "mode", "budget", "cagr", "sharpe", "max_dd", "hedge_pnl_pa_pct"] + \
        [c for c in df.columns if c.startswith("dd ")]
    print(df[cols].round(3).to_string())
