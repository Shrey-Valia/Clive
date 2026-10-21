"""Second synthetic tail method (SYNTHETIC, not a backtest): map each sleeve to a Cboe
strategy index that behaves like it and replay real crash paths.

  buy & hold SPY / core SPY / idle SPY  -> SPX (price; dividends ignored)
  idle QQQ / buy & hold QQQ             -> SPX daily return x 1.27 (QQQ beta to SPY here)
  collar SPY                            -> CLL (Cboe 95-110 collar)
  covered call / wheel QQQ              -> BXMD (30-delta buy-write) x 1.27/1.0 beta scaling
  dip-PCS, iron condor (cash collateral)-> cash, plus ONE full max loss of the spread risk cap
Weights are static sub-accounts (as implemented). Max loss is applied on top of the path DD:
dd_total = (1 + dd_path) * (1 - spread_cap) - 1.
Windows: GFC 2008-08-29..2009-03-09 (CLL starts 2008-08), COVID 2020-02-19..03-23, 2022 01-03..10-12.
"""
from __future__ import annotations

import json
import sys

from common import *  # noqa
sys.path.insert(0, str(ROOT / "longhist"))
from data import load_cboe  # noqa: E402

IDX = {s: load_cboe(s) for s in ("SPX", "CLL", "BXMD")}
WIN = {"GFC": ("2008-08-29", "2009-03-09"), "COVID": ("2020-02-19", "2020-03-23"),
       "2022": ("2022-01-03", "2022-10-12")}
QB = 1.27


def path(kind, a, b):
    if kind == "cash":
        return None
    src, mult = {"spx": ("SPX", 1.0), "qqq": ("SPX", QB), "collar": ("CLL", 1.0),
                 "bw_qqq": ("BXMD", QB)}[kind]
    r = IDX[src][a:b].pct_change().fillna(0.0) * mult
    return (1 + r).cumprod()


# exposures: list of (weight, kind), spread_cap = fraction of total equity at max loss
CANDS = {
    "10k_B_core80_overlay": ([(0.8, "spx")], 0.20),
    "10k_A_cap25_cash": ([], 0.25),
    "10k_A_cap25_SPY": ([(0.96, "spx")], 0.25),
    "10k_C_ic+dip_cap50_cash": ([], 0.50),
    "10k_dip_SPY": ([], 0.25),
    "10k_ic_SPY": ([], 0.20),
    "bh_SPY": ([(1.0, "spx")], 0.0),
    "100k_L_collar_SPY60_erc40_engine": ([(0.60, "collar"), (0.0946, "spx")], 0.127 * 0.25 + 0.179 * 0.20),
    "100k_L_cc_QQQ60_erc40_engine": ([(0.60, "bw_qqq"), (0.0946, "spx")], 0.127 * 0.25 + 0.179 * 0.20),
    "100k_L_wheel_QQQ60_erc40_engine": ([(0.60, "bw_qqq"), (0.0946, "spx")], 0.127 * 0.25 + 0.179 * 0.20),
    "100k_collar_SPY": ([(1.0, "collar")], 0.0),
    "100k_cc_QQQ": ([(1.0, "bw_qqq")], 0.0),
}
rows = []
for cid, (exp, cap) in CANDS.items():
    row = {"id": cid, "spread_cap": cap}
    for w, (a, b) in WIN.items():
        idx = IDX["SPX"][a:b].index
        val = pd.Series(1.0 - sum(x for x, _ in exp), index=idx)
        for x, k in exp:
            val = val + x * path(k, a, b).reindex(idx).ffill()
        dd = float((val / val.cummax() - 1).min())
        row[f"{w} path dd"] = dd
        row[f"{w} +maxloss dd"] = (1 + dd) * (1 - cap) - 1
    rows.append(row)
df = pd.DataFrame(rows)
df.to_csv(HERE / "stress_proxy.csv", index=False)
print(df.round(3).to_string())
