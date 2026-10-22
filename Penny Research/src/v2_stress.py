"""v2 under cost x2 / borrow stress with the shorting engine (read-only import), for the LS1 flip stress."""
import os, sys
from dataclasses import replace
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "Stock Shorting Bot", "research", "backtest"))
import analyze
from engine import Config, run
ev, days = analyze.load()
p7 = replace(Config(), entry="open1", exit="close1", sizing="equal", gross=0.5, min_score=70, borrow_gate_price=10, borrow_gate_adv=10e6)
for nm, c in (("cost_x2", replace(p7, cost_mult=2)), ("borrow_x2", replace(p7, borrow_base=0.30, borrow_htb=1.0)),
              ("cost_x2_borrow_x2", replace(p7, cost_mult=2, borrow_base=0.30, borrow_htb=1.0))):
    d, t = run(c, ev, days)
    d.to_parquet(os.path.join(ROOT, ".cache", f"v2_{nm}_daily.parquet"))
print("ok")
