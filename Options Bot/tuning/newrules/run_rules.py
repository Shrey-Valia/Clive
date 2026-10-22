#!/usr/bin/env python3
"""Rules 1-4 on the existing finalists (engine runs). Run from the Options Bot folder:

    python3 tuning/newrules/run_rules.py            # all families -> .cache/rows_engine.pkl

Families (IS 2024-03-01..2025-06-30 for choices, OOS 2025-07-01..2026-10-20 frozen):
  BASE       frozen dip_pcs finalist, SPY/QQQ/IWM $10k basket, 15:55 decision frames
  R1_vrp     VIX(VXN,RVX) - RV10 > k, k in {0,2,4}; VIX for all; soft sizing
  R2_ts      expiry by VIX9D/VIX (or VIX/VIX3M) on dip days; same or hold-to-21DTE exits
  R2_condor  held-to-expiry SPY condor, 30/45/60 DTE x 0.10/0.16/0.25 delta, 3% wings, $100k
  R3_timing  exits at the next open (option open prints), open take-profit, next-open entry,
             Thursday/Friday entry
  R4_news    dip PCS and the SPY condor finalist: skip / skip+close on neg / cat / otm / any flags
Diagnostics (not counted as trials): signal_lag=1 (engine), prior-day VIX/RV and VIX9D/VIX,
double costs, research-close frames.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib as L                                                         # noqa: E402
from optionsbot import strategies_newrules as NR                       # noqa: E402
from optionsbot.engine import Costs                                     # noqa: E402

D = NR.DipPCSNR
B = NR.SpreadBookNR
DOUBLE = Costs(slip_abs=0.06, slip_pct=0.04, slip_cap=0.50)


def condor(dte, delta):
    win = {30: (23, 37), 45: (38, 52), 60: (50, 70)}[dte]
    return {**L.CONDOR, "put_delta": delta, "call_delta": delta, "dte_min": win[0], "dte_max": win[1],
            "dte_target": dte}


def variants():
    V = []                                   # (rule, variant, cls, params, syms, capital, extra, counted, note)
    V.append(("BASE", "dip_pcs_1555", D, {}, L.ETFS, L.CAP10, "", True, "frozen finalist at 15:55"))
    for idx in ("own", "VIX"):
        for k in (0, 2, 4):
            V.append(("R1_vrp", f"vrp_{idx}_k{k}", D, {"vrp_k": k, "vrp_index": idx}, L.ETFS, L.CAP10, "", True, ""))
        V.append(("R1_vrp", f"vrp_{idx}_soft", D, {"vrp_soft": True, "vrp_index": idx}, L.ETFS, L.CAP10, "", True, ""))
    for rule in ("vix9d", "vix3m"):
        for ex in ("same", "hold"):
            V.append(("R2_ts", f"ts_{rule}_{ex}", D, {"ts_rule": rule, "long_exits": ex}, L.ETFS, L.CAP10, "", True, ""))
    for dte in (30, 45, 60):
        for dl in (0.10, 0.16, 0.25):
            V.append(("R2_condor", f"ic_{dte}d_{int(dl * 100)}d", B, condor(dte, dl), ["SPY"], 100_000, "", True,
                      "F1 finalist" if (dte, dl) == (60, 0.10) else ""))
    V.append(("R3_timing", "exit_open", D, {"exit_at": "open"}, L.ETFS, L.CAP10, "", True, ""))
    V.append(("R3_timing", "exit_open_tp", D, {"exit_at": "open", "open_tp": True}, L.ETFS, L.CAP10, "", True, ""))
    V.append(("R3_timing", "entry_next_open", D, {"entry_at": "next_open"}, L.ETFS, L.CAP10, "", True, ""))
    V.append(("R3_timing", "entry_thu_fri", D, {"entry_day": "thu_fri"}, L.ETFS, L.CAP10, "", True, ""))
    V.append(("R3_timing", "entry_fri", D, {"entry_day": "fri"}, L.ETFS, L.CAP10, "", True, ""))
    for fl in ("neg", "cat", "otm", "any"):
        for act in ("skip", "skip_close"):
            V.append(("R4_news", f"dip_{fl}_{act}", D, {"news_flag": fl, "news_action": act}, L.ETFS, L.CAP10,
                      "flags", True, ""))
            V.append(("R4_news", f"ic_{fl}_{act}", B, {**L.CONDOR, "news_flag": fl, "news_action": act}, ["SPY"],
                      100_000, "flags", True, ""))
    # reference for the condor news family (= R2_condor ic_60d_10d, not re-counted)
    V.append(("R4_news", "ic_none", B, dict(L.CONDOR), ["SPY"], 100_000, "", False, "condor finalist F1, reference"))
    return V


def diagnostics():
    X = []
    X.append(("DIAG", "dip_close_frames", D, {"frame": "close"}, L.ETFS, L.CAP10, "", False, "research close frames", 0, None))
    X.append(("DIAG", "dip_1555_lag1", D, {}, L.ETFS, L.CAP10, "", False, "engine signal_lag=1", 1, None))
    X.append(("DIAG", "dip_1555_double", D, {}, L.ETFS, L.CAP10, "", False, "double costs", 0, DOUBLE))
    return X


def run(V, diag=False):
    rows, curves, trades = [], {}, {}
    for v in V:
        rule, name, cls, params, syms, cap, extra, counted, note = v[:9]
        lag = v[9] if len(v) > 9 else 0
        costs = v[10] if len(v) > 10 else None
        row, b, tr = L.evaluate(rule, name, cls, params, syms, cap, extra, costs=costs, counted=counted,
                                note=note, lag=lag)
        rows.append(row)
        curves[(rule, name)] = b
        trades[(rule, name)] = tr
        print(f"{rule:10} {name:22} IS {row['is_sharpe']:+.2f} OOS {row['oos_sharpe']:+.2f} "
              f"trades {row['is_trades']}/{row['oos_trades']} DD {row['full_maxdd']:.3f}", flush=True)
    return rows, curves, trades


def main():
    L.frames_1555()
    NR.NEWS_FLAGS.update(pickle.loads((L.CACHE / "news_flags.pkl").read_bytes()))
    rows, curves, trades = run(variants() + diagnostics())
    (L.CACHE / "rows_engine.pkl").write_bytes(pickle.dumps({"rows": rows, "curves": curves, "trades": trades,
                                                            "core": L.CORE}))
    print("core hash", L.CORE, "open-bar requests", NR.OBARS.n_requests)


if __name__ == "__main__":
    main()
