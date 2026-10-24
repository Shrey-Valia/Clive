"""What deltas did the real-chain engine actually trade? For every leg of the reproduced dip and
F1 trades: the leg's delta from its own same-day print (Black-Scholes on the print), and the
delta of the same strike from the proxy surface. Exposes the engine's call-strike selection."""
import pickle

import numpy as np
import pandas as pd

import psim as P
from common import OUT, WORK
from optionsbot.data import parse_occ

real = pickle.load(open(WORK / "repro.pkl", "rb"))
iv = {s: pd.read_pickle(WORK / f"iv_{s}.pkl") for s in ("SPY", "IWM", "QQQ")}
rows = []
for (name, sym), rr in real.items():
    u = P.U(sym, surf_name=sym)
    ivs = iv[sym].set_index(["date", "expiry", "strike", "cp"])
    for t in rr["trades"].itertuples():
        d = pd.Timestamp(t.open_date)
        for leg in t.legs.split(";"):
            q, rest = leg.split(" ", 1)
            occ, fill = rest.split("@")
            _, exp, cp, K = parse_occ(occ)
            key = (d, pd.Timestamp(exp), K, cp)
            dl_print = float(np.atleast_1d(ivs.loc[key, "delta"])[0]) if key in ivs.index else np.nan
            _, dl_proxy = P.price_strike(u, d, pd.Timestamp(exp), cp, K)
            rows.append({"sleeve": name, "sym": sym, "date": d.date(), "cp": cp, "side": "short" if int(q) < 0 else "long",
                         "strike": K, "spot": u.spot(d), "otm_pct": (K / u.spot(d) - 1) * 100,
                         "delta_print": abs(dl_print), "delta_proxy": abs(dl_proxy)})
df = pd.DataFrame(rows)
df.to_csv(OUT / "engine_traded_deltas.csv", index=False)
print(df.groupby(["sleeve", "sym", "cp", "side"])[["otm_pct", "delta_print", "delta_proxy"]].median().round(3))
print(df[(df.sleeve == "ic") & (df.side == "short")].round(3).to_string())
