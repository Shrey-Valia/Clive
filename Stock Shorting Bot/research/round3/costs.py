"""Time-of-day spread calibration from the T+1 minute bars (Abdi & Ranaldo 2017 close-high-low estimator).

For consecutive minute bars t, t+1 with eta = (ln H + ln L) / 2:
    s^2 = 4 * E[(ln C_t - eta_t) * (ln C_t - eta_{t+1})]
averaged per time-of-day bucket over all strict-gate events (negative estimates floored at 0
after averaging). The multipliers TOD = spread(bucket) / spread(15:30-15:55) scale the round-1
base cost, which round 1 applied to 15:55 fills. Writes results/costs.json and results/tod_spread.csv.
"""
import json
import os

import numpy as np
import pandas as pd

import data
from r3common import RES

BUCKETS = [(0, 1), (1, 5), (5, 15), (15, 30), (30, 60), (60, 360)]
ANCHOR = (360, 386)


def ar_by_minute(B, mask):
    H, L, C = (np.log(B[k][mask].astype(float)) for k in ("H", "L", "C"))
    eta = (H + L) / 2
    x = (C[:, :-1] - eta[:, :-1]) * (C[:, :-1] - eta[:, 1:])
    return 4 * np.nanmean(x, axis=0), np.isfinite(x).sum(axis=0)


def main():
    ev, B = data.load()
    rows = []
    out = {}
    for lab, m in (("all", ev.has_day1.values),
                   ("$10-20", ev.has_day1.values & (ev.p1545_raw.values < 20)),
                   ("$20+", ev.has_day1.values & (ev.p1545_raw.values >= 20))):
        s2, nobs = ar_by_minute(B, m)
        def sp(a, b):
            v = np.nanmean(s2[a:b])
            return float(np.sqrt(max(v, 0)))
        anchor = sp(*ANCHOR)
        for a, b in BUCKETS + [ANCHOR]:
            rows.append({"group": lab, "from_min": a, "to_min": b, "spread_bps": 1e4 * sp(a, b),
                         "mult_vs_1530_1555": sp(a, b) / anchor if anchor else np.nan})
        out[lab] = [sp(a, b) / anchor for a, b in BUCKETS]
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(RES, "tod_spread.csv"), index=False)
    print(df.round(3).to_string())
    tod = [max(1.0, round(x, 2)) for x in out["all"]]
    json.dump({"tod": tod, "auction": 1.0, "note": "multipliers on round-1 base cost; auction print = base"},
              open(os.path.join(RES, "costs.json"), "w"), indent=1)
    print("TOD", tod)


if __name__ == "__main__":
    main()
