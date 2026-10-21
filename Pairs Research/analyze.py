"""Post-processing: comparison tables, by-year, event windows, failure analysis, capital model ($10k/$100k,
whole vs fractional shares), cost sensitivity, Chen alpha regression, earnings flags, Alpaca asset flags,
summary.json and finalists.json. Reads .cache/run_state.pkl from run_backtest.py."""
import json, math, pickle, re
import numpy as np
import pandas as pd
import statsmodels.api as sm
import pairs_lib as L
from fetch_data import ETFS

R = L.ROOT / "results"
S = pickle.load(open(L.CACHE / "run_state.pkl", "rb"))
store, reports, vdf, canon = S["store"], S["reports"], S["vdf"], S["canon"]
panel = L.load_panel()
O, C, dates = panel["open"], panel["close"], panel["dates"]
n = len(dates)
spy_r = C["SPY"].pct_change().fillna(0)
dv = S["dv"]
COSTS = L.Costs(etfs=ETFS)
IS_START, IS_END, OOS_START = pd.Timestamp("2017-01-01"), pd.Timestamp("2021-12-31"), pd.Timestamp("2022-01-01")
split = lambda r: (r.loc[IS_START:IS_END], r.loc[OOS_START:])
import run_backtest_helpers as H  # noqa: E402  (earnings lookup shared with run_backtest)

# ------------------------------------------------------------------ choose finalists
bbm = {d["method"]: d["id"] for d in S["best_by_method"]}
finalists = {}
labels = {"ggr": "GGR distance", "coint": "Engle-Granger cointegration", "kalman": "Kalman dynamic hedge",
          "al": "Avellaneda-Lee ETF residual", "chen": "Chen et al. quasi-pairs"}
for m, k in bbm.items():
    if m in labels:
        finalists[f"{m}_best_IS"] = k
for m, k in canon.items():
    finalists[f"{m}_canonical"] = k
for k in ("etf_ggr", "etf_coint", "etf_distinct_ggr", "etf_distinct_coint", "classic_ggr", "classic_coint"):
    finalists[k] = k

# ------------------------------------------------------------------ method comparison table
rows = []
for lab, k in finalists.items():
    rep = reports.get(k)
    if rep is None:
        continue
    for per in ("IS", "OOS"):
        row = dict(label=lab, id=k, period=per)
        for cl in ("net", "gross", "net_2x_costs", "net_same_close", "net_flat_10bp_rt"):
            m = rep[cl][per]
            row[f"{cl}_sharpe"] = m.get("sharpe"); row[f"{cl}_cagr"] = m.get("cagr")
        mn = rep["net"][per]
        row.update(net_sortino=mn["sortino"], net_mdd=mn["max_drawdown"], net_vol=mn["vol"],
                   corr_spy=mn.get("corr_spy"), beta_spy=mn.get("beta_spy"),
                   cost_drag_cagr=rep["cost_drag_cagr"][per], turnover_x_yr=rep.get("turnover_x_per_year"))
        ts = rep.get(f"trades_{per}", {})
        row.update(trades=ts.get("trades"), win_rate=ts.get("win_rate"), profit_factor=ts.get("profit_factor"),
                   pct_converged=ts.get("pct_converged"), avg_hold_days=ts.get("avg_hold_days"))
        bs = rep[f"bootstrap_{per}"]
        row.update(sharpe_ci_lo=bs["sharpe_ci95"][0], sharpe_ci_hi=bs["sharpe_ci95"][1], p_sharpe_le_0=bs["p_sharpe_le_0"])
        if per == "IS":
            row.update(deflated_sharpe_prob=rep["deflated_IS"]["dsr"], dsr_sr0=rep["deflated_IS"]["sr0_ann"], n_trials=rep["deflated_IS"]["n_trials"])
        else:
            row.update(psr_vs_0=rep["psr_OOS"]["psr_vs_0"])
        rows.append(row)
