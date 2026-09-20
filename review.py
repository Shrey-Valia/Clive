#!/usr/bin/env python3
"""Track record so far: what the scanner predicted vs what actually happened.

    python3 review.py

Reads output/journal.csv, written by daily.py as positions are closed.
The column that matters is predicted base rate (exp_avg_pct) against
realised result (pnl_pct) - that is the only honest test of whether any of
this works.
"""
from __future__ import annotations

import os
import sys

import pandas as pd

JOURNAL = os.path.join("output", "journal.csv")


def main() -> int:
    if not os.path.exists(JOURNAL):
        print(f"No journal yet at {JOURNAL}.")
        print("It is written the first time daily.py --execute closes a position.")
        return 0

    d = pd.read_csv(JOURNAL)
    if d.empty:
        print("Journal is empty.")
        return 0

    n = len(d)
    wins = (d.pnl > 0).sum()
    print(f"\n{n} completed round trip(s), "
          f"{d.entry_date.min()} to {d.exit_date.max()}\n")

    print("OVERALL")
    print(f"  total P/L      ${d.pnl.sum():+,.2f}")
    print(f"  avg per trade  ${d.pnl.mean():+,.2f}   ({d.pnl_pct.mean():+.2f}%)")
    print(f"  median         {d.pnl_pct.median():+.2f}%")
    print(f"  win rate       {100*wins/n:.1f}%  ({wins}W / {n-wins}L)")
    if wins and n - wins:
        aw, al = d[d.pnl > 0].pnl.mean(), abs(d[d.pnl <= 0].pnl.mean())
        print(f"  avg win ${aw:,.2f} vs avg loss ${al:,.2f}   "
              f"payoff {aw/al:.2f}x" if al else "")
    print(f"  best {d.pnl_pct.max():+.2f}%   worst {d.pnl_pct.min():+.2f}%")

    # The real question: did the measured base rate predict anything?
    m = d.dropna(subset=["exp_avg_pct", "pnl_pct"])
    if len(m) >= 5:
        print("\nPREDICTED vs REALISED")
        print(f"  mean predicted base rate  {m.exp_avg_pct.mean():+.2f}%")
        print(f"  mean realised             {m.pnl_pct.mean():+.2f}%")
        print(f"  gap                       {m.pnl_pct.mean()-m.exp_avg_pct.mean():+.2f} pts")
        if len(m) >= 10:
            print(f"  correlation               {m.exp_avg_pct.corr(m.pnl_pct):+.3f}"
                  "   (near zero = the base rate is not predicting)")

    for col, label in (("call", "BY DIRECTION"), ("archetype", "BY ARCHETYPE"),
                       ("overnight_risk", "BY OVERNIGHT RISK")):
        if col not in d.columns or d[col].isna().all():
            continue
        g = d.groupby(col)
        print(f"\n{label}")
        print(f"  {'':<22}{'n':>4}{'avg %':>9}{'win%':>8}{'total $':>11}")
        for k, v in g:
            print(f"  {str(k):<22}{len(v):>4}{v.pnl_pct.mean():>8.2f}%"
                  f"{100*(v.pnl>0).mean():>7.1f}%{v.pnl.sum():>10,.2f}")

    if n < 20:
        print(f"\n  NOTE: {n} trades is far too few to conclude anything. "
              "Expect 30+ before the numbers mean much.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
