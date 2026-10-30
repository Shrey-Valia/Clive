"""Cost sensitivity of the live sleeves and the grid leaders (proxy, long history).

Cost models (per leg, on the option price):
  zero        no slippage, no fees
  calibrated  max($0.01, 0.73% x price) capped $0.20 (1.25 x the median indicative half-spread on
              SPY, tuning/spreads/REPORT.md) - roughly a fill at mid
  default     max($0.03, 2% x price) capped $0.25 (engine default) - about the natural price: the
              2026-10-23 live condor (mid 2.29, natural 2.13, no fill at 2.20 on paper) needs a
              0.16 concession over 4 legs; this model charges ~0.17
  stress      default x 1.5, and x 2 more when VIX > 30 (spreads widen in stress)
Writes out/costs.csv.
"""
import multiprocessing as mp

import pandas as pd

import book as BK
import psim as P

COSTS = {"zero": P.Costs(slip_abs=0, slip_pct=0, slip_cap=0, fee=0),
         "calibrated": P.Costs(slip_abs=0.01, slip_pct=0.0073, slip_cap=0.20),
         "default": P.Costs(),
         "stress": P.Costs(mult=1.5, stress_vix=30, stress_mult=2.0)}
RUNS = {
    "condor F1 SPX10 1990-2026": ("SPX10", "1990-03-01", lambda u: [P.Condor(u)]),
    "condor 20/10 5% 45d sl2x SPX10": ("SPX10", "1990-03-01",
                                       lambda u: [P.Condor(u, put_delta=0.2, call_delta=0.1, width_pct=0.05,
                                                           dte_min=35, dte_max=55, dte_target=45, sl_mult=2.0)]),
    "dip SPY 2016-2026": ("SPY", "2016-10-20", lambda u: [P.DipPCS([u])]),
    "dip IWM 2016-2026": ("IWM", "2016-10-20", lambda u: [P.DipPCS([u])]),
}


def job(a):
    run, cost = a
    sym, start, mk = RUNS[run]
    bk = P.Book(100_000, start, "2026-10-20", mk(BK.U(sym)), costs=COSTS[cost], risk_total=10).run()
    m = P.metrics(bk.equity_series())
    return {"run": run, "costs": cost, **m}


if __name__ == "__main__":
    with mp.get_context("fork").Pool(7) as pool:
        rows = pool.map(job, [(r, c) for r in RUNS for c in COSTS])
    df = pd.DataFrame(rows)
    df.to_csv("out/costs.csv", index=False)
    print(df.pivot(index="run", columns="costs", values="sharpe").round(3).to_string())
    print(df.pivot(index="run", columns="costs", values="cagr").round(3).to_string())