for b, d in S["bench"].items():
    for per in ("IS", "OOS"):
        m = d[per]
        rows.append(dict(label=f"bench_{b}", id=b, period=per, net_sharpe=m["sharpe"], net_cagr=m["cagr"], net_sortino=m["sortino"],
                         net_mdd=m["max_drawdown"], net_vol=m["vol"], corr_spy=m.get("corr_spy"), beta_spy=m.get("beta_spy")))
cmp_ = pd.DataFrame(rows)
cmp_.to_csv(R / "method_comparison.csv", index=False)

# ------------------------------------------------------------------ by-year and event windows
series = {lab: store[k]["r"] for lab, k in finalists.items() if k in store}
series["SPY"] = store["bench_spy"]["r"]; series["reversal_5d"] = store["bench_reversal"]["r"]
if "pit_ggr" in store:
    series["ggr_canonical_PIT"] = store["pit_ggr"]["r"]; series["coint_canonical_PIT"] = store["pit_coint"]["r"]
by = {}
for lab, r in series.items():
    by[lab] = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1)
by = pd.DataFrame(by)
by.index.name = "year"
by.to_csv(R / "by_year.csv")
win = {"covid_crash_2020-02-19_to_03-23": ("2020-02-19", "2020-03-23"), "march_2020": ("2020-03-01", "2020-03-31"),
       "rebound_2020-03-24_to_06-30": ("2020-03-24", "2020-06-30"), "year_2022": ("2022-01-01", "2022-12-31"),
       "april_2025_tariffs": ("2025-04-01", "2025-04-30"), "liberation_day_2025-04-02_to_04-08": ("2025-04-02", "2025-04-08")}
ev = []
for lab, r in series.items():
    row = dict(series=lab)
    for w, (a, b) in win.items():
        x = r.loc[a:b]
        eq = (1 + x).cumprod()
        row[w + "_ret"] = float(eq.iloc[-1] - 1) if len(x) else np.nan
        row[w + "_mdd"] = float((eq / eq.cummax() - 1).min()) if len(x) else np.nan
    ev.append(row)
ev = pd.DataFrame(ev)
ev.to_csv(R / "event_windows.csv", index=False)

# ------------------------------------------------------------------ failure analysis
ch = pd.read_csv(L.CACHE / "sp500_changes.csv", parse_dates=["date"])
acq = ch[ch.reason.astype(str).str.contains("acqui|merg|spun|spin", case=False, regex=True)]
deal_names = set(acq.rem_t.astype(str)) | set(acq.add_t.astype(str))
ret = C.pct_change()


def classify(t):
    i0, i1 = t.i0, t.i1
    a, b = t.a, t.b
    tags = []
    if t.reason == "delist" or a in deal_names or b in deal_names:
        # deal tag only if the change happened within 6 months of the trade
        near = acq[(acq.date >= dates[i0] - pd.Timedelta(days=183)) & (acq.date <= dates[i1] + pd.Timedelta(days=183))]
        if t.reason == "delist" or a in set(near.rem_t) | set(near.add_t) or b in set(near.rem_t) | set(near.add_t):
            tags.append("M&A/index change")
    if H.earn_between(a, i0, i1) or H.earn_between(b, i0, i1):
        tags.append("earnings inside trade")
    ra, rb = ret[a].iloc[i0:i1 + 1].abs().max(), ret[b].iloc[i0:i1 + 1].abs().max()
    if max(ra, rb) > 0.08:
        tags.append(f"gap>8% ({a if ra >= rb else b} {max(ra, rb):.0%})")
    if not tags:
        tags.append("slow drift / regime change")
    return "; ".join(tags)

