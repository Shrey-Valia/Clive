"""Two extra trials (score threshold on the intraday fade) + deflated Sharpe under the null-variance convention."""
import math, os, sys, json
from dataclasses import replace
import numpy as np, pandas as pd
from scipy import stats as sst
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze
from analyze import IS_END, OOS_START, deflated_sharpe, psr
from common import RESULTS
from engine import Config, run, stats

ev, days = analyze.load()
ev["score_chg"] = ev.chg1545
base = Config()
intr = dict(entry="open1", exit="close1", sizing="equal", gross=0.5)
cands = {"P6_intraday_top5_eq50_min70": replace(base, **intr, min_score=70),
         "P7_intraday_top5_eq50_min70_strict": replace(base, **intr, min_score=70, borrow_gate_price=10, borrow_gate_adv=10e6),
         "P3s_intraday_top10_atr50_minchg20_strict": replace(base, entry="open1", exit="close1", n=10, sizing="atr", gross=0.5,
                                                             max_name=0.075, min_chg=0.20, borrow_gate_price=10, borrow_gate_adv=10e6),
         "P5s_gapup_fade_top5_eq50_strict": replace(base, **intr, filters=(("gap1", ">", 0.0),), borrow_gate_price=10, borrow_gate_adv=10e6),
         "P5_gapup_fade_top5_eq50": replace(base, **intr, filters=(("gap1", ">", 0.0),)),
         "P1_intraday_top5_eq50": replace(base, **intr),
         "best_is_grid": None}
tr = pd.read_csv(os.path.join(RESULTS, "trials.csv"))
best = tr[(tr.is_active_days >= 300) & (~tr.is_ruined.astype(bool))].sort_values("is_sharpe").iloc[-1]
cands["best_is_grid"] = Config(name="best_is", **json.loads(best.cfg))
N = len(tr) + 4
g = 0.5772156649
rows = []
for nm, c in cands.items():
    d, t = run(replace(c, name=nm), ev, days)
    (di, ti), (do, to) = analyze.split(d, t)
    si, so = stats(di, ti), stats(do, to)
    r = di.ret.values; T = len(r); sr = r.mean() / r.std(ddof=1)
    emax = (1 - g) * sst.norm.ppf(1 - 1 / N) + g * sst.norm.ppf(1 - 1 / (N * math.e))
    out = {"config": nm}
    for neff in (N, 100, 20):
        em = (1 - g) * sst.norm.ppf(1 - 1 / neff) + g * sst.norm.ppf(1 - 1 / (neff * math.e))
        sr0 = em / math.sqrt(T)
        den = math.sqrt(1 - pd.Series(r).skew() * sr + (pd.Series(r).kurt() + 2) / 4 * sr ** 2)
        out[f"dsr_is_N{neff}"] = float(sst.norm.cdf((sr - sr0) * math.sqrt(T - 1) / den))
    out.update({"is_sharpe": si["sharpe"], "is_cagr": si["cagr"], "is_maxdd": si["maxdd"], "is_trades": si.get("trades"),
                "oos_sharpe": so["sharpe"], "oos_cagr": so["cagr"], "oos_maxdd": so["maxdd"], "oos_worst_day": so["worst_day"],
                "oos_trades": so.get("trades"), "oos_mean_trade": so.get("mean_trade"), "oos_psr": psr(do.ret.values),
                "oos_cagr_costx2": stats(*analyze.split(*run(replace(c, cost_mult=2), ev, days))[1])["cagr"]})
    rows.append(out)
df = pd.DataFrame(rows); df.to_csv(os.path.join(RESULTS, "dsr_and_finalists.csv"), index=False)
print("total trials incl. extras:", N); print(df.round(3).to_string())
