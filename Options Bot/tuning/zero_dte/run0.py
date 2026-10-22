"""Run tests 1-3 of the 0DTE research harness on the cached sample.

PRE-REGISTRATION (fixed before looking at any OOS number):
  * In-sample (IS): sessions 2024-01-18 .. 2025-06-30. Out-of-sample (OOS): 2025-07-01 ..
  * Primary metric: P&L / max risk per trade, VWAP fills, 1x slippage, $0.03/contract fees.
  * Within each test family the config with the highest IS mean at 1x is "selected";
    only its OOS number is used for the verdict. All other OOS numbers are descriptive.
  * Pass bar (from papers_0dte.md): OOS mean > 0 at 1x with annualised net Sharpe > 1
    AND OOS mean > 0 at 2x.
  * Every config evaluated in IS counts as a trial.

Outputs: trades_t1.csv, trades_fomc.csv, signals.csv, (results via analyze0.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import engine0 as e  # noqa: E402

SPLIT = pd.Timestamp("2025-07-01")
STRUCTS = [("fly", w) for w in ["1.0x", "1.5x", "$3", "$5", "$10"]] + \
          [("ic", w) for w in ["1.5x", "$3", "$5", "$10"]]
ENTRIES = ["10:00", "12:00", "14:00"]
EXITS = [("hold", None), ("pt25", 0.25), ("pt50", 0.50)]
SLIPS = [0.0, 1.0, 2.0]
FOMC_EXITS = ["14:05", "14:10", "14:15", "15:25"]
KEEP = ["ok", "fail", "strikes", "K0", "sig_move", "straddle", "width", "S", "credit", "max_risk",
        "pnl", "pnl_usd", "ret", "pnl_bp", "mae", "mae_ret", "exit_t", "pt_hit",
        "max_leg_age_entry", "move_entry_exit"]


def main():
    days = pd.read_csv(HERE / "sample_days.csv", parse_dates=["date"]).fillna({"event": ""})
    rows, frows = [], []
    t0 = time.time()
    for n, dr in days.iterrows():
        day = dr.date.strftime("%Y-%m-%d")
        base = {"date": day, "kind": dr.kind, "event": dr.event,
                "period": "OOS" if dr.date >= SPLIT else "IS"}
        for mode in ("vw", "c"):
            try:
                d = e.Day(day, mode=mode)
            except Exception as exc:  # noqa: BLE001
                rows.append({**base, "mode": mode, "ok": False, "fail": f"load:{exc}"[:80]})
                continue
            slips = SLIPS if mode == "vw" else [1.0]
            for st, w in STRUCTS:
                for ent in ENTRIES:
                    for xid, pt in EXITS:
                        for sl in slips:
                            r = e.run_trade(d, st, w, ent, "15:25", pt=pt, slip=sl)
                            rows.append({**base, "mode": mode, "structure": st, "wing": w,
                                         "entry": ent, "exit_rule": xid, "slip": sl,
                                         "cfg": f"{st}|{w}|{ent}|{xid}",
                                         **{k: r.get(k) for k in KEEP}})
                # FOMC-style early exits for the 10:00 fly, run on every sample day
                if st == "fly":
                    for xt in FOMC_EXITS:
                        for sl in slips:
                            r = e.run_trade(d, st, w, "10:00", xt, pt=None, slip=sl)
                            frows.append({**base, "mode": mode, "structure": st, "wing": w,
                                          "entry": "10:00", "exit_rule": f"t{xt}", "slip": sl,
                                          "cfg": f"{st}|{w}|10:00|x{xt}",
                                          **{k: r.get(k) for k in KEEP}})
        if n % 10 == 0:
            print(n, day, f"{time.time() - t0:.0f}s", flush=True)
    pd.DataFrame(rows).to_csv(HERE / "trades_t1.csv", index=False)
    pd.DataFrame(frows).to_csv(HERE / "trades_fomc.csv", index=False)
    print("done", len(rows), len(frows))


if __name__ == "__main__":
    main()
