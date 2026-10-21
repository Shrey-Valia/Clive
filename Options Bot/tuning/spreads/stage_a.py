"""Stage A: coarse structural grid (short delta x wing width x DTE x management bundle)
for PCS and symmetric IC on SPY and QQQ, $100k, 10% max-loss sizing, single position,
no kill switch. Run under default AND calibrated costs. Selection uses IS only."""
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import *  # noqa

DTES = {30: (24, 38, 30, 14), 45: (35, 52, 45, 21), 60: (50, 70, 60, 28)}
MGMT = {
    "M1_tp50_sl2_half": dict(tp=0.5, sl_mult=2.0, breach=False, half=True),
    "M2_tp50_nostop_half": dict(tp=0.5, sl_mult=None, breach=False, half=True),
    "M3_hold_expiry": dict(tp=None, sl_mult=None, breach=False, half=False),
    "M4_tp50_breach_half": dict(tp=0.5, sl_mult=None, breach=True, half=True),
}
DELTAS = [0.10, 0.15, 0.20, 0.25, 0.30]
WINGS = [("pct", 0.005), ("pct", 0.01), ("pct", 0.02), ("pct", 0.03)]


def params(structure, delta, wing, dte, mg):
    lo, hi, tgt, half = DTES[dte]
    m = MGMT[mg]
    p = dict(structure=structure, put_delta=delta, call_delta=delta, wing=wing[0], width_pct=wing[1],
             dte_min=lo, dte_max=hi, dte_target=tgt, tp=m["tp"], sl_mult=m["sl_mult"], breach=m["breach"],
             close_dte=half if m["half"] else None, risk_frac=0.10, max_positions=1)
    return p


def specs():
    out = []
    for costs in ("default", "calib"):
        for st, d, w, dte, mg, sym in itertools.product(("pcs", "ic"), DELTAS, WINGS, DTES, MGMT, ("SPY", "QQQ")):
            vid = f"A|{st}|d{d}|w{w[1]}|dte{dte}|{mg}"
            out.append(dict(vid=vid, stage="A", params=params(st, d, w, dte, mg), sym=sym, capital=100_000,
                            costs=costs))
    return out


if __name__ == "__main__":
    s = specs()
    if len(sys.argv) > 2:
        s = [x for x in s if x["sym"] in sys.argv[2].split(",")]
    tag = sys.argv[2] if len(sys.argv) > 2 else "SPY"
    if len(sys.argv) > 3:
        s = [x for x in s if x["costs"] == sys.argv[3]]
        tag += "_" + sys.argv[3]
    print(len(s), "runs", flush=True)
    run_grid(s, workers=int(sys.argv[1]) if len(sys.argv) > 1 else 2, out_csv=("raw_stage_a.csv" if tag == "SPY" else f"raw_stage_a_{tag}.csv"))
