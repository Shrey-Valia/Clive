"""Q1: the CURRENT live rules over the longest history the proxy supports.

* dip PCS (SPY, IWM; RSI2<10, IBS<0.2, close>200DMA, 0.25/0.10 put spread 7-14 DTE, TP 50% /
  close>5DMA / 5 days), 2016-10..2026-10 on real ETF bars;
* hypothetical dip on SPX/10 1990-2026 without IBS (no index OHLC before 2016);
* F1 condor (SPY 0.10/0.10, 3% wings, 50-70 DTE, held to the day before expiry) on SPY 2016-2026 and
  SPX/10 1990-2026;
each alone at $10k (like the research) with and without the live kill switch.
The window 2016-10..2024-02 (dip) / 1990..2024-02 (condor) was never seen by the original tuning,
so it is a genuine out-of-sample test of the frozen rules.
Writes out/q1_summary.csv, out/q1_years.csv, out/q1_events.csv, out/q1_worst_trades.csv.
"""
import numpy as np
import pandas as pd

import psim as P
import rlib as R
from common import OUT

U = {s: P.U(s) for s in ("SPY", "IWM")}
U["SPX10"] = P.U("SPX10", surf_name="SPY", div=0.018)
runs = {
    "dip SPY": (lambda k: [P.DipPCS([U["SPY"]], kill=k)], "2016-10-20"),
    "dip IWM": (lambda k: [P.DipPCS([U["IWM"]], kill=k)], "2016-10-20"),
    "dip SPX10 (RSI2 only, hypothetical weeklies)": (lambda k: [P.DipPCS([U["SPX10"]], kill=k, ibs_lo=None)],
                                                     "1990-11-01"),
    "dip SPY (RSI2 only)": (lambda k: [P.DipPCS([U["SPY"]], kill=k, ibs_lo=None)], "2016-10-20"),
    "condor F1 SPY": (lambda k: [P.Condor(U["SPY"], kill=k)], "2016-02-01"),
    "condor F1 SPX10": (lambda k: [P.Condor(U["SPX10"], kill=k)], "1990-03-01"),
    "condor F1 SPX10 monthly-only": (lambda k: [P.Condor(U["SPX10"], kill=k, monthly_only=True)], "1990-03-01"),
}
summ, years, evs, worst = [], {}, {}, []
for name, (mk, start) in runs.items():
    for kill in (False, True):
        bk = P.Book(10_000, start, "2026-10-20", mk(kill), risk_total=10.0).run()
        eq = bk.equity_series()
        tr = bk.trades()
        tag = f"{name}{' +kill' if kill else ''}"
        m = P.metrics(eq)
        ts = R.trade_stats(tr, eq)
        pre = eq[:"2024-02-29"]
        mp = P.metrics(pre) if len(pre) > 60 else {}
        post = eq["2024-03-01":]
        mo = P.metrics(post)
        summ.append({"run": tag, "start": start, "years": (eq.index[-1] - eq.index[0]).days / 365.25, **m,
                     **{f"t_{k}": v for k, v in ts.items()},
                     "sharpe_pre2024_03": mp.get("sharpe"), "cagr_pre2024_03": mp.get("cagr"),
                     "dd_pre2024_03": mp.get("max_dd"), "sharpe_2024_03on": mo.get("sharpe"),
                     "cagr_2024_03on": mo.get("cagr")})
        years[tag] = R.per_year(eq)
        e = R.event_table(eq)
        if len(e):
            evs[tag] = e["max_dd"]
        w = tr[tr.reason != "open"].nsmallest(5, "pnl").assign(run=tag)
        w["pnl_pct_equity"] = w.pnl.values / eq.reindex(pd.to_datetime(w.open_date), method="ffill").values
        worst.append(w)
        print(tag, {k: round(v, 3) if isinstance(v, float) else v for k, v in summ[-1].items()}, flush=True)
pd.DataFrame(summ).to_csv(OUT / "q1_summary.csv", index=False)
pd.DataFrame(years).to_csv(OUT / "q1_years.csv")
pd.DataFrame(evs).to_csv(OUT / "q1_events.csv")
pd.concat(worst).to_csv(OUT / "q1_worst_trades.csv", index=False)
