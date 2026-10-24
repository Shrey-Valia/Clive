"""Q5: sizing at $15k - Kelly sanity check on long-history trade distributions, and a cap grid
on the live book (2016-10..2026-10, integer contracts at $15k).

Kelly: f* = argmax_f mean(log(1 + f * r)), r = trade P&L / max loss at entry (so f is the share of
equity put at max loss per trade). Bootstrap (2,000 resamples) gives a 90% interval.
Writes out/q5_kelly.csv and out/q5_caps.csv.
"""
import multiprocessing as mp

import numpy as np
import pandas as pd

import book as BK
import psim as P
import rlib as R
from common import OUT


def kelly(r, grid=np.linspace(0, 1, 401)):
    r = np.asarray(r, float)
    best, fb = -1e9, 0.0
    for f in grid:
        x = 1 + f * r
        if (x <= 0).any():
            break
        g = np.log(x).mean()
        if g > best:
            best, fb = g, f
    return fb, best


def kelly_table():
    srcs = {
        "dip SPY 2016-26": dict(sleeves=lambda: [P.DipPCS([BK.U("SPY")])], start="2016-10-20"),
        "dip IWM 2016-26": dict(sleeves=lambda: [P.DipPCS([BK.U("IWM")])], start="2016-10-20"),
        "dip SPX10 RSI2-only 1990-26": dict(sleeves=lambda: [P.DipPCS([BK.U("SPX10")], ibs_lo=None)],
                                            start="1990-11-01"),
        "condor F1 SPY 2016-26": dict(sleeves=lambda: [P.Condor(BK.U("SPY"))], start="2016-02-01"),
        "condor F1 SPX10 1990-26": dict(sleeves=lambda: [P.Condor(BK.U("SPX10"))], start="1990-03-01"),
    }
    rows = []
    rng = np.random.default_rng(0)
    for name, s in srcs.items():
        bk = P.Book(1_000_000, s["start"], "2026-10-20", s["sleeves"](), risk_total=10).run()
        t = bk.trades()
        t = t[t.reason != "open"]
        r = (t.pnl / t.max_loss0).values
        f, g = kelly(r)
        boots = [kelly(rng.choice(r, len(r)))[0] for _ in range(2000)]
        rows.append({"source": name, "n": len(r), "mean_r": r.mean(), "win": (r > 0).mean(),
                     "worst_r": r.min(), "p_loss_gt_50pct": (r < -0.5).mean(),
                     "kelly_f": f, "kelly_p05": np.quantile(boots, 0.05), "kelly_p95": np.quantile(boots, 0.95),
                     "half_kelly": f / 2, "growth_at_kelly_per_trade": g})
        print(rows[-1], flush=True)
    return pd.DataFrame(rows)


CAPS = [(rp, rt, c, cr) for rp in (0.05, 0.10, 0.15, 0.25) for rt in (0.10, 0.20, 0.30, 0.50)
        for c in (True, False) for cr in (0.20,) if rp <= rt]


def cap_job(args):
    rp, rt, condor, cr = args
    bk = BK.run_book(15_000, risk_total=rt, condor=condor, dip_kw=dict(risk_frac=rp),
                     condor_kw=dict(risk_frac=cr))
    eq = bk.equity_series()
    tr = bk.trades()
    m = P.metrics(eq)
    ev = R.event_table(eq)
    out = {"risk_per_spread": rp, "risk_total": rt, "condor": condor, "condor_risk": cr, **m,
           "n_dip": int((tr.sleeve == "dip").sum()), "n_condor": int((tr.sleeve == "condor").sum()),
           "is_sharpe": P.metrics(eq[:"2020-12-31"]).get("sharpe"),
           "oos_sharpe": P.metrics(eq["2021-01-01":]).get("sharpe")}
    for k, v in ev["max_dd"].items():
        out[f"dd {k}"] = v
    return out


if __name__ == "__main__":
    kt = kelly_table()
    kt.to_csv(OUT / "q5_kelly.csv", index=False)
    with mp.get_context("fork").Pool(7) as pool:
        rows = pool.map(cap_job, CAPS)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "q5_caps.csv", index=False)
    pd.set_option("display.width", 250)
    print(df.round(3).to_string())