fail_rows, fail_sum = [], {}
for lab in ("ggr_canonical", "coint_canonical", "coint_lit_canonical", "kalman_best_IS", "coint_best_IS", "ggr_best_IS"):
    k = finalists.get(lab)
    if k is None or store.get(k, {}).get("tdf") is None:
        continue
    t = store[k]["tdf"].copy()
    t["spans_earnings"] = [H.earn_between(a, i0, i1) or H.earn_between(b, i0, i1) for a, b, i0, i1 in zip(t.a, t.b, t.i0, t.i1)]
    ne = t[t.reason != "converge"]
    bad = ne[ne.net < 0].copy()
    bad["cause"] = [classify(x) for x in bad.itertuples()]
    bad["primary"] = bad.cause.str.split(";").str[0].str.replace(r" \(.*", "", regex=True)
    fail_sum[lab] = dict(trades=int(len(t)), not_converged=int(len(ne)), not_converged_losers=int(len(bad)),
                         loss_from_nonconverged=float(bad.net.sum()), total_net=float(t.net.sum()),
                         net_converged=float(t[t.reason == "converge"].net.sum()),
                         cause_counts={k_: int(v) for k_, v in bad.cause.str.split("; ").explode().str.replace(r" \(.*", "", regex=True).value_counts().items()},
                         cause_loss={k_: float(v) for k_, v in bad.groupby("primary").net.sum().items()},
                         spans_earnings_share=float(t.spans_earnings.mean()),
                         mean_net_spanning_earnings=float(t[t.spans_earnings].net.mean()) if t.spans_earnings.any() else None,
                         mean_net_no_earnings=float(t[~t.spans_earnings].net.mean()) if (~t.spans_earnings).any() else None,
                         worst=bad.nsmallest(5, "net")[["a", "b", "entry", "exit", "reason", "net", "cause"]].astype(str).to_dict("records"))
    bad.insert(0, "config", lab)
    fail_rows.append(bad.nsmallest(40, "net"))
pd.concat(fail_rows).to_csv(R / "failure_cases.csv", index=False)

# ------------------------------------------------------------------ cost sensitivity
cs = []
for lab in ("ggr_canonical", "coint_lit_canonical", "kalman_best_IS", "al_canonical", "al_longonly_canonical", "etf_ggr", "etf_distinct_ggr", "classic_ggr", "chen_canonical"):
    k = finalists.get(lab)
    if k is None or k not in store:
        continue
    s = store[k]
    for mult in (0, 0.5, 1, 1.5, 2, 3, 4):
        c = COSTS.scaled(mult) if mult else L.ZERO
        if "built" in s:
            r, _ = L.run_portfolios(s["built"], panel, dv, c, "open", s["p"].get("hedge", "dollar"), s["p"]["npairs"])
            r = r.loc[s["r"].index[0]:]
        else:
            continue
        i_, o_ = split(r)
        cs.append(dict(config=lab, cost_mult=mult, IS_sharpe=L.metrics(i_)["sharpe"], OOS_sharpe=L.metrics(o_)["sharpe"],
                       IS_cagr=L.metrics(i_)["cagr"], OOS_cagr=L.metrics(o_)["cagr"]))
# weights-based methods: recompute with run_backtest W is not stored; use stored default/2x/gross points
for lab in ("al_canonical", "al_longonly_canonical", "chen_canonical"):
    k = finalists.get(lab)
    if k not in store:
        continue
    s = store[k]
    for mult, ser in ((0, s["r_gross"]), (1, s["r"]), (2, s["r_2x"])):
        i_, o_ = split(ser)
        cs.append(dict(config=lab, cost_mult=mult, IS_sharpe=L.metrics(i_)["sharpe"], OOS_sharpe=L.metrics(o_)["sharpe"],
                       IS_cagr=L.metrics(i_)["cagr"], OOS_cagr=L.metrics(o_)["cagr"]))
cs = pd.DataFrame(cs)
cs.to_csv(R / "cost_sensitivity.csv", index=False)

