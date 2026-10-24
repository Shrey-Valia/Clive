"""Condor proxy validation with a like-for-like expiry set (3rd-Friday monthlies only) on both
the real-chain engine and the proxy, plus the start-date (phase) sensitivity of F1 on the real
chain: the same rule started on each of the first 20 trading days of the sample."""
import pickle

import numpy as np
import pandas as pd

import psim as P
from common import F1, OUT, WORK, engine_client
from optionsbot import runner
from optionsbot.strategies_spreads import SpreadBook

engine_client()
rows = []
real = runner.run_one(SpreadBook, {**F1, "monthly_only": True}, "SPY", "2024-03-01", "2026-10-20", 10_000)
rt = real["trades"]
u = P.U("SPY", surf_name="SPY_is")
bk = P.Book(10_000, "2024-03-01", "2026-10-20",
            [P.Condor(u, kill=False, close_before_expiry=False, monthly_only=True)], risk_total=10).run()
pt = bk.trades()
print("REAL monthly-only F1:", P.metrics(real["equity"]["equity"]))
print(rt[["open_date", "close_date", "entry_cash", "pnl", "reason"]].to_string())
print("PROXY monthly-only F1:", P.metrics(bk.equity_series()))
print(pt[["open_date", "close_date", "expiry", "credit", "entry_cash", "pnl", "reason"]].to_string())
m = pd.merge(rt.assign(open_date=pd.to_datetime(rt.open_date)), pt, on="open_date", suffixes=("_r", "_p"))
m.to_csv(OUT / "proxy_validation_condor_monthly.csv", index=False)
print("matched", len(m), "credit err/ct mean", (m.entry_cash_p - m.entry_cash_r).mean(),
      "mae", (m.entry_cash_p - m.entry_cash_r).abs().mean(), "real mean", m.entry_cash_r.mean())

# phase sensitivity on the real chain (default F1 expiry ranking)
ph = []
days = runner.market("SPY").stock.index
days = days[(days >= "2024-03-01")][:20]
for d0 in days:
    r = runner.run_one(SpreadBook, F1, "SPY", str(d0.date()), "2026-10-20", 10_000)
    t = r["trades"]
    t = t[t.reason != "open_at_end"]
    met = P.metrics(r["equity"]["equity"])
    ph.append({"start": d0.date(), "trades": len(t), "wins": int((t.pnl > 0).sum()),
               "worst_pnl": t.pnl.min(), **met})
    print(ph[-1], flush=True)
pd.DataFrame(ph).to_csv(OUT / "condor_phase_real.csv", index=False)
