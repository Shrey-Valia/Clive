"""Stage 2: selection on IS, OOS, DSR, bootstrap, walk-forward, combos (LS1), stress, capacity,
account variants, episodes, decomposition, finalists.json, summary.json, plots.

    python3 src/analyze.py     (after run_all.py)
"""
from __future__ import annotations

import json
import os
import pickle
from dataclasses import replace

import numpy as np
import pandas as pd

import lib
import strats as S
from common import CACHE, IS_END, OOS_START, OUT, RESULTS
from lib import block_boot, boot_trades, dsr, split, stats
from plot import plots

DAYS = S.CAL
EXTRA_TRIALS: list[dict] = []


def ser_stats(r: pd.Series, t=None):
    d = pd.DataFrame({"date": r.index, "ret": r.values, "n": (r != 0).astype(int).values})
    return stats(d, t)


def period(r, a, b):
    return r[(r.index >= a) & (r.index <= b)]


def row_for(name, r, t, n_trials, var_sr, family=""):
    ri, ro = period(r, "2016-01-01", IS_END), period(r, OOS_START, "2099")
    ti = t[t.date <= IS_END] if t is not None else None
    to = t[t.date >= OOS_START] if t is not None else None
    si, so = ser_stats(ri, ti), ser_stats(ro, to)
    bi = block_boot(ro.values)
    lo, hi = boot_trades(to.net.values) if to is not None and len(to) else (np.nan, np.nan)
    out = {"family": family, "name": name}
    for k in ("sharpe", "sortino", "cagr", "total_return", "max_drawdown", "worst_day", "trades", "trades_per_year",
              "mean_trade", "median_trade", "win_rate", "profit_factor", "mean_gross", "mean_cost", "q01", "q05",
              "q99", "worst_trade", "p_lt_-25"):
        out[f"is_{k}"] = si.get(k, np.nan)
        out[f"oos_{k}"] = so.get(k, np.nan)
    # deflated Sharpe, null-variance convention (Var[SR] = 1/T under H0), as in the shorting research extras.py
    out["is_dsr"] = dsr(ri.values, n_trials, 1 / max(len(ri), 1))
    out["is_dsr_incl_shorting_trials"] = dsr(ri.values, n_trials + 1037, 1 / max(len(ri), 1))
    out["full_dsr"] = dsr(r.values, n_trials, 1 / max(len(r), 1))
    out["oos_psr"] = dsr(ro.values, 1, 0)
    out.update({"oos_sharpe_ci_lo": bi["sharpe_lo"], "oos_sharpe_ci_hi": bi["sharpe_hi"],
                "oos_cagr_ci_lo": bi["cagr_lo"], "oos_cagr_ci_hi": bi["cagr_hi"],
                "oos_mean_trade_ci_lo": lo, "oos_mean_trade_ci_hi": hi})
    return out


def walk_forward(names, SERIES):
    """Each year, pick the family variant with the best Sharpe over the prior 3 years."""
    X = pd.DataFrame({n: SERIES[n] for n in names}).fillna(0.0)
    picks, parts = [], []
    for y in range(2019, 2027):
        tr = X[(X.index >= f"{y-3}-01-01") & (X.index <= f"{y-1}-12-31")]
        te = X[(X.index >= f"{y}-01-01") & (X.index <= f"{y}-12-31")]
        sh = tr.mean() / tr.std().replace(0, np.nan)
        best = sh.idxmax() if sh.notna().any() else names[0]
        parts.append(te[best])
        picks.append({"year": y, "chosen": best, "test_return": float(np.prod(1 + te[best]) - 1)})
    return pd.concat(parts), picks


