"""Tasks 2-5: correlation / drawdown overlap, candidate metrics (IS / OOS / full), deflated
Sharpe, weekly block-bootstrap CIs, real + synthetic stress, selection, outputs.

Inputs: cache/sleeves.pkl (sleeves.py), cache/p10k.pkl (p10k.py), cache/p100k.pkl (p100k.py).
Outputs: results.csv, portfolios.json, sleeve_table.csv, correlation.csv, drawdown_overlap.csv,
         stress.csv, out/*.png
"""
from __future__ import annotations

import json
import pickle
import sys

from common import *  # noqa
sys.path.insert(0, str(ROOT / "longhist"))
from data import load_cboe  # noqa: E402  (longhist/data.py: cached Cboe index CSVs)

UPSTREAM_TRIALS = 128 + 193 + 1563     # signals + wheel + spreads studies
S = pickle.loads((CACHE / "sleeves.pkl").read_bytes())
P10 = pickle.loads((CACHE / "p10k.pkl").read_bytes())
P100 = pickle.loads((CACHE / "p100k.pkl").read_bytes())
S.update(P100["sleeves_extra"])
SPY = S[("bh_SPY", 100_000, False)]["equity"]
QQQ = S[("bh_QQQ", 100_000, False)]["equity"]
CAL = SPY.index

SLEEVE_NAMES = {
    "bh_SPY": "Buy & hold SPY", "bh_QQQ": "Buy & hold QQQ", "bh_IWM": "Buy & hold IWM",
    "dip_SPY": "Dip-PCS SPY (signals finalist)", "dip_QQQ": "Dip-PCS QQQ", "dip_IWM": "Dip-PCS IWM",
    "dip_pool25": "Dip-PCS SPY+QQQ+IWM, 25% shared cap", "dip_pool50": "Dip-PCS SPY+QQQ+IWM, 50% shared cap",
    "wheel_QQQ": "Wheel QQQ 0.30d put / 0.15d call (wheel F1)", "cc_QQQ": "Covered call QQQ 0.40d hold (wheel F2)",
    "collar_SPY": "Collar SPY, 90 DTE put (wheel F3)", "ic_SPY": "SPY iron condor 10d/3%/60 DTE (spreads F1)",
    "ic_PORT": "Iron condor SPY/QQQ/IWM thirds (spreads F2)"}
CORR_SET = ["bh_SPY", "bh_QQQ", "bh_IWM", "dip_SPY", "dip_QQQ", "dip_IWM", "dip_pool25", "wheel_QQQ",
            "cc_QQQ", "collar_SPY", "ic_SPY", "ic_PORT"]
SPREAD_CAP = {"dip_SPY": 0.25, "dip_QQQ": 0.25, "dip_IWM": 0.25, "dip_pool25": 0.25, "dip_pool50": 0.50,
              "ic_SPY": 0.20, "ic_PORT": 0.20}
plt = plt_setup()


# ------------------------------------------------------------------ candidates
def trade_stats(tr):
    if tr is None or not len(tr):
        return {"win_rate": None, "profit_factor": None, "trades": 0}
    t = tr[tr["reason"] != "open_at_end"]
    if not len(t):
        return {"win_rate": None, "profit_factor": None, "trades": 0}
    w, l = t.pnl[t.pnl > 0], t.pnl[t.pnl <= 0]
    return {"win_rate": float(len(w) / len(t)),
            "profit_factor": float(w.sum() / -l.sum()) if l.sum() < 0 else None, "trades": int(len(t))}


C = {}   # cid -> dict
for (tid, cy), x in P10.items():
    cid = f"10k_{tid}"
    C.setdefault(cid, {"account": 10_000, "family": "10k grid", "trial": True, "params": x["params"],
                       "name": tid})
    C[cid]["eq_ty" if cy else "eq"] = x["equity"]
    if not cy:
        C[cid]["usage"] = x["usage"]
        C[cid]["trades"] = x["trades"]
        kw = x["params"]
        C[cid]["spread_cap"] = kw.get("cap", 0.25)
