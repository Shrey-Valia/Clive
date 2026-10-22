"""All strategy families, trial log, IS/OOS, DSR, bootstrap, walk-forward, stress, capacity, episodes.

    python3 src/run_all.py
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, replace

import numpy as np
import pandas as pd

import lib
import strats as S
from common import CACHE, IS_END, OOS_START, RESULTS, SCACHE
from lib import Book, block_boot, boot_trades, dsr, split, stats
from scan import classify_neg, news_class

DAYS = S.CAL
TRIALS: list[dict] = []
SERIES: dict[str, pd.Series] = {}
TRADES: dict[str, pd.DataFrame] = {}
BOOKS: dict[str, Book] = {}
FAMILY: dict[str, str] = {}
TT: dict[str, pd.DataFrame] = {}


def evaluate(name, family, tt, book, params, log=True):
    if len(tt) == 0:
        print("skip (no trades)", name)
        return None, None, None
    TT[name] = tt
    d, t = lib.run(tt, replace(book, name=name), DAYS)
    (di, ti), (do, to) = split(d, t)
    si, so = stats(di, ti), stats(do, to)
    row = {"family": family, "name": name, "params": json.dumps(params),
           **{f"is_{k}": v for k, v in si.items()}, **{f"oos_{k}": v for k, v in so.items()}}
    if log:
        TRIALS.append(row)
    SERIES[name] = d.set_index("date").ret
    TRADES[name] = t
    BOOKS[name] = replace(book, name=name)
    FAMILY[name] = family
    print(f"{family:<5} {name:<44} IS SR {si.get('sharpe', 0):5.2f} CAGR {si.get('cagr', 0):7.1%} n {si.get('trades', 0):5d} "
          f"| OOS SR {so.get('sharpe', 0):5.2f} CAGR {so.get('cagr', 0):7.1%} DD {so.get('max_drawdown', 0):6.1%} "
          f"n {so.get('trades', 0):5d} mean {so.get('mean_trade', np.nan):+.4f}", flush=True)
    return row, d, t


def v2_series():
    d = pd.read_parquet(os.path.join(CACHE, "v2_daily.parquet"))
    t = pd.read_parquet(os.path.join(CACHE, "v2_trades.parquet"))
    d["date"] = d.date.map(S.NEXT)
    t["date"] = t.date.map(S.NEXT)
    t = t.rename(columns={"net_ret": "net", "gross_ret": "gross"})
    d = d.dropna(subset=["date"]).set_index("date").reindex(DAYS).fillna({"ret": 0.0, "n": 0}).reset_index()
    return d, t


def spy_series():
    e = pd.read_parquet(os.path.join(SCACHE, "etf_daily.parquet"))
    s = e[e.symbol == "SPY"].set_index("date").c
    r = (s / s.shift(1) - 1).reindex(DAYS).fillna(0.0)
    return pd.DataFrame({"date": DAYS, "ret": r.values, "n": 1})


def load_losers():
    pp = pd.read_parquet(os.path.join(CACHE, "pp.parquet"))
    cl = pd.read_parquet(os.path.join(CACHE, "cand_lose.parquet"))[["symbol", "date"]]
    x = cl.merge(pp, on=["symbol", "date"], how="left")
    keep = None
    for k in (1, 3, 5):     # the exact request set of fetch.py news_l
        m = x.groupby("date")[f"r{k}"].rank(ascending=True, method="first") <= 6
        keep = m if keep is None else (keep | m)
    req = set(zip(x[keep].symbol, x[keep].date))
    nc = news_class("l", x[["symbol", "date"]], classify_neg, req)
    x = x.merge(nc.rename(columns={"cls": "ncls"}), on=["symbol", "date"], how="left")
    return x, pp


def main():
    M100 = Book("m100", n=5, w=0.10, gross_cap=0.50, equity=100_000)
    v2d, v2t = v2_series()
    spy = spy_series()
    SERIES["v2_short"] = v2d.set_index("date").ret
    SERIES["SPY"] = spy.set_index("date").ret
    TRADES["v2_short"] = v2t
    out = {}
    s = S.load_scans()

    o30 = S.load_open30()
    # ---------------- L1 overnight long of spike names (buy 15:55, sell next open)
    for lo, hi, lab in ((1, 5, "1-5"), (5, 10, "5-10"), (10, 1e9, "10+")):
        for ms in (None, 70):
            adv = 10e6 if lo >= 10 else 3e6
            nm = f"L1_{lab}_score{ms or 0}"
            evaluate(nm, "L1", S.tt_l1(s, lo, hi, adv, ms), M100, dict(lo=lo, hi=hi, adv=adv, min_score=ms))
    evaluate("L1_v2names", "L1", S.tt_l1(s, 10, 1e9, 10e6, 70), M100, dict(lo=10, adv=10e6, min_score=70, v2=True), log=False)
    # literature "Night Owl" (rule set 1)
    for lo, hi, lab in ((1, 5, "1-5"), (5, 10, "5-10"), (1, 10, "1-10")):
        for dv in (5e6, 20e6):
            nm = f"L1NO_{lab}_dv{int(dv/1e6)}m"
            evaluate(nm, "L1", S.tt_nightowl(s, lo, hi, dv), M100, dict(lo=lo, hi=hi, day_dollar_vol=dv, min_chg=0.2,
                                                                        rvol=3, close_in_range=70, exit="open"))
    if o30 is not None:
        evaluate("L1NO_1-10_dv5m_x0931", "L1", S.tt_nightowl(s, 1, 10, 5e6, "c0931", o30), M100,
                 dict(lo=1, hi=10, day_dollar_vol=5e6, exit="09:31-09:34 avg"))

    # ---------------- S1 v2 fade applied to penny names
    for lo, hi, lab in ((1, 5, "1-5"), (5, 10, "5-10")):
        for adv in (1e6, 3e6, 10e6):
            nm = f"S1_{lab}_adv{int(adv/1e6)}m"
            evaluate(nm, "S1", S.tt_s1(s, lo, hi, adv, 70), replace(M100, borrow_mode="alpaca", intraday=True),
                     dict(lo=lo, hi=hi, adv=adv, min_score=70))
    if o30 is not None:   # literature rule set 2: hardened fade (ORL break entry, SSR skip, +8% stop)
        for lo, hi, lab in ((1, 5, "1-5"), (5, 10, "5-10")):
            for ms in (None, 70):
                nm = f"S1H_{lab}_score{ms or 0}"
                evaluate(nm, "S1", S.tt_s1h(s, o30, lo, hi, 3e6, ms), replace(M100, borrow_mode="alpaca", intraday=True),
                         dict(lo=lo, hi=hi, adv=3e6, min_score=ms, entry="5m ORL break", stop="min(max(HOD,+3%),+8%)"))

    # ---------------- L2 day-2 continuation
    for lo, hi, lab in ((1, 5, "1-5"), (5, 10, "5-10")):
        for hold in (1, 3):
            nm = f"L2_{lab}_h{hold}"
            evaluate(nm, "L2", S.tt_l2(s, lo, hi, 3e6, 0.0, hold), replace(M100, hold=hold, intraday=hold == 1),
                     dict(lo=lo, hi=hi, adv=3e6, gap=0.0, hold=hold))
    for news in (True, False):     # literature rule set 3 and its no-news control
        for hold in (1, 3, 5):
            nm = f"L2N_{'news' if news else 'nonews'}_h{hold}"
            evaluate(nm, "L2", S.tt_l2b(s, news, hold), replace(M100, n=3, hold=hold),
                     dict(gap=0.10, cir=75, rvol=5, lo=3, hi=10, news=news, hold=hold, entry="15:55 day 1"))

    # ---------------- L3 reversal
    lx, pp = load_losers()
    for k in (1, 3, 5):
        nm = f"L3_r{k}_h2"
        evaluate(nm, "L3", S.tt_l3(lx, k, 2, 3e6, True), replace(M100, hold=2), dict(k=k, hold=2, adv=3e6, news=True))
    for lo, hi, lab in ((5, 10, "5-10"), (2, 5, "2-5")):   # literature rule set 5 (cost control)
        for hold in (1, 3, 5):
            nm = f"L3B_{lab}_h{hold}"
            evaluate(nm, "L3", S.tt_l3b(lx, lo, hi, hold), replace(M100, hold=hold),
                     dict(lo=lo, hi=hi, r1_max=-0.15, news="none", dv=5e6, hold=hold, entry="close"))

    # ---------------- L4 opening-range breakout (literature rule set 4, long side)
    p = os.path.join(CACHE, "orb_events.parquet")
    have_orb = os.path.isdir(os.path.join(CACHE, "minute_orb")) and not os.environ.get("SKIP_ORB")
    if have_orb:
        oe = pd.read_parquet(p) if os.path.exists(p) else S.orb_events(pp)
        oe.to_parquet(p)
        for N in (5, 15, 30):
            for stop in ("orl", "atr"):
                nm = f"L4_orb{N}_{stop}"
                evaluate(nm, "L4", S.tt_l4(oe, pp, N, stop), replace(M100, intraday=True),
                         dict(N=N, stop=stop, green=True, min_rvol=1.0, top=20, atr_min=0.05, lo=2, hi=10))

    tr = pd.DataFrame(TRIALS)
    tr.to_csv(os.path.join(RESULTS, "trials.csv"), index=False)
    import pickle
    pickle.dump(TT, open(os.path.join(CACHE, "tt.pkl"), "wb"))
    pickle.dump({"SERIES": SERIES, "TRADES": TRADES, "BOOKS": BOOKS, "FAMILY": FAMILY}, open(os.path.join(CACHE, "stage1.pkl"), "wb"))


if __name__ == "__main__":
    main()
