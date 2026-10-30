"""Q1 validation: proxy-chain backtest vs the real-chain engine on the overlap 2024-03..2026-10.

Same rules, same $10k, same cost model. The proxy surface is the one fitted on 2024-01..2025-06
prints only (surface_<SYM>_is.json), so 2025-07..2026-10 is out-of-sample for the proxy itself.
Writes out/proxy_validation_trades.csv and out/proxy_validation.csv.
"""
import pickle

import numpy as np
import pandas as pd

import psim as P
from common import OUT, WORK

real = pickle.load(open(WORK / "repro.pkl", "rb"))
rows, trows = [], []
for surf_tag in ("_is", ""):
    for name, sym in (("dip", "SPY"), ("dip", "IWM"), ("dip", "QQQ"), ("ic", "SPY")):
        u = P.U(sym, surf_name=sym + surf_tag)
        if name == "dip":
            sl = P.DipPCS([u], kill=False)
        else:
            sl = P.Condor(u, kill=False, close_before_expiry=False)
        bk = P.Book(10_000, "2024-03-01", "2026-10-20", [sl], risk_total=10.0).run()
        eq = bk.equity_series()
        tr = bk.trades()
        rr = real[(name, sym)]
        req = rr["equity"]["equity"]
        rtr = rr["trades"]
        rtr = rtr[rtr.reason != "open_at_end"]
        for seg, a, b_ in (("full", None, None), ("IS", None, "2025-06-30"), ("OOS", "2025-07-01", None)):
            pm = P.metrics(eq[a:b_])
            rm = P.metrics(req[a:b_])
            rows.append({"surface": "IS-fit" if surf_tag else "full-fit", "sleeve": name, "sym": sym, "seg": seg,
                         "proxy_cagr": pm["cagr"], "real_cagr": rm["cagr"], "proxy_sharpe": pm["sharpe"],
                         "real_sharpe": rm["sharpe"], "proxy_dd": pm["max_dd"], "real_dd": rm["max_dd"],
                         "proxy_trades": int(((tr.open_date >= (a or "1900")) & (tr.open_date <= (b_ or "2100"))).sum()),
                         "real_trades": int(((pd.to_datetime(rtr.open_date) >= (a or "1900")) &
                                             (pd.to_datetime(rtr.open_date) <= (b_ or "2100"))).sum())})
        if surf_tag == "_is":
            rd = {pd.Timestamp(r.open_date): r for r in rtr.itertuples()}
            for t in tr.itertuples():
                r = rd.get(t.open_date)
                ncon_real = r.contracts if r is not None else np.nan
                trows.append({"sleeve": name, "sym": sym, "open_date": t.open_date.date(),
                              "proxy_credit": t.credit, "proxy_n": t.n, "proxy_pnl": t.pnl,
                              "proxy_reason": t.reason,
                              "real_entry_cash_per_ct": (r.entry_cash / ncon_real) if r is not None else np.nan,
                              "proxy_entry_cash_per_ct": t.entry_cash / t.n,
                              "real_n": ncon_real, "real_pnl": r.pnl if r is not None else np.nan,
                              "real_reason": r.reason if r is not None else "",
                              "matched": r is not None})
            missing = [d for d in rd if d not in set(tr.open_date)]
            for d in missing:
                r = rd[d]
                trows.append({"sleeve": name, "sym": sym, "open_date": d.date(), "matched": False,
                              "real_entry_cash_per_ct": r.entry_cash / r.contracts, "real_n": r.contracts,
                              "real_pnl": r.pnl, "real_reason": r.reason})
res = pd.DataFrame(rows)
res.to_csv(OUT / "proxy_validation.csv", index=False)
tv = pd.DataFrame(trows).sort_values(["sleeve", "sym", "open_date"])
tv.to_csv(OUT / "proxy_validation_trades.csv", index=False)
pd.set_option("display.width", 250)
print(res.round(3).to_string())
m = tv[tv.matched]
for (s, y), g in m.groupby(["sleeve", "sym"]):
    e = g.proxy_entry_cash_per_ct - g.real_entry_cash_per_ct
    print(s, y, "matched", len(g), "of real", int(tv[(tv.sleeve == s) & (tv.sym == y) & tv.real_n.notna()].shape[0]),
          "proxy-only", int(((tv.sleeve == s) & (tv.sym == y) & tv.real_n.isna()).sum()),
          f"credit/ct err mean {e.mean():.1f} mae {e.abs().mean():.1f} (real mean {g.real_entry_cash_per_ct.mean():.1f})",
          f"pnl/ct corr {np.corrcoef(g.proxy_pnl / g.proxy_n, g.real_pnl / g.real_n)[0, 1]:.2f}")