for sid in ("dip_SPY", "ic_SPY", "bh_SPY", "bh_QQQ"):
    cid = f"10k_{sid}"
    C[cid] = {"account": 10_000, "family": "10k sleeve" if sid[:2] != "bh" else "benchmark",
              "trial": sid[:2] != "bh", "name": SLEEVE_NAMES[sid], "params": {"sleeve": sid},
              "eq": S[(sid, 10_000, False)]["equity"], "eq_ty": S[(sid, 10_000, True)]["equity"],
              "usage": S[(sid, 10_000, False)]["usage"], "trades": S[(sid, 10_000, False)]["trades"],
              "spread_cap": SPREAD_CAP.get(sid, 0.0)}
for (bid, cy), x in P100["blends"].items():
    for impl in ("engine", "ideal"):
        cid = f"100k_{bid}_{impl}"
        C.setdefault(cid, {"account": 100_000, "family": f"100k blend ({impl})", "trial": impl == "engine",
                           "name": f"{bid} [{impl}]",
                           "params": {"weights": {k: round(float(v), 4) for k, v in x["weights"].items() if v > 1e-4},
                                      "implementation": impl}})
        C[cid]["eq_ty" if cy else "eq"] = x[impl]
        if not cy:
            C[cid]["usage"] = x["usage"] if impl == "engine" else None
            C[cid]["trades"] = x["trades"]
            C[cid]["feas"] = x["feas"]
            C[cid]["spread_cap"] = sum(float(v) * SPREAD_CAP.get(k, 0.0) for k, v in x["weights"].items())
for sid in ("wheel_QQQ", "cc_QQQ", "collar_SPY", "dip_pool25", "ic_SPY", "ic_PORT", "bh_SPY", "bh_QQQ"):
    cid = f"100k_{sid}"
    C[cid] = {"account": 100_000, "family": "100k sleeve" if sid[:2] != "bh" else "benchmark",
              "trial": sid[:2] != "bh", "name": SLEEVE_NAMES[sid], "params": {"sleeve": sid},
              "eq": S[(sid, 100_000, False)]["equity"], "eq_ty": S[(sid, 100_000, True)]["equity"],
              "usage": S[(sid, 100_000, False)]["usage"], "trades": S[(sid, 100_000, False)]["trades"],
              "spread_cap": SPREAD_CAP.get(sid, 0.0)}

N_TRIALS = {a: sum(1 for c in C.values() if c["account"] == a and c["trial"]) for a in (10_000, 100_000)}
print("trials:", N_TRIALS)


# ------------------------------------------------------------------ metrics
def segstats(eq, lo=None, hi=None, usage=None):
    e = eq[lo:hi] if (lo or hi) else eq
    u = usage[lo:hi] if (usage is not None and (lo or hi)) else usage
    return full_stats(e, SPY, u)


var_sr = {}
for a in (10_000, 100_000):
    srs = []
    for c in C.values():
        if c["account"] == a and c["trial"]:
            r = rets(c["eq"][:IS_END])
            srs.append(r.mean() / r.std())
    var_sr[a] = float(np.var(srs, ddof=1))

