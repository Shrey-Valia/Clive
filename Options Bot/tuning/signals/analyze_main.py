"""Selection on IS, frozen OOS evaluation, controls, costs, lag, RS routing, outputs.

python3 tuning/signals/analyze.py      (after run_grid.py; reruns are cached)
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

import common as C
from common import (CAPITAL, IS_END, MEGA, OOS_START, UNIV, basket, boot_ci, deflated_sharpe,
                    em, evaluate, run_cached, trade_returns, tstat)
from run_grid import run_tasks
from variants import GRID, PMCC_ALLOC, PMCC_UNIV, RS_STRUCT, baselines

MIN_IS_TRADES = {"S4_vol_spike": 5}
ROWS = []          # every evaluated configuration (trials + controls + robustness)


def add(row, role):
    row["role"] = role
    ROWS.append(row)
    return row


def ensure(specs, costs=("default",)):
    run_tasks([(sid, p, s, c) for (sid, p, syms) in specs for s in syms for c in costs])


# ----------------------------------------------------------------- 1. grid
ensure([(sid, p, syms) for (_, _, sid, p, syms) in GRID + baselines()])
grid_rows = []
for fam, lab, sid, p, syms in GRID:
    row, *_ = evaluate(sid, p, syms, label=lab, family=fam)
    grid_rows.append(add(row, "trial"))
for fam, lab, sid, p, syms in baselines():
    row, *_ = evaluate(sid, p, syms, label=lab, family=fam)
    add(row, "baseline")
G = pd.DataFrame(grid_rows)

# ------------------------------------------------------------- 2. selection
selected = {}
for fam, g in G.groupby("family"):
    ok = g[g["is_ntr"] >= MIN_IS_TRADES.get(fam, 15)]
    best = ok.sort_values("is_sharpe", ascending=False).iloc[0]
    spec = next(x for x in GRID if x[1] == best["variant"])
    selected[fam] = {"label": best["variant"], "sid": spec[2], "params": spec[3], "syms": spec[4],
                     "is_sharpe_var": float(np.nanvar(g["is_sharpe"])), "n_family": len(g)}
    print(f"{fam}: picked {best['variant']}  IS Sh {best['is_sharpe']:.2f}  OOS Sh {best['oos_sharpe']:.2f}")

# ---------------------------------------------------------------- 3. S6 RS
def rs_months(k, look):
    """Top-k of MEGA by (close[t-21]/close[t-look]-1) at each prior month-end."""
    closes = pd.DataFrame({s: C.market(s).stock["split_close"] for s in MEGA}).dropna()
    mom = closes.shift(21) / closes.shift(look) - 1
    me = mom.groupby(mom.index.to_period("M")).tail(1)
    act = {s: set() for s in MEGA}
    for d, r in me.iterrows():
        nxt = (d.to_period("M") + 1).strftime("%Y-%m")
        if r.isna().any():
            continue
        for s in r.sort_values(ascending=False).index[:k]:
            act[s].add(nxt)
    return act


def rs_portfolio(struct_p, k, look, sid="sig_spread", cost="default", gated=True, cap_k=None):
    act = rs_months(k, look)
    cap = round(CAPITAL / (cap_k or k))
    specs = []
    for s in MEGA:
        p = dict(struct_p, rs_months=frozenset(act[s])) if gated else dict(struct_p)
        specs.append((s, p))
    res = {s: run_cached(sid, p, s, cost, capital=cap) for s, p in specs}
    rets = pd.DataFrame({s: r["equity"]["equity"].pct_change() for s, r in res.items()}).fillna(0.0)
    if gated:
        prevm = pd.Series(rets.index, index=rets.index).shift(1).dt.strftime("%Y-%m")
        w = pd.DataFrame({s: prevm.isin(act[s]).astype(float) for s in MEGA}, index=rets.index)
        w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
        pr = (rets * w).sum(axis=1)
    else:
        pr = rets.mean(axis=1)
    pr.iloc[0] = 0.0
    eq = CAPITAL * (1 + pr).cumprod()
    tr = pd.concat([trade_returns(r["trades"]) for r in res.values()], ignore_index=True)
    return eq, tr, res, act


def rs_row(eq, tr, label, fam, params, role):
    row = {"family": fam, "variant": label, "strategy": "sig_spread+rs", "cost": "default",
           "underlyings": ",".join(MEGA), "params": json.dumps({k: v for k, v in params.items()
                                                                 if k != "rs_months"}, default=str)}
    for tag, (a, z) in {"full": (None, None), "is": (None, IS_END), "oos": (OOS_START, None)}.items():
        m = em(eq, a, z)
        row.update({f"{tag}_cagr": m.get("cagr"), f"{tag}_sharpe": m.get("sharpe"),
                    f"{tag}_maxdd": m.get("max_drawdown"), f"{tag}_ret": m.get("total_return"),
                    f"{tag}_vol": m.get("ann_vol")})
    for tag, t in {"is": tr[tr.open_date <= IS_END], "oos": tr[tr.open_date >= OOS_START],
                   "full": tr}.items():
        row[f"{tag}_ntr"] = len(t)
        row[f"{tag}_tr_mean"] = float(t["ret"].mean()) if len(t) else np.nan
        row[f"{tag}_tr_t"] = tstat(t["ret"]) if len(t) else np.nan
        row[f"{tag}_win"] = float((t["pnl"] > 0).mean()) if len(t) else np.nan
    lo, hi = boot_ci(tr[tr.open_date >= OOS_START])
    row["oos_tr_ci_lo"], row["oos_tr_ci_hi"] = lo, hi
    return add(row, role)


s1 = selected["S1_dip_pcs"]
rs_structs = dict(RS_STRUCT)
rs_structs["dipS1"] = dict(s1["params"])
rs_rows, rs_store = [], {}
for name, sp in rs_structs.items():
    for k in (2, 3):
        for look in (252, 126):
            lab = f"S6 RS top{k} mom{'12' if look == 252 else '6'}-1 {name}"
            eq, tr, res, act = rs_portfolio(sp, k, look)
            rs_rows.append(rs_row(eq, tr, lab, "S6_rs_routing", sp, "trial"))
            rs_store[lab] = (eq, tr, sp, k, look)
R6 = pd.DataFrame(rs_rows)
best6 = R6.sort_values("is_sharpe", ascending=False).iloc[0]
eq6, tr6, sp6, k6, look6 = rs_store[best6["variant"]]
selected["S6_rs_routing"] = {"label": best6["variant"], "sid": "sig_spread", "params": sp6,
                             "syms": MEGA, "is_sharpe_var": float(np.nanvar(R6["is_sharpe"])),
                             "n_family": len(R6), "k": k6, "look": look6}
print(f"S6: picked {best6['variant']} IS Sh {best6['is_sharpe']:.2f} OOS Sh {best6['oos_sharpe']:.2f}")
# S6 controls: same structure ungated on all 6; B&H top-k momentum; B&H EW-6
for name, sp in rs_structs.items():
    eq, tr, _, _ = rs_portfolio(sp, 6, 252, gated=False, cap_k=k6)
    rs_row(eq, tr, f"S6 control ungated EW6 {name}", "S6_rs_routing", sp, "control")
    rs_store[f"ctrl {name}"] = (eq, tr, sp, 6, 252)
eqbh, _, _, _ = rs_portfolio({}, k6, look6, sid="buy_hold")
rs_row(eqbh, pd.DataFrame(columns=["open_date", "ret", "pnl"]), f"S6 B&H top{k6} momentum",
       "S6_rs_routing", {}, "benchmark")
eqbh6, _, _, _ = rs_portfolio({}, 6, 252, sid="buy_hold", gated=False, cap_k=k6)
rs_row(eqbh6, pd.DataFrame(columns=["open_date", "ret", "pnl"]), "S6 B&H EW6", "S6_rs_routing", {},
       "benchmark")
rs_store["bh_topk"], rs_store["bh_ew6"] = eqbh, eqbh6

N_TRIALS = len(GRID) + len(R6)
print("trials:", N_TRIALS)

# --------------------------------------------- 4. controls / costs / lag
CTRL = {}
for fam, s in selected.items():
    if fam in ("S5_pmcc_signal", "S6_rs_routing"):
        continue
    p = s["params"]
    ctl = {"ctrl_always_sametrend": dict(p, signal="always"),
           "ctrl_always_notrend": dict(p, signal="always", trend=None)}
    # random placebo, entry rate matched to the selected signal
    rows = [run_cached(s["sid"], p, x) for x in s["syms"]]
    flat = sum(int((r["equity"]["n_positions"] == 0).sum()) for r in rows)
    ntr = sum(len(r["trades"]) for r in rows)
    rate = min(0.9, ntr / max(flat, 1))
    for seed in range(5):
        ctl[f"ctrl_random_s{seed}"] = dict(p, signal="random", rand_p=rate, rand_seed=seed)
    ctl["lag1"] = dict(p, lag=1)
    ensure([(s["sid"], q, s["syms"]) for q in ctl.values()])
    ensure([(s["sid"], p, s["syms"])], costs=("half", "double"))
    CTRL[fam] = {}
    for nm, q in ctl.items():
        row, *_ = evaluate(s["sid"], q, s["syms"], label=f"{s['label']} | {nm}", family=fam)
        CTRL[fam][nm] = add(row, "control" if nm.startswith("ctrl") else "robustness")
    for c in ("half", "double"):
        row, *_ = evaluate(s["sid"], p, s["syms"], cost=c, label=f"{s['label']} | cost={c}", family=fam)
        CTRL[fam][f"cost_{c}"] = add(row, "robustness")
# S5 costs
s5 = selected["S5_pmcc_signal"]
ensure([(s5["sid"], s5["params"], s5["syms"])], costs=("half", "double"))
CTRL["S5_pmcc_signal"] = {}
for c in ("half", "double"):
    row, *_ = evaluate(s5["sid"], s5["params"], s5["syms"], cost=c, label=f"{s5['label']} | cost={c}",
                       family="S5_pmcc_signal")
    CTRL["S5_pmcc_signal"][f"cost_{c}"] = add(row, "robustness")
# S6 costs
for c in ("half", "double"):
    eq, tr, _, _ = rs_portfolio(sp6, k6, look6, cost=c)
    r = rs_row(eq, tr, f"{best6['variant']} | cost={c}", "S6_rs_routing", sp6, "robustness")
    CTRL.setdefault("S6_rs_routing", {})[f"cost_{c}"] = r

# -------------------------------------------------- 5. per-variant stats
res_df = pd.DataFrame(ROWS)
for fam, s in selected.items():
    fam_rows = res_df[(res_df.family == fam) & (res_df.role == "trial")]
    sel = fam_rows[fam_rows.variant == s["label"]].iloc[0]
    n_is = 330  # ~IS trading days
    dsr_fam, sr0_fam = deflated_sharpe(sel["is_sharpe"], n_is, s["n_family"], s["is_sharpe_var"])
    dsr_all, sr0_all = deflated_sharpe(sel["is_sharpe"], n_is, N_TRIALS,
                                       float(np.nanvar(res_df[res_df.role == "trial"]["is_sharpe"])))
    psr_oos, _ = deflated_sharpe(sel["oos_sharpe"], 330, 1, 0.0)
    s.update(dsr_is_family=dsr_fam, sr0_family=sr0_fam, dsr_is_all=dsr_all, sr0_all=sr0_all,
             psr_oos=psr_oos, row=sel.to_dict())

res_df["selected"] = res_df.apply(lambda r: r["role"] == "trial" and
                                  selected.get(r["family"], {}).get("label") == r["variant"], axis=1)
res_df.to_csv(C.HERE / "results.csv", index=False)

# buy & hold references
bh = {s: C.bh_equity(s) for s in UNIV}
bh_rows = {}
for nm, syms in {"BH_9": UNIV, "BH_ETF3": C.ETFS, "BH_PMCC5": PMCC_UNIV, "BH_MEGA6": MEGA}.items():
    b = basket({s: bh[s] for s in syms})
    bh_rows[nm] = {t: em(b, a, z) for t, (a, z) in
                   {"full": (None, None), "is": (None, IS_END), "oos": (OOS_START, None)}.items()}

json.dump({"selected": {k: {kk: vv for kk, vv in v.items() if kk != "params"} | {
               "params": {pk: (f"<{len(pv)} months>" if isinstance(pv, (set, frozenset)) else pv)
                          for pk, pv in v["params"].items()}} for k, v in selected.items()},
           "controls": {f: {k: {kk: vv for kk, vv in r.items()} for k, r in d.items()}
                        for f, d in CTRL.items()},
           "bh": bh_rows, "n_trials": N_TRIALS},
          open(C.HERE / "cache" / "analysis.json", "w"), default=str, indent=1)
print("done; rows:", len(res_df))

# ------------------------------------------- 6. series for charts / json
import pickle  # noqa: E402

SER = {}
for fam, s in selected.items():
    if fam == "S6_rs_routing":
        ctrl_name = "ctrl " + best6["variant"].split()[-1]
        SER[fam] = {"eq": eq6, "tr": tr6, "ctrl": rs_store[ctrl_name][0],
                    "bh": rs_store["bh_topk"], "bh_ew": rs_store["bh_ew6"], "syms": MEGA,
                    "act": rs_months(k6, look6)}
        continue
    _, res, b, tr = evaluate(s["sid"], s["params"], s["syms"])
    d = {"eq": b, "tr": tr, "syms": s["syms"],
         "per": {x: r["equity"]["equity"] for x, r in res.items()},
         "trades": {x: r["trades"] for x, r in res.items()},
         "bh": basket({x: bh[x] for x in s["syms"]})}
    if fam == "S5_pmcc_signal":
        _, _, d["ctrl"], d["ctrl_tr"] = evaluate("pmcc_sma50", dict(alloc=PMCC_ALLOC), PMCC_UNIV)
        _, _, d["ctrl2"], _ = evaluate("pmcc", dict(alloc=PMCC_ALLOC), PMCC_UNIV)
    else:
        q = dict(s["params"], signal="always")
        _, _, d["ctrl"], d["ctrl_tr"] = evaluate(s["sid"], q, s["syms"])
    SER[fam] = d
pickle.dump(SER, open(C.HERE / "cache" / "series.pkl", "wb"))
print("series saved")
