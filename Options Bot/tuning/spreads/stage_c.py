"""Stage C: one-lever-at-a-time around the stage-B cores (cores_stage_b.json):
sizing, laddered cadence, kill switch, entry filters, condor skew, RSI2>90 call-spread overlay,
underlying (SPY/QQQ/IWM/PORT) and capital ($10k/$100k). Default costs (calib for a subset)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import *  # noqa


def levers(core):
    p0 = core["params"]
    r = p0["risk_frac"]
    L = {"base": {}}
    for rf in (0.05, 0.10, 0.15, 0.20, 0.25):
        if rf != r:
            L[f"risk{rf}"] = {"risk_frac": rf}
    for mp, gap in ((2, 14), (3, 7), (4, 7), (6, 5)):
        L[f"ladder{mp}x{gap}d"] = {"max_positions": mp, "entry_gap": gap, "risk_frac": round(r / mp, 4)}
    L["kill"] = {"kill_switch": True}
    L["f_sma200"] = {"filter": "sma200"}
    L["f_vix30"] = {"filter": "vix"}
    L["f_sma200_vix30"] = {"filter": "sma200_vix"}
    if p0["structure"] == "ic":
        d = p0["put_delta"]
        for pd_, cd in ((d + 0.05, d - 0.05), (d + 0.05, d), (d, d - 0.05), (d - 0.05, d + 0.05)):
            if 0.05 <= cd <= 0.35 and 0.05 <= pd_ <= 0.35:
                L[f"skew_p{pd_:.2f}_c{cd:.2f}"] = {"put_delta": round(pd_, 2), "call_delta": round(cd, 2)}
    else:
        L["rsi2_ccs"] = {"rsi2_ccs": True}
    return L


def specs(cores):
    out = []
    for core in cores:
        for ln, lv in levers(core).items():
            p = {**core["params"], **lv}
            for sym in ("SPY", "QQQ", "IWM", "PORT"):
                for cap in (10_000, 100_000):
                    vid = f"C|{core['name']}|{ln}"
                    out.append(dict(vid=vid, stage="C", params=p, sym=sym, capital=cap, costs="default"))
                    if cap == 100_000 and sym in ("SPY", "QQQ"):
                        out.append(dict(vid=vid, stage="C", params=p, sym=sym, capital=cap, costs="calib"))
    return out


if __name__ == "__main__":
    cores = json.loads((OUT / "cores_stage_b.json").read_text())
    s = specs(cores)
    print(len(s), "runs")
    run_grid(s, workers=int(sys.argv[1]) if len(sys.argv) > 1 else 2, out_csv="raw_stage_c.csv")