rows = []
for cid, c in C.items():
    eq = c["eq"]
    usage = c.get("usage")
    full, is_, oos = segstats(eq, usage=usage), segstats(eq, None, IS_END, usage), segstats(eq, OOS_START, None, usage)
    fty = segstats(c["eq_ty"]) if "eq_ty" in c else {}
    oty = segstats(c["eq_ty"], OOS_START, None) if "eq_ty" in c else {}
    r_is, r_full, r_oos = rets(eq[:IS_END]), rets(eq), rets(eq[OOS_START:])
    n = N_TRIALS[c["account"]]
    dsr_l, sr0_l = deflated_sharpe(r_is, n, var_sr[c["account"]])
    dsr_g, sr0_g = deflated_sharpe(r_is, n + UPSTREAM_TRIALS, var_sr[c["account"]])
    dsr_full, _ = deflated_sharpe(r_full, n, var_sr[c["account"]])
    psr_oos = psr(r_oos.mean() / r_oos.std(), len(r_oos), float(r_oos.skew()), float(r_oos.kurt()) + 3)
    bs_sh = block_bootstrap(r_full, sharpe_arr)
    bs_cg = block_bootstrap(r_full, cagr_arr)
    bs_dd = block_bootstrap(r_full, maxdd_arr)
    bs_oos = block_bootstrap(r_oos, sharpe_arr)
    # paired Sharpe difference vs SPY (same weeks resampled)
    j = pd.concat([r_full, rets(SPY)], axis=1).dropna()
    j.columns = ["a", "b"]
    groups = [g.values for _, g in j.groupby(pd.Grouper(freq="W-FRI")) if len(g)]
    rng = np.random.default_rng(3)
    diffs = []
    for _ in range(2000):
        x = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        diffs.append(sharpe_arr(x[:, 0]) - sharpe_arr(x[:, 1]))
    diffs = np.array(diffs)
    ts = trade_stats(c.get("trades"))
    c["metrics"] = {"full": full, "is": is_, "oos": oos, "full_tbill": fty, "oos_tbill": oty}
    row = {"id": cid, "account": c["account"], "family": c["family"], "trial": c["trial"], "name": c["name"],
           "params": json.dumps(c["params"])}
    for pre, m in (("full", full), ("is", is_), ("oos", oos)):
        for k in ("cagr", "total_return", "ann_vol", "sharpe", "sortino", "max_drawdown", "calmar", "worst_month",
                  "beta_spy", "avg_capital_usage", "max_capital_usage"):
            row[f"{pre}_{k}"] = m.get(k)
    row.update({"tbill_full_cagr": fty.get("cagr"), "tbill_full_sharpe": fty.get("sharpe"),
                "tbill_full_max_drawdown": fty.get("max_drawdown"), "tbill_oos_sharpe": oty.get("sharpe"),
                "n_trials": n, "dsr_is_local": dsr_l, "sr0_is_local_ann": sr0_l,
                "dsr_is_global": dsr_g, "sr0_is_global_ann": sr0_g, "dsr_full_local": dsr_full,
                "psr_oos_vs0": psr_oos,
                "boot_full_sharpe_lo": np.nanpercentile(bs_sh, 2.5), "boot_full_sharpe_hi": np.nanpercentile(bs_sh, 97.5),
                "boot_full_cagr_lo": np.nanpercentile(bs_cg, 2.5), "boot_full_cagr_hi": np.nanpercentile(bs_cg, 97.5),
                "boot_full_maxdd_lo": np.nanpercentile(bs_dd, 2.5), "boot_full_maxdd_med": np.nanpercentile(bs_dd, 50),
                "boot_oos_sharpe_lo": np.nanpercentile(bs_oos, 2.5), "boot_oos_sharpe_hi": np.nanpercentile(bs_oos, 97.5),
                "boot_sharpe_minus_spy_lo": np.nanpercentile(diffs, 2.5),
                "boot_sharpe_minus_spy_hi": np.nanpercentile(diffs, 97.5),
                "p_sharpe_gt_spy": float((diffs > 0).mean()),
                "spread_cap": c.get("spread_cap", 0.0), **ts})
    if "feas" in c:
        row["infeasible_sleeves"] = ",".join(k for k, v in c["feas"].items() if v["infeasible"])
    rows.append(row)
res = pd.DataFrame(rows)

# ------------------------------------------------------------------ stress
SPX, PUT = load_cboe("SPX"), load_cboe("PUT")
LH = pd.concat([SPX.pct_change(), PUT.pct_change()], axis=1).dropna()
LH.columns = ["spx", "put"]
b_put = LH["put"].cov(LH["spx"]) / LH["spx"].var()
LH["vol"] = LH["put"] - b_put * LH["spx"]
SYN = {"SYN GFC 2007-10..2009-03": ("2007-10-09", "2009-03-09"),
       "SYN COVID 2020-02..03": ("2020-02-19", "2020-03-23"),
       "SYN 2022 bear": ("2022-01-03", "2022-10-12")}


def factor_betas(eq):
    r = rets(eq)
    j = pd.concat([r, LH[["spx", "vol"]]], axis=1, join="inner").dropna()
    X = np.column_stack([np.ones(len(j)), j["spx"], j["vol"]])
    coef = np.linalg.lstsq(X, j.iloc[:, 0].values, rcond=None)[0]
    return float(coef[1]), float(coef[2])


def path_stats(r):
    e = np.cumprod(1 + r)
    return float(e[-1] - 1), float((e / np.maximum.accumulate(e) - 1).min())


