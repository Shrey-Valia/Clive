"""Leg-level proxy check: price every leg the real-chain engine actually traded (same strike,
same expiry, same entry day) with the IS-fitted proxy surface; compare with the engine's mark.

Engine marks are recovered from fills: short fill = mark - slip, long fill = mark + slip,
slip = clip(2% x mark, 0.03, 0.25)."""
import pickle
import re

import numpy as np
import pandas as pd

import psim as P
from common import OUT, WORK
from optionsbot.data import parse_occ


def mark_from_fill(fill, qty):
    if qty < 0:      # fill = mark - slip
        m = fill + 0.03
        if m * 0.02 > 0.03:
            m = fill / 0.98
        if m * 0.02 > 0.25:
            m = fill + 0.25
    else:
        m = fill - 0.03
        if m * 0.02 > 0.03:
            m = fill / 1.02
        if m * 0.02 > 0.25:
            m = fill - 0.25
    return max(m, 0.01)


real = pickle.load(open(WORK / "repro.pkl", "rb"))
rows = []
for (name, sym), rr in real.items():
    u = P.U(sym, surf_name=sym + "_is")
    for t in rr["trades"].itertuples():
        d = pd.Timestamp(t.open_date)
        cr_real = cr_proxy = 0.0
        for leg in t.legs.split(";"):
            q, rest = leg.split(" ", 1)
            occ, fill = rest.split("@")
            root, exp, cp, K = parse_occ(occ)
            qty = int(q)
            sgn = 1 if qty > 0 else -1
            mk = mark_from_fill(float(fill), sgn)
            pr, _ = P.price_strike(u, d, pd.Timestamp(exp), cp, K)
            cr_real -= sgn * mk
            cr_proxy -= sgn * pr
            rows.append({"sleeve": name, "sym": sym, "date": d.date(), "leg": occ, "side": sgn, "real_mark": mk,
                         "proxy": pr, "err": pr - mk, "seg": "IS" if d <= pd.Timestamp("2025-06-30") else "OOS"})
        rows.append({"sleeve": name, "sym": sym, "date": d.date(), "leg": "NET CREDIT", "side": 0,
                     "real_mark": cr_real, "proxy": cr_proxy, "err": cr_proxy - cr_real,
                     "seg": "IS" if d <= pd.Timestamp("2025-06-30") else "OOS"})
df = pd.DataFrame(rows)
df.to_csv(OUT / "proxy_validation_legs.csv", index=False)
net = df[df.leg == "NET CREDIT"]
g = net.groupby(["sleeve", "sym", "seg"]).agg(n=("err", "size"), real_credit=("real_mark", "mean"),
                                               proxy_credit=("proxy", "mean"), bias=("err", "mean"),
                                               mae=("err", lambda x: x.abs().mean()))
g["bias_pct"] = g.bias / g.real_credit
g["mae_pct"] = g.mae / g.real_credit
print(g.round(3).to_string())
lg = df[df.leg != "NET CREDIT"].copy()
lg["rel"] = lg.err / lg.real_mark
print(lg.groupby(["sleeve", "sym", "side"]).agg(n=("err", "size"), mark=("real_mark", "median"),
                                                 bias=("err", "mean"), med_rel=("rel", "median")).round(3))
