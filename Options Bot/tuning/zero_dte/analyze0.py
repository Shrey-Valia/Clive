"""Statistics, results.csv and terminal-styled charts for tests 1-3.

Inference: date-clustered SEs (trades aggregated to one mean per date, so the effective
N is the number of days) and a date-block bootstrap (B=4000) 95% CI of the mean.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
SPLIT = "2025-07-01"
RNG = np.random.default_rng(7)
B = 4000

# ------------------------------------------------------------------ statistics


def stats(df: pd.DataFrame, col="ret", per_year=252.0) -> dict:
    if df is None or len(df) == 0:
        return {"n": 0}
    g = df.groupby("date")[col].mean()
    x = g.to_numpy(float)
    n = len(x)
    m = x.mean()
    sd = x.std(ddof=1) if n > 1 else np.nan
    se = sd / np.sqrt(n) if n > 1 else np.nan
    bs = RNG.choice(x, size=(B, n), replace=True).mean(1) if n > 1 else np.array([np.nan])
    out = {"n": n, "mean": m, "se": se, "t": m / se if se and se > 0 else np.nan,
           "ci_lo": np.nanpercentile(bs, 2.5), "ci_hi": np.nanpercentile(bs, 97.5),
           "win": float((x > 0).mean()), "sr_ann": m / sd * np.sqrt(per_year) if sd and sd > 0 else np.nan}
    for c in ("pnl_usd", "pnl_bp", "mae_ret"):
        if c in df:
            out[f"mean_{c}"] = float(df.groupby("date")[c].mean().mean())
    if "mae_ret" in df:
        out["mae_p05"] = float(df.groupby("date")["mae_ret"].mean().quantile(0.05))
    return out


def paired(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    """Event-minus-matched difference; a, b indexed by event date (b = its matched day)."""
    d = (a - b).dropna().to_numpy(float)
    n = len(d)
    if n < 2:
        return {"n_pairs": n}
    bs = RNG.choice(d, size=(B, n), replace=True).mean(1)
    se = d.std(ddof=1) / np.sqrt(n)
    return {"n_pairs": n, "diff": d.mean(), "diff_t": d.mean() / se,
            "diff_ci_lo": np.percentile(bs, 2.5), "diff_ci_hi": np.percentile(bs, 97.5)}


# ------------------------------------------------------------------ helpers
def period_of(s):
    return np.where(pd.to_datetime(s) >= pd.Timestamp(SPLIT), "OOS", "IS")


def family_rows(t: pd.DataFrame, test: str, subset: str, cfgs, per_year, mode="vw"):
    rows = []
    for cfg in cfgs:
        c = t[(t.cfg == cfg) & (t["mode"] == mode)]
        for per in ("IS", "OOS", "ALL"):
            cp = c if per == "ALL" else c[c.period == per]
            for sl in sorted(cp.slip.unique()):
                s = stats(cp[cp.slip == sl], per_year=per_year)
                rows.append({"test": test, "subset": subset, "cfg": cfg, "mode": mode,
                             "period": per, "slip": sl, **s})
    return rows


def select(rows, test, subset):
    r = pd.DataFrame(rows)
    is1 = r[(r.test == test) & (r.subset == subset) & (r.period == "IS") & (r.slip == 1.0) & (r["mode"] == "vw")]
    is1 = is1[is1.n >= 5]
    return is1.sort_values("mean", ascending=False).cfg.iloc[0] if len(is1) else None


# ------------------------------------------------------------------ main
def main():
    t1 = pd.read_csv(HERE / "trades_t1.csv").fillna({"event": ""})
    tf = pd.read_csv(HERE / "trades_fomc.csv").fillna({"event": ""})
    sig = pd.read_csv(HERE / "signals.csv", parse_dates=["date"]).set_index("date")
    t1 = t1[t1.ok == True].copy()  # noqa: E712
    tf = tf[tf.ok == True].copy()  # noqa: E712
    fail = pd.read_csv(HERE / "trades_t1.csv")
    fail_rate = fail[(fail["mode"] == "vw") & (fail.slip == 1.0)].ok.eq(False).mean()
    rows, trials = [], {}

    # ---- test 1: unconditional, ordinary (non-event) days
    ordn = t1[t1.kind == "ordinary"]
    cfgs1 = sorted(t1.cfg.unique())
    rows += family_rows(ordn, "T1", "ordinary", cfgs1, 252)
    rows += family_rows(ordn, "T1", "ordinary", cfgs1, 252, mode="c")
    trials["T1"] = len(cfgs1)

    # ---- test 2a: FOMC 10:00 fly, early exits vs hold, on FOMC days + matched ordinary days
    fomc = tf[tf.event.str.contains("FOMC")]
    cfgs_f = sorted(tf.cfg.unique())
    rows += family_rows(fomc, "T2_FOMC", "FOMC", cfgs_f, 8)
    rows += family_rows(tf[tf.kind == "ordinary"], "T2_FOMC", "ordinary_same_cfg", cfgs_f, 252)
    trials["T2_FOMC"] = len(cfgs_f)

    # ---- test 2b: CPI / NFP days, 10:00 entries (all 10:00 test-1 configs)
    cfgs_d = [c for c in cfgs1 if "|10:00|" in c]
    for ev, per_year in (("CPI", 12), ("NFP", 12)):
        sub = t1[t1.event.str.contains(ev)]
        rows += family_rows(sub, "T2_DATA", ev, cfgs_d, per_year)
    data = t1[t1.event.str.contains("CPI|NFP")]
    rows += family_rows(data, "T2_DATA", "CPI+NFP", cfgs_d, 24)
    trials["T2_DATA"] = len(cfgs_d)

    # matched non-event comparisons (nearest ordinary sample day in calendar time)
    ord_dates = pd.to_datetime(pd.Series(sorted(ordn.date.unique())))
    match_rows = []

    def nearest(dd):
        dd = pd.Timestamp(dd)
        return ord_dates.iloc[(ord_dates - dd).abs().argmin()].strftime("%Y-%m-%d")

    for label, src, evmask, cfgset in (("FOMC", tf, "FOMC", cfgs_f), ("CPI+NFP", t1, "CPI|NFP", cfgs_d)):
        for cfg in cfgset:
            c = src[(src.cfg == cfg) & (src["mode"] == "vw") & (src.slip == 1.0)]
            ev = c[c.event.str.contains(evmask)].set_index("date").ret
            oc = c[c.kind == "ordinary"].set_index("date").ret
            m = pd.Series({dt: oc.get(nearest(dt), np.nan) for dt in ev.index})
            for per in ("IS", "OOS", "ALL"):
                keep = ev.index if per == "ALL" else ev.index[period_of(ev.index) == per]
                match_rows.append({"test": "T2_match", "subset": label, "cfg": cfg, "period": per,
                                   "slip": 1.0, "mode": "vw", "event_mean": ev[keep].mean(),
                                   "matched_mean": m[keep].mean(), **paired(ev[keep], m[keep])})

    # ---- test 3: VRP filter (top IS tercile) + skip |gap| > 1 sigma
    t1 = t1.join(sig[["gap_z"] + [c for c in sig.columns if c.startswith(("vrp_vix", "har_"))]], on=pd.to_datetime(t1.date))
    for ent in ("10:00", "12:00", "14:00"):
        mk = t1.entry == ent
        t1.loc[mk, "sig_vix"] = t1.loc[mk, f"vrp_vix_{ent}"]
        t1.loc[mk, "sig_std"] = (t1.loc[mk, "sig_move"] / t1.loc[mk, "S"]) ** 2 - t1.loc[mk, f"har_{ent}"]
    sig_is = sig[sig.index < SPLIT]
    thr = {}
    for ent in ("10:00", "12:00", "14:00"):
        thr[("sig_vix", ent)] = sig_is[f"vrp_vix_{ent}"].quantile(2 / 3)
        base = t1[(t1.entry == ent) & (t1.period == "IS") & (t1.cfg.str.startswith("fly|1.0x")) &
                  (t1.slip == 1.0) & (t1["mode"] == "vw")].drop_duplicates("date")
        thr[("sig_std", ent)] = base.sig_std.quantile(2 / 3)
    for sname in ("sig_vix", "sig_std"):
        keep = np.zeros(len(t1), bool)
        for ent in ("10:00", "12:00", "14:00"):
            mk = (t1.entry == ent).to_numpy()
            keep |= mk & (t1[sname] >= thr[(sname, ent)]).to_numpy() & (t1.gap_z <= 1.0).to_numpy()
        filt = t1[keep]
        rows += family_rows(filt[filt.kind == "ordinary"], "T3", f"{sname}_top3_ordinary", cfgs1, 84)
        rows += family_rows(filt, "T3", f"{sname}_top3_allsample", cfgs1, 84)
        # bottom-two-tercile complement for contrast
        comp = t1[~keep & (t1.kind == "ordinary")]
        rows += family_rows(comp, "T3", f"{sname}_rest_ordinary", cfgs1, 168)
    trials["T3"] = 2 * len(cfgs1)

    res = pd.DataFrame(rows)
    sel = {}
    for test, subset in (("T1", "ordinary"), ("T2_FOMC", "FOMC"), ("T2_DATA", "CPI+NFP"),
                         ("T3", "sig_vix_top3_ordinary"), ("T3", "sig_std_top3_ordinary")):
        sel[(test, subset)] = select(rows, test, subset)
    res["selected"] = [sel.get((a, b)) == c for a, b, c in zip(res.test, res.subset, res.cfg)]
    res["trials_in_family"] = res.test.map(trials)
    res = pd.concat([res, pd.DataFrame(match_rows)], ignore_index=True)
    res.to_csv(HERE / "results.csv", index=False)
    total = sum(trials.values())
    print("trials", trials, "total", total, "vw/1x fail rate", round(fail_rate, 4))
    print("thresholds", {f"{k[0]}@{k[1]}": f"{v:.2e}" for k, v in thr.items()})
    print("selected", {f"{k[0]}/{k[1]}": v for k, v in sel.items()})
    pd.to_pickle({"sel": sel, "trials": trials, "thr": thr}, HERE / ".cache" / "meta.pkl")
    t1.to_csv(HERE / ".cache" / "t1_with_signals.csv", index=False)
    charts(res, t1, tf, sel)


# ------------------------------------------------------------------ charts
BG, AMBER, WHITE, GREEN, RED, GREY = "#000000", "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#666666"


def style():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
                         "axes.edgecolor": GREY, "axes.labelcolor": WHITE, "text.color": WHITE,
                         "xtick.color": WHITE, "ytick.color": WHITE, "grid.color": "#222222",
                         "font.family": "monospace", "font.size": 10, "axes.grid": True,
                         "axes.titlecolor": AMBER, "axes.titleweight": "bold", "legend.frameon": False})
    return plt


def bar_ci(ax, xs, r, color, label, off):
    m = r["mean"].to_numpy() * 100
    lo = (r["mean"] - r.ci_lo).to_numpy() * 100
    hi = (r.ci_hi - r["mean"]).to_numpy() * 100
    ax.bar(xs + off, m, 0.38, color=color, label=label, alpha=0.9)
    ax.errorbar(xs + off, m, yerr=[lo, hi], fmt="none", ecolor=WHITE, elinewidth=1, capsize=3)


def charts(res, t1, tf, sel):
    plt = style()
    r = res[(res["mode"] == "vw") & (res.slip == 1.0) & (res.test == "T1") & (res.subset == "ordinary")]
    # 1. P&L by entry time, hold-to-15:25, per structure, IS vs OOS
    structs = ["fly|1.0x", "fly|1.5x", "fly|$5", "fly|$10", "ic|1.5x", "ic|$5", "ic|$10"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=True)
    for ax, ent in zip(axes, ["10:00", "12:00", "14:00"]):
        xs = np.arange(len(structs))
        for per, col, off in (("IS", AMBER, -0.2), ("OOS", GREEN, 0.2)):
            rr = pd.DataFrame([r[(r.cfg == f"{s}|{ent}|hold") & (r.period == per)].iloc[0] for s in structs])
            bar_ci(ax, xs, rr, col, per, off)
        ax.axhline(0, color=WHITE, lw=0.8)
        ax.set_xticks(xs, structs, rotation=45, ha="right")
        ax.set_title(f"ENTRY {ent} -> EXIT 15:25")
    axes[0].set_ylabel("mean P&L, % of max risk (1x cost)")
    axes[0].legend(loc="lower left")
    fig.suptitle("SPY 0DTE | non-event days | 95% date-bootstrap CI", color=WHITE)
    fig.tight_layout()
    fig.savefig(OUT / "pnl_by_entry.png", dpi=130)
    plt.close(fig)

    # 2. event vs non-event (10:00 ATM fly 1.0x)
    fig, ax = plt.subplots(figsize=(11, 5))
    labels, vals, cis, cols = [], [], [], []
    tfv = tf[(tf["mode"] == "vw") & (tf.slip == 1.0)]
    t1v = t1[(t1["mode"] == "vw") & (t1.slip == 1.0)]
    groups = [("FOMC x14:05", tfv[(tfv.cfg == "fly|1.0x|10:00|x14:05") & tfv.event.str.contains("FOMC")]),
              ("FOMC x15:25", tfv[(tfv.cfg == "fly|1.0x|10:00|x15:25") & tfv.event.str.contains("FOMC")]),
              ("ordinary x14:05", tfv[(tfv.cfg == "fly|1.0x|10:00|x14:05") & (tfv.kind == "ordinary")]),
              ("CPI", t1v[(t1v.cfg == "fly|1.0x|10:00|hold") & t1v.event.str.contains("CPI")]),
              ("NFP", t1v[(t1v.cfg == "fly|1.0x|10:00|hold") & t1v.event.str.contains("NFP")]),
              ("ordinary", t1v[(t1v.cfg == "fly|1.0x|10:00|hold") & (t1v.kind == "ordinary")])]
    for lab, g in groups:
        s = stats(g)
        labels.append(f"{lab}\nn={s['n']}")
        vals.append(s["mean"] * 100)
        cis.append([(s["mean"] - s["ci_lo"]) * 100, (s["ci_hi"] - s["mean"]) * 100])
        cols.append(GREEN if s["mean"] > 0 else RED)
    xs = np.arange(len(labels))
    ax.bar(xs, vals, color=cols)
    ax.errorbar(xs, vals, yerr=np.array(cis).T, fmt="none", ecolor=WHITE, capsize=4)
    ax.axhline(0, color=WHITE, lw=0.8)
    ax.set_xticks(xs, labels)
    ax.set_ylabel("mean P&L, % of max risk")
    ax.set_title("ATM IRON FLY (WINGS 1.0 SIGMA), ENTRY 10:00, 1x COST, ALL YEARS")
    fig.tight_layout()
    fig.savefig(OUT / "event_vs_nonevent.png", dpi=130)
    plt.close(fig)

    # 3. cost sensitivity (OOS, ordinary): selected + a few anchors
    fig, ax = plt.subplots(figsize=(9, 5))
    rr = res[(res.test == "T1") & (res.subset == "ordinary") & (res["mode"] == "vw") & (res.period == "OOS")]
    anchors = [sel[("T1", "ordinary")], "fly|1.0x|10:00|hold", "fly|$5|12:00|hold", "ic|$5|10:00|hold",
               "ic|1.5x|14:00|hold", "fly|1.0x|14:00|pt25"]
    pal = [AMBER, WHITE, GREEN, RED, "#4fc3f7", "#c792ea"]
    for cfg, col in zip(dict.fromkeys(anchors), pal):
        q = rr[rr.cfg == cfg].sort_values("slip")
        ax.plot(q.slip, q["mean"] * 100, "-o", color=col, label=cfg + (" [IS pick]" if cfg == anchors[0] else ""))
    ax.axhline(0, color=WHITE, lw=0.8)
    ax.set_xticks([0, 1, 2], ["0x", "1x", "2x"])
    ax.set_xlabel("per-leg slippage multiple (1x = \\$0.01/0.02/0.03 by price tier) + \\$0.03/contract fees")
    ax.set_ylabel("OOS mean P&L, % of max risk")
    ax.set_title("COST SENSITIVITY | OOS 2025-07..2026-10 | non-event days")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "cost_sensitivity.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