srows = []
for cid, c in C.items():
    eq = c["eq"]
    bm, bv = factor_betas(eq)
    st = {"id": cid, "account": c["account"], "beta_spx": bm, "beta_shortvol": bv,
          "spread_cap": c.get("spread_cap", 0.0)}
    for k, (a, b) in SHOCKS.items():
        w = window(eq, a, b)
        st[f"{k} ret"], st[f"{k} dd"] = w.get("ret"), w.get("max_dd")
    for k, (a, b) in SYN.items():
        x = LH.loc[a:b]
        ret, dd = path_stats((bm * x["spx"] + bv * x["vol"]).values)
        st[f"{k} factor ret"], st[f"{k} factor dd"] = ret, dd
        cap = c.get("spread_cap", 0.0)
        st[f"{k} +maxloss dd"] = (1 + dd) * (1 - cap) - 1      # factor path + one full max loss at the cap
    srows.append(st)
stress = pd.DataFrame(srows)
for k in SYN:
    x = LH.loc[SYN[k][0]:SYN[k][1]]
    print(k, "SPX", path_stats(x["spx"].values), "PUT", path_stats(x["put"].values))
res = res.merge(stress.drop(columns=["account", "spread_cap"]), on="id")
res.to_csv(HERE / "results.csv", index=False)
stress.to_csv(HERE / "stress.csv", index=False)

# ------------------------------------------------------------------ correlation / overlap
Rs = pd.DataFrame({s: rets(S[(s, 100_000, False)]["equity"]) for s in CORR_SET})
corr = Rs.corr()
corr_oos = Rs[OOS_START:].corr()
Rw = (1 + Rs).resample("W-FRI").prod() - 1
corr_w = Rw.corr()
corr.to_csv(HERE / "correlation.csv")
DD = pd.DataFrame({s: M.drawdown(S[(s, 100_000, False)]["equity"]) for s in CORR_SET})
ov = []
spy_dd = DD["bh_SPY"] < -0.05
spy_bad = rets(SPY) <= rets(SPY).quantile(0.05)
for s in CORR_SET:
    r = Rs[s]
    ov.append({"sleeve": s, "corr_daily_spy": corr.at[s, "bh_SPY"], "corr_weekly_spy": corr_w.at[s, "bh_SPY"],
               "corr_daily_qqq": corr.at[s, "bh_QQQ"], "corr_oos_spy": corr_oos.at[s, "bh_SPY"],
               "max_dd": DD[s].min(), "dd_corr_spy": DD[s].corr(DD["bh_SPY"]),
               "pct_spy_dd5_days_also_dd5": float((DD[s][spy_dd] < -0.05).mean()),
               "mean_ret_spy_worst5pct_days": float(r[spy_bad.reindex(r.index).fillna(False)].mean()),
               "spy_mean_worst5pct_days": float(rets(SPY)[spy_bad].mean()),
               "worst_dd_trough": str(DD[s].idxmin().date())})
ovd = pd.DataFrame(ov)
ovd.to_csv(HERE / "drawdown_overlap.csv", index=False)

# sleeve table (both capitals)
st_rows = []
for (sid, cap, cy), x in S.items():
    if cy:
        continue
    m, mi, mo = (segstats(x["equity"]), segstats(x["equity"], None, IS_END), segstats(x["equity"], OOS_START))
    st_rows.append({"sleeve": sid, "capital": cap, "infeasible": x["infeasible"], "blocked_days": x["blocked_days"],
                    "cagr": m["cagr"], "sharpe": m["sharpe"], "max_dd": m["max_drawdown"], "beta_spy": m["beta_spy"],
                    "is_sharpe": mi["sharpe"], "oos_sharpe": mo["sharpe"],
                    "tbill_cagr": segstats(S[(sid, cap, True)]["equity"])["cagr"],
                    "avg_usage": float(x["usage"].mean())})
pd.DataFrame(st_rows).sort_values(["capital", "sleeve"]).to_csv(HERE / "sleeve_table.csv", index=False)

# ------------------------------------------------------------------ selection (IS only)
def pick(account, mask, dd_floor):
    d = res[(res.account == account) & res.trial & mask & (res.is_max_drawdown >= dd_floor)]
    return d.sort_values("is_sharpe", ascending=False)


sel10 = pick(10_000, res.family == "10k grid", -0.20)
sel10_cash = pick(10_000, res.id.str.match(r"10k_A_cap\d+_cash"), -0.20)
eng = res.family.str.contains("engine") & (res.infeasible_sleeves.fillna("") == "")
sel100 = pick(100_000, eng, -0.15)
sel100_any = pick(100_000, res.family.str.contains("engine") | (res.family == "100k sleeve"), -0.15)
REC = {"10k_primary": sel10.id.iloc[0], "10k_alt_spreads_only": sel10_cash.id.iloc[0],
       "100k_primary": sel100.id.iloc[0]}
