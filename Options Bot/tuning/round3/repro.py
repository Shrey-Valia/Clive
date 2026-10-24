"""Reproduce the two live sleeves on the real chain (sanity check of the cache copy)."""
import pickle

from common import DIP, END, F1, START, WORK, engine_client
from optionsbot import runner
from optionsbot.strategies_signals import SIGNAL_REGISTRY
from optionsbot.strategies_spreads import SpreadBook

engine_client()
out = {}
for name, cls, p, sym in [("dip", SIGNAL_REGISTRY["dip_pcs"], DIP, "SPY"),
                          ("dip", SIGNAL_REGISTRY["dip_pcs"], DIP, "IWM"),
                          ("dip", SIGNAL_REGISTRY["dip_pcs"], DIP, "QQQ"),
                          ("ic", SpreadBook, F1, "SPY")]:
    r = runner.run_one(cls, p, sym, START, END, 10_000)
    m = r["metrics"]
    print(name, sym, round(m["cagr"], 4), round(m["sharpe"], 3), round(m["max_drawdown"], 4),
          m["n_trades"], "req", runner.client().n_requests, flush=True)
    out[(name, sym)] = {"equity": r["equity"], "trades": r["trades"]}
pickle.dump(out, open(WORK / "repro.pkl", "wb"))
