#!/usr/bin/env python3
"""Selection (IS only), frozen OOS read-out, deflated Sharpe, results.csv, finalists.json, plots.

    python3 tuning/newrules/analyze.py
"""
from __future__ import annotations

import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hype_fade as H                                                   # noqa: E402
import lib as L                                                         # noqa: E402
import run_rules as R                                                   # noqa: E402
from optionsbot import metrics as M                                    # noqa: E402
from optionsbot import strategies_newrules as NR                       # noqa: E402

AMBER, WHITE, GREEN, RED, GREY = "#ffa028", "#ffffff", "#3ddc84", "#ff4d4d", "#777777"


# ------------------------------------------------------------------ rule 5 rows
def hype_rows():
    f = L.CACHE / "hype_trades.pkl"
    if not f.exists():
        return [], {}, {}
    base = pickle.loads(f.read_bytes())
    sets = {"hype": {"hype"}, "stale": {"stale"}, "hype_stale": {"hype", "stale"},
            "nonfade": {"real", "other"}, "random": {"random"}}
    rows, curves, trades = [], {}, {}
    for (entry, dl), lst in base.items():
        for sname, labs in sets.items():
            for cm, cname in ((1, "default"), (2, "double")):
                if cname == "double" and sname not in ("hype", "hype_stale", "stale"):
                    continue
                sel = [t for t in lst if t["label"] in labs]
                eq, tr = H.portfolio(sel, H.costs(cm))
                counted = sname in ("hype", "stale", "hype_stale") and cname == "default"
                rule = "R5_hype" if sname in ("hype", "stale", "hype_stale") else "R5_control"
                if cname == "double":
                    rule = "DIAG"
                name = f"{sname}_{entry}_{int(dl * 100)}d" + ("" if cname == "default" else "_double")
                row = {"rule": rule, "variant": name, "strategy": "bear_call_spread", "underlyings": "single names",
                       "capital": 100_000, "counted_trial": counted, "note": f"{len(sel)} signals",
                       "params": json.dumps({"labels": sorted(labs), "entry": entry, "short_delta": dl,
                                             "long_delta": 0.10, "dte": "14-30", "tp": 0.5, "max_sessions": 5,
                                             "risk_per_trade": 0.02, "cost_mult": cm}), "lag": 0, "core": L.CORE}
                if len(tr):
                    tr["entry_date"] = pd.to_datetime(tr["entry_date"])
                for tag, (a, z) in {"full": (None, None), "is": (None, L.IS_END), "oos": (L.OOS_START, None)}.items():
                    m = L.em(eq, a, z)
                    row.update({f"{tag}_cagr": m.get("cagr"), f"{tag}_sharpe": m.get("sharpe"),
                                f"{tag}_sortino": m.get("sortino"), f"{tag}_maxdd": m.get("max_drawdown"),
                                f"{tag}_ret": m.get("total_return")})
                    t = tr if (tag == "full" or not len(tr)) else (tr[tr.entry_date <= L.IS_END] if tag == "is"
                                                                  else tr[tr.entry_date >= L.OOS_START])
                    n = len(t)
                    win = t[t.pnl > 0].pnl.sum() if n else 0
                    loss = -t[t.pnl < 0].pnl.sum() if n else 0
                    row.update({f"{tag}_trades": n, f"{tag}_win": float((t.pnl > 0).mean()) if n else np.nan,
                                f"{tag}_pf": float(win / loss) if loss > 0 else None,
                                f"{tag}_pnl": float(t.pnl.sum()) if n else 0.0,
                                f"{tag}_tr_mean": float(t.ret.mean()) if n else np.nan,
                                f"{tag}_worst_tr": float(t.ret.min()) if n else np.nan})
                    if n >= 3:
                        row[f"{tag}_tr_t"] = float(t.ret.mean() / (t.ret.std(ddof=1) / math.sqrt(n)))
                rows.append(row)
                curves[(rule, name)] = eq
                trades[(rule, name)] = tr
    return rows, curves, trades


