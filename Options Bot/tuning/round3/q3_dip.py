"""Q3: dip put-credit-spread grid on the proxy, SPY + IWM book (the live universe).

Sample: real SIP bars 2016-10-20 .. 2026-10-20 (IBS needs OHLC; the 200DMA needs ~10 months).
  IS  = 2016-10-20 .. 2020-12-31 (includes Feb-2018, Q4-2018, Mar-2020)
  OOS = 2021-01-01 .. 2026-10-20 (includes 2022, Aug-2024, Apr-2025)
Book: one $100k account, SPY then IWM, risk 25% of equity per spread, 50% total (live sizing).
Stage A: entry signal (structure/exits fixed at live). Stage B: structure (delta, width, DTE) on
the top-5 IS signals + live. Stage C: exits on the top-5 IS (signal, structure) + live.
Writes WORK/q3_<stage>.jsonl.
"""
import itertools
import json
import multiprocessing as mp
import sys

import numpy as np
import pandas as pd

import psim as P
from common import WORK

IS_END, OOS_START = "2020-12-31", "2021-01-01"
START, END = "2016-10-20", "2026-10-20"
_U = {}


def U(sym):
    if sym not in _U:
        _U[sym] = P.U(sym)
    return _U[sym]


SIGNALS = {
    "live rsi<10 ibs<0.2": dict(signal="rsi_ibs", rsi_lo=10, ibs_lo=0.2),
    "rsi<5 ibs<0.2": dict(signal="rsi_ibs", rsi_lo=5, ibs_lo=0.2),
    "rsi<15 ibs<0.2": dict(signal="rsi_ibs", rsi_lo=15, ibs_lo=0.2),
    "rsi<10 ibs<0.1": dict(signal="rsi_ibs", rsi_lo=10, ibs_lo=0.1),
    "rsi<10 ibs<0.3": dict(signal="rsi_ibs", rsi_lo=10, ibs_lo=0.3),
    "rsi<5": dict(signal="rsi", rsi_lo=5, ibs_lo=None),
    "rsi<10": dict(signal="rsi", rsi_lo=10, ibs_lo=None),
    "ibs<0.1": dict(signal="ibs", ibs_lo=0.1),
    "ibs<0.2": dict(signal="ibs", ibs_lo=0.2),
    "close<sma5-1%": dict(signal="pct_below", pct_below=0.01, pct_sma=5),
    "close<sma5-2%": dict(signal="pct_below", pct_below=0.02, pct_sma=5),
    "close<sma10-2%": dict(signal="pct_below", pct_below=0.02, pct_sma=10),
    "close<sma10-3%": dict(signal="pct_below", pct_below=0.03, pct_sma=10),
    "3 down days": dict(signal="down_days", down_days=3),
    "4 down days": dict(signal="down_days", down_days=4),
    "always (control)": dict(signal="always"),
    "live, no 200DMA": dict(signal="rsi_ibs", rsi_lo=10, ibs_lo=0.2, trend=None),
    "live, 100DMA": dict(signal="rsi_ibs", rsi_lo=10, ibs_lo=0.2, trend="sma100"),
}
STRUCT = {
    "live 25/10 7-14": dict(short_delta=0.25, long_delta=0.10, dte_min=7, dte_max=14, dte_target=10),
    "20/10 7-14": dict(short_delta=0.20, long_delta=0.10, dte_min=7, dte_max=14, dte_target=10),
    "30/10 7-14": dict(short_delta=0.30, long_delta=0.10, dte_min=7, dte_max=14, dte_target=10),
    "25/05 7-14": dict(short_delta=0.25, long_delta=0.05, dte_min=7, dte_max=14, dte_target=10),
    "25/15 7-14": dict(short_delta=0.25, long_delta=0.15, dte_min=7, dte_max=14, dte_target=10),
    "15/05 7-14": dict(short_delta=0.15, long_delta=0.05, dte_min=7, dte_max=14, dte_target=10),
    "25/w2% 7-14": dict(short_delta=0.25, width_pct=0.02, dte_min=7, dte_max=14, dte_target=10),
    "25/10 3-7": dict(short_delta=0.25, long_delta=0.10, dte_min=3, dte_max=7, dte_target=5),
    "25/10 14-21": dict(short_delta=0.25, long_delta=0.10, dte_min=14, dte_max=21, dte_target=17),
    "25/10 21-35": dict(short_delta=0.25, long_delta=0.10, dte_min=21, dte_max=35, dte_target=28),
    "20/05 14-21": dict(short_delta=0.20, long_delta=0.05, dte_min=14, dte_max=21, dte_target=17),
}
EXITS = {
    "live tp50/sma5/5d": dict(tp=0.5, exit_sma5=True, max_days=5),
    "tp50/5d": dict(tp=0.5, exit_sma5=False, max_days=5),
    "tp50/sma5/3d": dict(tp=0.5, exit_sma5=True, max_days=3),
    "tp50/sma5/10d": dict(tp=0.5, exit_sma5=True, max_days=10),
    "tp25/sma5/5d": dict(tp=0.25, exit_sma5=True, max_days=5),
    "tp75/sma5/5d": dict(tp=0.75, exit_sma5=True, max_days=5),
    "sma5/5d (no tp)": dict(tp=None, exit_sma5=True, max_days=5),
    "live + sl1x": dict(tp=0.5, exit_sma5=True, max_days=5, sl_mult=1.0),
    "live + sl2x": dict(tp=0.5, exit_sma5=True, max_days=5, sl_mult=2.0),
    "hold to expiry": dict(tp=None, exit_sma5=False, max_days=None),
    "tp50 to expiry": dict(tp=0.5, exit_sma5=False, max_days=None),
}


