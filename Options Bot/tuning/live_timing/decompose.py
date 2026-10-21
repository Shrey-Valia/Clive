#!/usr/bin/env python3
"""Follow-ups to run_timing.py:

1. Entry vs exit decomposition: entry signal at T with end-of-day exits on the
   true close, and the reverse, to see which side of the rule loses fidelity.
2. A finer grid of decision times (15:40 .. 15:58) for smoothness.

    python3 tuning/live_timing/decompose.py      -> decompose.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_timing as R                                     # noqa: E402
from optionsbot import strategies_signals as SS            # noqa: E402


class Mixed(SS.DipPCS):
    """Entry decisions on ENTRY frame, end-of-day exits (sma5/close) on EXIT frame."""
    id = "dip_pcs_mixed"
    ENTRY: dict = {}
    EXIT: dict = {}

    def manage(self, eng, d):
        f = self.EXIT.get(eng.m.symbol)
        orig = SS.features
        if f is not None:
            SS.features = lambda m, _f=f: _f
        try:
            super().manage(eng, d)
        finally:
            SS.features = orig

    def signal_ok(self, eng, d):
        f = self.ENTRY.get(eng.m.symbol)
        orig = SS.features
        if f is not None:
            SS.features = lambda m, _f=f: _f
        try:
            return super().signal_ok(eng, d)
        finally:
            SS.features = orig


def run_mixed(entry, exit_):
    res = {}
    for s in R.SYMS:
        Mixed.ENTRY = {s: entry[s]} if entry else {}
        Mixed.EXIT = {s: exit_[s]} if exit_ else {}
        r = R.run_one(Mixed, R.PARAMS, s, R.K.START, R.K.END, R.K.CAPITAL)
        res[s] = {"equity": r["equity"], "trades": r["trades"]}
    return res


def main():
    base = R.run_variant("close", None, "close")
    _, _, ref, _ = R.metrics_row("close", "close", base, None)
    days = {s: SS.features(R.market(s)).loc[R.K.START:R.K.END].index for s in R.SYMS}
    R.T_MINUTES.clear()
    R.T_MINUTES.update({f"15:{60 - m:02d}": m for m in (20, 15, 10, 8, 6, 5, 4, 3, 2)})
    part = {s: R.partial_bars(s, days[s], "sip") for s in R.SYMS}
    rows = []
    for lab, mb in R.T_MINUTES.items():
        fr = {s: R.t_features(R.market(s), part[s][lab]) for s in R.SYMS}
        for kind, res in (("both_at_T", R.run_variant(lab, fr, "close")),
                          ("entry_at_T_exit_close", run_mixed(fr, None)),
                          ("entry_close_exit_at_T", run_mixed(None, fr)) if mb in (10, 5, 4) else (None, None)):
            if kind is None:
                continue
            row, *_ = R.metrics_row(lab, "close", res, ref)
            row["kind"] = kind
            rows.append(row)
            print(f"{lab} {kind:24} IS {row['is_sharpe']:.2f} OOS {row['oos_sharpe']:.2f} full {row['full_sharpe']:.2f} "
                  f"tr {row['full_trades']} kept {row['kept']} missed {row['missed']} added {row['added']}")
    pd.DataFrame(rows).to_csv(HERE / "decompose.csv", index=False)


if __name__ == "__main__":
    main()