# ------------------------------------------------------------------ Chen alpha vs reversal
chen_alpha = None
kc = canon.get("chen")
if kc in store and "r_rev1m" in store[kc]:
    rc = store[kc]["r"]; rr = store[kc]["r_rev1m"].reindex(rc.index).fillna(0)
    mc = (1 + rc).groupby(rc.index.to_period("M")).prod() - 1
    mr = (1 + rr).groupby(rr.index.to_period("M")).prod() - 1
    ms = (1 + spy_r.reindex(rc.index)).groupby(rc.index.to_period("M")).prod() - 1
    X = sm.add_constant(pd.DataFrame({"rev1m": mr, "spy": ms}))
    fit = sm.OLS(mc, X).fit(cov_type="HAC", cov_kwds={"maxlags": 3})
    chen_alpha = dict(alpha_monthly=float(fit.params["const"]), alpha_t=float(fit.tvalues["const"]),
                      beta_rev1m=float(fit.params["rev1m"]), t_rev1m=float(fit.tvalues["rev1m"]),
                      beta_spy=float(fit.params["spy"]), r2=float(fit.rsquared), months=int(len(mc)),
                      raw_mean_monthly=float(mc.mean()), corr_with_rev1m=float(np.corrcoef(mc, mr)[0, 1]))

# ------------------------------------------------------------------ capital model ($10k / $100k on Alpaca)

OV = {c: O[c].values for c in O.columns}; CV = {c: C[c].values for c in C.columns}


def capital_sim(built, capital, K=10, gross=1.5, whole=True, costs=COSTS):
    """Event-driven: trades from all staggered portfolios, first-come by signal date, at most K concurrent
    pairs, no duplicate pair. Each leg = equity * gross / (2K) at entry. Next-open execution."""
    cands = sorted([t for _, _, trs in built for t in trs], key=lambda t: (t["es"], t["port"]))
    by_entry = {}
    for t in cands:
        by_entry.setdefault(t["es"] + 1, []).append(t)
    eq = capital; cash_curve = np.full(n, np.nan)
    openp = []
    skipped_size = skipped_slots = taken = 0
    imb = []
    first = min(by_entry) if by_entry else 0
    Ov, Cv = OV, CV
    prevC = {}
    for d in range(first, n):
        pnl = 0.0
        # exits at today's open
        still = []
        for p in openp:
            if p["x"] == d and not p["x_close"]:
                pa, pb = Ov[p["a"]][d], Ov[p["b"]][d]
                pnl += p["qa"] * (pa - p["la"]) + p["qb"] * (pb - p["lb"])
                pnl -= abs(p["qa"]) * pa * costs.bps(p["a"], dv.get(p["a"], 1e9)) + abs(p["qb"]) * pb * costs.bps(p["b"], dv.get(p["b"], 1e9))
            else:
                still.append(p)
        openp = still
        # entries at today's open
        for t in by_entry.get(d, []):
            key = (t["a"], t["b"])
            if any((p["a"], p["b"]) == key for p in openp):
                continue
            if len(openp) >= K:
                skipped_slots += 1; continue
            leg = max(eq, 0) * gross / (2 * K)
            pa, pb = Ov[t["a"]][d], Ov[t["b"]][d]
            if not (pa > 0 and pb > 0):
                continue
            na, nb = leg / pa, leg / pb
            if whole:
                na, nb = math.floor(na), math.floor(nb)
            if na <= 0 or nb <= 0:
                skipped_size += 1; continue
            qa, qb = t["side"] * na, -t["side"] * nb
            pnl -= na * pa * costs.bps(t["a"], dv.get(t["a"], 1e9)) + nb * pb * costs.bps(t["b"], dv.get(t["b"], 1e9))
            imb.append(abs(na * pa - nb * pb) / leg)
            xs = t["xs"]; x_close = t.get("reason") == "delist" or xs + 1 >= n
            openp.append(dict(a=t["a"], b=t["b"], qa=qa, qb=qb, la=pa, lb=pb, x=xs if x_close else xs + 1, x_close=x_close))
            taken += 1
        # mark to close; close-exits
        still = []
        for p in openp:
            ca, cb = Cv[p["a"]][d], Cv[p["b"]][d]
            if np.isnan(ca) or np.isnan(cb):
                ca = p["la"] if np.isnan(ca) else ca; cb = p["lb"] if np.isnan(cb) else cb
            pnl += p["qa"] * (ca - p["la"]) + p["qb"] * (cb - p["lb"])
            p["la"], p["lb"] = ca, cb
            short_mv = (-p["qa"] * ca if p["qa"] < 0 else 0) + (-p["qb"] * cb if p["qb"] < 0 else 0)
            if p["x_close"] and p["x"] == d:
                pnl -= abs(p["qa"]) * ca * costs.bps(p["a"], dv.get(p["a"], 1e9)) + abs(p["qb"]) * cb * costs.bps(p["b"], dv.get(p["b"], 1e9))
            else:
                pnl -= short_mv * costs.borrow / L.TD
                still.append(p)
        openp = still
        eq += pnl
        cash_curve[d] = eq
    curve = pd.Series(cash_curve, index=dates).dropna()
    r = curve.pct_change().fillna(0)
    return r, dict(taken=taken, skipped_no_slot=skipped_slots, skipped_whole_share=skipped_size,
                   mean_leg_imbalance=float(np.mean(imb)) if imb else None, final_equity=float(curve.iloc[-1]))