def main():
    st = pickle.load(open(os.path.join(CACHE, "stage1.pkl"), "rb"))
    SERIES, TRADES, BOOKS, FAMILY = st["SERIES"], st["TRADES"], st["BOOKS"], st["FAMILY"]
    trials = pd.read_csv(os.path.join(RESULTS, "trials.csv"))
    tt_cache = pickle.load(open(os.path.join(CACHE, "tt.pkl"), "rb"))     # trade tables per variant (pre-selection)

    def rerun(name, **kw):
        b = replace(BOOKS[name], **kw)
        d, t = lib.run(tt_cache[name], b, DAYS)
        return d.set_index("date").ret, t

    # ---------------- LS1 combos and v2 add-ons (each one is a counted trial)
    combos = {}
    v2 = SERIES["v2_short"]

    def add(name, r, t=None, fam="LS1"):
        combos[name] = (r, t)
        SERIES[name] = r
        FAMILY[name] = fam
        if t is not None:
            TRADES[name] = t
        ri, ro = period(r, "2016", IS_END), period(r, OOS_START, "2099")
        EXTRA_TRIALS.append({"family": fam, "name": name, "is_sharpe": ser_stats(ri)["sharpe"],
                             "oos_sharpe": ser_stats(ro)["sharpe"]})

    tcat = lambda *ts: pd.concat([x for x in ts if x is not None], ignore_index=True)
    if "L1_v2names" in SERIES:
        add("LS1_flip_v2names", SERIES["L1_v2names"] + v2, tcat(TRADES["L1_v2names"], TRADES["v2_short"]))
    for a, b_ in (("L1_5-10_score70", "S1_5-10_adv3m"), ("L1_1-5_score70", "S1_1-5_adv3m")):
        if a in SERIES and b_ in SERIES:
            add(f"LS1_flip_{a[3:]}", SERIES[a] + SERIES[b_], tcat(TRADES[a], TRADES[b_]))

    tr_all = pd.concat([trials, pd.DataFrame(EXTRA_TRIALS)], ignore_index=True)
    fams = {f: [n for n, ff in FAMILY.items() if ff == f and n in set(trials.name)] for f in ("L1", "L2", "L3", "L4", "S1")}
    # IS-best per family (>= 60 IS trades), then market-neutral L + S combos from the IS-best legs
    best = {}
    for f, names in fams.items():
        sub = trials[trials.name.isin(names) & (trials.is_trades >= 60)]
        if len(sub):
            best[f] = sub.sort_values("is_sharpe", ascending=False).iloc[0]["name"]
    if "L3" in best and "S1" in best:
        add("LS1_L3+S1", 0.5 * SERIES[best["L3"]] + 0.5 * SERIES[best["S1"]])
    for f in ("L1", "L2", "L3", "L4"):
        if f in best:
            add(f"v2+{f}", v2 + SERIES[best[f]], fam="ADD")
    tr_all = pd.concat([trials, pd.DataFrame(EXTRA_TRIALS)], ignore_index=True)
    n_trials = len(tr_all) + 1                                               # + the v2 baseline itself
    var_sr = float((tr_all.is_sharpe.dropna() / np.sqrt(252)).var())
    print("trials", n_trials, "var daily SR", var_sr, "best", best)

    # ---------------- comparison table: every strategy (IS / OOS)
    rows = []
    for name in list(trials.name) + list(combos) + ["v2_short", "SPY"]:
        r = SERIES[name].reindex(DAYS).fillna(0.0)
        rows.append(row_for(name, r, TRADES.get(name), n_trials, var_sr, FAMILY.get(name, name)))
    comp = pd.DataFrame(rows)
    comp.to_csv(os.path.join(RESULTS, "comparison.csv"), index=False)
    tr_all.to_csv(os.path.join(RESULTS, "trials_all.csv"), index=False)

    # ---------------- walk-forward per family
    wf_rows, wf_series = [], {}
    for f, names in fams.items():
        if not names:
            continue
        w, picks = walk_forward(names, SERIES)
        wf_series[f] = w
        s_ = ser_stats(w)
        wf_rows.append({"family": f, "wf_sharpe_2019_26": s_["sharpe"], "wf_cagr": s_["cagr"],
                        "wf_maxdd": s_["max_drawdown"], "picks": json.dumps(picks)})
    pd.DataFrame(wf_rows).to_csv(os.path.join(RESULTS, "walk_forward.csv"), index=False)

    # ---------------- finalists: IS-best of each family + combos + v2 (selection on IS only)
    finals = [best[f] for f in ("L1", "L2", "L3", "L4", "S1") if f in best]
    finals += [n for n in ("L1_v2names", "LS1_flip_v2names") if n in SERIES]
    # IS-best penny-only L1 and Night Owl variants (the >=$10 overnight leg wins L1 overall)
    for pref in ("L1_1-", "L1_5-", "L1NO_"):
        sub = trials[trials.name.str.startswith(pref)]
        if len(sub):
            finals.append(sub.sort_values("is_sharpe", ascending=False).iloc[0]["name"])
    finals = list(dict.fromkeys(f for f in finals if f != "L1_10+_score70"))
    finals += [n for n in combos if n.startswith("v2+") or n == "LS1_L3+S1"]

    # ---------------- stress, capacity and account variants on the single-strategy finalists
    stress = []
    for name in [n for n in finals if n in tt_cache]:
        b0 = BOOKS[name]
        is_short = (tt_cache[name].side < 0).any()
        variants = {"base": {}, "cost_x2": {"cost_mult": 2.0}, "impact_x2": {"impact_y": 1.0},
                    "equity_10k": {"equity": 10_000}, "equity_1m": {"equity": 1_000_000},
                    "equity_10m": {"equity": 10_000_000}}
        if is_short:
            variants.update({"borrow_x2": {"extra": {"borrow_mult": 2.0}},
                             "locate_htb100_50bps": {"borrow_mode": "locate", "htb_fee": 1.0, "locate_bps": 50},
                             "locate_htb200_100bps": {"borrow_mode": "locate", "htb_fee": 2.0, "locate_bps": 100},
                             "borrow_seed1": {"seed": 1}, "borrow_seed2": {"seed": 2}})
        if b0.intraday:
            variants["acct_10k_margin_pdt"] = {"equity": 10_000, "pdt": True}
        for vn, kw in variants.items():
            r, t = rerun(name, **kw)
            si, so = ser_stats(period(r, "2016", IS_END), t[t.date <= IS_END]), ser_stats(period(r, OOS_START, "2099"), t[t.date >= OOS_START])
            stress.append({"name": name, "variant": vn, "is_sharpe": si.get("sharpe"), "is_cagr": si.get("cagr"),
                           "oos_sharpe": so.get("sharpe"), "oos_cagr": so.get("cagr"), "oos_maxdd": so.get("max_drawdown"),
                           "oos_mean_trade": so.get("mean_trade"), "oos_trades": so.get("trades")})
    # v2 itself at $10k margin with PDT: at most 3 intraday shorts per 5 sessions
    v2t = TRADES["v2_short"].copy()
    v2t["entry_date"] = v2t.date
    keep, hist = [], []
    for i, d in enumerate(DAYS):
        g = v2t[v2t.entry_date == d]
        k = max(0, 3 - sum(1 for j in hist if i - j < 5))
        keep.append(g.head(k)); hist += [i] * min(k, len(g))
    vk = pd.concat(keep)
    r = (vk.assign(c=vk.w * vk.net).groupby("date").c.sum()).reindex(DAYS).fillna(0.0)
    stress.append({"name": "v2_short", "variant": "acct_10k_margin_pdt",
                   "is_sharpe": ser_stats(period(r, "2016", IS_END))["sharpe"], "is_cagr": ser_stats(period(r, "2016", IS_END))["cagr"],
                   "oos_sharpe": ser_stats(period(r, OOS_START, "2099"))["sharpe"],
                   "oos_cagr": ser_stats(period(r, OOS_START, "2099"))["cagr"],
                   "oos_maxdd": ser_stats(period(r, OOS_START, "2099"))["max_drawdown"],
                   "oos_mean_trade": vk[vk.date >= OOS_START].net.mean(), "oos_trades": int((vk.date >= OOS_START).sum())})
    # LS1 flip (overnight long of v2 names + v2 intraday short) under stress
    def v2s(nm):
        d = pd.read_parquet(os.path.join(CACHE, f"v2_{nm}_daily.parquet"))
        d["date"] = d.date.map(S.NEXT)
        return d.dropna(subset=["date"]).set_index("date").ret.reindex(DAYS).fillna(0.0)
    if "L1_v2names" in tt_cache:
        l1c, _ = rerun("L1_v2names", cost_mult=2.0)
        l1k, _ = rerun("L1_v2names", equity=10_000)
        l1m, _ = rerun("L1_v2names", equity=1_000_000)
        for vn, r in (("base", SERIES["L1_v2names"].reindex(DAYS).fillna(0) + v2), ("cost_x2", l1c + v2s("cost_x2")),
                      ("borrow_x2", SERIES["L1_v2names"].reindex(DAYS).fillna(0) + v2s("borrow_x2")),
                      ("cost_x2_borrow_x2", l1c + v2s("cost_x2_borrow_x2")),
                      ("acct_10k_margin_pdt", l1k + r.reindex(DAYS).fillna(0)), ("equity_1m", l1m + v2)):
            si, so = ser_stats(period(r, "2016", IS_END)), ser_stats(period(r, OOS_START, "2099"))
            stress.append({"name": "LS1_flip_v2names", "variant": vn, "is_sharpe": si["sharpe"], "is_cagr": si["cagr"],
                           "oos_sharpe": so["sharpe"], "oos_cagr": so["cagr"], "oos_maxdd": so["max_drawdown"]})
        for vn in ("cost_x2", "borrow_x2", "cost_x2_borrow_x2"):
            r = v2s(vn)
            si, so = ser_stats(period(r, "2016", IS_END)), ser_stats(period(r, OOS_START, "2099"))
            stress.append({"name": "v2_short", "variant": vn, "is_sharpe": si["sharpe"], "is_cagr": si["cagr"],
                           "oos_sharpe": so["sharpe"], "oos_cagr": so["cagr"], "oos_maxdd": so["max_drawdown"]})
        a, b2 = SERIES["L1_v2names"].reindex(DAYS).fillna(0), v2
        both = (a != 0) | (b2 != 0)
        json.dump({"corr_all_days": float(np.corrcoef(a, b2)[0, 1]), "corr_active_days": float(np.corrcoef(a[both], b2[both])[0, 1])},
                  open(os.path.join(RESULTS, "flip_correlation.json"), "w"), indent=1)
    stress = pd.DataFrame(stress)
    stress.to_csv(os.path.join(RESULTS, "stress_capacity.csv"), index=False)

    # ---------------- by year, episodes, trade distributions
    show = finals + ["v2_short", "SPY"]
    X = pd.DataFrame({n: SERIES[n].reindex(DAYS).fillna(0.0) for n in show})
    X.index = pd.Index(DAYS, name="date")
    yr = X.groupby(X.index.str[:4]).apply(lambda g: (1 + g).prod() - 1)
    yr.to_csv(os.path.join(RESULTS, "by_year.csv"))
    eps = {"jan2021_meme": ("2021-01-04", "2021-02-12"), "y2022_bear": ("2022-01-03", "2022-12-30"),
           "apr2025_tariff": ("2025-04-01", "2025-04-30")}
    ep_rows = []
    for en, (a, b_) in eps.items():
        sub = X[(X.index >= a) & (X.index <= b_)]
        for n in show:
            eq = (1 + sub[n]).cumprod()
            ep_rows.append({"episode": en, "name": n, "return": eq.iloc[-1] - 1,
                            "max_dd": (eq / eq.cummax() - 1).min(), "worst_day": sub[n].min()})
    pd.DataFrame(ep_rows).to_csv(os.path.join(RESULTS, "episodes.csv"), index=False)
    dist = []
    for n in show:
        t = TRADES.get(n)
        if t is None or not len(t):
            continue
        for per, x in (("IS", t[t.date <= IS_END]), ("OOS", t[t.date >= OOS_START])):
            v = x.net
            if not len(v):
                continue
            dist.append({"name": n, "period": per, "n": len(v), "mean": v.mean(), "median": v.median(), "std": v.std(),
                         "skew": v.skew(), "q01": v.quantile(.01), "q05": v.quantile(.05), "q25": v.quantile(.25),
                         "q75": v.quantile(.75), "q95": v.quantile(.95), "q99": v.quantile(.99), "min": v.min(),
                         "max": v.max(), "p_lt_-10": (v < -.1).mean(), "p_lt_-25": (v < -.25).mean(),
                         "p_gt_+25": (v > .25).mean()})
    pd.DataFrame(dist).to_csv(os.path.join(RESULTS, "trade_distributions.csv"), index=False)
    # by bucket for every single-strategy trial (mean gross / cost / net)
    bk = []
    for n in trials.name:
        t = TRADES[n]
        for (per, bkt), x in t.groupby([np.where(t.date <= IS_END, "IS", "OOS"), "bucket"]):
            bk.append({"name": n, "period": per, "bucket": bkt, "n": len(x), "mean_gross": x.gross.mean(),
                       "mean_cost": x.cost.mean(), "mean_net": x.net.mean(), "win": (x.net > 0).mean()})
    pd.DataFrame(bk).to_csv(os.path.join(RESULTS, "by_bucket.csv"), index=False)

    # ---------------- decomposition by price bucket (gross legs of the spike names)
    s = S.load_scans()
    dec = []
    pb = lambda p: np.select([p < 3, p < 5, p < 10], ["$1-3", "$3-5", "$5-10"], "$10+")
    s["pb"] = pb(s.p1545_raw.values)
    s = s[(s.adv20 >= 3e6) & ~s.excl & (s.n_after >= 1)]
    s["on_long"] = s.o1 / s.p1555 - 1
    s["id_short"] = 1 - s.c1 / s.o1
    s["full_short"] = 1 - s.x1555 / s.p1555
    s["rt_cost"] = 2 * lib.one_way_bps(s.p1545_raw.values, s.adv20.values) / 1e4 \
        + 2 * lib.impact(10_000, s.adv20.values, S.sigma(s.atr14.values), 0.5)
    for sel, x0 in (("all_top20", s), ("score70", s[s.short_score >= 70])):
        for (per, b_), x in x0.groupby([np.where(x0.date <= IS_END, "IS", "OOS"), "pb"]):
            dec.append({"sample": sel, "period": per, "bucket": b_, "n": len(x),
                        "overnight_long_mean": x.on_long.mean(), "overnight_long_median": x.on_long.median(),
                        "intraday_short_mean": x.id_short.mean(), "intraday_short_median": x.id_short.median(),
                        "full_1555_short_mean": x.full_short.mean(), "roundtrip_cost_est": x.rt_cost.median()})
    dec = pd.DataFrame(dec)
    dec.to_csv(os.path.join(RESULTS, "decomposition.csv"), index=False)

    # ---------------- finalists.json (dashboard shape) and summary.json
    def dash(name, capital=100_000):
        r = SERIES[name].reindex(DAYS).fillna(0.0)
        t = TRADES.get(name)
        eq = capital * (1 + r).cumprod()
        dd = eq / eq.cummax() - 1
        m = r.groupby(r.index.str[:7]).apply(lambda g: (1 + g).prod() - 1)
        a = ser_stats(r, t)
        o = ser_stats(period(r, OOS_START, "2099"), t[t.date >= OOS_START] if t is not None else None)
        key = lambda z: {"cagr": z.get("cagr"), "total_return": z.get("total_return"), "max_drawdown": z.get("max_drawdown"),
                         "sharpe": z.get("sharpe"), "sortino": z.get("sortino"), "win_rate": z.get("win_rate"),
                         "profit_factor": z.get("profit_factor"), "trades": z.get("trades")}
        b = BOOKS.get(name)
        params = {"family": FAMILY.get(name)}
        tr_row = trials[trials.name == name]
        if len(tr_row):
            params.update(json.loads(tr_row.iloc[0].params))
        if b is not None:
            params.update({"n": b.n, "weight_per_name": b.w, "gross_cap": b.gross_cap, "hold_days": b.hold,
                           "equity_for_costs": b.equity, "impact_y": b.impact_y, "borrow_mode": b.borrow_mode})
        return {"id": name.lower().replace("+", "_plus_").replace("$", ""), "name": name,
                "underlying": "US listed stocks (NASDAQ/NYSE/AMEX)" + (", $1-$10" if any(z in name for z in ("1-5", "5-10", "L3", "L4")) else ""),
                "capital": capital, "metrics": key(a),
                "equity": [{"date": d, "value": round(float(v), 2)} for d, v in eq.items()],
                "drawdown": [{"date": d, "value": round(float(v), 5)} for d, v in dd.items()],
                "monthly_returns": [{"month": k, "ret": round(float(v), 5)} for k, v in m.items()],
                "params": params, "oos_metrics": key(o)}
    fj = [dash(n) for n in finals + ["v2_short"]]
    json.dump(fj, open(os.path.join(RESULTS, "finalists.json"), "w"), default=lambda x: None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x))
    cmp_short = comp[["family", "name", "is_sharpe", "is_cagr", "is_max_drawdown", "is_trades", "is_mean_trade", "oos_sharpe",
                      "oos_cagr", "oos_max_drawdown", "oos_trades", "oos_mean_trade", "is_dsr", "is_dsr_incl_shorting_trials", "oos_psr",
                      "oos_sharpe_ci_lo", "oos_sharpe_ci_hi"]]
    summary = {"n_trials": n_trials, "var_is_daily_sharpe": var_sr, "is_period": ["2016-01-04", IS_END],
               "oos_period": [OOS_START, DAYS[-1]], "is_best_per_family": best, "finalists": finals,
               "walk_forward": wf_rows, "comparison": json.loads(cmp_short.to_json(orient="records"))}
    json.dump(summary, open(os.path.join(RESULTS, "summary.json"), "w"), indent=1, default=float)
    plots(SERIES, finals, yr, dec, stress, comp, wf_series)
    pd.set_option("display.width", 250)
    print(cmp_short.round(3).to_string())
    print(stress.round(3).to_string())
    print(yr.round(3).to_string())
    print(pd.DataFrame(ep_rows).round(3).to_string())
    print(dec.round(4).to_string())
    print(pd.DataFrame(wf_rows)[["family", "wf_sharpe_2019_26", "wf_cagr", "wf_maxdd"]].round(3).to_string())


if __name__ == "__main__":
    main()
