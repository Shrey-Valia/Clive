"""Walk-forward validation of options regime filters on CBOE strategy indexes.

Run: python3 -I study.py   (writes longhist/out/*.csv, *.json; charts via charts.py)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from engine import (build, run, run_with_exit, stats, period_ret, IS_END, OOS_START)  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
RNG = np.random.default_rng(7)

CRASHES = {
    "GFC 2008-09": ("2008-09-01", "2009-03-31"),
    "Aug 2011": ("2011-07-22", "2011-10-03"),
    "2015-16": ("2015-08-01", "2016-02-29"),
    "Feb 2018": ("2018-01-26", "2018-02-28"),
    "Q4 2018": ("2018-10-01", "2018-12-31"),
    "COVID 2020": ("2020-02-19", "2020-04-30"),
    "2022": ("2022-01-01", "2022-12-31"),
    "Apr 2025": ("2025-04-01", "2025-04-30"),
}


def b(x):  # bool -> exposure
    return x.astype(float).where(x.notna() if hasattr(x, "notna") else True)


# --------------------------------------------------------------- filter families
# Each family: name -> (list of params, fn(df, p) -> target exposure series)
# NaN target -> treated as invested (signal history missing).
def fam_vrp(df, p):
    return (df.VRP > p).astype(float).where(df.VRP.notna())


def fam_ts(df, p):
    return (df.TS < p).astype(float).where(df.TS.notna())


def fam_ts9(df, p):
    return (~(df.TS9 > p)).astype(float).where(df.TS9.notna())


def fam_sma(df, p):
    s = df[f"SMA{p}"]
    return (df.SPX > s).astype(float).where(s.notna())


def fam_or(df, p):
    x = (df.SPX > df.SMA200) | (df.TS < p)
    return x.astype(float)


def fam_and(df, p):
    x = (df.SPX > df.SMA200) & (df.TS < p)
    return x.astype(float).where(df.TS.notna())


def fam_ts_vrp(df, p):
    t, v = p
    x = (df.TS < t) & (df.VRP > v)
    return x.astype(float).where(df.TS.notna())


def fam_ts_ts9(df, p):
    t, t9 = p
    x = (df.TS < t) & ~(df.TS9 > t9)
    return x.astype(float).where(df.TS.notna())


def fam_vixstop(df, p):
    return (df.VIX <= p).astype(float)


def fam_vix_ts(df, p):  # R6 second half: no trades if VIX>p and TS>1
    return (~((df.VIX > p) & (df.TS > 1.0))).astype(float)


def fam_ddts(df, p):  # R13: off if DD<-p & TS>1 ; half size if DD<-p & TS<=1
    deep = df.DD < -p
    e = pd.Series(1.0, index=df.index)
    e[deep] = 0.5
    e[deep & (df.TS > 1.0)] = 0.0
    return e


def fam_voltgt(df, p):
    vol = df.PUTVOL63
    return (p / vol).clip(upper=1.5).where(vol.notna())


def fam_vixmed(df, p):
    return (df.VIXMED / df.VIX).clip(upper=p).where(df.VIXMED.notna())


FAMILIES = {
    "VRP>x": ([-2, 0, 2, 4], fam_vrp),
    "VIX/VIX3M<x": ([0.90, 0.95, 1.00, 1.05, 1.10], fam_ts),
    "VIX9D/VIX>x block": ([1.0, 1.05, 1.1, 1.2], fam_ts9),
    "SPX>SMAx": ([100, 200], fam_sma),
    "SMA200 OR TS<x": ([0.90, 0.95, 1.00], fam_or),
    "SMA200 AND TS<x": ([0.95, 1.00, 1.05], fam_and),
    "TS<a AND VRP>b": ([(0.95, 0), (1.0, 0), (1.0, 2), (1.05, 0)], fam_ts_vrp),
    "TS<a AND 9D/VIX<=b": ([(0.95, 1.1), (1.0, 1.1), (1.0, 1.2)], fam_ts_ts9),
    "VIX<=x stop": ([30, 35, 40, 50], fam_vixstop),
    "not(VIX>x & TS>1)": ([30, 40], fam_vix_ts),
    "DD>x & TS>1 block": ([0.05, 0.10, 0.15], fam_ddts),
    "VolTarget x": ([0.06, 0.08, 0.10, 0.12], fam_voltgt),
    "VIXmed/VIX cap x": ([1.0, 1.5], fam_vixmed),
}


# Exit families: (grid, fn(df,p) -> (entry_target, exit_signal)). Exit can fire
# on any day (acted on the day after the signal close, that day's return still
# earned); re-entry only at the next monthly roll when entry_target is 1.
def ex_ts(df, p):
    s = df.TS > p
    return (~s).astype(float), s


def ex_vix(df, p):
    s = df.VIX > p
    return (~s).astype(float), s


def ex_ts9(df, p):
    s = df.TS9 > p
    return (~s).astype(float).where(df.TS9.notna()), s


def ex_stop(df, p, asset="PUT"):
    from engine import monthly_decision_days
    dd = monthly_decision_days(df.index)
    lvl = df[asset]
    anchor = pd.Series(np.nan, index=df.index)
    # anchor = close of day before roll (position opened at roll ~ that level)
    pos = df.index.get_indexer(dd)
    anchor.iloc[np.clip(pos - 1, 0, None)] = lvl.iloc[np.clip(pos - 1, 0, None)].values
    anchor = anchor.ffill()
    s = (lvl / anchor - 1) < -p
    return pd.Series(1.0, index=df.index), s


EXIT_FAMILIES = {
    "Exit TS>x": ([1.0, 1.05, 1.10, 1.20], ex_ts),
    "Exit VIX>x": ([30, 35, 40, 50], ex_vix),
    "Exit 9D/VIX>x": ([1.1, 1.2, 1.3], ex_ts9),
    "Stop -x since roll": ([0.03, 0.05, 0.08], ex_stop),
}


def pstr(p):
    return "/".join(str(x) for x in p) if isinstance(p, tuple) else str(p)


def split(r):
    return r[r.index <= IS_END], r[r.index >= OOS_START]


def main():
    df = build()
    cash = df.CASH_RET
    one = pd.Series(1.0, index=df.index)
    rows, rets, exps = [], {}, {}

    def record(fam, p, r, asset, freq):
        key = f"{fam} [{pstr(p)}]" if p is not None else fam
        e = r.attrs["exposure"]
        ins, oos = split(r)
        d = dict(family=fam, param=pstr(p) if p is not None else "", key=key,
                 asset=asset, freq=freq, switches=r.attrs["switches"])
        for tag, rr in (("IS", ins), ("OOS", oos), ("FULL", r)):
            for k, v in stats(rr, cash, e).items():
                d[f"{tag}_{k}"] = v
        for cn, (a, bb) in CRASHES.items():
            d[f"crash_{cn}"] = period_ret(r, a, bb)
        rows.append(d)
        rets[(asset, freq, key)] = r
        exps[(asset, freq, key)] = e
        return d

    # ---- monthly PUT: baseline + all families
    for asset, freq in (("PUT", "M"), ("WPUT", "W"), ("BXM", "M"), ("CNDR", "M")):
        record("Baseline", None, run(df, one, asset, freq), asset, freq)
        for fam, (grid, fn) in FAMILIES.items():
            for p in grid:
                tgt = fn(df, p)
                if fam == "VolTarget x" and asset != "PUT":
                    vol = df[f"R_{asset}"].rolling(63).std() * np.sqrt(252)
                    tgt = (p / vol).clip(upper=1.5).where(vol.notna())
                record(fam, p, run(df, tgt, asset, freq), asset, freq)

    # ---- intramonth exit families (monthly PUT and BXM): re-entry only at roll
    for asset in ("PUT", "BXM"):
        for fam, (grid, fn) in EXIT_FAMILIES.items():
            for p in grid:
                tgt, sig = fn(df, p, asset) if fn is ex_stop else fn(df, p)
                record(fam, p, run_with_exit(df, tgt, sig, asset, "M"), asset, "M")
    # exit-cost sensitivity (not part of selection; diagnostic)
    for xc in (0.005, 0.01):
        for fam, p in (("Exit TS>x", 1.10), ("Exit VIX>x", 40)):
            tgt, sig = EXIT_FAMILIES[fam][1](df, p)
            record(f"{fam} exitcost{xc}", p,
                   run_with_exit(df, tgt, sig, "PUT", "M", exit_cost=xc), "PUT", "M")
    # combined entry gate + exit
    record("TS<1.05 entry + Exit TS>1.10", None,
           run_with_exit(df, fam_ts(df, 1.05), df.TS > 1.10, "PUT", "M"), "PUT", "M")
    record("Exit TS>1.10 OR VIX>40", None,
           run_with_exit(df, (~((df.TS > 1.10) | (df.VIX > 40))).astype(float),
                         (df.TS > 1.10) | (df.VIX > 40), "PUT", "M"), "PUT", "M")

    # ---- benchmarks
    for nm, col in (("SPX price", "SPX"), ("SPX +1.8% div (approx TR)", "SPXTR")):
        r = df[f"R_{col}"].fillna(0)
        r.attrs["exposure"] = one
        r.attrs["switches"] = 0
        record(nm, None, r, "SPX", "-")

    res = pd.DataFrame(rows)
    res.to_csv(OUT / "all_results.csv", index=False)

    # ---- walk-forward: choose best IS Sharpe param per family, per asset/freq
    wf = []
    for (asset, freq), g in res[res.family.isin(list(FAMILIES) + list(EXIT_FAMILIES))].groupby(["asset", "freq"]):
        base = res[(res.family == "Baseline") & (res.asset == asset)].iloc[0]
        for fam, gg in g.groupby("family", sort=False):
            best = gg.loc[gg.IS_Sharpe.idxmax()]
            d = best.to_dict()
            d["dSharpe_IS"] = best.IS_Sharpe - base.IS_Sharpe
            d["dSharpe_OOS"] = best.OOS_Sharpe - base.OOS_Sharpe
            d["grid_OOS_Sharpe"] = "; ".join(f"{pp}:{s:.2f}" for pp, s in zip(gg.param, gg.OOS_Sharpe))
            wf.append(d)
    wf = pd.DataFrame(wf)
    wf.to_csv(OUT / "walkforward.csv", index=False)

    # ---- placebo test for OOS: circular-shift each selected PUT filter's exposure
    # (same % invested, same run lengths, random timing)
    put_base = rets[("PUT", "M", "Baseline")]
    R = df.R_PUT.fillna(0)
    oos_mask = df.index >= OOS_START
    placebo = []
    sel = wf[(wf.asset == "PUT")]
    n_shift = 1000
    for _, row in sel.iterrows():
        e = exps[("PUT", "M", row.key)]
        actual = stats(rets[("PUT", "M", row.key)][oos_mask], cash)["Sharpe"]
        eo = e[oos_mask].values
        Ro, co = R[oos_mask].values, cash[oos_mask].values
        n = len(eo)
        sh = []
        for _ in range(n_shift):
            k = RNG.integers(21, n - 21)
            es = np.roll(eo, k)
            x = es * (Ro - co) - 0.001 * np.abs(np.diff(es, prepend=es[0]))
            sh.append(x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else 0)
        sh = np.array(sh)
        placebo.append(dict(key=row.key, OOS_Sharpe=actual, placebo_median=np.median(sh),
                            placebo_p=(sh >= actual).mean()))
    placebo = pd.DataFrame(placebo)
    placebo.to_csv(OUT / "placebo.csv", index=False)

    # ---- White reality check on OOS Sharpe differences vs baseline (PUT monthly),
    # stationary block bootstrap of daily returns, mean block 63 days.
    keys = list(sel.key)
    M = np.column_stack([(rets[("PUT", "M", k)] - cash)[oos_mask].values for k in keys])
    B = (put_base - cash)[oos_mask].values

    def sr(x):
        return x.mean(0) / x.std(0) * np.sqrt(252)

    d_obs = sr(M) - sr(B[:, None])[0]
    T = len(B)
    nb = 2000
    maxstat = np.empty(nb)
    boot_d = np.empty((nb, len(keys)))
    pblk = 1 / 63
    for i in range(nb):
        idx = np.empty(T, dtype=int)
        t = RNG.integers(T)
        for j in range(T):
            if j > 0 and RNG.random() > pblk:
                t = (t + 1) % T
            else:
                t = RNG.integers(T)
            idx[j] = t
        dstar = sr(M[idx]) - sr(B[idx][:, None])[0]
        boot_d[i] = dstar
        maxstat[i] = np.max(dstar - d_obs)
    rc_p = float((maxstat >= d_obs.max()).mean())
    indiv_p = ((boot_d - d_obs) >= d_obs).mean(0)  # one-sided H0: dSR<=0
    rc = pd.DataFrame(dict(key=keys, dSharpe_OOS=d_obs, boot_p=indiv_p,
                           boot_ci_lo=np.percentile(boot_d, 5, axis=0),
                           boot_ci_hi=np.percentile(boot_d, 95, axis=0)))
    rc.to_csv(OUT / "reality_check.csv", index=False)

    # ---- deflated Sharpe ratio (Bailey & Lopez de Prado 2014) over ALL PUT variants, full sample
    from math import erf, sqrt, log
    def Phi(x): return 0.5 * (1 + erf(x / sqrt(2)))
    def Phinv(p):  # Acklam-ish via bisection
        lo, hi = -10, 10
        for _ in range(100):
            m = (lo + hi) / 2
            lo, hi = (m, hi) if Phi(m) < p else (lo, m)
        return (lo + hi) / 2
    put_all = res[res.asset == "PUT"]
    N = len(put_all)
    # monthly Sharpe (non-annualised) variance across trials
    mon = {}
    for k in put_all.key:
        x = rets[("PUT", "M", k)] - cash
        mon[k] = (1 + x).groupby(x.index.to_period("M")).prod() - 1
    srs = np.array([m.mean() / m.std() for m in mon.values()])
    var_sr = srs.var(ddof=1)
    emc = 0.5772156649
    sr0 = sqrt(var_sr) * ((1 - emc) * Phinv(1 - 1 / N) + emc * Phinv(1 - 1 / (N * np.e)))
    dsr = []
    for k, m in mon.items():
        s = m.mean() / m.std()
        g3 = ((m - m.mean()) ** 3).mean() / m.std() ** 3
        g4 = ((m - m.mean()) ** 4).mean() / m.std() ** 4
        Tm = len(m)
        z = (s - sr0) * sqrt(Tm - 1) / sqrt(1 - g3 * s + (g4 - 1) / 4 * s ** 2)
        dsr.append(dict(key=k, SR_monthly=s, SR_ann=s * sqrt(12), skew=g3, kurt=g4, DSR=Phi(z)))
    bm = rets[("PUT", "M", "Baseline")] - cash
    bm = (1 + bm).groupby(bm.index.to_period("M")).prod() - 1
    dsr = pd.DataFrame(dsr).sort_values("DSR", ascending=False)
    dsr.to_csv(OUT / "deflated_sharpe.csv", index=False)

    # ---- rolling 5y windows (monthly step), PUT monthly, selected params
    roll = []
    starts = pd.date_range("2007-01-01", "2021-10-01", freq="MS")
    base_r = rets[("PUT", "M", "Baseline")]
    for _, row in sel.iterrows():
        r = rets[("PUT", "M", row.key)]
        wins, dsum, ddwin = 0, [], 0
        for s0 in starts:
            s1 = s0 + pd.DateOffset(years=5)
            a = stats(r[(r.index >= s0) & (r.index < s1)], cash)
            bb = stats(base_r[(base_r.index >= s0) & (base_r.index < s1)], cash)
            dsum.append(a["Sharpe"] - bb["Sharpe"])
            wins += a["Sharpe"] > bb["Sharpe"]
            ddwin += a["MaxDD"] > bb["MaxDD"]
        roll.append(dict(key=row.key, win_rate_sharpe=wins / len(starts),
                         median_dSharpe=np.median(dsum), min_dSharpe=np.min(dsum),
                         win_rate_maxdd=ddwin / len(starts), n_windows=len(starts)))
    roll = pd.DataFrame(roll)
    roll.to_csv(OUT / "rolling5y.csv", index=False)

    # ---- exposure-matched static mix controls (Sharpe is leverage-invariant; DD is not)
    ctrl = []
    for _, row in sel.iterrows():
        e = exps[("PUT", "M", row.key)]
        w = e.mean()
        r = cash + w * (df.R_PUT.fillna(0) - cash)
        ins, oos = split(r)
        ctrl.append(dict(key=row.key, avg_exposure=w,
                         static_FULL_MaxDD=stats(r, cash)["MaxDD"],
                         static_OOS_MaxDD=stats(oos, cash)["MaxDD"],
                         static_FULL_CAGR=stats(r, cash)["CAGR"]))
    pd.DataFrame(ctrl).to_csv(OUT / "static_controls.csv", index=False)

    # persist returns for charts
    pd.DataFrame({f"{a}|{f}|{k}": v for (a, f, k), v in rets.items()}).to_pickle(OUT / "rets.pkl")
    pd.DataFrame({f"{a}|{f}|{k}": v for (a, f, k), v in exps.items()}).to_pickle(OUT / "exps.pkl")
    cash.to_pickle(OUT / "cash.pkl")
    json.dump(dict(n_variants_PUT=int(N), n_variants_total=int(len(res)), rc_p=rc_p,
                   dsr_sr0_monthly=sr0, dsr_sr0_ann=sr0 * sqrt(12),
                   baseline_SR_ann_full=float(bm.mean() / bm.std() * sqrt(12)),
                   oos_start=OOS_START, is_end=IS_END),
              open(OUT / "meta.json", "w"), indent=2)
    print("done", N, rc_p)


if __name__ == "__main__":
    main()
