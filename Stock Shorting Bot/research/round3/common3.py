"""Loaders shared by the round-3 study scripts, plus the trial log."""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

import data
import sim
from r3common import IS_END, LAST_DAY, OOS_START, RES, RESEARCH, SRC

TRIAL_FILE = os.path.join(RES, "trials_r3.csv")


def load_all():
    ev, B = data.load()
    sc = os.path.join(data.CACHE, "r3_scores.parquet")
    if os.path.exists(sc) and len(pd.read_parquet(sc)) == len(ev):
        S = pd.read_parquet(sc)
    else:
        import rescore
        S = pd.DataFrame({"score_live": rescore.score(ev)})          # live config: FTD weight 0
        S.to_parquet(sc)
    for c in S.columns:
        ev[c] = S[c].values
    sv = os.path.join(data.CACHE, "r3_score_variants.parquet")      # written by study_q3.py
    if os.path.exists(sv):
        V = pd.read_parquet(sv)
        if len(V) == len(ev):
            for c in V.columns:
                ev[c] = V[c].values
    days = sorted(pd.read_parquet(os.path.join(SRC, "events.parquet"), columns=["date"]).date.unique())
    days = [d for d in days if d <= LAST_DAY]
    return ev, B, days


def flush_trials(group: str):
    """Append this script's logged trials to results/trials_r3.csv (one row per config)."""
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in sim.TRIALS]
    if not rows:
        return
    df = pd.DataFrame(rows)
    df.insert(0, "script", group)
    if os.path.exists(TRIAL_FILE):
        old = pd.read_csv(TRIAL_FILE)
        old = old[old.script != group]
        df = pd.concat([old, df], ignore_index=True)
    df.to_csv(TRIAL_FILE, index=False)
    print(f"[trials] {group}: {len(rows)} logged; total round-3 trials {len(df)}")


def prior_is_sharpes() -> np.ndarray:
    t = pd.read_csv(os.path.join(RESEARCH, "results", "trials.csv"))
    return t.is_sharpe.dropna().values


N_PRIOR = 1037


def dsr_for(daily_is: np.ndarray, extra_trials: int = 0) -> dict:
    """Deflated Sharpe on IS daily returns vs all trials so far (1,037 prior + round 3)."""
    t3 = pd.read_csv(TRIAL_FILE) if os.path.exists(TRIAL_FILE) else pd.DataFrame({"is_sharpe": []})
    r3 = t3.is_sharpe.dropna().values
    allsr = np.r_[prior_is_sharpes(), r3]
    n = N_PRIOR + len(t3) + extra_trials       # feature screens (no Sharpe) count toward N too
    var = float(np.var(allsr / np.sqrt(252), ddof=1))
    r = pd.Series(daily_is)
    sr = r.mean() / r.std(ddof=1)
    d, sr0 = sim.deflated_sharpe(sr, len(r), r.skew(), r.kurt(), n, var)
    # Round-1 headline convention (extras.py, "DSR 0.49 over 1,037 trials"): null variance of the
    # Sharpe estimate = 1/T, i.e. sr0 = E[max of N standard normals] / sqrt(T).
    dn, sr0n = sim.deflated_sharpe(sr, len(r), r.skew(), r.kurt(), n, 1.0 / len(r))
    return {"dsr": dn, "sr0_annual": sr0n * np.sqrt(252), "dsr_xvar": d, "sr0_xvar_annual": sr0 * np.sqrt(252),
            "n_trials": n}


def fmt(row: dict, keys=("sharpe", "cagr", "maxdd", "trades", "mean", "worst")) -> str:
    def g(p, k):
        v = row.get(f"{p}_{k}")
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            return "   nan"
        if k in ("trades",):
            return f"{int(v):5d}"
        if k in ("sharpe",):
            return f"{v:5.2f}"
        return f"{100 * v:6.2f}%"
    return " | ".join(f"{p.upper()} " + " ".join(f"{k}={g(p, k)}" for k in keys) for p in ("is", "oos"))


def broad_stats(tr: pd.DataFrame, ev: pd.DataFrame, days: list, mask=None) -> dict:
    """Mean net trade over all eligible strict-gate events (no score filter): rule-level evidence
    with thousands of trades, to check whether a rule's effect on the 336-trade v2 book generalises."""
    exl = ev.exclude_bot.fillna("")
    m = tr.ok.values & ~exl.str.startswith("squeeze").values & ~exl.str.startswith("real").values
    if mask is not None:
        m &= mask
    t = tr[m]
    out = {}
    for p, s in (("is", t[t.date <= IS_END]), ("oos", t[t.date >= OOS_START])):
        x = s.net.clip(-1.5, 1)
        out[f"{p}_bmean"] = x.mean()
        out[f"{p}_bt"] = x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 2 else np.nan
        out[f"{p}_bn"] = len(x)
        # equal-weight daily book of all eligible names (1/n of 50% gross), Sharpe
        dl = [d for d in days if (d <= IS_END if p == "is" else d >= OOS_START)]
        dd = s.groupby("date").net.mean().reindex(dl).fillna(0)
        out[f"{p}_bsharpe"] = dd.mean() / dd.std(ddof=1) * np.sqrt(252) if dd.std() > 0 else np.nan
    return out
