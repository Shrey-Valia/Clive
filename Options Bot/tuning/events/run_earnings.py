"""Run E1-E5 over every EDGAR earnings event with option data (2024-01 .. 2026-10).

Outputs
  tuning/events/ledger.csv           E1 implied vs realized + gate inputs, one row per report
  tuning/events/trades_earnings.csv  one row per (event, strategy variant, cost level, mode)

Usage: python tuning/events/run_earnings.py [TICKER ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from optionsbot.engine import Costs
from optionsbot.runner import client, market
from optionsbot.strategies_events import (EarningsCalendar, EarningsCondor, EarningsFly,
                                          EventData, ImpliedMove, RunupStraddle)

TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "NFLX"]
FIRST_D, LAST_D = "2024-01-19", "2026-10-19"     # need D+1 close <= 2026-10-20
OOS_START = "2025-07-01"

VARIANTS = [
    ("E2_k3", RunupStraddle, {"k": 3}),
    ("E2_k5", RunupStraddle, {"k": 5}),
    ("E2_k2", RunupStraddle, {"k": 2}),
    ("E3_ic_1.0_2.0", EarningsCondor, {"short_mult": 1.0, "wing_mult": 2.0}),
    ("E4_fly_1.5", EarningsFly, {}),
    ("E5_cal_straddle", EarningsCalendar, {"sides": "CP"}),
    ("E5_cal_call", EarningsCalendar, {"sides": "C"}),
]
SLIPS = [0.0, 0.5, 1.0, 2.0]


def realized_history(tk: str) -> pd.Series:
    """|close(D+1)/close(D)-1| per EDGAR report since 2016 (total-return adjusted closes)."""
    s = client().stock_bars(tk, "2016-01-01", "2026-10-20")["adj_close"]
    ev = pd.read_csv(HERE / "earnings_dates.csv", parse_dates=["D"])
    ev = ev[ev["ticker"] == tk]
    out = {}
    for D in ev["D"]:
        if D not in s.index:
            continue
        i = s.index.get_loc(D)
        if i + 1 < len(s):
            out[D] = s.iloc[i + 1] / s.iloc[i] - 1
    return pd.Series(out).sort_index()


def main(tickers):
    costs = Costs()
    ledger, trades = [], []
    ev_all = pd.read_csv(HERE / "earnings_dates.csv", parse_dates=["D"])
    for tk in tickers:
        ed = EventData(market(tk))
        rh = realized_history(tk)
        evs = ev_all[(ev_all["ticker"] == tk) & (ev_all["D"] >= FIRST_D) & (ev_all["D"] <= LAST_D)]
        for _, e in evs.iterrows():
            D = e["D"]
            prior = rh[rh.index < D].abs()
            med8 = float(prior.iloc[-8:].median()) if len(prior) >= 8 else np.nan
            ev = {"ticker": tk, "D": D}
            row = {"ticker": tk, "D": str(D.date()), "accepted_et": e["accepted_et"],
                   "period": "OOS" if D >= pd.Timestamp(OOS_START) else "IS",
                   "med8_rm": med8, "n_hist": len(prior)}
            r1 = ImpliedMove().trade(ed, ev)
            row.update({k: v for k, v in r1.items() if k != "ok"})
            row["ok"] = r1["ok"]
            if r1["ok"]:
                row["gate_ratio"] = r1["im"] / med8 if med8 > 0 else np.nan
            ledger.append(row)
            print(tk, row["D"], "IM=%.3f RM=%.3f" % (r1.get("im", np.nan), r1.get("rm", np.nan))
                  if r1["ok"] else r1.get("fail"), "req=", ed.c.n_requests, flush=True)
            for vid, cls, params in VARIANTS:
                strat = cls(**params)
                for mode, strict, slips in (("strict", True, SLIPS), ("relaxed", False, [1.0])):
                    for sm in slips:
                        try:
                            r = strat.trade(ed, ev, costs, slip_mult=sm, strict=strict)
                        except Exception as exc:  # keep the run going; recorded as failure
                            r = {"ok": False, "fail": f"error:{type(exc).__name__}:{exc}"[:120]}
                        trades.append({"ticker": tk, "D": row["D"], "period": row["period"],
                                       "variant": vid, "mode": mode, "slip_mult": sm,
                                       "gate_ratio": row.get("gate_ratio", np.nan),
                                       "im_D": row.get("im", np.nan), "rm_D": row.get("rm", np.nan),
                                       **r})
    suffix = "" if tickers == TICKERS else "_" + "_".join(tickers)
    pd.DataFrame(ledger).to_csv(HERE / f"ledger{suffix}.csv", index=False)
    pd.DataFrame(trades).to_csv(HERE / f"trades_earnings{suffix}.csv", index=False)
    print("requests:", client().n_requests)


if __name__ == "__main__":
    main(sys.argv[1:] or TICKERS)
