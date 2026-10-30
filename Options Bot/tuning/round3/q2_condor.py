"""Q2: iron-condor management / structure / entry-filter grid on the proxy.

Main sample: SPX/10 (cost-rescaled, see psim.U.scale) 1990-03..2026-10.
  IS  = 1990-03-01 .. 2009-12-31 (parameters chosen here only)
  OOS = 2010-01-01 .. 2026-10-20 (looked at once, for the IS-chosen finalists)
Each config runs as a $100k account with risk_frac 0.20 (the live F1 sizing) and no other sleeve.
Every config is one trial for the deflated Sharpe.

Stage A: structure x management (no filter). Stage B: entry filters on the top-10 IS stage-A configs
plus the live F1. Writes out/q2_grid.csv.
"""
import itertools
import json
import multiprocessing as mp
import sys

import numpy as np
import pandas as pd

import psim as P
from common import OUT, WORK

IS_END, OOS_START = "2009-12-31", "2010-01-01"
START, END = "1990-03-01", "2026-10-20"
_U = None


def U():
    global _U
    if _U is None:
        _U = P.U("SPX10", surf_name="SPY", div=0.018)
    return _U


MGMT = {
    "hold": dict(),
    "tp50": dict(tp=0.5),
    "tp50_dte21": dict(tp=0.5, close_dte=21),
    "dte21": dict(close_dte=21),
    "sl2x": dict(sl_mult=2.0),
    "tp50_sl2x_dte21": dict(tp=0.5, sl_mult=2.0, close_dte=21),
    "breach30_close": dict(breach_delta=0.30, breach_action="close"),
    "breach30_side": dict(breach_delta=0.30, breach_action="side"),
    "tp50_breach30": dict(tp=0.5, breach_delta=0.30, breach_action="close"),
}
DELTAS = {"10/10": (0.10, 0.10), "05/05": (0.05, 0.05), "08/08": (0.08, 0.08), "12/12": (0.12, 0.12),
          "16/16": (0.16, 0.16), "10/05": (0.10, 0.05), "16/08": (0.16, 0.08), "20/10": (0.20, 0.10)}
WINGS = [0.02, 0.03, 0.05]
DTES = {"30": (20, 40, 30), "45": (35, 55, 45), "60": (50, 70, 60)}
FILTERS = {
    "none": dict(),
    "vix<25": dict(vix_max=25.0),
    "vix15-30": dict(vix_min=15.0, vix_max=30.0),
    "vix>15": dict(vix_min=15.0),
    "ts<1.0": dict(ts_max=1.0),
    "ts<0.9": dict(ts_max=0.9),
    "skip_fomc_cpi_2d": dict(event_skip=2),
    "skip_fomc_cpi_5d": dict(event_skip=5),
    "kill_live": dict(kill=True),
}


def cfg_params(c):
    pd_, cd_ = DELTAS[c["delta"]]
    lo, hi, tg = DTES[c["dte"]]
    p = dict(put_delta=pd_, call_delta=cd_, width_pct=c["wing"], dte_min=lo, dte_max=hi, dte_target=tg,
             kill=False, risk_frac=0.20)
    p.update(MGMT[c["mgmt"]])
    p.update(FILTERS[c["filter"]])
    return p


def run_cfg(c):
    p = cfg_params(c)
    bk = P.Book(100_000, START, END, [P.Condor(U(), **p)], risk_total=10.0).run()
    eq = bk.equity_series()
    tr = bk.trades()
    t = tr[tr.reason != "open"]
    ret = t.pnl / t.max_loss0
    r = eq.pct_change().dropna()
    out = dict(c)
    for seg, a, b in (("full", None, None), ("is", None, IS_END), ("oos", OOS_START, None)):
        m = P.metrics(eq[a:b])
        out.update({f"{seg}_{k}": v for k, v in m.items()})
        tt = t[(t.open_date >= (a or "1900")) & (t.open_date <= (b or "2100"))]
        rr = tt.pnl / tt.max_loss0
        out[f"{seg}_n"] = len(tt)
        out[f"{seg}_win"] = float((tt.pnl > 0).mean()) if len(tt) else np.nan
        out[f"{seg}_ret_on_risk"] = float(rr.mean()) if len(tt) else np.nan
        out[f"{seg}_n_bigloss"] = int((rr <= -0.75).sum())
    out["worst_trade_ret"] = float(ret.min()) if len(ret) else np.nan
    out["daily_skew"] = float(r.skew())
    out["daily_kurt"] = float(r.kurt() + 3)
    return out


def main(stage):
    cache = WORK / f"q2_{stage}.jsonl"
    done = {}
    if cache.exists():
        for line in cache.read_text().splitlines():
            d = json.loads(line)
            done[(d["delta"], d["wing"], d["dte"], d["mgmt"], d["filter"])] = d
    if stage == "A":
        cfgs = [dict(delta=d, wing=w, dte=t, mgmt=m, filter="none")
                for d, w, t, m in itertools.product(DELTAS, WINGS, DTES, MGMT)]
    else:
        a = pd.read_json(WORK / "q2_A.jsonl", lines=True)
        top = a.sort_values("is_sharpe", ascending=False).head(10)
        base = [dict(delta="10/10", wing=0.03, dte="60", mgmt="hold")]
        keys = base + [dict(delta=r.delta, wing=r.wing, dte=str(r.dte), mgmt=r.mgmt) for r in top.itertuples()]
        cfgs = [dict(**k, filter=f) for k in keys for f in FILTERS if f != "none"]
    todo = [c for c in cfgs if (c["delta"], c["wing"], c["dte"], c["mgmt"], c["filter"]) not in done]
    print(stage, len(cfgs), "configs,", len(todo), "to run", flush=True)
    with mp.get_context("fork").Pool(7) as pool, cache.open("a") as fh:
        for i, row in enumerate(pool.imap_unordered(run_cfg, todo, chunksize=2)):
            fh.write(json.dumps(row, default=float) + "\n")
            fh.flush()
            if i % 25 == 0:
                print(i, row["delta"], row["wing"], row["dte"], row["mgmt"], row["filter"],
                      round(row["is_sharpe"], 3), round(row["oos_sharpe"], 3), flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "A")
