"""Deep dive on the noise-area intraday momentum candidate (F5b).

1. Base spec with one-minute fill latency (next-minute open) instead of the signal bar's close.
2. Cost sensitivity: 0, 0.25, 0.5, 1, 1.5, 3 bp per side.
3. Parameter neighbourhood (lookback 10/14/20, check step 15/30/60, band x0.75/1/1.25): plateau check.
   Every neighbourhood variant is registered as a trial (counts toward the DSR).
4. Cross-sectional robustness: the same untouched spec on 13 ETFs (registered).
5. Sub-periods: IS 2016-21, OOS 2022-26, post-publication (2024-05-20 onwards; SSRN 4824172).
"""
import itertools
import os
import sys

import numpy as np
import pandas as pd

from common import RES, by_year, corr_block, perf, register, split_perf
from noise import run

PUB = "2024-05-20"
rows = []


def summarise(key, r, info, extra):
    sp = split_perf(r)
    pp = perf(r[r.index >= PUB])
    rows.append(dict(name=key, **extra, sides_per_day=info.sides.mean(), expo=info.time_in_mkt_x_lev.mean(),
                     post_pub_sharpe=pp["sharpe"], post_pub_cagr=pp["cagr"], **sp, **corr_block(r)))


mode = sys.argv[1] if len(sys.argv) > 1 else "all"
series = {}
if mode in ("all", "base"):
    for sym in ["QQQ", "SPY", "IWM"]:
        for fn in (True, False):
            r, info = run(sym, fill_next=fn)
            key = f"F5b_NOISE_voltgt_{sym}_fill{'next' if fn else 'bar'}"
            if fn:
                register(key, "F5_intraday", r, {"sym": sym, "fill_next": True})
            series[key] = r
            summarise(key, r, info, {"sym": sym, "cost_bp": 0.5, "fill_next": fn})
    for cs in [0, 0.25e-4, 1e-4, 1.5e-4, 3e-4]:
        r, info = run("QQQ", cost_side=cs, sell_fee=0 if cs == 0 else 0.3e-4)
        summarise(f"QQQ_cost{cs*1e4:.2f}", r, info, {"sym": "QQQ", "cost_bp": cs * 1e4})
    for lb, step, bm in itertools.product([10, 14, 20], [15, 30, 60], [0.75, 1.0, 1.25]):
        if (lb, step, bm) == (14, 30, 1.0):
            continue
        r, info = run("QQQ", lb=lb, step=step, band_mult=bm)
        key = f"F5b_NB_QQQ_lb{lb}_s{step}_b{bm}"
        register(key, "F5_noise_nbhd", r, {"lb": lb, "step": step, "band": bm})
        summarise(key, r, info, {"sym": "QQQ", "lb": lb, "step": step, "band": bm})
    for lev in ["lev1"]:
        r, info = run("QQQ", lev_mode=lev)
        summarise(f"QQQ_{lev}", r, info, {"sym": "QQQ", "lev": lev})
    pd.DataFrame(series).to_parquet(os.path.join(RES, "f5b_series.parquet"))
    pd.DataFrame(rows).to_csv(os.path.join(RES, "f5b_deep.csv"), index=False)
if mode in ("all", "xs"):
    rows = []
    xs = {}
    for sym in ["SPY", "QQQ", "IWM", "DIA", "MDY", "XLK", "XLF", "XLE", "XLV", "TLT", "GLD", "EEM", "EFA"]:
        try:
            r, info = run(sym)
        except Exception as e:  # missing data
            print(sym, e); continue
        key = f"F5b_XS_{sym}"
        if sym not in ("SPY", "QQQ", "IWM"):
            register(key, "F5_noise_xs", r, {"sym": sym})
        xs[sym] = r
        summarise(key, r, info, {"sym": sym})
    pd.DataFrame(xs).to_parquet(os.path.join(RES, "f5b_xs_series.parquet"))
    pd.DataFrame({k: by_year(v) for k, v in xs.items()}).to_csv(os.path.join(RES, "f5b_xs_by_year.csv"))
    pd.DataFrame(rows).to_csv(os.path.join(RES, "f5b_xs.csv"), index=False)
pd.set_option("display.width", 250)
cols = ["name", "sides_per_day", "expo", "is_sharpe", "oos_sharpe", "post_pub_sharpe", "oos_cagr", "all_maxdd",
        "corr_spy", "corr_shortbot", "ret_on_spy_worst20", "ret_on_bot_worst20"]
print(pd.DataFrame(rows).reindex(columns=cols).round(3).to_string())
