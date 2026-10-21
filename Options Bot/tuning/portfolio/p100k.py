"""Task 3b: $100k blends. Weights are estimated on IS (2024-03 -> 2025-06) daily returns of
each sleeve run alone at $100k, then frozen.

Sleeve universe (6): wheel_QQQ, cc_QQQ, collar_SPY, dip_pool25 (dip-PCS SPY/QQQ/IWM, 25% shared
cap, idle cash: the IS-best cash variant of the $10k grid), ic_SPY (spreads F1), bh_SPY.

Blends (7 counted trials):
  I1 equal weight, I2 inverse vol, I3 equal risk contribution, I4 min IS max-DD s.t. IS CAGR>=10%
      -> "idealized": monthly rebalanced return blend (assumes fractional sleeves)
      -> "engine":    each sleeve run as its own sub-account at w x $100k (whole lots, no rebalance)
  L1..L3 lot-feasible: one stock sleeve (wheel / CC / collar) at 60% + ERC weights on
      {dip_pool25, ic_SPY, bh_SPY} for the other 40%; engine implementation is the headline.
"""
from __future__ import annotations

import pickle
import time

from scipy.optimize import minimize

from common import *  # noqa
from sleeves import SLEEVES, run_sleeve

SLEEVES["dip_pool25"] = ("pool", dict(cap=0.25, per_trade=0.25, idle="cash"))
U = ["wheel_QQQ", "cc_QQQ", "collar_SPY", "dip_pool25", "ic_SPY", "bh_SPY"]
STOCK = ["wheel_QQQ", "cc_QQQ", "collar_SPY"]
FRAC = ["dip_pool25", "ic_SPY", "bh_SPY"]


def blend_monthly(R: pd.DataFrame, w: dict) -> pd.Series:
    """Monthly-rebalanced blend of daily return streams -> equity (starts at 1)."""
    w = pd.Series(w).reindex(R.columns).fillna(0.0)
    eq, vals = [], w.values.copy()
    month = None
    for d, row in R.iterrows():
        m = d.strftime("%Y-%m")
        if m != month:
            month = m
            tot = vals.sum()
            vals = w.values * tot
        vals = vals * (1 + row.values)
        eq.append(vals.sum())
    return pd.Series(eq, index=R.index)


def ivol(R):
    v = 1 / R.std()
    return (v / v.sum()).to_dict()


def erc(R):
    C = R.cov().values
    n = len(C)

    def obj(x):
        x = np.abs(x) / np.abs(x).sum()
        rc = x * (C @ x)
        return ((rc - rc.mean()) ** 2).sum() * 1e8

    r = minimize(obj, np.ones(n) / n, method="Nelder-Mead", options={"maxiter": 20000, "xatol": 1e-8,
                                                                      "fatol": 1e-14})
    x = np.abs(r.x) / np.abs(r.x).sum()
    return dict(zip(R.columns, x))


def min_dd(R, min_cagr=0.10, n=6000, seed=11):
    rng = np.random.default_rng(seed)
    best, bw = -9, None
    X = rng.dirichlet(np.ones(R.shape[1]) * 0.7, n)
    A = R.values
    for x in X:
        r = np.nan_to_num(A @ x)       # daily-rebalanced proxy for speed
        e = np.cumprod(1 + r)
        cg = e[-1] ** (TD / len(r)) - 1
        if cg < min_cagr:
            continue
        dd = (e / np.maximum.accumulate(e) - 1).min()
        if dd > best:
            best, bw = dd, x
    return dict(zip(R.columns, bw))


def main():
    t0 = time.time()
    S = pickle.loads((CACHE / "sleeves.pkl").read_bytes())
    for cy in (False, True):
        S[("dip_pool25", 100_000, cy)] = run_sleeve("dip_pool25", 100_000, cy)
    eqs = {s: S[(s, 100_000, False)]["equity"] for s in U}
    R = pd.DataFrame({s: e.pct_change() for s, e in eqs.items()}).dropna()
    Ris = R[:IS_END]
    W = {"I1_equal_weight": {s: 1 / len(U) for s in U},
         "I2_inverse_vol": ivol(Ris),
         "I3_risk_parity_erc": erc(Ris),
         "I4_min_drawdown": min_dd(Ris)}
    sub = erc(Ris[FRAC])
    for s in STOCK:
        W[f"L_{s}60_erc40"] = {s: 0.60, **{k: 0.40 * v for k, v in sub.items()}}
    for k, w in W.items():
        print(k, {a: round(b, 3) for a, b in w.items() if b > 1e-4})

    out = {"weights": W, "blends": {}}
    for bid, w in W.items():
        for cy in (False, True):
            Rc = pd.DataFrame({s: S[(s, 100_000, cy)]["equity"].pct_change() for s in U}).dropna()
            ideal = blend_monthly(Rc, w) * 100_000
            ideal = pd.concat([pd.Series([100_000.0], index=[eqs[U[0]].index[0]]), ideal])
            parts, uses, inf, trs = {}, {}, {}, []
            for s, x in w.items():
                if x < 1e-4:
                    continue
                r = run_sleeve(s, x * 100_000, cy)
                parts[s] = r["equity"]
                uses[s] = r["usage"] * r["equity"]
                if len(r["trades"]):
                    trs.append(r["trades"].assign(sleeve=s))
                inf[s] = {"infeasible": r["infeasible"], "blocked_days": r["blocked_days"],
                          "dollars": x * 100_000}
            P = pd.DataFrame(parts).ffill()
            eng = P.sum(axis=1)
            usage = pd.DataFrame(uses).ffill().sum(axis=1) / eng
            out["blends"][(bid, cy)] = {"ideal": ideal, "engine": eng, "usage": usage, "parts": P,
                                        "feas": inf, "weights": w,
                                        "trades": pd.concat(trs, ignore_index=True) if trs else pd.DataFrame()}
            mi, me = M.equity_metrics(ideal), M.equity_metrics(eng)
            print(f"{bid:28s} ty={int(cy)} ideal CAGR {mi['cagr']:6.2%} Sh {mi['sharpe']:4.2f} DD {mi['max_drawdown']:6.2%}"
                  f" | engine CAGR {me['cagr']:6.2%} Sh {me['sharpe']:4.2f} DD {me['max_drawdown']:6.2%}"
                  f" | infeasible: {[s for s, v in inf.items() if v['infeasible']]} [{time.time()-t0:.0f}s]",
                  flush=True)
    out["sleeves_extra"] = {k: v for k, v in S.items() if k[0] == "dip_pool25"}
    (CACHE / "p100k.pkl").write_bytes(pickle.dumps(out))


if __name__ == "__main__":
    main()
