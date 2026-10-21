"""All research questions in one pass. Writes results/*.csv and out/*.png.

    python3 research/backtest/analyze.py

IS = 2016-01-04 .. 2021-12-31 (choose), OOS = 2022-01-03 .. 2026-10-19 (validate).
Every config evaluated on IS is logged as a trial (results/trials.csv); the
deflated Sharpe of the IS winner uses that trial count and the cross-trial
variance of IS Sharpe ratios.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import sys
from dataclasses import asdict, replace

import numpy as np
import pandas as pd
from scipy import stats as sst

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, OUT, RESULTS  # noqa: E402
from engine import Config, prep, run, stats  # noqa: E402

IS_END, OOS_START = "2021-12-31", "2022-01-01"
TRIALS: list[dict] = []


# ------------------------------------------------------------------ helpers
def split(daily, trades):
    di, do = daily[daily.date <= IS_END], daily[daily.date >= OOS_START]
    ti, to = trades[trades.date <= IS_END], trades[trades.date >= OOS_START]
    return (di, ti), (do, to)


def evaluate(cfg: Config, ev, days, group="", log=True):
    daily, trades = run(cfg, ev, days)
    (di, ti), (do, to) = split(daily, trades)
    s_all, s_is, s_oos = stats(daily, trades), stats(di, ti), stats(do, to)
    row = {"group": group, "name": cfg.name,
           **{f"is_{k}": v for k, v in s_is.items()}, **{f"oos_{k}": v for k, v in s_oos.items()},
           **{f"all_{k}": v for k, v in s_all.items() if k in ("sharpe", "cagr", "maxdd", "trades", "mean_trade")},
           "cfg": json.dumps({k: v for k, v in asdict(cfg).items() if k != "name"}, default=str)}
    if log:
        TRIALS.append(row)
    return row, daily, trades


def deflated_sharpe(sr_daily, n_obs, skew, kurt_excess, n_trials, var_sr_trials):
    """Bailey & Lopez de Prado (2014). Sharpe ratios per period (daily)."""
    g = 0.5772156649
    if n_trials > 1 and var_sr_trials > 0:
        sr0 = math.sqrt(var_sr_trials) * ((1 - g) * sst.norm.ppf(1 - 1 / n_trials)
                                          + g * sst.norm.ppf(1 - 1 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    k = kurt_excess + 3
    den = math.sqrt(max(1e-12, 1 - skew * sr_daily + (k - 1) / 4 * sr_daily ** 2))
    return float(sst.norm.cdf((sr_daily - sr0) * math.sqrt(n_obs - 1) / den)), sr0


def psr(daily_ret):
    r = np.asarray(daily_ret)
    sr = r.mean() / r.std(ddof=1)
    return deflated_sharpe(sr, len(r), pd.Series(r).skew(), pd.Series(r).kurt(), 1, 0)[0]


def daily_rank(ev, col):
    return ev.groupby("date")[col].rank(pct=True)


# ------------------------------------------------------------------ load
def load():
    ev = pd.read_parquet(os.path.join(CACHE, "events.parquet"))
    ev = prep(ev)
    ev["y"] = 1 - ev.x1555 / ev.p1555                     # gross short return, baseline hold
    ev["ext20"] = ev.p1545 / ev.sma20 - 1
    ev["logcap"] = np.log10(ev.mcap)
    ev["logadv"] = np.log10(ev.adv20)
    ev["gap_open"] = ev.o / ev.pc - 1
    ev["intraday"] = ev.p1545 / ev.o - 1                  # move made after the open
    ev["off_high"] = ev.p1545 / ev.hi - 1
    ev["cat_dil"] = (ev.cat_class == "dilution").astype(float)
    ev["cat_none"] = (ev.cat_class == "no_news").astype(float)
    ev["cat_real"] = (ev.cat_class == "real_catalyst").astype(float)
    ev["rank_in_scan"] = ev.rank1545
    ev["etb_now_f"] = (ev.etb_now == True).astype(float)          # today's Alpaca flag (look-ahead!)
    ev["ff_ret"] = ev.x1555 / ev.p1555 - 1                        # T+1 15:55 vs T 15:55
    ev["gap1"] = ev.o1 / ev.p1555 - 1                             # T+1 open vs entry
    for c in ["move_volume", "crowding", "ftd", "catalyst"]:
        ev[f"{c}_f"] = ev[c].fillna(ev.groupby("date")[c].transform("median")).fillna(0.5)
    days = sorted(ev.date[ev.date <= "2026-10-19"].unique())
    ev = ev[ev.date <= "2026-10-19"]
    return ev, days


# ------------------------------------------------------------------ Q2 components
FEATURES = ["short_score", "move_volume", "crowding", "ftd", "catalyst", "chg1545", "rvol", "wick", "cir",
            "dtc", "si_pct", "ftd_days", "n_news", "cat_dil", "cat_none", "cat_real", "atr14", "ext20",
            "ret5_prior", "gap_open", "intraday", "off_high", "logcap", "logadv", "p1545_raw", "rank_in_scan"]


def component_power(ev):
    base = Config()
    from engine import eligible
    e = ev[eligible(ev, replace(base, use_bot_exclusions=False)) & ev.y.notna()].copy()
    lo, hi = e.y.quantile([0.005, 0.995])
    e["yw"] = e.y.clip(lo, hi)
    rows = []
    for f in FEATURES:
        for per, sub in (("IS", e[e.date <= IS_END]), ("OOS", e[e.date >= OOS_START])):
            s = sub[["date", f, "yw"]].dropna()
            ics = []
            for d, g in s.groupby("date"):
                if len(g) >= 5 and g[f].nunique() > 1:
                    ics.append(sst.spearmanr(g[f], g.yw)[0])
            ics = np.array([x for x in ics if np.isfinite(x)])
            pooled = sst.spearmanr(s[f], s.yw)[0] if len(s) > 30 else np.nan
            q = pd.qcut(s[f].rank(method="first"), 5, labels=False) if len(s) > 100 else None
            qm = s.groupby(q).yw.mean() if q is not None else pd.Series(dtype=float)
            rows.append({"feature": f, "period": per, "n": len(s), "days": len(ics),
                         "mean_ic": ics.mean() if len(ics) else np.nan,
                         "ic_t": ics.mean() / ics.std(ddof=1) * math.sqrt(len(ics)) if len(ics) > 2 else np.nan,
                         "pooled_spearman": pooled,
                         "q1_mean": qm.iloc[0] if len(qm) else np.nan, "q5_mean": qm.iloc[-1] if len(qm) else np.nan,
                         "q5_minus_q1": (qm.iloc[-1] - qm.iloc[0]) if len(qm) else np.nan})
    cp = pd.DataFrame(rows)
    cp.to_csv(os.path.join(RESULTS, "component_ic.csv"), index=False)
    # catalyst classes
    cat = e.groupby([np.where(e.date <= IS_END, "IS", "OOS"), "cat_class"]).yw.agg(["count", "mean", "median",
                                                                                   lambda x: (x > 0).mean()])
    cat.columns = ["n", "mean_short_ret", "median", "win"]
    cat.to_csv(os.path.join(RESULTS, "catalyst_classes.csv"))
    # bot exclusions: what happened to the names the bot would have excluded
    exc = e.assign(exc=e.exclude_bot.fillna("eligible").str.split(":").str[0]).groupby("exc").yw.agg(["count", "mean", "median"])
    exc.to_csv(os.path.join(RESULTS, "exclusion_outcomes.csv"))
    return cp, cat, exc, e


def fit_recal(e):
    """IS-only weights: OLS of winsorised short return on daily-rank features (all 0..1)."""
    cols = ["move_volume_f", "crowding_f", "ftd_f", "catalyst_f"]
    s = e[e.date <= IS_END].copy()
    X = np.column_stack([s.groupby("date")[c].rank(pct=True).values for c in cols] + [np.ones(len(s))])
    beta, *_ = np.linalg.lstsq(X, s.yw.values, rcond=None)
    return dict(zip(cols, beta[:-1]))


# ------------------------------------------------------------------ main
def main():
    ev, days = load()
    print("events", len(ev), "days", len(days))
    cp, cat, exc, e = component_power(ev)
    print(cp[cp.feature.isin(["short_score", "move_volume", "crowding", "ftd", "catalyst", "chg1545"])].round(4).to_string())
    print(cat.round(4).to_string())
    print(exc.round(4).to_string())

    # ---- alternative scores
    w = fit_recal(e)
    print("recal weights", {k: round(v, 4) for k, v in w.items()})
    for c in ["move_volume_f", "crowding_f", "ftd_f", "catalyst_f"]:
        ev[c + "_r"] = ev.groupby("date")[c].rank(pct=True)
    ev["score_recal"] = sum(max(v, 0) * ev[c + "_r"] for c, v in w.items())
    # simple raw-feature scores, one per hypothesis (each is a trial)
    ev["score_chg"] = ev.chg1545
    ev["score_ext"] = ev.ext20
    ev["score_offhigh"] = -ev.off_high
    ev["score_rank"] = -ev.rank1545
    ev["score_random"] = np.random.default_rng(0).random(len(ev))
    subsets = {}
    comps = ["move_volume", "crowding", "ftd", "catalyst"]
    W = {"move_volume": 35, "crowding": 20, "ftd": 10, "catalyst": 35}
    for r in range(1, 5):
        for sub in itertools.combinations(comps, r):
            name = "score_" + "+".join(x[:3] for x in sub)
            num = sum(W[c] * ev[c].fillna(0) for c in sub)
            den = sum(W[c] * ev[c].notna() for c in sub)
            ev[name] = (100 * num / den.replace(0, np.nan)).fillna(0)
            subsets[name] = sub

    base = Config()
    results = []

    def sweep(group, cfgs):
        for c in cfgs:
            row, _, _ = evaluate(c, ev, days, group)
            results.append(row)
            print(f"{group:<10} {c.name:<28} IS SR {row.get('is_sharpe', 0):5.2f} CAGR {row.get('is_cagr', 0):7.1%} "
                  f"| OOS SR {row.get('oos_sharpe', 0):5.2f} CAGR {row.get('oos_cagr', 0):7.1%} DD {row.get('oos_maxdd', 0):6.1%}",
                  flush=True)

    # Q1 baseline + sensitivity (borrow proxy, costs) - sensitivity runs are not selection trials
    row_b, daily_b, trades_b = evaluate(base, ev, days, "Q1", log=True)
    results.append(row_b)
    print("BASELINE", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in row_b.items() if k != "cfg"})
    sens = []
    for nm, c in [("gate_none", replace(base, borrow_gate_price=0, borrow_gate_adv=0)),
                  ("gate_p3_adv1m", replace(base, borrow_gate_price=3, borrow_gate_adv=1e6)),
                  ("gate_p10_adv10m", replace(base, borrow_gate_price=10, borrow_gate_adv=10e6)),
                  ("gate_etb_today", replace(base, filters=(("etb_now_f", "==", 1.0),))),
                  ("no_costs", replace(base, cost_mult=0, borrow_base=0, borrow_htb=0)),
                  ("cost_x2", replace(base, cost_mult=2)),
                  ("borrow_low_5_25", replace(base, borrow_base=0.05, borrow_htb=0.25)),
                  ("borrow_high_30_100", replace(base, borrow_base=0.30, borrow_htb=1.00)),
                  ("no_bot_exclusions", replace(base, use_bot_exclusions=False))]:
        r, _, _ = evaluate(replace(c, name=nm), ev, days, "sens", log=False)
        sens.append(r)
        print(f"sens {nm:<22} IS SR {r.get('is_sharpe', 0):5.2f} mean {r.get('is_mean_trade', 0):+.4f} | "
              f"OOS SR {r.get('oos_sharpe', 0):5.2f} mean {r.get('oos_mean_trade', 0):+.4f}", flush=True)
    pd.DataFrame(sens).to_csv(os.path.join(RESULTS, "sensitivity_baseline.csv"), index=False)

    # Q2 scores
    sweep("Q2score", [replace(base, name=s, score=s) for s in
                      ["score_recal", "score_chg", "score_ext", "score_offhigh", "score_rank", "score_random"]
                      + list(subsets)])
    # Q3 breadth / thresholds
    sweep("Q3", [replace(base, name=f"n{n}", n=n) for n in (1, 3, 10, 20)]
          + [replace(base, name=f"min{t}", min_score=t) for t in (50, 60, 70)])
    # Q4 sizing
    sweep("Q4", [replace(base, name="equal85", sizing="equal", gross=0.85),
                 replace(base, name="equal50", sizing="equal", gross=0.50),
                 replace(base, name="equal25", sizing="equal", gross=0.25),
                 replace(base, name="atr85", sizing="atr", gross=0.85, max_name=0.25),
                 replace(base, name="atr50", sizing="atr", gross=0.50, max_name=0.15),
                 replace(base, name="n10_equal50", n=10, sizing="equal", gross=0.50),
                 replace(base, name="n10_atr50", n=10, sizing="atr", gross=0.50, max_name=0.08)])
    # Q5 risk controls
    sweep("Q5", [replace(base, name=f"stop{int(s*100)}", stop=s) for s in (0.10, 0.15, 0.25, 0.40)]
          + [replace(base, name=f"maxchg{int(m*100)}", max_chg=m) for m in (0.30, 0.50, 1.00)]
          + [replace(base, name="minchg20", min_chg=0.20),
             replace(base, name="skip_weekend", skip_weekend=True),
             replace(base, name="hedge50", hedge=0.5), replace(base, name="hedge100", hedge=1.0),
             replace(base, name="iwm_below_sma50", regime="iwm_below_sma50"),
             replace(base, name="iwm_above_sma50", regime="iwm_above_sma50"),
             replace(base, name="vix_above_20", regime="vix_above_20"),
             replace(base, name="vix_below_30", regime="vix_below_30")])
    # Q6 hold / entry
    sweep("Q6", [replace(base, name="exit_open1", exit="open1"),
                 replace(base, name="exit_close1", exit="close1"),
                 replace(base, name="exit_close2", exit="close2"),
                 replace(base, name="exit_close3", exit="close3"),
                 replace(base, name="exit_close5", exit="close5"),
                 replace(base, name="entry_close_exit_close1", entry="close", exit="close1"),
                 replace(base, name="entry_open1_exit_close1", entry="open1", exit="close1"),
                 replace(base, name="entry_open1_exit_close2", entry="open1", exit="close2")])
    # Q7 alternatives on the same signal
    sweep("Q7", [replace(base, name="failed_follow_through", entry="x1555", exit="close2",
                         filters=(("ff_ret", "<", 0.0),)),
                 replace(base, name="gapup_open_fade", entry="open1", exit="close1",
                         filters=(("gap1", ">", 0.0),))])

    pd.DataFrame(results).to_csv(os.path.join(RESULTS, "sweeps.csv"), index=False)
    return ev, days, results, daily_b, trades_b


if __name__ == "__main__":
    main()
