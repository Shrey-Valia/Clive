"""Q6 the overnight long sleeve: buy at T 15:55, sell at the T+1 open.

Entry: T 15:55 minute-bar open (round-1 p1555), a marketable limit at the ask -> pays the base
one-way cost. Exits compared:
  opg   official opening print (market-on-open; live needs the --open job before 09:28)
  m1    VWAP of the 09:31 bar (a market order at 09:31: the live default), 09:31 spread multiplier
  m5    VWAP of the 09:35 bar
Names with dilution / offering news are skipped (live OVERNIGHT_LONG_SKIP_IF_NEWS proxy:
catalyst class 'dilution' or any dilution headline). 5% each, 25% gross cap, as live.

    python3 study_q6.py
"""
from __future__ import annotations

import os
from dataclasses import replace

import numpy as np
import pandas as pd

import common3 as c3
import sim
from r3common import IS_END, OOS_START, RES

ev, B, days = c3.load_all()
VW = B["VW"]
bc = sim.base_cost(ev.p1545_raw.values, ev.adv20.values, ev.chg1545.values)
ent = ev.p1555.values
EXITS = {"opg": (ev.o1.values, sim.TOD_AUCTION), "m1": (VW[:, 1].astype(float), sim.tod_mult(np.array([2]), "m")[0]),
         "m5": (VW[:, 5].astype(float), sim.tod_mult(np.array([6]), "m")[0])}
news_skip = (ev.cat_class.values == "dilution") | (ev.n_dilution.fillna(0).values > 0)


def long_trades(exit_kind: str, cost_mult: float = 1.0) -> pd.DataFrame:
    x, mult = EXITS[exit_kind]
    gross = x / ent - 1
    c_in = bc * cost_mult
    c_out = bc * mult * cost_mult * (x / ent) + sim.FEE_SELL
    ok = np.isfinite(gross) & (ent > 0) & ev.has_day1.values & ev.ok1.values & ~news_skip
    return pd.DataFrame({"i": np.arange(len(ev)), "date": ev.date.values, "symbol": ev.symbol.values, "ok": ok,
                         "ent": ent, "ex": x, "te": -5, "tx": 1, "why": exit_kind, "gap": np.nan,
                         "gross": gross, "cost": c_in + c_out, "net": gross - c_in - c_out})


rows = []
SERIES = {}


def run(bk, exit_kind, group, log=True, cost_mult=1.0):
    trl = long_trades(exit_kind, cost_mult)
    r = sim.Rule(name=f"long_{exit_kind}" + ("" if cost_mult == 1 else f"_cost{cost_mult}"))
    row, d, t = sim.evaluate(ev, B, days, r, bk, group, tr=trl, log=log)
    rows.append(row)
    SERIES[(bk.name, r.name)] = (d, t)
    print(f"{group:<8} {bk.name:<28} {r.name:<14} {c3.fmt(row)}", flush=True)
    return row, d, t


LBK = sim.Book(name="sleeve_v2picks", score="score_live", w=0.05, gross_cap=0.25)
for xk in ("opg", "m1", "m5"):
    run(LBK, xk, "anchor" if xk == "m1" else "Q6exit", log=(xk != "m1"))
run(LBK, "m1", "stress", log=False, cost_mult=2.0)

# ---------------------------------------------------------------- different subsets than the short
subs = {
    "all_strict_top5_by_chg": sim.Book(name="all_strict_top5_by_chg", score="chg1545", min_score=None, w=0.05, gross_cap=0.25),
    "score60": replace(LBK, name="score60", min_score=60),
    "score50": replace(LBK, name="score50", min_score=50),
    "v2+cir>=80": replace(LBK, name="v2+cir>=80", filters=(("cir", ">=", 80),)),
    "v2+cir<80": replace(LBK, name="v2+cir<80", filters=(("cir", "<", 80),)),
    "v2+chg>=0.5": replace(LBK, name="v2+chg>=0.5", filters=(("chg1545", ">=", 0.5),)),
    "v2+chg<0.5": replace(LBK, name="v2+chg<0.5", filters=(("chg1545", "<", 0.5),)),
    "v2+no_news": replace(LBK, name="v2+no_news", filters=(("cat_none_f", "==", 1),)),
    "v2+hype": replace(LBK, name="v2+hype", filters=(("cat_hype_f", "==", 1),)),
    "strict_cir>=90_top5": sim.Book(name="strict_cir>=90_top5", score="chg1545", min_score=None, w=0.05,
                                    gross_cap=0.25, filters=(("cir", ">=", 90),)),
    "strict_no_excl_top5": sim.Book(name="strict_no_excl_top5", score="chg1545", min_score=None, w=0.05,
                                    gross_cap=0.25, use_excl=False),
}
ev["cat_none_f"] = (ev.cat_class == "no_news").astype(float)
ev["cat_hype_f"] = (ev.cat_class == "hype").astype(float)
for nm, bk in subs.items():
    run(bk, "m1", "Q6subset")

df = pd.DataFrame(rows)
df.drop(columns=["cfg"]).to_csv(os.path.join(RES, "q6_long_sleeve.csv"), index=False)

# per-year and worst trades for the live sleeve (m1 exit)
d, t = SERIES[("sleeve_v2picks", "long_m1")]
yr = d.assign(y=d.date.str[:4]).groupby("y").ret.apply(lambda r: np.prod(1 + r) - 1)
ty = t.assign(y=t.date.str[:4]).groupby("y").net.agg(["count", "mean", "median"])
pd.concat([yr.rename("ret"), ty], axis=1).to_csv(os.path.join(RES, "q6_sleeve_by_year.csv"))
t.sort_values("net").head(15)[["date", "symbol", "ent", "ex", "gross", "net"]].to_csv(
    os.path.join(RES, "q6_sleeve_worst.csv"), index=False)
d.to_csv(os.path.join(RES, "daily_sleeve_live.csv"), index=False)
t.to_csv(os.path.join(RES, "trades_sleeve_live.csv"), index=False)
print(pd.concat([yr.rename("ret"), ty], axis=1).round(4).to_string())
# opening print vs 09:31 fill: the slippage the sleeve gives up by selling at 09:31
m = t.i.values
o, m1 = ev.o1.values[m], VW[m, 1]
print("sleeve exit: 09:31 VWAP vs opening print, mean %+.3f%% median %+.3f%%" % (
    100 * np.nanmean(m1 / o - 1), 100 * np.nanmedian(m1 / o - 1)))
c3.flush_trials("q6")