cap_rows = []
cap_series = {}
for lab in ("ggr_canonical", "coint_lit_canonical", "kalman_best_IS", "etf_distinct_ggr", "classic_ggr"):
    k = finalists.get(lab)
    if k not in store or "built" not in store[k]:
        continue
    for capital in (10_000, 100_000):
        for K in (5, 10, 20):
            for whole in (True, False):
                r, info = capital_sim(store[k]["built"], capital, K, 1.5, whole)
                i_, o_ = split(r)
                mi, mo = L.metrics(i_), L.metrics(o_)
                cap_rows.append(dict(config=lab, capital=capital, max_pairs=K, whole_shares=whole, IS_sharpe=mi["sharpe"], IS_cagr=mi["cagr"],
                                     OOS_sharpe=mo["sharpe"], OOS_cagr=mo["cagr"], OOS_mdd=mo["max_drawdown"], **info))
                cap_series[(lab, capital, K, whole)] = r
cap = pd.DataFrame(cap_rows)
cap.to_csv(R / "capital_sim.csv", index=False)

# ------------------------------------------------------------------ Alpaca asset flags (current-day)
assets = json.loads((L.CACHE / "assets.json").read_text())
A = pd.DataFrame(assets)
A = A.sort_values("status").drop_duplicates("symbol", keep="first").set_index("symbol")  # "active" sorts first
flags = {}
for lab in ("ggr_canonical", "coint_lit_canonical", "kalman_best_IS"):
    k = finalists.get(lab)
    if k not in store or store[k].get("tdf") is None:
        continue
    t = store[k]["tdf"]
    t = t[pd.to_datetime(t.entry) >= OOS_START]
    shorted = np.where(t.side > 0, t.b, t.a)
    sh = pd.Series(shorted)
    present = sh.isin(A.index)
    flags[lab] = dict(oos_short_legs=int(len(sh)), still_listed_active=float(sh.map(lambda s: s in A.index and A.loc[s, "status"] == "active" if s in A.index else False).mean()),
                      shortable_today=float(sh.map(lambda s: bool(A.loc[s, "shortable"]) if s in A.index else False).mean()),
                      easy_to_borrow_today=float(sh.map(lambda s: bool(A.loc[s, "easy_to_borrow"]) if s in A.index else False).mean()),
                      fractionable_today=float(sh.map(lambda s: bool(A.loc[s, "fractionable"]) if s in A.index else False).mean()))
uni = pd.read_csv(L.CACHE / "universe_current.csv")
u = uni.symbol[uni.symbol.isin(A.index)]
flags["current_sp500"] = dict(n=int(len(uni)), in_alpaca=int(len(u)), tradable=float(A.loc[u, "tradable"].mean()),
                              shortable=float(A.loc[u, "shortable"].mean()), easy_to_borrow=float(A.loc[u, "easy_to_borrow"].mean()))