alt100 = [i for i in sel100.id if i != REC["100k_primary"]]
REC["100k_alt"] = alt100[0] if alt100 else sel100_any.id.iloc[0]
print("IS selection 10k:\n", sel10[["id", "is_sharpe", "is_max_drawdown", "oos_sharpe", "oos_cagr"]].head(6).to_string())
print("IS selection 100k:\n", sel100[["id", "is_sharpe", "is_max_drawdown", "oos_sharpe", "oos_cagr"]].to_string())
print("REC", REC)
(HERE / "selection.json").write_text(json.dumps({"rules": {
    "10k": "max IS Sharpe among the 16-trial grid with IS max DD >= -20%",
    "10k_alt": "max IS Sharpe among spreads-only (idle cash) grid rows, IS max DD >= -20%",
    "100k": "max IS Sharpe among engine-implemented (whole-lot) blends with no infeasible sleeve and IS max DD >= -15%"},
    "picked": REC, "n_trials": N_TRIALS, "upstream_trials": UPSTREAM_TRIALS}, indent=1))

# ------------------------------------------------------------------ portfolios.json
def ds(eq, step=2):
    e = eq.iloc[::step]
    return e if e.index[-1] == eq.index[-1] else pd.concat([e, eq.iloc[[-1]]])


def dash(m, ts):
    return {"cagr": clean(m.get("cagr")), "total_return": clean(m.get("total_return")),
            "max_drawdown": clean(m.get("max_drawdown")), "sharpe": clean(m.get("sharpe")),
            "sortino": clean(m.get("sortino")), "win_rate": clean(ts.get("win_rate")),
            "profit_factor": clean(ts.get("profit_factor")), "trades": ts.get("trades", 0),
            "ann_vol": clean(m.get("ann_vol")), "calmar": clean(m.get("calmar")),
            "worst_month": clean(m.get("worst_month")), "beta_spy": clean(m.get("beta_spy")),
            "avg_capital_usage": clean(m.get("avg_capital_usage"))}


def underlying_of(c):
    p = c["params"]
    if "weights" in p:
        return "+".join(p["weights"].keys())
    s = p.get("sleeve")
    if s:
        return {"ic_PORT": "SPY+QQQ+IWM", "dip_pool25": "SPY+QQQ+IWM"}.get(s, s.split("_")[-1])
    return "SPY+QQQ+IWM" + ("+IC_SPY" if p.get("with_ic") else "")


order = list(REC.values()) + [i for i in C if i not in REC.values()]
dashboard = []
for cid in order:
    c = C[cid]
    eq = c["eq"]
    r = res.set_index("id").loc[cid]
    tr = c.get("trades")
    tsf = trade_stats(tr)
    tsi = trade_stats(tr[pd.to_datetime(tr.open_date) <= IS_END]) if tr is not None and len(tr) else {}
    tso = trade_stats(tr[pd.to_datetime(tr.open_date) >= OOS_START]) if tr is not None and len(tr) else {}
    tag = [k for k, v in REC.items() if v == cid]
    dashboard.append({
        "id": f"pf_{cid}", "name": (f"[REC {tag[0]}] " if tag else "") + c["name"],
        "underlying": underlying_of(c), "capital": c["account"],
        "metrics": dash(c["metrics"]["full"], tsf),
        "equity": [{"date": str(d.date()), "value": round(float(v), 2)} for d, v in ds(eq).items()],
        "drawdown": [{"date": str(d.date()), "value": round(float(v), 4)} for d, v in ds(M.drawdown(eq)).items()],
        "monthly_returns": [{"month": d.strftime("%Y-%m"), "ret": round(float(v), 5)}
                            for d, v in M.monthly_returns(eq).items()],
        "params": {**c["params"], "family": c["family"], "counted_trial": bool(c["trial"]),
                   "selection": tag[0] if tag else None},
        "is_metrics": dash(c["metrics"]["is"], tsi), "oos_metrics": dash(c["metrics"]["oos"], tso),
        "tbill_metrics": {"full": dash(c["metrics"]["full_tbill"], {}), "oos": dash(c["metrics"]["oos_tbill"], {})},
        "stats": {"dsr_is_local": clean(r["dsr_is_local"]), "dsr_is_global": clean(r["dsr_is_global"]),
                  "n_trials": int(r["n_trials"]), "psr_oos_vs0": clean(r["psr_oos_vs0"]),
                  "boot_full_sharpe_ci": [clean(r["boot_full_sharpe_lo"]), clean(r["boot_full_sharpe_hi"])],
                  "boot_full_cagr_ci": [clean(r["boot_full_cagr_lo"]), clean(r["boot_full_cagr_hi"])],
                  "boot_sharpe_minus_spy_ci": [clean(r["boot_sharpe_minus_spy_lo"]), clean(r["boot_sharpe_minus_spy_hi"])]},
        "stress": {k: clean(v) for k, v in stress.set_index("id").loc[cid].items() if k not in ("account",)},
    })
