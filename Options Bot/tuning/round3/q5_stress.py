"""Q5: stress loss of the whole book at $15k (options) + $10k (shorting bot).

A. Historical replays with the book FULLY LOADED at the close before the shock (worst plausible
   timing): condor F1 (20%) planned first, then dip spreads on SPY and IWM (25% each, all inside
   the 50% shared budget), entered regardless of signals; then the live rules run (dip exits,
   condor held) for 25 trading days. SPX/10 stands in for SPY before 2016 (one dip spread at 30%).
B. Synthetic overnight gaps on three starting states (calm / typical / elevated VIX): the book is
   opened at the close of the reference day and revalued on the next day with spot x (1+gap) and
   the VIX term structure replaced (VIX9D = 1.15 x, VIX3M = 0.85 x, VIX6M = 0.78 x the new VIX).
   Shorting bot: 5 names x 5% short, squeezed +30% (no stop fill), per the institutional review.
Both with and without the recommended configuration and hedges. Writes out/q5_stress_*.csv.
"""
import copy
import sys

import numpy as np
import pandas as pd

import book as BK
import psim as P
from common import OUT

REPLAYS = {   # entry close, label
    "2018-02-01": "Feb-2018 Volmageddon", "2020-02-21": "Feb/Mar-2020 COVID", "2024-08-01": "Aug-2024 yen carry",
    "2025-04-02": "Apr-2025 tariffs", "2018-10-03": "Oct-2018", "2022-04-20": "Apr/May-2022",
    "2023-09-14": "Sep-2023", "2008-09-26": "Oct-2008 (SPX)", "2010-05-05": "May-2010 flash (SPX)",
    "2011-07-29": "Aug-2011 (SPX)", "2015-08-19": "Aug-2015 (SPX)", "1998-08-04": "Aug-1998 LTCM (SPX)",
    "1997-10-23": "Oct-1997 (SPX)",
}


def forced_book(E, cfg, hedge=None, horizon=25):
    E = pd.Timestamp(E)
    spx = E < pd.Timestamp("2016-10-20")
    spy = BK.U("SPX10" if spx else "SPY")
    names = [spy] if (spx or cfg.get("spy_only")) else [spy, BK.U("IWM")]
    dip_kw = dict(cfg.get("dip", {}))
    dip_kw.update(signal="always", only_dates={E}, kill=False)
    if spx and "risk_frac" not in cfg.get("dip", {}):
        dip_kw["risk_frac"] = 0.30          # one index spread stands in for SPY + IWM
    cond_kw = dict(cfg.get("condor", {}), only_dates={E}, kill=False)
    sleeves = []
    if cfg.get("condor_on", True):
        sleeves.append(P.Condor(spy, **cond_kw))
    sleeves.append(P.DipPCS(names, **dip_kw))
    if hedge:
        sleeves.append(P.PutHedge(spy, **hedge))
    idx = spy.idx
    end = idx[min(idx.get_loc(E) + horizon, len(idx) - 1)]
    bk = P.Book(15_000, E, end, sleeves, risk_total=cfg.get("risk_total", 0.5)).run()
    return bk


def shock_state(u, d_next, gap, vix):
    """Copy of u with day d_next replaced by a synthetic shock relative to the previous day."""
    v = copy.copy(u)
    v._c = dict(u._c)
    v._vols = u._vols.copy()
    v._vix = u._vix.copy()
    v._ts = u._ts.copy()
    i = u._pos[d_next]
    prev = u.idx[i - 1]
    v._c[d_next] = u._c[prev] * (1 + gap)
    v._vols[i] = np.array([vix * 1.15, vix, vix * 0.85, vix * 0.78])
    v._vix[i] = vix
    v._ts[i] = 1 / 0.85
    return v


def synthetic(E, gap, vix, cfg, hedge=None):
    E = pd.Timestamp(E)
    spy0, iwm0 = BK.U("SPY"), BK.U("IWM")
    nxt = spy0.idx[spy0.idx.get_loc(E) + 1]
    beta_iwm = 1.25
    spy = shock_state(spy0, nxt, gap, vix)
    iwm = shock_state(iwm0, nxt, gap * beta_iwm, vix)
    dip_kw = dict(cfg.get("dip", {}), signal="always", only_dates={E}, kill=False)
    sleeves = []
    if cfg.get("condor_on", True):
        sleeves.append(P.Condor(spy, **dict(cfg.get("condor", {}), only_dates={E}, kill=False)))
    sleeves.append(P.DipPCS([spy, iwm], **dip_kw))
    if hedge:
        sleeves.append(P.PutHedge(spy, **hedge))
    bk = P.Book(15_000, E, nxt, sleeves, risk_total=cfg.get("risk_total", 0.5))
    bk.run()
    eq = bk.equity_series()
    return eq.iloc[-1] / 15_000 - 1, bk


def short_on(dates, sr):
    return float((1 + sr.reindex(dates).fillna(0)).prod() - 1)


CONFIGS = {
    "current live": dict(),
}


def main(cfgs=CONFIGS, hedges=None, tag=""):
    hedges = hedges or {"no hedge": None}
    sr = BK.shorting_daily()
    rows = []
    for cname, cfg in cfgs.items():
        for hname, h in hedges.items():
            for E, lab in REPLAYS.items():
                bk = forced_book(E, cfg, h)
                eq = bk.equity_series()
                tr = bk.trades()
                worst = eq.min() / 15_000 - 1
                end = eq.iloc[-1] / 15_000 - 1
                srt = short_on(eq.index[1:], sr) if pd.Timestamp(E) >= pd.Timestamp("2016-02-01") else np.nan
                rows.append({"config": cname, "hedge": hname, "event": lab, "entry": E,
                             "opt_worst_close_pct": worst, "opt_end_pct": end,
                             "opt_worst_$": worst * 15_000, "short_bot_pct": srt,
                             "book_worst_$": worst * 15_000 + (srt * 10_000 if np.isfinite(srt) else 0.0),
                             "max_loss_reserved_pct": sum(t.max_loss0 for t in tr.itertuples()
                                                          if t.sleeve != "hedge") / 15_000})
                print(rows[-1], flush=True)
    rep = pd.DataFrame(rows)
    rep.to_csv(OUT / f"q5_stress_replay{tag}.csv", index=False)
    srows = []
    states = {"calm (2024-07-10, VIX 12.9)": "2024-07-10", "typical (2026-10-16)": "2026-10-16",
              "elevated (2024-08-07, VIX 27)": "2024-08-07"}
    shocks = [(-0.03, 25), (-0.05, 30), (-0.10, 45), (-0.20, 80), (+0.05, 12)]
    for cname, cfg in cfgs.items():
        for hname, h in hedges.items():
            for sname, E in states.items():
                for g, v in shocks:
                    r, bk = synthetic(E, g, v, cfg, h)
                    squeeze = -0.25 * 0.30 if g < 0 else -0.25 * 0.10
                    srows.append({"config": cname, "hedge": hname, "state": sname, "gap": g, "vix_to": v,
                                  "opt_pct": r, "opt_$": r * 15_000, "short_bot_$ (squeeze)": squeeze * 10_000,
                                  "book_$": r * 15_000 + squeeze * 10_000,
                                  "book_pct_of_25k": (r * 15_000 + squeeze * 10_000) / 25_000})
                    print(srows[-1], flush=True)
    syn = pd.DataFrame(srows)
    syn.to_csv(OUT / f"q5_stress_synthetic{tag}.csv", index=False)
    return rep, syn


if __name__ == "__main__":
    main()
