"""Task 3a: $10k candidates (all run as one shared-cash account through pool.DipPool).

Trial grid (16, all counted):
  A  dip-PCS SPY+QQQ+IWM, per-trade max loss 25% of equity, total cap {25,50,75}% x idle {cash, SPY, QQQ}
  B  core SPY {70,80,90}% (monthly rebalance) + dip-PCS overlay on the remaining cash, cap 25%
  C  SPY iron condor (spreads F1, 20%/trade) + dip-PCS ETFs under one cap {50,75}% x idle {cash, SPY}
Each trial is also run with T-bill interest on cash (reporting variant, not a new trial).
"""
from __future__ import annotations

import pickle
import time

from common import *  # noqa
from optionsbot import runner
from pool import DipPool

TRIALS = []
for cap in (0.25, 0.50, 0.75):
    for idle in ("cash", "SPY", "QQQ"):
        TRIALS.append((f"A_cap{int(cap*100)}_{idle}", dict(cap=cap, idle=idle)))
for core in (0.7, 0.8, 0.9):
    TRIALS.append((f"B_core{int(core*100)}_overlay", dict(cap=0.25, core=core)))
for cap in (0.50, 0.75):
    for idle in ("cash", "SPY"):
        TRIALS.append((f"C_ic+dip_cap{int(cap*100)}_{idle}", dict(cap=cap, idle=idle, with_ic=True)))


def main(capital=10_000):
    out = {}
    t0 = time.time()
    for tid, kw in TRIALS:
        for cy in (False, True):
            p = DipPool(capital, cash_yield=cy, **kw).run()
            f = p.equity_frame()
            out[(tid, cy)] = {"equity": f["equity"], "usage": p.usage(), "frame": f,
                              "trades": p.trades_frame(), "params": kw}
            m = M.equity_metrics(f["equity"])
            mi = M.equity_metrics(f["equity"][:IS_END])
            mo = M.equity_metrics(f["equity"][OOS_START:])
            print(f"{tid:24s} ty={int(cy)} CAGR {m['cagr']:7.2%} Sh {m['sharpe']:5.2f} DD {m['max_drawdown']:7.2%}"
                  f" | IS Sh {mi['sharpe']:5.2f} DD {mi['max_drawdown']:7.2%} | OOS Sh {mo['sharpe']:5.2f}"
                  f" n={len(out[(tid, cy)]['trades'])} [{time.time()-t0:.0f}s]", flush=True)
    (CACHE / f"p{capital // 1000}k.pkl").write_bytes(pickle.dumps(out))


if __name__ == "__main__":
    main()
