"""Family F1: calendar effects on SPY / IWM / QQQ (long history, Yahoo adjusted; IS <= 2015, OOS 2016+).

Hypotheses
- Turn of month (Ariel 1987; Lakonishok & Smidt 1988; McConnell & Xu 2008): returns concentrate from the
  last trading day of the month through the first ~3 trading days.
- Pre-holiday (Ariel 1990; Lakonishok & Smidt 1988): the trading day before a market holiday is unusually
  strong.
- Pre-FOMC drift (Lucca & Moench 2015): equities rise in the 24h before scheduled FOMC announcements.
- Halloween / Sell-in-May (Bouman & Jacobsen 2002): Nov-Apr returns exceed May-Oct.
Rules are close-to-close holds (MOC in, MOC out). Costs: 1.5 bp per side + sell fees.
"""
import json
import os

import numpy as np
import pandas as pd

from common import LH_IS_END, LH_OOS_START, RES, corr_block, by_year, register, split_perf
from lib import holidays_from_calendar, hold_mask_returns, month_pos, tstat_mean, yahoo

FOMC = pd.to_datetime(pd.read_csv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".cache", "fomc.csv")).date)

rows, series = [], {}
for sym in ["SPY", "IWM", "QQQ"]:
    px = yahoo(sym)
    px = px[px.index >= "1994-01-01"]
    idx = px.index
    mp = month_pos(idx)
    r = px.c.pct_change()
    masks = {}
    for a, b in [(1, 3), (2, 3), (1, 2)]:
        masks[f"TOM_m{a}_p{b}"] = (mp.bwd >= -a) | (mp.fwd <= b)
    masks["PREHOL"] = holidays_from_calendar(idx)
    f = pd.Series(idx.isin(FOMC), index=idx)
    masks["FOMC_day"] = f & (idx >= "1999-06-01")
    masks["HALLOWEEN"] = pd.Series(idx.month.isin([11, 12, 1, 2, 3, 4]), index=idx)
    masks["CAL_UNION"] = masks["TOM_m1_p3"] | masks["PREHOL"] | masks["FOMC_day"]
    for name, m in masks.items():
        if sym != "SPY" and name in ("FOMC_day", "HALLOWEEN", "CAL_UNION"):
            pass
        sr = hold_mask_returns(px, m)
        key = f"F1_{name}_{sym}"
        series[key] = sr
        register(key, "F1_calendar", sr, {"sym": sym, "rule": name})
        sp = split_perf(sr, LH_IS_END, LH_OOS_START)
        inm = r[m.reindex(idx).fillna(False)]
        outm = r[~m.reindex(idx).fillna(False)]
        d = dict(name=key, sym=sym, rule=name, exposure=m.mean(),
                 mean_in_bp=inm.mean() * 1e4, mean_out_bp=outm.mean() * 1e4,
                 t_in_vs_out=(inm.mean() - outm.mean()) / np.sqrt(inm.var() / len(inm) + outm.var() / len(outm)),
                 is_mean_in_bp=inm[inm.index <= LH_IS_END].mean() * 1e4,
                 oos_mean_in_bp=inm[inm.index >= LH_OOS_START].mean() * 1e4,
                 oos_mean_out_bp=outm[outm.index >= LH_OOS_START].mean() * 1e4,
                 **sp, **corr_block(sr[sr.index >= "2016-01-01"]))
        rows.append(d)
    # buy and hold benchmark
    bh = r.fillna(0)
    series[f"BH_{sym}"] = bh
    rows.append(dict(name=f"BH_{sym}", sym=sym, rule="buy_hold", exposure=1.0, **split_perf(bh, LH_IS_END, LH_OOS_START),
                     **corr_block(bh[bh.index >= "2016-01-01"])))

res = pd.DataFrame(rows)
res.to_csv(os.path.join(RES, "f1_calendar.csv"), index=False)
pd.DataFrame(series).to_parquet(os.path.join(RES, "f1_series.parquet"))
pd.DataFrame({k: by_year(v) for k, v in series.items()}).to_csv(os.path.join(RES, "f1_by_year.csv"))
cols = ["name", "exposure", "mean_in_bp", "mean_out_bp", "t_in_vs_out", "is_mean_in_bp", "oos_mean_in_bp",
        "oos_mean_out_bp", "is_sharpe", "oos_sharpe", "oos_cagr", "oos_maxdd", "corr_spy", "corr_shortbot"]
pd.set_option("display.width", 250)
print(res[cols].round(3).to_string())