def run_cfg(c, syms=("SPY", "IWM")):
    p = dict(kill=True, risk_frac=0.25)
    p.update(SIGNALS[c["signal"]])
    p.update(STRUCT[c["struct"]])
    p.update(EXITS[c["exit"]])
    p.update(c.get("extra", {}))
    bk = P.Book(100_000, START, END, [P.DipPCS([U(s) for s in syms], **p)], risk_total=0.5).run()
    eq = bk.equity_series()
    tr = bk.trades()
    t = tr[tr.reason != "open"]
    r = eq.pct_change().dropna()
    out = {k: v for k, v in c.items() if k != "extra"}
    for seg, a, b in (("full", None, None), ("is", None, IS_END), ("oos", OOS_START, None)):
        m = P.metrics(eq[a:b])
        out.update({f"{seg}_{k}": v for k, v in m.items()})
        tt = t[(t.open_date >= (a or "1900")) & (t.open_date <= (b or "2100"))]
        rr = tt.pnl / tt.max_loss0
        out[f"{seg}_n"] = len(tt)
        out[f"{seg}_win"] = float((tt.pnl > 0).mean()) if len(tt) else np.nan
        out[f"{seg}_ret_on_risk"] = float(rr.mean()) if len(tt) else np.nan
        out[f"{seg}_n_bigloss"] = int((rr <= -0.5).sum())
    out["daily_skew"] = float(r.skew())
    out["daily_kurt"] = float(r.kurt() + 3)
    return out


def main(stage):
    cache = WORK / f"q3_{stage}.jsonl"
    done = set()
    if cache.exists():
        for line in cache.read_text().splitlines():
            d = json.loads(line)
            done.add((d["signal"], d["struct"], d["exit"]))
    live = dict(signal="live rsi<10 ibs<0.2", struct="live 25/10 7-14", exit="live tp50/sma5/5d")
    if stage == "A":
        cfgs = [dict(live, signal=s) for s in SIGNALS]
    elif stage == "B":
        a = pd.read_json(WORK / "q3_A.jsonl", lines=True)
        a = a[a.signal != "always (control)"]
        top = list(a.sort_values("is_sharpe", ascending=False).signal.head(5))
        sigs = list(dict.fromkeys(top + [live["signal"]]))
        cfgs = [dict(live, signal=s, struct=st) for s in sigs for st in STRUCT]
    else:
        b = pd.read_json(WORK / "q3_B.jsonl", lines=True)
        top = b.sort_values("is_sharpe", ascending=False).head(5)
        keys = [(r.signal, r.struct) for r in top.itertuples()] + [(live["signal"], live["struct"])]
        keys = list(dict.fromkeys(keys))
        cfgs = [dict(live, signal=s, struct=st, exit=e) for s, st in keys for e in EXITS]
    todo = [c for c in cfgs if (c["signal"], c["struct"], c["exit"]) not in done]
    print(stage, len(cfgs), "configs,", len(todo), "to run", flush=True)
    with mp.get_context("fork").Pool(6) as pool, cache.open("a") as fh:
        for row in pool.imap_unordered(run_cfg, todo):
            fh.write(json.dumps(row, default=float) + "\n")
            fh.flush()
            print(row["signal"], "|", row["struct"], "|", row["exit"], round(row["is_sharpe"], 3),
                  round(row["oos_sharpe"], 3), row["full_n"], flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "A")
