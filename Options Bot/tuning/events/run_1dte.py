"""Z1 / F1 / F2: SPY & QQQ options expiring the next session, sold at close T,
settled at intrinsic on the T+1 close. Every trading day 2024-01-19 .. 2026-10-19
(subsample with --step N). FOMC/CPI eves are tagged so F1/F2 are subsets.

Output: tuning/events/trades_1dte.csv
Usage: python tuning/events/run_1dte.py [--symbols SPY,QQQ] [--step 1]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

import numpy as np
import pandas as pd

from optionsbot.engine import Costs
from optionsbot.runner import client, market
from optionsbot.strategies_events import EventData, OneDayCondor

VARIANTS = [
    ("Z1_ic_w5", {"structure": "ic", "short_mult": 1.0, "wing_abs": 5.0}),
    ("Z1_ic_w2x", {"structure": "ic", "short_mult": 1.0, "wing_mult": 2.0}),
    ("Z1_fly_w1x", {"structure": "fly", "wing_mult": 1.0}),
    ("Z1_straddle", {"structure": "straddle"}),
]
SLIPS = [0.0, 0.5, 1.0, 2.0]


def prefetch(ed: EventData, exp, T):
    """One batch: puts below / calls above spot out to ~3.2x a VIX-based 1-day move + $6."""
    S = ed.spot(T)
    vix = ed.m.stock.at[T, "vix"] if "vix" in ed.m.stock else np.nan
    v = (vix if np.isfinite(vix) else 18.0) / 100 * (1.25 if ed.m.symbol == "QQQ" else 1.0)
    est = v / math.sqrt(252)
    lo, hi = S * (1 - 3.2 * est) - 6, S * (1 + 3.2 * est) + 6
    puts = ed.strikes_between(exp, "P", lo, S + 3)
    calls = ed.strikes_between(exp, "C", S - 3, hi)
    # keep at most 50 per side, nearest to spot
    puts = sorted(puts, key=lambda x: abs(x[0] - S))[:50]
    calls = sorted(calls, key=lambda x: abs(x[0] - S))[:50]
    ed.load([s for _, s in puts + calls], exp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--step", type=int, default=1)
    a = ap.parse_args()
    macro = pd.read_csv(HERE / "macro_dates.csv", parse_dates=["date"])
    fomc = set(macro.loc[macro.event == "FOMC", "date"])
    cpi = set(macro.loc[macro.event == "CPI", "date"])
    costs = Costs()
    rows = []
    for sym in a.symbols.split(","):
        ed = EventData(market(sym))
        days = ed.tdays[(ed.tdays >= "2024-01-19") & (ed.tdays <= "2026-10-19")][::a.step]
        for n, T in enumerate(days):
            T1 = ed.tday(T, 1)
            tag = "FOMC" if T1 in fomc else ("CPI" if T1 in cpi else "")
            if T1 in ed.m.expiries["C"]:
                prefetch(ed, T1, T)
            for vid, params in VARIANTS:
                strat = OneDayCondor(**params)
                for sm in SLIPS:
                    try:
                        r = strat.trade(ed, {"D": T}, costs, slip_mult=sm, strict=True)
                    except Exception as exc:
                        r = {"ok": False, "fail": f"error:{type(exc).__name__}:{exc}"[:120]}
                    rows.append({"ticker": sym, "D": str(T.date()), "event": tag,
                                 "period": "OOS" if T >= pd.Timestamp("2025-07-01") else "IS",
                                 "variant": vid, "mode": "strict", "slip_mult": sm, **r})
            if n % 25 == 0:
                print(sym, T.date(), "req=", client().n_requests, flush=True)
                pd.DataFrame(rows).to_csv(HERE / "trades_1dte.csv", index=False)
    pd.DataFrame(rows).to_csv(HERE / "trades_1dte.csv", index=False)
    print("done requests:", client().n_requests)


if __name__ == "__main__":
    main()