(HERE / "portfolios.json").write_text(json.dumps(dashboard, allow_nan=False))
print("portfolios.json entries:", len(dashboard), f"{(HERE / 'portfolios.json').stat().st_size / 1e6:.2f} MB")

# ------------------------------------------------------------------ charts
def shade(ax):
    ax.axvspan(pd.Timestamp(OOS_START), CAL[-1], color="#1a1a1a", zorder=0)
    ax.text(pd.Timestamp(OOS_START), ax.get_ylim()[1], " OOS", color=GREY, va="top", fontsize=8)


def equity_chart(ids, fname, title, extra):
    fig, ax = plt.subplots(figsize=(11, 5.2))
    for i, cid in enumerate(ids):
        eq = C[cid]["eq"]
        ax.plot(eq.index, eq / eq.iloc[0], color=PALETTE[i], label=C[cid]["name"][:60], lw=1.8)
    for lab, e, col in extra:
        ax.plot(e.index, e / e.iloc[0], color=col, lw=1.2, ls="--", label=lab)
    ax.set_ylabel("growth of $1")
    ax.set_title(title, loc="left")
    shade(ax)
    ax.legend(loc="upper left", fontsize=7.5)
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=130)
    plt.close(fig)


equity_chart([REC["10k_primary"], REC["10k_alt_spreads_only"], "10k_A_cap25_SPY", "10k_dip_SPY", "10k_ic_SPY"],
             "equity_10k.png", "$10K PORTFOLIOS vs SPY  (IS 2024-03..2025-06 | OOS shaded)",
             [("Buy & hold SPY", SPY, WHITE), ("Buy & hold QQQ", QQQ, GREY)])
equity_chart([REC["100k_primary"], REC["100k_alt"], "100k_I3_risk_parity_erc_ideal",
              "100k_I1_equal_weight_engine", "100k_cc_QQQ"],
             "equity_100k.png", "$100K PORTFOLIOS vs SPY  (engine = whole lots, static sub-accounts)",
             [("Buy & hold SPY", SPY, WHITE), ("Buy & hold QQQ", QQQ, GREY)])

fig, ax = plt.subplots(figsize=(9.5, 8))
labels = [s for s in CORR_SET]
im = ax.imshow(corr.values, cmap="RdYlGn_r", vmin=-1, vmax=1)
ax.set_xticks(range(len(labels)), labels, rotation=55, ha="right")
ax.set_yticks(range(len(labels)), labels)
ax.grid(False)
for i in range(len(labels)):
    for j in range(len(labels)):
        v = corr.values[i, j]
        ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                color="#000000" if abs(v) > 0.55 else WHITE)
ax.set_title("DAILY RETURN CORRELATION, SLEEVES @ $100K (2024-03..2026-10)", loc="left")
cb = fig.colorbar(im, ax=ax, fraction=0.04)
cb.ax.yaxis.set_tick_params(color=WHITE)
fig.tight_layout()
fig.savefig(OUT / "correlation_heatmap.png", dpi=130)
plt.close(fig)

fig, ax = plt.subplots(figsize=(11, 4.8))
ax.fill_between(SPY.index, M.drawdown(SPY), 0, color=RED, alpha=0.25, label="Buy & hold SPY")
ax.plot(QQQ.index, M.drawdown(QQQ), color=GREY, lw=1, ls="--", label="Buy & hold QQQ")
for i, cid in enumerate([REC["10k_primary"], REC["10k_alt_spreads_only"], REC["100k_primary"], REC["100k_alt"]]):
    ax.plot(C[cid]["eq"].index, M.drawdown(C[cid]["eq"]), color=PALETTE[i], lw=1.4, label=C[cid]["name"][:55])