# ------------------------------------------------------------------ helpers
def payload(id_, name, und, cap, eq, tr_stats, params, is_m, oos_m, status, extra=None):
    eq = eq.dropna()
    dd = M.drawdown(eq)
    mr = M.monthly_returns(eq)
    m = M.equity_metrics(eq)
    step = max(1, len(eq) // 400)
    return {"id": id_, "name": name, "underlying": und, "capital": cap, "status": status,
            "metrics": {"cagr": m.get("cagr"), "total_return": m.get("total_return"),
                        "max_drawdown": m.get("max_drawdown"), "sharpe": m.get("sharpe"),
                        "sortino": m.get("sortino"), **tr_stats},
            "equity": [{"date": str(d.date()), "value": round(float(v), 2)} for d, v in eq.iloc[::step].items()],
            "drawdown": [{"date": str(d.date()), "value": round(float(v), 4)} for d, v in dd.iloc[::step].items()],
            "monthly_returns": [{"month": str(k)[:7], "ret": round(float(v), 5)} for k, v in mr.items()],
            "params": params, "is_metrics": is_m, "oos_metrics": oos_m, **(extra or {})}


def seg(row, tag):
    pf = row.get(f"{tag}_pf")
    return {"cagr": row.get(f"{tag}_cagr"), "total_return": row.get(f"{tag}_ret"),
            "max_drawdown": row.get(f"{tag}_maxdd"), "sharpe": row.get(f"{tag}_sharpe"),
            "sortino": row.get(f"{tag}_sortino"), "win_rate": row.get(f"{tag}_win"),
            "profit_factor": None if (pf is None or (isinstance(pf, float) and not np.isfinite(pf))) else pf,
            "trades": row.get(f"{tag}_trades")}


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    return o


def style(ax, fig):
    import matplotlib.pyplot as plt  # noqa: F401
    fig.patch.set_facecolor("black")
    ax.set_facecolor("black")
    for s in ax.spines.values():
        s.set_color(GREY)
    ax.tick_params(colors=WHITE, labelsize=8)
    ax.xaxis.label.set_color(WHITE)
    ax.yaxis.label.set_color(WHITE)
    ax.title.set_color(AMBER)
    ax.grid(color="#222222", lw=0.6)


# ------------------------------------------------------------------ main
def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "monospace"

    E = pickle.loads((L.CACHE / "rows_engine.pkl").read_bytes())
    hr, hc, ht = hype_rows()
    rows = E["rows"] + hr
    curves = {**E["curves"], **hc}
    df = pd.DataFrame(rows)
    df["family"] = df["rule"]
    m4 = df.rule == "R4_news"
    df.loc[m4, "family"] = np.where(df.loc[m4, "variant"].str.startswith("dip_"), "R4_news_dip", "R4_news_ic")

    counted = df[df.counted_trial.astype(bool)]
    N_all = len(counted)
    is_sr = {}
    for _, r in counted.iterrows():
        is_sr[(r.rule, r.variant)] = L.daily_sr(curves[(r.rule, r.variant)][:L.IS_END])
    srs = np.array(list(is_sr.values()))

    ref = {"R1_vrp": ("BASE", "dip_pcs_1555"), "R2_ts": ("BASE", "dip_pcs_1555"),
           "R3_timing": ("BASE", "dip_pcs_1555"), "R4_news_dip": ("BASE", "dip_pcs_1555"),
           "R2_condor": ("R2_condor", "ic_60d_10d"), "R4_news_ic": ("R4_news", "ic_none")}
    fam_rows = []
    selected = {}
    for fam in ["R1_vrp", "R2_ts", "R2_condor", "R3_timing", "R4_news_dip", "R4_news_ic", "R5_hype"]:
        g = df[(df.family == fam) & df.counted_trial.astype(bool)]
        if g.empty:
            continue
        best = g.sort_values("is_sharpe", ascending=False).iloc[0]
        selected[fam] = best
        r_ = best
        eq = curves[(r_.rule, r_.variant)]
        d_all = L.dsr(eq[:L.IS_END].pct_change().dropna(), srs, N_all)
        fam_srs = np.array([is_sr[(x.rule, x.variant)] for _, x in g.iterrows()])
        d_fam = L.dsr(eq[:L.IS_END].pct_change().dropna(), fam_srs, len(g))
        d_full = L.dsr(eq.pct_change().dropna(), np.array([L.daily_sr(curves[(x.rule, x.variant)]) for _, x in counted.iterrows()]), N_all)
        oos_psr = L.dsr(eq[L.OOS_START:].pct_change().dropna(), np.array([0.0]), 1)["psr_vs0"]
        rk = df[(df.family == fam)][["is_sharpe", "oos_sharpe"]].dropna()
        spearman = float(rk.is_sharpe.rank().corr(rk.oos_sharpe.rank())) if len(rk) > 2 else np.nan
        if fam in ref:
            rr = df[(df.rule == ref[fam][0]) & (df.variant == ref[fam][1])].iloc[0]
        else:   # R5: random-date control, same entry / delta
            parts = best.variant.split("_")
            ctl = f"random_{parts[-2]}_{parts[-1]}"
            rr = df[(df.rule == "R5_control") & (df.variant == ctl)].iloc[0]
        fam_rows.append({"family": fam, "n_variants": len(g), "selected": best.variant,
                         "sel_is_sharpe": best.is_sharpe, "sel_oos_sharpe": best.oos_sharpe,
                         "sel_is_dd": best.is_maxdd, "sel_oos_dd": best.oos_maxdd,
                         "sel_is_trades": best.is_trades, "sel_oos_trades": best.oos_trades,
                         "ref": rr.variant, "ref_is_sharpe": rr.is_sharpe, "ref_oos_sharpe": rr.oos_sharpe,
                         "ref_is_dd": rr.is_maxdd, "ref_oos_dd": rr.oos_maxdd,
                         "ref_is_trades": rr.is_trades, "ref_oos_trades": rr.oos_trades,
                         "family_median_oos": float(g.oos_sharpe.median()),
                         "family_share_oos_beats_ref": float((g.oos_sharpe > rr.oos_sharpe).mean()),
                         "is_oos_spearman": spearman,
                         "dsr_is_Nall": d_all["dsr"], "dsr_is_Nfam": d_fam["dsr"], "dsr_full_Nall": d_full["dsr"],
                         "sr0_is_ann_Nall": d_all["sr0_ann"], "oos_psr": oos_psr,
                         "skew_is": d_all["skew"], "kurt_is": d_all["kurt"]})
    fams = pd.DataFrame(fam_rows)

    # ---------------- diagnostics on the selected dip variants (not counted)
    L.frames_1555()
    NR.NEWS_FLAGS.update(pickle.loads((L.CACHE / "news_flags.pkl").read_bytes()))
    diag = []
    for fam in ["R1_vrp", "R2_ts", "R4_news_dip"]:
        if fam not in selected:
            continue
        b = selected[fam]
        p = json.loads(b.params)
        extra = "flags" if fam == "R4_news_dip" else ""
        r1, e1, _ = L.evaluate("DIAG", b.variant + "_lag1", NR.DipPCSNR, p, extra=extra, counted=False,
                               note="engine signal_lag=1", lag=1)
        r2, e2, _ = L.evaluate("DIAG", b.variant + "_double", NR.DipPCSNR, p, extra=extra, costs=R.DOUBLE,
                               counted=False, note="double costs")
        diag += [r1, r2]
        curves[("DIAG", r1["variant"])] = e1
        curves[("DIAG", r2["variant"])] = e2
        if fam == "R1_vrp":
            r3, e3, _ = L.evaluate("DIAG", b.variant + "_priorday", NR.DipPCSNR, {**p, "vrp_lag": 1}, counted=False,
                                   note="prior-session VIX and RV10")
            diag.append(r3)
        if fam == "R2_ts":
            r3, e3, _ = L.evaluate("DIAG", b.variant + "_priorday", NR.DipPCSNR, {**p, "ts_lag": 1}, counted=False,
                                   note="prior-session VIX9D/VIX")
            diag.append(r3)
        if fam == "R4_news_dip" and "otm" in p.get("news_flag", "") or p.get("news_flag") == "any":
            fl = p["news_flag"] + "_lag1"
            r3, e3, _ = L.evaluate("DIAG", b.variant + "_otmlag1", NR.DipPCSNR, {**p, "news_flag": fl},
                                   extra="flags", counted=False, note="OTM volume from the prior session")
            diag.append(r3)
    for fam, refv in (("R2_condor", None), ("R4_news_ic", None)):
        if fam in selected:
            b = selected[fam]
            p = json.loads(b.params)
            extra = "flags" if fam == "R4_news_ic" else ""
            rr, ee, _ = L.evaluate("DIAG", b.variant + "_lag1", NR.SpreadBookNR, p, ["SPY"], 100_000, extra,
                                   counted=False, note="engine signal_lag=1", lag=1)
            diag.append(rr)
    rr, _, _ = L.evaluate("DIAG", "ic_60d_10d_F1_lag1", NR.SpreadBookNR, dict(L.CONDOR), ["SPY"], 100_000,
                          counted=False, note="condor finalist F1, engine signal_lag=1", lag=1)
    diag.append(rr)
    bl1, _, _ = L.evaluate("DIAG", "dip_pcs_1555_lag1", NR.DipPCSNR, {}, counted=False, note="engine signal_lag=1",
                           lag=1)
    diag.append(bl1)
    df = pd.concat([df, pd.DataFrame(diag)], ignore_index=True)
    df["family"] = df["family"].fillna(df["rule"])

    cols = ["rule", "family", "variant", "counted_trial", "strategy", "underlyings", "capital", "note", "lag",
            "is_sharpe", "oos_sharpe", "full_sharpe", "is_cagr", "oos_cagr", "full_cagr", "is_maxdd", "oos_maxdd",
            "full_maxdd", "is_sortino", "oos_sortino", "is_trades", "oos_trades", "full_trades", "is_win",
            "oos_win", "is_pf", "oos_pf", "is_tr_mean", "oos_tr_mean", "is_worst_tr", "oos_worst_tr", "is_pnl",
            "oos_pnl", "n_open_print", "n_open_model", "params", "core"]
    df = df[[c for c in cols if c in df.columns] + [c for c in df.columns if c not in cols]]
    df.to_csv(HERE / "results.csv", index=False)
    fams.to_csv(HERE / "families.csv", index=False)

    # ---------------- finalists.json
    fin = []
    names = {"R1_vrp": "VRP gate on the dip put spread", "R2_ts": "Term-structure expiry on the dip put spread",
             "R2_condor": "Held-to-expiry SPY condor (DTE x delta sweep)", "R3_timing": "Entry/exit timing on the dip put spread",
             "R4_news_dip": "News veto on the dip put spread", "R4_news_ic": "News veto on the SPY condor",
             "R5_hype": "Hype fade bear call spread"}
    base_row = df[(df.rule == "BASE")].iloc[0]
    fin.append(payload("nr_base_dip_pcs_1555", "Dip put credit spread finalist at 15:55 (reference)", "SPY+QQQ+IWM",
                       10_000, curves[("BASE", "dip_pcs_1555")],
                       {k: seg(base_row, "full")[k] for k in ("win_rate", "profit_factor", "trades")},
                       json.loads(base_row.params), seg(base_row, "is"), seg(base_row, "oos"), "reference"))
    for _, f in fams.iterrows():
        b = selected[f.family]
        eq = curves[(b.rule, b.variant)]
        und = b.underlyings.replace(",", "+")
        st = {k: seg(b, "full")[k] for k in ("win_rate", "profit_factor", "trades")}
        fin.append(payload(f"nr_{f.family}_{b.variant}", f"{names[f.family]}: {b.variant}", und, int(b.capital), eq, st,
                           {**json.loads(b.params), "family": f.family, "selection": "best IS Sharpe in family, frozen"},
                           seg(b, "is"), seg(b, "oos"), "research",
                           {"reference": {"variant": f.ref, "is_sharpe": f.ref_is_sharpe, "oos_sharpe": f.ref_oos_sharpe},
                            "deflated_sharpe": {"is_N_all": f.dsr_is_Nall, "is_N_family": f.dsr_is_Nfam,
                                                "N_all": N_all, "N_family": int(f.n_variants)}}))
    (HERE / "finalists.json").write_text(json.dumps(clean(fin), indent=1))

    # ---------------- plots
    fig, ax = plt.subplots(figsize=(9, 6))
    style(ax, fig)
    colors = {"R1_vrp": AMBER, "R2_ts": GREEN, "R2_condor": "#4fc3f7", "R3_timing": WHITE,
              "R4_news_dip": RED, "R4_news_ic": "#ff8a80", "R5_hype": "#b388ff", "BASE": AMBER}
    for fam, g in df[df.counted_trial.astype(bool)].groupby("family"):
        ax.scatter(g.is_sharpe, g.oos_sharpe, s=22, color=colors.get(fam, GREY), label=fam, alpha=0.9)
    b0 = base_row
    ax.scatter([b0.is_sharpe], [b0.oos_sharpe], s=140, marker="*", color=AMBER, label="dip finalist @15:55")
    ax.axhline(0, color=GREY, lw=0.8)
    ax.axvline(0, color=GREY, lw=0.8)
    ax.set_xlabel("IS Sharpe (2024-03 .. 2025-06)")
    ax.set_ylabel("OOS Sharpe (2025-07 .. 2026-10)")
    ax.set_title(f"NEW RULES  |  {N_all} counted trials  |  IS vs OOS")
    leg = ax.legend(fontsize=7, facecolor="black", edgecolor=GREY, labelcolor=WHITE, loc="lower right")
    fig.tight_layout()
    fig.savefig(HERE / "out" / "is_vs_oos.png", dpi=120, facecolor="black")
    plt.close(fig)

    for fam in fams.family:
        b = selected[fam]
        f = fams[fams.family == fam].iloc[0]
        fig, ax = plt.subplots(figsize=(10, 4.5))
        style(ax, fig)
        eq = curves[(b.rule, b.variant)]
        if fam == "R5_hype":
            refk = ("R5_control", f.ref)
        else:
            refk = (ref[fam][0], ref[fam][1])
        er = curves[refk]
        ax.plot(er.index, er / er.iloc[0], color=WHITE, lw=1.1, label=f"reference {refk[1]}  IS {f.ref_is_sharpe:.2f} / OOS {f.ref_oos_sharpe:.2f}")
        ax.plot(eq.index, eq / eq.iloc[0], color=AMBER, lw=1.4, label=f"selected {b.variant}  IS {b.is_sharpe:.2f} / OOS {b.oos_sharpe:.2f}")
        if fam == "R5_hype":
            parts = b.variant.split("_")
            k2 = ("R5_control", f"nonfade_{parts[-2]}_{parts[-1]}")
            if k2 in curves:
                e2 = curves[k2]
                ax.plot(e2.index, e2 / e2.iloc[0], color=RED, lw=1.0, label=f"non-fade spikes (real catalyst / other news) {k2[1]}")
        ax.axvline(pd.Timestamp(L.OOS_START), color=GREEN, ls="--", lw=0.9)
        ax.text(pd.Timestamp(L.OOS_START), ax.get_ylim()[1], "  OOS frozen", color=GREEN, va="top", fontsize=8)
        ax.set_title(f"{fam}  |  DSR(IS, N={N_all}) {f.dsr_is_Nall:.2f}")
        ax.set_ylabel("growth of 1")
        ax.legend(fontsize=7, facecolor="black", edgecolor=GREY, labelcolor=WHITE, loc="upper left")
        fig.tight_layout()
        fig.savefig(HERE / "out" / f"equity_{fam}.png", dpi=120, facecolor="black")
        plt.close(fig)

    g = df[df.family == "R2_condor"].copy()
    if len(g):
        fig, axs = plt.subplots(1, 2, figsize=(10, 4))
        for ax, tag in zip(axs, ("is", "oos")):
            style(ax, fig)
            mat = np.full((3, 3), np.nan)
            for i, dte in enumerate((30, 45, 60)):
                for j, dl in enumerate((10, 16, 25)):
                    x = g[g.variant == f"ic_{dte}d_{dl}d"]
                    if len(x):
                        mat[i, j] = x[f"{tag}_sharpe"].iloc[0]
            im = ax.imshow(mat, cmap="RdYlGn", vmin=-1.5, vmax=1.5)
            for i in range(3):
                for j in range(3):
                    ax.text(j, i, f"{mat[i, j]:.2f}", ha="center", va="center", color="black", fontsize=9)
            ax.set_xticks(range(3), ["0.10d", "0.16d", "0.25d"])
            ax.set_yticks(range(3), ["30 DTE", "45 DTE", "60 DTE"])
            ax.set_title(f"SPY condor held to expiry, {tag.upper()} Sharpe")
        fig.tight_layout()
        fig.savefig(HERE / "out" / "condor_grid.png", dpi=120, facecolor="black")
        plt.close(fig)

    print(fams.round(3).to_string())
    print("N_all", N_all)


if __name__ == "__main__":
    main()
