"""Stage B: management refinement around the IS-plateau cores chosen in stage A.
tp x stop rule x exit DTE, SPY + QQQ, $100k, 10% risk, single position, default + calib costs.
Cores are read from cores_stage_a.json (written by hand after reading stage_a_summary.txt)."""
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import *  # noqa

TP = [0.25, 0.5, 0.75, None]
STOP = {"none": dict(sl_mult=None, breach=False), "sl2": dict(sl_mult=2.0, breach=False),
        "sl3": dict(sl_mult=3.0, breach=False), "breach": dict(sl_mult=None, breach=True)}
EXIT = [None, 14, 21]


def specs(cores):
    out = []
    for core in cores:
        for tp, (sn, sv), ex, sym, costs in itertools.product(TP, STOP.items(), EXIT, ("SPY", "QQQ"),
                                                             ("default",)):
            p = dict(core["params"])
            if ex is not None and p["dte_min"] <= ex:
                continue
            p.update(tp=tp, close_dte=ex, **sv)
            vid = f"B|{core['name']}|tp{tp}|{sn}|ex{ex}"
            out.append(dict(vid=vid, stage="B", params=p, sym=sym, capital=100_000, costs=costs))
    return out


if __name__ == "__main__":
    cores = json.loads((OUT / "cores_stage_a.json").read_text())
    s = specs(cores)
    print(len(s), "runs")
    run_grid(s, workers=int(sys.argv[1]) if len(sys.argv) > 1 else 2, out_csv="raw_stage_b.csv")
