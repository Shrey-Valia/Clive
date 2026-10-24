"""Q3 real-chain check: the frozen dip PCS on extra ETFs, 2024-03..2026-10, optionsbot engine.

Fetches (read-only, data.alpaca.markets) only what the engine needs on signal days into the scratch
cache copy, throttled to <= 60 requests/min. Run outside 09:25-10:00 and 15:30-16:30 ET so the live
bots' data calls are never crowded out. Writes out/realchain_extra.csv and WORK/realchain_extra.pkl.
"""
import os
import pickle
import sys

import alpaca_env

alpaca_env.load()
os.environ["OPTIONSBOT_MIN_INTERVAL"] = "1.0"
os.environ["R3_ONLINE"] = "1"

import pandas as pd  # noqa: E402

from common import DIP, END, OUT, START, WORK, engine_client  # noqa: E402
from optionsbot import runner  # noqa: E402
from optionsbot.strategies_signals import SIGNAL_REGISTRY  # noqa: E402

engine_client()
syms = sys.argv[1:] or ["DIA", "XLF", "XLE", "XLK", "XLI", "XLV", "GLD", "TLT", "EEM", "SMH"]
p = WORK / "realchain_extra.pkl"
out = pickle.load(open(p, "rb")) if p.exists() else {}
rows = []
for s in syms:
    if s not in out:
        n0 = runner.client().n_requests
        r = runner.run_one(SIGNAL_REGISTRY["dip_pcs"], DIP, s, START, END, 10_000)
        out[s] = {"equity": r["equity"], "trades": r["trades"], "metrics": {k: v for k, v in r["metrics"].items()
                                                                           if not isinstance(v, dict)},
                  "is": r["metrics"]["in_sample"], "oos": r["metrics"]["out_of_sample"],
                  "requests": runner.client().n_requests - n0}
        pickle.dump(out, open(p, "wb"))
    m = out[s]
    rows.append({"sym": s, "cagr": m["metrics"]["cagr"], "sharpe": m["metrics"]["sharpe"],
                 "max_dd": m["metrics"]["max_drawdown"], "trades": m["metrics"]["n_trades"],
                 "win": m["metrics"].get("win_rate"), "is_sharpe": m["is"].get("sharpe"),
                 "oos_sharpe": m["oos"].get("sharpe"), "requests": m["requests"]})
    print(rows[-1], flush=True)
pd.DataFrame(rows).to_csv(OUT / "realchain_extra.csv", index=False)