for k, (a, b) in SHOCKS.items():
    ax.axvspan(pd.Timestamp(a), pd.Timestamp(b), color=AMBER, alpha=0.07)
ax.set_title("DRAWDOWN OVERLAY (shocks shaded amber)", loc="left")
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
ax.legend(fontsize=7.5, loc="lower left")
fig.tight_layout()
fig.savefig(OUT / "drawdown_overlay.png", dpi=130)
plt.close(fig)

sids = [REC["10k_primary"], REC["10k_alt_spreads_only"], REC["100k_primary"], REC["100k_alt"], "100k_bh_SPY", "100k_bh_QQQ"]
cols = [f"{k} dd" for k in SHOCKS] + [f"{k} +maxloss dd" for k in SYN]
labs = ["Aug-24", "Jan-25", "Apr-25", "SYN GFC*", "SYN COVID*", "SYN 2022*"]
st = stress.set_index("id")
fig, ax = plt.subplots(figsize=(12, 5.2))
w = 0.8 / len(sids)
for i, cid in enumerate(sids):
    v = st.loc[cid, cols].astype(float).values
    ax.bar(np.arange(len(cols)) + i * w - 0.4 + w / 2, v, w, color=PALETTE[i] if cid[5:7] != "bh" else
           (WHITE if "SPY" in cid else GREY), label=C[cid]["name"][:50])
ax.set_xticks(range(len(cols)), labs)
ax.axvline(2.5, color=AMBER, lw=1, ls=":")
fig.text(0.01, 0.005, "* SYNTHETIC (not backtested): Cboe SPX + PUT-index daily path x each portfolio's fitted SPX / short-vol betas,"
         " then one full max loss on its spread risk cap. GFC 2007-10-09..2009-03-09, COVID 2020-02-19..03-23, 2022 01-03..10-12.",
         color=AMBER, fontsize=7, va="bottom")
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
ax.set_title("STRESS: MAX DRAWDOWN IN WINDOW (real 2024-25 shocks | synthetic tails)", loc="left")
ax.legend(fontsize=7, loc="lower left", ncol=2)
fig.tight_layout()
fig.subplots_adjust(bottom=0.1)
fig.savefig(OUT / "stress_bars.png", dpi=130)
plt.close(fig)

# IS vs OOS scatter, all counted trials
fig, ax = plt.subplots(figsize=(7.5, 5.5))
for a, col in ((10_000, AMBER), (100_000, GREEN)):
    d = res[(res.account == a) & res.trial]
    ax.scatter(d.is_sharpe, d.oos_sharpe, color=col, s=22, label=f"${a // 1000}k trials (n={len(d)})")
for k, v in REC.items():
    r = res.set_index("id").loc[v]
    ax.annotate(k, (r.is_sharpe, r.oos_sharpe), color=WHITE, fontsize=7)
b = res.set_index("id").loc["100k_bh_SPY"]
ax.scatter([b.is_sharpe], [b.oos_sharpe], color=RED, marker="x", s=60, label="SPY buy & hold")
ax.set_xlabel("IS Sharpe (2024-03..2025-06)")
ax.set_ylabel("OOS Sharpe (2025-07..2026-10)")
ax.set_title("IS vs OOS SHARPE, PORTFOLIO CANDIDATES", loc="left")
ax.legend(fontsize=7.5)
fig.tight_layout()
fig.savefig(OUT / "is_vs_oos.png", dpi=130)
plt.close(fig)

# console summary
pd.set_option("display.width", 250)
show = ["id", "full_cagr", "full_sharpe", "full_max_drawdown", "is_sharpe", "oos_sharpe", "oos_cagr", "oos_max_drawdown",
        "full_worst_month", "full_beta_spy", "full_avg_capital_usage", "tbill_full_cagr", "dsr_is_local", "dsr_is_global",
        "boot_full_sharpe_lo", "boot_full_sharpe_hi", "p_sharpe_gt_spy", "trades"]
print(res[show].round(3).to_string())
print(ovd.round(3).to_string())
print(corr.round(2).to_string())
print(stress.round(3).to_string())
