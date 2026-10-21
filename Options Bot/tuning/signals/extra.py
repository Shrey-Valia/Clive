"""Extra robustness for the finalists: wider placebo sets, random RS routing,
single-$10k-account concurrency, signal-vs-control trade tests.
Writes cache/extra.json. Run after analyze.py."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats

import common as C
from common import CAPITAL, IS_END, MEGA, OOS_START, em, evaluate, run_cached, trade_returns, tstat


def main():
    from run_grid import run_tasks
    A = json.load(open(C.HERE / "cache" / "analysis.json"))
    R = pd.read_csv(C.HERE / "results.csv")
    out = {}

    # ---------------- S1: 20-seed random placebo (same trend gate, same structure/exits)
    s1 = A["selected"]["S1_dip_pcs"]
    p1 = s1["params"]
    rate_row = R[(R.family == "S1_dip_pcs") & R.variant.str.endswith("ctrl_random_s0")].iloc[0]
    rate = json.loads(rate_row["params"])["rand_p"]
    seeds = range(20)
    run_tasks([("dip_pcs", dict(p1, signal="random", rand_p=rate, rand_seed=s), x, "default")
               for s in seeds for x in s1["syms"]])
    pl = []
    for s in seeds:
        row, *_ = evaluate("dip_pcs", dict(p1, signal="random", rand_p=rate, rand_seed=s), s1["syms"])
        pl.append({k: row[k] for k in ("is_sharpe", "oos_sharpe", "full_sharpe", "is_tr_mean",
                                       "oos_tr_mean", "is_ntr", "oos_ntr")})
    pl = pd.DataFrame(pl)
    sel = R[(R.family == "S1_dip_pcs") & R.selected].iloc[0]
    out["S1_placebo"] = {
        "rand_p": rate, "n_seeds": len(pl),
        "placebo_mean": pl.mean().to_dict(), "placebo_p90": pl.quantile(0.9).to_dict(),
        "signal": {k: float(sel[k]) for k in pl.columns},
        "pct_placebo_below_signal": {k: float((pl[k] < sel[k]).mean()) for k in pl.columns},
    }

    # ---------------- S1: signal vs unconditional per-trade (Welch) and pooled full-period stats
    _, _, _, tr_sig = evaluate("dip_pcs", p1, s1["syms"])
    _, _, _, tr_ctl = evaluate("dip_pcs", dict(p1, signal="always"), s1["syms"])
    o = {}
    for tag, (a, z) in {"is": ("1900", IS_END), "oos": (OOS_START, "2100"), "full": ("1900", "2100")}.items():
        x = tr_sig[(tr_sig.open_date >= a) & (tr_sig.open_date <= z)]["ret"]
        y = tr_ctl[(tr_ctl.open_date >= a) & (tr_ctl.open_date <= z)]["ret"]
        w = stats.ttest_ind(x, y, equal_var=False)
        o[tag] = {"sig_n": len(x), "sig_mean": float(x.mean()), "sig_t": tstat(x),
                  "ctl_n": len(y), "ctl_mean": float(y.mean()), "welch_t": float(w.statistic),
                  "welch_p": float(w.pvalue),
                  "sig_ci": C.boot_ci(tr_sig[(tr_sig.open_date >= a) & (tr_sig.open_date <= z)])}
    out["S1_vs_uncond"] = o

    # ---------------- S1: one $10k account trading all 9 names (scaled)
    res = {x: run_cached("dip_pcs", p1, x) for x in s1["syms"]}
    reserved = pd.DataFrame({x: r["equity"]["reserved"] for x, r in res.items()}).fillna(0)
    pnl = pd.DataFrame({x: r["equity"]["equity"] - CAPITAL for x, r in res.items()}).ffill().fillna(0)
    conc = reserved.sum(axis=1)
    npos = pd.DataFrame({x: r["equity"]["n_positions"] for x, r in res.items()}).fillna(0).sum(axis=1)
    peak = float(conc.max())
    scale = CAPITAL / peak            # every sleeve scaled so that peak collateral = $10k
    shared = CAPITAL + pnl.sum(axis=1) * scale
    out["S1_shared_account"] = {
        "peak_concurrent_collateral_9x10k": peak, "scale": scale,
        "max_concurrent_positions": int(npos.max()), "pct_days_any_position": float((npos > 0).mean()),
        "full": em(shared), "is": em(shared, None, IS_END), "oos": em(shared, OOS_START, None),
        "note": "sum of 9 per-name P&L streams, each sized for $10k, scaled by 10k/peak collateral; "
                "fractional contracts implied (granularity ignored)."}
    shared.to_pickle(C.HERE / "cache" / "s1_shared.pkl")

    # ---------------- S6: random routing placebo (k random names per month)
    out["S6_random_routing"] = s6_random(A)
    json.dump(out, open(C.HERE / "cache" / "extra.json", "w"), indent=1, default=str)
    print(json.dumps(out, indent=1, default=str)[:6000])


def s6_random(A, n_seeds=20):
    s6 = A["selected"]["S6_rs_routing"]
    k = int(s6["k"])
    sp = {k_: v for k_, v in s6["params"].items() if k_ != "rs_months"}   # frozen S6 structure
    months = [str(m) for m in pd.period_range("2024-03", "2026-10", freq="M")]
    cap = round(CAPITAL / k)
    rows = []
    for seed in range(n_seeds):
        rng = np.random.default_rng(1000 + seed)
        act = {s: set() for s in MEGA}
        for m in months:
            for s in rng.choice(MEGA, size=k, replace=False):
                act[s].add(m)
        from run_grid import run_tasks
        run_tasks([("sig_spread", dict(sp, rs_months=frozenset(act[s])), s, "default", cap)
                   for s in MEGA])
        res = {s: run_cached("sig_spread", dict(sp, rs_months=frozenset(act[s])), s, capital=cap)
               for s in MEGA}
        rets = pd.DataFrame({s: r["equity"]["equity"].pct_change() for s, r in res.items()}).fillna(0)
        prevm = pd.Series(rets.index, index=rets.index).shift(1).dt.strftime("%Y-%m")
        w = pd.DataFrame({s: prevm.isin(act[s]).astype(float) for s in MEGA}, index=rets.index)
        w = w.div(w.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
        pr = (rets * w).sum(axis=1)
        pr.iloc[0] = 0
        eq = CAPITAL * (1 + pr).cumprod()
        tr = pd.concat([trade_returns(r["trades"]) for r in res.values()], ignore_index=True)
        rows.append({"seed": seed, "is_sharpe": em(eq, None, IS_END).get("sharpe"),
                     "oos_sharpe": em(eq, OOS_START, None).get("sharpe"),
                     "full_sharpe": em(eq).get("sharpe"), "full_cagr": em(eq).get("cagr"),
                     "ntr": len(tr), "tr_mean": float(tr["ret"].mean()) if len(tr) else np.nan})
    df = pd.DataFrame(rows)
    R = pd.read_csv(C.HERE / "results.csv")
    sel = R[(R.family == "S6_rs_routing") & R.selected].iloc[0]
    return {"n_seeds": n_seeds, "mean": df.mean(numeric_only=True).to_dict(),
            "p90": df.quantile(0.9, numeric_only=True).to_dict(),
            "pct_below_signal": {c: float((df[c] < sel[c]).mean())
                                 for c in ("is_sharpe", "oos_sharpe", "full_sharpe")},
            "rows": df.to_dict("records")}


if __name__ == "__main__":
    main()