ue = [s for s in ETFS if s in A.index]
flags["etfs"] = dict(n=len(ETFS), shortable=float(A.loc[ue, "shortable"].mean()), easy_to_borrow=float(A.loc[ue, "easy_to_borrow"].mean()))

# ------------------------------------------------------------------ outputs
def fin_entry(lab, k, capital=100_000):
    s = store[k]; r = s["r"]; rep = reports[k]
    eq = capital * (1 + r).cumprod()
    ddv = eq / eq.cummax() - 1
    mr = (1 + r).groupby(r.index.to_period("M")).prod() - 1
    full = rep["net"]["FULL"]
    ts = {}
    if s.get("tdf") is not None and len(s["tdf"]):
        ts = L.trade_stats(s["tdf"], len(r) / L.TD)
    wk = lambda x: x.iloc[::5]  # weekly sampling keeps the file small
    return dict(id=lab, name=f"{labels.get(s['p']['method'], s['p']['method'])} [{lab}]",
                underlying="S&P 500 (current constituents)" if not lab.startswith(("etf", "classic")) else ("ETF pairs" if lab.startswith("etf") else "Classic pairs"),
                capital=capital,
                metrics=dict(cagr=full["cagr"], total_return=full["total_return"], max_drawdown=full["max_drawdown"], sharpe=full["sharpe"],
                             sortino=full["sortino"], win_rate=ts.get("win_rate"), profit_factor=ts.get("profit_factor"), trades=ts.get("trades")),
                equity=[dict(date=str(d.date()), value=round(float(v), 2)) for d, v in wk(eq).items()],
                drawdown=[dict(date=str(d.date()), value=round(float(v), 5)) for d, v in wk(ddv).items()],
                monthly_returns=[dict(month=str(m), ret=round(float(v), 5)) for m, v in mr.items()],
                params=s["p"],
                is_metrics=rep["net"]["IS"], oos_metrics=dict(**rep["net"]["OOS"], **{f"trades_{k2}": v for k2, v in rep.get("trades_OOS", {}).items() if k2 != "reasons"}))

fin = [fin_entry(lab, k) for lab, k in finalists.items() if k in store and k in reports]
json.dump(fin, open(L.ROOT / "finalists.json", "w"), indent=1, default=str)

summary = dict(
    data=dict(source="Alpaca v2 stocks/bars 1Day adjustment=all feed=sip", start=str(dates[0].date()), end=str(dates[-1].date()),
              symbols=int(C.shape[1]), universe_current=int(len(uni)), pit_symbols=int(pd.read_csv(L.CACHE / "pit_membership.csv").symbol.nunique()),
              earnings_events=int(sum(len(v) for v in H.EARN.values()) // 2)),
    protocol=dict(IS="2017-01..2021-12 (formation from 2016-01)", OOS="2022-01..2026-10 frozen", n_variants=int(len(vdf)),
                  execution="signal at close t, fill at open t+1 (same-close reported as optimistic bound)",
                  capital_convention="committed capital: $1 long + $1 short per $1 per pair slot (2x gross = Reg T 50% limit)"),
    best_by_method_IS=bbm, canonical=canon, best_overall_IS=S["best_overall"]["id"],
    reports={lab: reports[k] for lab, k in finalists.items() if k in reports},
    benchmarks=S["bench"], survivorship=S["surv"], failure_analysis=fail_sum, chen_alpha_vs_reversal=chen_alpha,
    alpaca_asset_flags_current_day=flags,
    capital_model=cap.to_dict("records"),
)
json.dump(summary, open(L.ROOT / "summary.json", "w"), indent=1, default=str)
pickle.dump(dict(finalists=finalists, series=series, cap_series=cap_series, cs=cs, by=by), open(L.CACHE / "analyze_state.pkl", "wb"))
print("analysis written")
