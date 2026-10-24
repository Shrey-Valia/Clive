"""Q3: do more names add independent trades, or just more of the same tail?

1. Signal overlap 2016-10..2026-10 (bars only, all ETFs): live dip signal days per name, share of
   them within 2 trading days of a SPY or IWM signal, and the name's worst 5-day return after its
   signals (what a 0.25-delta short put has to survive).
2. Proxy book with names that have a Cboe vol index (DIA: VXD, QQQ: VXN) added to SPY+IWM at the
   live sizing; IS 2016-2020 / OOS 2021-2026.
Writes out/q3_overlap.csv, out/q3_names_book.csv.
"""
import multiprocessing as mp

import numpy as np
import pandas as pd

import book as BK
import psim as P
from common import HERE, OUT

NAMES = ["SPY", "IWM", "QQQ", "DIA", "XLF", "XLE", "XLK", "XLI", "XLV", "XLY", "XLP", "XLU", "XLB", "SMH",
         "GLD", "TLT", "EEM", "EFA", "MDY", "IJR", "RSP"]
START, END = "2016-10-20", "2026-10-20"


def signals():
    out = {}
    for s in NAMES:
        u = P.U(s, surf_name="SPY") if s not in ("IWM", "QQQ") else P.U(s)
        f = u.f[(u.f.index >= START) & (u.f.index <= END)]
        sig = (f.close > f.sma200) & (f.rsi2 < 10) & (f.ibs < 0.2)
        fwd = f.close.shift(-5) / f.close - 1
        out[s] = (sig, fwd)
    return out


def overlap():
    sg = signals()
    base = (sg["SPY"][0] | sg["IWM"][0]).astype(int)
    near = base.rolling(5, center=True, min_periods=1).max() > 0
    rows = []
    for s, (sig, fwd) in sg.items():
        d = sig[sig].index
        rows.append({"name": s, "signals": len(d), "per_year": len(d) / 10.0,
                     "share_near_spy_iwm_signal": float(near.reindex(d).mean()) if len(d) else np.nan,
                     "worst_5d_after_signal": float(fwd.reindex(d).min()) if len(d) else np.nan,
                     "p5_5d_after_signal": float(fwd.reindex(d).quantile(0.05)) if len(d) else np.nan})
    return pd.DataFrame(rows)


def book_job(names):
    us = [BK.U(n) if n in ("SPY", "IWM") else P.U(n) for n in names]
    bk = P.Book(15_000, START, END, [P.Condor(BK.U("SPY")), P.DipPCS(us)], risk_total=0.5).run()
    bk2 = P.Book(15_000, START, END, [P.DipPCS(us)], risk_total=0.5).run()
    rows = []
    for tag, b in (("with condor", bk), ("dip only", bk2)):
        eq = b.equity_series()
        t = b.trades()
        rows.append({"names": "+".join(names), "book": tag, **P.metrics(eq),
                     "is_sharpe": P.metrics(eq[:"2020-12-31"])["sharpe"],
                     "oos_sharpe": P.metrics(eq["2021-01-01":])["sharpe"],
                     "dip_trades": int((t.sleeve == "dip").sum())})
    return rows


if __name__ == "__main__":
    ov = overlap()
    ov.to_csv(OUT / "q3_overlap.csv", index=False)
    print(ov.round(3).to_string())
    sets = [["SPY", "IWM"], ["SPY"], ["SPY", "IWM", "DIA"], ["SPY", "IWM", "QQQ"], ["SPY", "DIA"],
            ["SPY", "IWM", "DIA", "QQQ"]]
    with mp.get_context("fork").Pool(6) as pool:
        res = sum(pool.map(book_job, sets), [])
    df = pd.DataFrame(res)
    df.to_csv(OUT / "q3_names_book.csv", index=False)
    print(df.round(3).to_string())
