"""Stage 2: one-at-a-time sweeps (analyze.main), then a pre-declared combined grid,
IS selection, OOS validation, deflated Sharpe, rolling walk-forward, plots.

    python3 research/backtest/final.py
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402
from analyze import IS_END, OOS_START, TRIALS, deflated_sharpe, evaluate, psr  # noqa: E402
from common import OUT, RESULTS  # noqa: E402
from engine import Config, run, stats  # noqa: E402
from plotstyle import AMBER, GREEN, GREY, RED, WHITE, plt  # noqa: E402


def best_in_group(results, group, extra=()):
    rows = [r for r in results if r["group"] == group] + list(extra)
    return max(rows, key=lambda r: r.get("is_sharpe", -9))


def by_year(daily):
    d = daily.assign(y=daily.date.str[:4])
    return d.groupby("y").ret.apply(lambda r: np.prod(1 + r) - 1)


def main():
    ev, days, results, daily_b, trades_b = analyze.main()
    base = Config()
    row_b = results[0]

    # ---- pre-declared combined grid; the two "best of" slots are chosen on IS only
    best_score = best_in_group(results, "Q2score", [row_b])["name"]
    best_score = "short_score" if best_score == "baseline" else best_score
    best_q6 = best_in_group(results, "Q6", [row_b])
    best_q7 = best_in_group(results, "Q7", [row_b])
    print("IS-best score:", best_score, "| IS-best hold:", best_q6["name"], "| IS-best alt:", best_q7["name"])
    scores = list(dict.fromkeys(["short_score", "score_recal", best_score]))
    holds = [("base", "1555", "1555n", ())]
    for r in (best_q6, best_q7):
        c = json.loads(r["cfg"])
        h = (r["name"], c["entry"], c["exit"], tuple(tuple(f) for f in c["filters"]))
        if h[1:] not in [x[1:] for x in holds]:
            holds.append(h)
    grid = []
    for sc, n, sz, stop, mc, wk, (hn, en, xt, flt) in itertools.product(
            scores, (3, 5, 10), ("bot", "equal50", "atr50"), (None, 0.25, 0.40), (None, 0.20), (False, True), holds):
        kw = dict(score=sc, n=n, stop=stop, min_chg=mc, skip_weekend=wk, entry=en, exit=xt, filters=flt)
        if sz == "bot":
            kw.update(sizing="bot")
        elif sz == "equal50":
            kw.update(sizing="equal", gross=0.5)
        else:
            kw.update(sizing="atr", gross=0.5, max_name=min(0.25, 1.5 * 0.5 / n))
        name = f"{sc}|n{n}|{sz}|stop{stop}|minchg{mc}|wk{wk}|{hn}"
        grid.append(replace(base, name=name, **kw))
    print("grid size", len(grid), flush=True)
    series = {}
    for i, c in enumerate(grid):
        row, daily, trades = evaluate(c, ev, days, "grid")
        series[c.name] = daily.set_index("date").ret
        if i % 50 == 0:
            print(i, c.name, round(row.get("is_sharpe", 0), 2), flush=True)

    trials = pd.DataFrame(TRIALS)
    trials.to_csv(os.path.join(RESULTS, "trials.csv"), index=False)
    gr = trials[trials.group == "grid"].sort_values("is_sharpe", ascending=False)
    gr.to_csv(os.path.join(RESULTS, "grid.csv"), index=False)

    # ---- selection on IS only: best IS Sharpe among configs with >= 300 active IS days
    elig = trials[(trials.is_active_days >= 300) & (~trials.is_ruined.astype(bool))]
    pick = elig.sort_values("is_sharpe", ascending=False).iloc[0]
    cfg_best = Config(name="best_is", **json.loads(pick.cfg))
    # robust pick: same config family whose 1-step neighbours are also good (plateau)
    gs = gr.set_index("name").is_sharpe
    def neigh_mean(nm):
        parts = nm.split("|")
        vals = [gs[o] for o in gs.index if sum(a != b for a, b in zip(o.split("|"), parts)) == 1]
        return np.mean(vals) if vals else np.nan
    gr = gr.assign(neigh=[neigh_mean(n) for n in gr.name])
    gr["plateau"] = 0.5 * gr.is_sharpe + 0.5 * gr.neigh
    rob = gr[(gr.is_active_days >= 300) & (~gr.is_ruined.astype(bool))].sort_values("plateau", ascending=False).iloc[0]
    cfg_rob = Config(name="robust_is", **json.loads(rob.cfg))
    gr.to_csv(os.path.join(RESULTS, "grid.csv"), index=False)

    out = {}
    for label, c in (("baseline", base), ("best_is", cfg_best), ("robust_is", cfg_rob)):
        d, t = run(c, ev, days)
        (di, ti), (do, to) = analyze.split(d, t)
        out[label] = {"cfg": c, "daily": d, "trades": t, "is": stats(di, ti), "oos": stats(do, to),
                      "all": stats(d, t), "psr_oos": psr(do.ret.values) if len(do) else np.nan}
        t.to_csv(os.path.join(RESULTS, f"trades_{label}.csv"), index=False)
        d.to_csv(os.path.join(RESULTS, f"daily_{label}.csv"), index=False)

    # deflated Sharpe on IS for the selected configs (N = all trials logged)
    n_trials = len(trials)
    sr_daily_trials = trials.is_sharpe.dropna() / np.sqrt(252)
    var_sr = float(sr_daily_trials.var())
    dsr = {}
    for label in ("baseline", "best_is", "robust_is"):
        d = out[label]["daily"]
        di = d[d.date <= IS_END].ret
        sr = di.mean() / di.std(ddof=1)
        dsr[label] = deflated_sharpe(sr, len(di), di.skew(), di.kurt(), n_trials, var_sr)
    summary = []
    for label, o in out.items():
        for per in ("is", "oos", "all"):
            summary.append({"config": label, "period": per, **o[per]})
    sm = pd.DataFrame(summary)
    sm.to_csv(os.path.join(RESULTS, "summary.csv"), index=False)
    yr = pd.DataFrame({k: by_year(o["daily"]) for k, o in out.items()})
    yr.to_csv(os.path.join(RESULTS, "by_year.csv"))

    # ---- rolling walk-forward: each year, pick the grid config with the best Sharpe over the prior 3 years
    S = pd.DataFrame(series)
    S.index = pd.Index(S.index, name="date")
    wf, picks = [], []
    for y in range(2019, 2027):
        tr = S[(S.index >= f"{y-3}-01-01") & (S.index <= f"{y-1}-12-31")]
        te = S[(S.index >= f"{y}-01-01") & (S.index <= f"{y}-12-31")]
        sh = tr.mean() / tr.std().replace(0, np.nan)
        best = sh.idxmax()
        picks.append({"year": y, "chosen": best, "train_sharpe": sh.max() * np.sqrt(252),
                      "test_return": float(np.prod(1 + te[best]) - 1),
                      "test_sharpe": float(te[best].mean() / te[best].std() * np.sqrt(252)) if te[best].std() > 0 else 0,
                      "baseline_return": float(np.prod(1 + daily_b.set_index("date").ret.reindex(te.index).fillna(0)) - 1)})
        wf.append(te[best])
    wf = pd.concat(wf)
    pd.DataFrame(picks).to_csv(os.path.join(RESULTS, "wf_rolling.csv"), index=False)
    wf_stats = stats(pd.DataFrame({"ret": wf.values, "n": 1}))

    # ---- plots
    def eq(d):
        return d.set_index("date").ret.add(1).cumprod()
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for label, col in (("baseline", RED), ("best_is", AMBER), ("robust_is", GREEN)):
        e = eq(out[label]["daily"])
        ax.plot(pd.to_datetime(e.index), e.values, color=col, lw=1.3, label=f"{label}")
    w = (1 + wf).cumprod()
    ax.plot(pd.to_datetime(w.index), w.values, color=WHITE, lw=1, ls="--", label="rolling WF (2019+)")
    ax.axvline(pd.Timestamp(OOS_START), color=GREY, ls=":")
    ax.text(pd.Timestamp(OOS_START), ax.get_ylim()[1], "  OOS ->", color=GREY, va="top")
    ax.set_yscale("log"); ax.set_title("EQUITY (net of costs, log scale)"); ax.legend(loc="upper left")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "equity_curves.png"), dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 4))
    for label, col in (("baseline", RED), ("best_is", AMBER), ("robust_is", GREEN)):
        e = eq(out[label]["daily"]); dd = e / e.cummax() - 1
        ax.fill_between(pd.to_datetime(dd.index), dd.values, 0, color=col, alpha=0.35, label=label)
    ax.set_title("DRAWDOWN"); ax.legend(loc="lower left")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "drawdown.png"), dpi=130); plt.close(fig)

    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, label in zip(axs, ("baseline", "robust_is")):
        t = out[label]["trades"].net_ret.clip(-1, 0.6) * 100
        ax.hist(t[t >= 0], bins=np.arange(0, 61, 2), color=GREEN)
        ax.hist(t[t < 0], bins=np.arange(-100, 1, 2), color=RED)
        ax.set_yscale("log")
        ax.set_title(f"TRADE NET RETURN % - {label}  (mean {t.mean():+.2f}%, n={len(t)})")
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "trade_hist.png"), dpi=130); plt.close(fig)

    fig, ax = plt.subplots(figsize=(12, 4.5))
    x = np.arange(len(yr)); wd = 0.27
    for i, (label, col) in enumerate((("baseline", RED), ("best_is", AMBER), ("robust_is", GREEN))):
        ax.bar(x + (i - 1) * wd, yr[label].values * 100, wd, color=col, label=label)
    ax.set_xticks(x, yr.index); ax.axhline(0, color=WHITE, lw=0.6); ax.set_title("RETURN BY YEAR %")
    ax.legend(); fig.tight_layout(); fig.savefig(os.path.join(OUT, "by_year.png"), dpi=130); plt.close(fig)

    cp = pd.read_csv(os.path.join(RESULTS, "component_ic.csv"))
    pv = cp.pivot(index="feature", columns="period", values="mean_ic").sort_values("IS")
    fig, ax = plt.subplots(figsize=(10, 8))
    y = np.arange(len(pv))
    ax.barh(y + 0.2, pv.IS, 0.4, color=AMBER, label="IS 2016-21")
    ax.barh(y - 0.2, pv.OOS, 0.4, color=WHITE, label="OOS 2022-26")
    ax.set_yticks(y, pv.index); ax.axvline(0, color=GREY)
    ax.set_title("PREDICTIVE POWER: mean daily rank IC vs next-day short return\n(+ = higher value -> better short)")
    ax.legend(); fig.tight_layout(); fig.savefig(os.path.join(OUT, "component_ic.png"), dpi=130); plt.close(fig)

    report = {"n_trials": n_trials, "var_sr_daily": var_sr,
              "dsr": {k: {"dsr": v[0], "sr0_daily": v[1]} for k, v in dsr.items()},
              "best_is": pick["name"], "robust_is": rob["name"],
              "psr_oos": {k: o["psr_oos"] for k, o in out.items()},
              "wf_rolling": wf_stats}
    json.dump(report, open(os.path.join(RESULTS, "selection.json"), "w"), indent=2, default=float)
    print(json.dumps(report, indent=2, default=float))
    print(sm.round(4).to_string())
    print(yr.round(3).to_string())
    print(pd.DataFrame(picks).to_string())


if __name__ == "__main__":
    main()
