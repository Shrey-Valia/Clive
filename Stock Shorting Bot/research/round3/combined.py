"""The two books together, as they would actually run.

Alpaca will not accept a short sale in a name the account is still long, so a name the overnight
sleeve holds cannot be shorted in the same opening auction it is sold in. Under the round-3 flags
such a name is shorted at 09:31 (market, the existing path) after the OPG sell fills; every other
pick is shorted market-on-open.

  A  live today      sleeve sold 09:31 market | short 09:31 market, 35% stop, 15:55 cover
  B  sleeve -> OPG   sleeve sold at the open  | short as live
  C  v2.1            sleeve sold at the open  | short MOO (09:31 for sleeve names), no stop, MOC cover
  D  v2.1, no sleeve                          | short MOO, no stop, MOC cover

    python3 combined.py
"""
from __future__ import annotations

import os
from dataclasses import replace

import numpy as np
import pandas as pd

import common3 as c3
import sim
import study_q6 as q6  # noqa: F401  (re-uses its long-trade builder; prints its own table)
from r3common import IS_END, OOS_START, RES

ev, B, days = q6.ev, q6.B, q6.days
BK = sim.Book(score="score_live")
LBK = sim.Book(name="sleeve", score="score_live", w=0.05, gross_cap=0.25)
LIVE = sim.Rule()
AUCT = replace(LIVE, name="v2_auction", entry="open", gap_ref="pm", stop=None, time_exit=390)
M1_NOSTOP_MOC = replace(LIVE, name="m1_nostop_moc", stop=None, time_exit=390)


def daily(tr, bk):
    d, t = sim.book(ev, tr, bk, days)
    return d.set_index("date").ret, t


def mixed(sleeve_names: set):
    """Short trade table: MOO for names not held by the sleeve, 09:31 for sleeve names."""
    a = sim.trade_returns(ev, B, AUCT)
    m = sim.trade_returns(ev, B, M1_NOSTOP_MOC)
    key = list(zip(ev.symbol, ev.date))
    use_m = np.array([k in sleeve_names for k in key])
    out = a.copy()
    out.loc[use_m] = m.loc[use_m].values
    out["ok"] = out.ok.astype(bool)
    for c in ("ent", "ex", "gross", "cost", "net", "gap"):
        out[c] = out[c].astype(float)
    return out


rows = []
for cm in (1.0, 2.0):
    l_m1 = q6.long_trades("m1", cm)
    l_opg = q6.long_trades("opg", cm)
    s_live = sim.trade_returns(ev, B, replace(LIVE, cost_mult=cm))
    L1, tl1 = daily(l_m1, LBK)
    L2, tl2 = daily(l_opg, LBK)
    held = set(zip(tl2.symbol, tl2.date))
    S_live, _ = daily(s_live, BK)
    mx = mixed(held)
    for c in ("cost", "net"):
        pass
    if cm != 1.0:   # scale the mixed table's costs: rebuild it from cost-multiplied rules
        a = sim.trade_returns(ev, B, replace(AUCT, cost_mult=cm))
        m = sim.trade_returns(ev, B, replace(M1_NOSTOP_MOC, cost_mult=cm))
        use_m = np.array([k in held for k in zip(ev.symbol, ev.date)])
        mx = a.copy(); mx.loc[use_m] = m.loc[use_m].values
        mx["ok"] = mx.ok.astype(bool)
        for c in ("ent", "ex", "gross", "cost", "net", "gap"):
            mx[c] = mx[c].astype(float)
    S_mix, _ = daily(mx, BK)
    S_auct, _ = daily(sim.trade_returns(ev, B, replace(AUCT, cost_mult=cm)), BK)
    books = {"A_live_today": L1 + S_live, "B_sleeve_opg": L2 + S_live, "C_v2.1_with_sleeve": L2 + S_mix,
             "D_v2.1_short_only": S_auct, "short_live_only": S_live}
    for nm, r in books.items():
        d = r.reset_index().rename(columns={0: "ret"})
        d.columns = ["date", "ret"]
        d["n"] = (d.ret != 0).astype(int)
        out = {"name": nm, "cost_mult": cm}
        for p, s in (("is", d[d.date <= IS_END]), ("oos", d[d.date >= OOS_START])):
            st = sim.stats(s)
            out.update({f"{p}_{k}": v for k, v in st.items() if k in ("sharpe", "cagr", "maxdd", "worst_day")})
        rows.append(out)
        print(f"{nm:<22} x{cm:g} IS SR {out['is_sharpe']:.2f} CAGR {100 * out['is_cagr']:.1f}% DD {100 * out['is_maxdd']:.1f}% | "
              f"OOS SR {out['oos_sharpe']:.2f} CAGR {100 * out['oos_cagr']:.1f}% DD {100 * out['oos_maxdd']:.1f}% worst day {100 * out['oos_worst_day']:.1f}%")
pd.DataFrame(rows).to_csv(os.path.join(RES, "combined_books.csv"), index=False)
print("sleeve names that would fall back to the 09:31 short: %.0f%% of v2 picks" % (
    100 * len(held & set(zip(*[sim.picks(ev, BK)[c] for c in ("symbol", "date")]))) / len(sim.picks(ev, BK))))
