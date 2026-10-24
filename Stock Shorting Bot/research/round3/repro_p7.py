"""Reproduce round-1 P7 with the round-1 engine on the read-only cache (sanity anchor)."""
import os
import sys
from dataclasses import replace

import r3common  # noqa: F401  (puts research/backtest on sys.path)
import common

common.CACHE = r3common.SRC
import engine  # noqa: E402

engine.CACHE = r3common.SRC
import analyze  # noqa: E402

analyze.CACHE = r3common.SRC

ev, days = analyze.load()
p7 = replace(engine.Config(), name="P7", entry="open1", exit="close1", sizing="equal", gross=0.5, min_score=70,
             borrow_gate_price=10, borrow_gate_adv=10e6)
d, t = engine.run(p7, ev, days)
(di, ti), (do, to) = analyze.split(d, t)
print("IS ", {k: round(v, 4) for k, v in engine.stats(di, ti).items() if isinstance(v, (float, int))})
print("OOS", {k: round(v, 4) for k, v in engine.stats(do, to).items() if isinstance(v, (float, int))})
t.to_csv(os.path.join(r3common.CACHE, "p7_trades_round1.csv"), index=False)
d.to_csv(os.path.join(r3common.CACHE, "p7_daily_round1.csv"), index=False)
