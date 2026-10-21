"""Post-selection robustness checks on the recommended $10k / $100k books (NOT used for
selection; not counted as trials): smaller overlay risk, 1-day signal lag, half / double
slippage. Writes sensitivity.csv."""
from __future__ import annotations

from common import *  # noqa
from optionsbot.engine import Costs
from pool import DipPool
from sleeves import SLEEVES, run_sleeve

HALF = Costs(slip_abs=0.015, slip_pct=0.01, slip_cap=0.125)
DOUBLE = Costs(slip_abs=0.06, slip_pct=0.04, slip_cap=0.50)
rows = []


def rec(name, eq, note=""):
    f, i, o = M.equity_metrics(eq), M.equity_metrics(eq[:IS_END]), M.equity_metrics(eq[OOS_START:])
    rows.append({"variant": name, "full_cagr": f["cagr"], "full_sharpe": f["sharpe"], "full_max_dd": f["max_drawdown"],
                 "is_sharpe": i["sharpe"], "oos_sharpe": o["sharpe"], "oos_cagr": o["cagr"], "note": note})
    print(f"{name:40s} CAGR {f['cagr']:6.2%} Sh {f['sharpe']:4.2f} DD {f['max_drawdown']:6.2%} IS {i['sharpe']:4.2f} OOS {o['sharpe']:4.2f}")


for label, kw in [("B_core80 (selected)", {}), ("B_core80 per-trade 15%", dict(per_trade=0.15)),
                  ("B_core80 per-trade 10%", dict(per_trade=0.10)),
                  ("B_core80 lag=1 signal", dict(params={"lag": 1})),
                  ("B_core80 half slippage", dict(costs=HALF)), ("B_core80 double slippage", dict(costs=DOUBLE))]:
    base = dict(cap=0.25, core=0.8)
    base.update({k: v for k, v in kw.items()})
    if "per_trade" in kw:
        base["cap"] = kw["per_trade"]
    rec(label, DipPool(10_000, **base).run().equity_frame()["equity"])
for label, kw in [("A_cap25_cash (alt)", {}), ("A_cap25_cash lag=1", dict(params={"lag": 1})),
                  ("A_cap25_cash double slippage", dict(costs=DOUBLE)), ("A_cap25_cash per-trade 10%", dict(per_trade=0.10, cap=0.10))]:
    base = dict(cap=0.25, idle="cash")
    base.update(kw)
    rec(label, DipPool(10_000, **base).run().equity_frame()["equity"])

# $100k primary with the dip sleeve lagged / double-slipped (other sleeves unchanged)
W = {"collar_SPY": 60_000, "dip_pool25": 12_689, "ic_SPY": 17_855, "bh_SPY": 9_456}
SLEEVES["dip_pool25"] = ("pool", dict(cap=0.25, per_trade=0.25, idle="cash"))
parts = {s: run_sleeve(s, d)["equity"] for s, d in W.items()}
rec("100k L_collar60 (selected)", pd.DataFrame(parts).ffill().sum(axis=1))
p2 = dict(parts)
p2["dip_pool25"] = DipPool(12_689, cap=0.25, params={"lag": 1}).run().equity_frame()["equity"]
rec("100k L_collar60, dip sleeve lag=1", pd.DataFrame(p2).ffill().sum(axis=1))
p3 = dict(parts)
p3["dip_pool25"] = DipPool(12_689, cap=0.25, costs=DOUBLE).run().equity_frame()["equity"]
rec("100k L_collar60, dip sleeve double slip", pd.DataFrame(p3).ffill().sum(axis=1))
pd.DataFrame(rows).to_csv(HERE / "sensitivity.csv", index=False)
