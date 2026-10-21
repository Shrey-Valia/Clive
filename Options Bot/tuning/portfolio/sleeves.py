"""Task 1: reproduce every finalist sleeve from code on the common daily calendar
(2024-03-01 -> 2026-10-20) at $10k and $100k, with and without T-bill interest.
Writes cache/sleeves.pkl and out/sleeve_validation.csv (vs the finalists.json numbers).
"""
from __future__ import annotations

import json
import pickle
import time

from common import *  # noqa
from optionsbot import runner
from optionsbot.strategies import BuyHold
from optionsbot.strategies_portfolio import PooledDipPCS
from optionsbot.strategies_spreads import SpreadBook
from optionsbot.strategies_wheel import WHEEL_REGISTRY
from pool import DipPool

TUN = HERE.parent
WHEEL = {f["id"]: f for f in json.loads((TUN / "wheel/finalist_choice.json").read_text())["finalists"]}
FROZEN_SP = {f["id"]: f for f in json.loads((TUN / "spreads/frozen.json").read_text())}
IC = FROZEN_SP["F1_ic10_w3_60d_hold"]["params"]

SLEEVES = {
    # id: (kind, spec)
    "bh_SPY": ("eng", (BuyHold, None, "SPY")),
    "bh_QQQ": ("eng", (BuyHold, None, "QQQ")),
    "bh_IWM": ("eng", (BuyHold, None, "IWM")),
    "dip_SPY": ("eng", (PooledDipPCS, None, "SPY")),
    "dip_QQQ": ("eng", (PooledDipPCS, None, "QQQ")),
    "dip_IWM": ("eng", (PooledDipPCS, None, "IWM")),
    "dip_pool50": ("pool", dict(cap=0.5, per_trade=0.25, idle="cash")),
    "wheel_QQQ": ("eng", (WHEEL_REGISTRY["wheel_x"], WHEEL["F1_wheel_qqq"]["params"], "QQQ")),
    "cc_QQQ": ("eng", (WHEEL_REGISTRY["wheel_x"], WHEEL["F2_cc_qqq"]["params"], "QQQ")),
    "collar_SPY": ("eng", (WHEEL_REGISTRY["collar_x"], WHEEL["F3_collar_spy"]["params"], "SPY")),
    "ic_SPY": ("eng", (SpreadBook, IC, "SPY")),
    "ic_PORT": ("port", IC),
}


def run_sleeve(sid, capital, cash_yield=False):
    kind, spec = SLEEVES[sid]
    if kind == "eng":
        cls, params, sym = spec
        r = runner.run_one(cls, params, sym, START, END, capital, cash_yield=cash_yield)
        ef = r["equity"]
        usage = ef["capital_at_risk"] / ef["equity"]
        return {"equity": ef["equity"], "usage": usage, "trades": r["trades"],
                "infeasible": bool(r["infeasible"]), "blocked_days": int(r["engine"].blocked_days),
                "notes": r["notes"][:6]}
    if kind == "pool":
        p = DipPool(capital, cash_yield=cash_yield, **spec).run()
        f = p.equity_frame()
        return {"equity": f["equity"], "usage": p.usage(), "trades": p.trades_frame(),
                "infeasible": False, "blocked_days": 0, "notes": []}
    if kind == "port":
        eqs, us, trs, inf, bl = [], [], [], [], 0
        for s in ("SPY", "QQQ", "IWM"):
            r = runner.run_one(SpreadBook, spec, s, START, END, capital / 3, cash_yield=cash_yield)
            ef = r["equity"]
            eqs.append(ef["equity"])
            us.append(ef["capital_at_risk"])
            if len(r["trades"]):
                trs.append(r["trades"])
            inf.append(r["infeasible"])
            bl += r["engine"].blocked_days
        eq = pd.concat(eqs, axis=1).ffill().sum(axis=1)
        usage = pd.concat(us, axis=1).ffill().sum(axis=1) / eq
        return {"equity": eq, "usage": usage, "trades": pd.concat(trs) if trs else pd.DataFrame(),
                "infeasible": all(inf), "blocked_days": bl, "notes": []}
    raise ValueError(kind)


def main():
    t0 = time.time()
    out = {}
    for cap in (10_000, 100_000):
        for cy in (False, True):
            for sid in SLEEVES:
                k = (sid, cap, cy)
                out[k] = run_sleeve(sid, cap, cy)
                eq = out[k]["equity"]
                m = M.equity_metrics(eq)
                print(f"{sid:12s} {cap:>7} ty={int(cy)} CAGR {m['cagr']:7.2%} Sh {m['sharpe']:5.2f} "
                      f"DD {m['max_drawdown']:7.2%} inf={out[k]['infeasible']} blk={out[k]['blocked_days']} "
                      f"[{time.time() - t0:.0f}s req={runner.client().n_requests}]", flush=True)
    (CACHE / "sleeves.pkl").write_bytes(pickle.dumps(out))

    # validation vs saved finalists
    ref = []
    sig = {x["id"]: x for x in json.loads((TUN / "signals/finalists.json").read_text())}
    wh = {x["id"]: x for x in json.loads((TUN / "wheel/finalists.json").read_text())["finalists"]}
    spf = {x["id"]: x for x in json.loads((TUN / "spreads/finalists.json").read_text())}
    pairs = [("dip_SPY", 10_000, sig["sig_dip_pcs_spy"]["metrics"]),
             ("dip_QQQ", 10_000, sig["sig_dip_pcs_qqq"]["metrics"]),
             ("wheel_QQQ", 100_000, wh["F1_wheel_qqq"]["metrics"]),
             ("cc_QQQ", 100_000, wh["F2_cc_qqq"]["metrics"]),
             ("collar_SPY", 100_000, wh["F3_collar_spy"]["metrics"]),
             ("ic_SPY", 10_000, spf["F1_ic10_w3_60d_hold:SPY:10000"]["metrics"]),
             ("ic_SPY", 100_000, spf["F1_ic10_w3_60d_hold:SPY:100000"]["metrics"]),
             ("ic_PORT", 100_000, spf["F2_ic10_w3_60d_hold_port:PORT:100000"]["metrics"])]
    for sid, cap, m0 in pairs:
        m = M.equity_metrics(out[(sid, cap, False)]["equity"])
        ref.append({"sleeve": sid, "capital": cap, "cagr": m["cagr"], "cagr_saved": m0["cagr"],
                    "sharpe": m["sharpe"], "sharpe_saved": m0["sharpe"],
                    "max_dd": m["max_drawdown"], "max_dd_saved": m0["max_drawdown"]})
    v = pd.DataFrame(ref)
    v.to_csv(OUT / "sleeve_validation.csv", index=False)
    print(v.round(4).to_string())


if __name__ == "__main__":
    main()
