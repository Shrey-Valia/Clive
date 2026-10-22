"""Reproduce the approved v2 short (P7) with the shorting research's own engine, imported read-only.

Runs in its own process so its 'common' module does not clash with ours. Saves
.cache/v2_daily.parquet, .cache/v2_trades.parquet and .cache/ev_prepped.parquet (events + legs).
Also a few v2 variants used by S1/LS1 at prices below $10 with the same engine (costs re-done later).
"""
import os
import sys
from dataclasses import replace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SBT = os.path.join(os.path.dirname(ROOT), "Stock Shorting Bot", "research", "backtest")
sys.path.insert(0, SBT)
import analyze  # noqa: E402
from engine import Config, run, stats  # noqa: E402

CACHE = os.path.join(ROOT, ".cache")

ev, days = analyze.load()
base = Config()
intr = dict(entry="open1", exit="close1", sizing="equal", gross=0.5)
p7 = replace(base, name="v2", **intr, min_score=70, borrow_gate_price=10, borrow_gate_adv=10e6)
d, t = run(p7, ev, days)
(di, ti), (do, to) = analyze.split(d, t)
print("v2 IS", {k: round(v, 3) for k, v in stats(di, ti).items() if isinstance(v, float)})
print("v2 OOS", {k: round(v, 3) for k, v in stats(do, to).items() if isinstance(v, float)})
d.to_parquet(os.path.join(CACHE, "v2_daily.parquet"))
t.to_parquet(os.path.join(CACHE, "v2_trades.parquet"))
keep = [c for c in ev.columns if ev[c].dtype != object or c in ("symbol", "date", "cat_class", "exclude_bot", "status")]
ev[keep].to_parquet(os.path.join(CACHE, "ev_prepped.parquet"))
import json
json.dump({"days": days}, open(os.path.join(CACHE, "v2_days.json"), "w"))
