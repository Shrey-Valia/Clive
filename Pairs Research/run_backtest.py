"""Walk-forward pairs-trading backtest. Writes results/*.csv, summary.json, finalists.json and
.cache/run_state.pkl (consumed by make_charts.py).

IS (parameter selection) = daily returns 2017-01..2021-12 (formation data from 2016). OOS = 2022-01..end,
parameters frozen at the IS choice. Every variant run is counted for the deflated Sharpe."""
import json, math, pickle, time
import numpy as np
import pandas as pd
import pairs_lib as L
from fetch_data import ETFS, ETF_PAIRS, CLASSIC, SECTOR_ETF

t_start = time.time()
R = L.ROOT / "results"; R.mkdir(exist_ok=True)
panel = L.load_panel()
O, C, V, dates = panel["open"], panel["close"], panel["volume"], panel["dates"]
n = len(dates)
spy_r = C["SPY"].pct_change().fillna(0)
IS_START, IS_END, OOS_START = pd.Timestamp("2017-01-01"), pd.Timestamp("2021-12-31"), pd.Timestamp("2022-01-01")
FORM, TRADE = 252, 126

# median dollar volume over 2016-2021 only (cost tiers; no look-ahead into OOS)
isv = (C * V).loc[:"2021-12-31"]
dv = isv.median().fillna(1e8).to_dict()
for s in C.columns:
    if np.isnan(dv.get(s, np.nan)):
        dv[s] = 1e8
COSTS = L.Costs(etfs=ETFS)            # default: 3/5 bp stocks, 2/4/8 bp ETFs, 0.5%/yr borrow
COSTS2 = COSTS.scaled(2.0)            # double slippage and double borrow (1%/yr)
FLAT5 = L.Costs(5, 5, 5, 5, 0.005, etfs=ETFS)  # literature spec: 10 bp round trip per leg

cur = L.universe_current()
cur_syms = [s for s in cur.symbol if s in C.columns]
sector_of = dict(zip(cur.symbol, cur.sector)); subind_of = dict(zip(cur.symbol, cur.sub_industry))
pit = pd.read_csv(L.CACHE / "pit_membership.csv")

# monthly trading starts
month_starts = []
for i in range(1, n):
    if dates[i].month != dates[i - 1].month and i - FORM >= 0:
        month_starts.append(i)
month_starts = [i for i in month_starts if dates[i] >= pd.Timestamp("2017-01-01")]


DUAL_CLASS_DROP = {"GOOG", "FOX", "NWS", "UA", "DISCK", "DISCB", "BF.A", "LEN.B"}


def groups_for(t0, mode, universe="current"):
    f0, f1 = t0 - FORM, t0 - 1
    if universe == "current":
        allowed = set(cur_syms); sec, sub = sector_of, subind_of
    else:  # point-in-time S&P 500 from the Jan-1 snapshot of the trading-start year
        m = pit[pit.year == dates[t0].year]
        allowed = set(m.symbol) & set(C.columns)
        sec, sub = dict(zip(m.symbol, m.sector)), dict(zip(m.symbol, m.sub_industry))
    allowed -= DUAL_CLASS_DROP  # one share class per company (GOOG/GOOGL etc. kept only as a sanity pair)
    elig = L.formation_eligible(C, V, f0, f1, allowed)
    key = sec if mode == "sector" else sub
    g = {}
    for s in elig:
        g.setdefault(key.get(s, "?"), []).append(s)
    return f0, f1, g


def pair_groups(t0, pairs):
    f0, f1 = t0 - FORM, t0 - 1
    allowed = {s for p in pairs for s in p}
    elig = set(L.formation_eligible(C, V, f0, f1, allowed))
    return f0, f1, {f"{a}/{b}": [a, b] for a, b in pairs if a in elig and b in elig}

# ------------------------------------------------------------------ selection caches
sel_cache = {}


def ggr_sel(t0, grouping, universe="current", pairs=None, top=20):
    k = ("ggr", t0, grouping, universe, tuple(pairs) if pairs else None)
    if k not in sel_cache:
        f0, f1, g = pair_groups(t0, pairs) if pairs else groups_for(t0, grouping, universe)
        sel_cache[k] = (f0, f1, L.ssd_pairs(C, f0, f1, g, top=max(top, 20)))
    f0, f1, df = sel_cache[k]
    return f0, f1, df.head(top)


def coint_sel(t0, rank, universe="current", pairs=None, top=20, alpha=0.05, hl_min=1, grouping="sector"):
    k = ("coint", t0, rank, universe, tuple(pairs) if pairs else None, alpha, hl_min, grouping)
    if k not in sel_cache:
        f0, f1, g = pair_groups(t0, pairs) if pairs else groups_for(t0, grouping, universe)
        sel_cache[k] = (f0, f1, L.coint_pairs(C, f0, f1, g, top=max(top, 20), rank=rank, alpha=alpha, hl_min=hl_min))
    f0, f1, df = sel_cache[k]
    return f0, f1, df.head(top)

# ------------------------------------------------------------------ strategy runners

def build_ggr(p, universe="current", pairs=None):
    out = []
    for t0 in month_starts:
        t_end = min(t0 + TRADE - 1, n - 2)
        f0, f1, sel = ggr_sel(t0, p.get("grouping", "sector"), universe, pairs, p["npairs"])
        trs = []
        for r in sel.itertuples():
            for tr in L.ggr_signals(C, r.a, r.b, f0, f1, t_end, k=p["k"]):
                tr.setdefault("xs", t_end); tr.setdefault("reason", "period_end"); tr["port"] = t0
                trs.append(tr)
        out.append((t0, t_end, trs))
    return out


def build_coint(p, universe="current", pairs=None):
    out = []
    for t0 in month_starts:
        t_end = min(t0 + TRADE - 1, n - 2)
        f0, f1, sel = coint_sel(t0, p.get("rank", "tstat"), universe, pairs, p["npairs"], p.get("alpha", 0.05),
                                p.get("hl_min", 1), p.get("grouping", "sector"))
        trs = []
        for r in sel.itertuples():
            if p["method"] == "kalman":
                ts_ = L.kalman_signals(C, r, f0, f1, t_end, delta=p["delta"], entry=p["entry"], exit=p["exit"])
            else:
                ts_ = L.coint_signals(C, r, f1, t_end, entry=p["entry"], exit=p["exit"], stop=p["stop"], tcap=p.get("tcap"))
            for tr in ts_:
                tr.setdefault("xs", t_end); tr.setdefault("reason", "period_end"); tr["port"] = t0
                trs.append(tr)
        out.append((t0, t_end, trs))
    return out


# ------------------------------------------------------------------ earnings (SEC EDGAR 8-K Item 2.02)
from run_backtest_helpers import EARN, earn_between  # noqa: E402


def filter_earnings(built, pre=10):
    """Skip entries if either leg has an earnings reaction day within the next `pre` trading days
    (earnings dates are pre-announced, so this is implementable ex ante)."""
    out = []
    for ts, te, trs in built:
        out.append((ts, te, [t for t in trs if not (earn_between(t["a"], t["es"] + 1, t["es"] + pre) or
                                                    earn_between(t["b"], t["es"] + 1, t["es"] + pre))]))
    return out


def run_pairs(p, costs=COSTS, exec_mode="open", universe="current", pairs=None, built=None):
    built = built or (build_ggr(p, universe, pairs) if p["method"] == "ggr" else build_coint(p, universe, pairs))
    if p.get("earn_filter"):
        built = filter_earnings(built)
    r, tdf = L.run_portfolios(built, panel, dv, costs, exec_mode, p.get("hedge", "dollar"), n_slots=p["npairs"])
    first = dates[month_starts[0]]
    return r.loc[first:], tdf, built

# ------------------------------------------------------------------ Avellaneda-Lee ETF residual

def al_weights(entry=1.25, exit_long=0.5, exit_short=0.75, window=60, kappa_min=252 / 30, slot=1 / 50, long_only=False):
    rets = C.pct_change()
    stocks = [s for s in cur_syms if SECTOR_ETF.get(sector_of.get(s)) in C.columns]
    etf_of = {s: SECTOR_ETF[sector_of[s]] for s in stocks}
    etfs = sorted(set(etf_of.values()))
    S = rets[stocks].values; E = rets[[etf_of[s] for s in stocks]].values
    T, m = S.shape
    pos = np.zeros(m); beta_now = np.zeros(m)
    Wst = np.zeros((T, m)); Wbeta = np.zeros((T, m))
    for t in range(window + 1, T):
        s = S[t - window + 1:t + 1]; e = E[t - window + 1:t + 1]
        ok = ~np.isnan(s).any(0) & ~np.isnan(e).any(0)
        sm, em = np.nanmean(s, 0), np.nanmean(e, 0)
        cov = np.nanmean((s - sm) * (e - em), 0); var = np.nanmean((e - em) ** 2, 0)
        b = np.where(var > 0, cov / np.where(var > 0, var, 1), 0)
        a = sm - b * em
        eps = s - a - b * e
        X = np.nancumsum(eps, 0)
        x0, x1 = X[:-1], X[1:]
        x0m, x1m = x0.mean(0), x1.mean(0)
        bb = ((x0 - x0m) * (x1 - x1m)).sum(0) / np.maximum(((x0 - x0m) ** 2).sum(0), 1e-18)
        aa = x1m - bb * x0m
        z = x1 - aa - bb * x0
        valid = ok & (bb > 0) & (bb < 1)
        kappa = np.where(valid, -np.log(np.where(valid, bb, 0.5)) * 252, 0)
        mm = np.where(valid, aa / (1 - np.where(valid, bb, 0)), 0)
        seq = np.where(valid, np.sqrt(z.var(0) / np.maximum(1 - bb ** 2, 1e-12)), np.inf)
        good = valid & (kappa > kappa_min)
        mbar = mm[good].mean() if good.any() else 0
        sc = np.where(good, -(mm - mbar) / seq, 0)
        # state machine
        new = pos.copy()
        new[(pos == 0) & good & (sc < -entry)] = 1
        if not long_only:
            new[(pos == 0) & good & (sc > entry)] = -1
        new[(pos == 1) & (sc > -exit_long)] = 0
        new[(pos == -1) & (sc < exit_short)] = 0
        new[np.isnan(S[t])] = 0
        opened = (new != 0) & (pos == 0)
        beta_now[opened] = b[opened]
        pos = new
        Wst[t] = pos; Wbeta[t] = pos * beta_now
    nopen = np.abs(Wst).sum(1)
    scale = np.where(nopen * slot > 1.0, 1.0 / np.maximum(nopen, 1), slot)  # stock gross <= 1 (+ hedge)
    Ws = pd.DataFrame(Wst * scale[:, None], index=dates, columns=stocks)
    He = pd.DataFrame(0.0, index=dates, columns=etfs)
    for j, s in enumerate(stocks):
        He[etf_of[s]] -= Wbeta[:, j] * scale
    W = pd.concat([Ws, He], axis=1)
    return W


def reversal_weights(lookback=5, frac=0.1):
    """Weekly short-term reversal: long the worst decile / short the best decile of the past `lookback`
    day return within the current universe, $1 long + $1 short (same 2x gross convention)."""
    cl = C[cur_syms]
    past = cl / cl.shift(lookback) - 1
    W = pd.DataFrame(np.nan, index=dates, columns=cur_syms)
    for i in range(lookback, n):
        if dates[i].weekday() != 4 and not (i + 1 < n and dates[i + 1].isocalendar()[1] != dates[i].isocalendar()[1]):
            continue
        x = past.iloc[i].dropna()
        k = max(int(len(x) * frac), 1)
        x = x.sort_values()
        w = pd.Series(0.0, index=cur_syms)
        w[x.index[:k]] = 1.0 / k; w[x.index[-k:]] = -1.0 / k
        W.iloc[i] = w.values
    return W.ffill().fillna(0)


def month_end_idx():
    return [i for i in range(n - 1) if dates[i + 1].month != dates[i].month]


def chen_weights(npeers=50, max_months=60, min_months=24, frac=0.1):
    """Chen, Chen, Chen & Li (2019) quasi-pairs: peers = the npeers most correlated stocks on monthly returns
    over the prior <=60 months; signal = peer-portfolio return last month - own return last month; long the top
    decile, short the bottom decile, hold one month. $1 long + $1 short. Also returns the 1-month reversal
    portfolio built on the same dates (long last-month losers / short winners) for the alpha regression."""
    me = month_end_idx()
    cl = C[cur_syms]
    mret = cl.iloc[me].pct_change()  # month-end to month-end
    W = pd.DataFrame(np.nan, index=dates, columns=cur_syms)
    Wr = W.copy()
    for k in range(min_months + 1, len(me)):
        hist = mret.iloc[max(1, k - max_months):k]  # months strictly before the signal month
        last = mret.iloc[k]
        hist = hist.loc[:, hist.notna().sum() >= min_months]
        cols = [c for c in hist.columns if not np.isnan(last.get(c, np.nan))]
        if len(cols) < 100:
            continue
        H = hist[cols]
        cm = H.corr(min_periods=min_months).values
        np.fill_diagonal(cm, -np.inf)
        cm = np.nan_to_num(cm, nan=-np.inf)
        top = np.argsort(-cm, axis=1)[:, :npeers]
        lv = last[cols].values
        peer = lv[top].mean(1)
        sig = pd.Series(peer - lv, index=cols).sort_values()
        q = max(int(len(sig) * frac), 1)
        w = pd.Series(0.0, index=cur_syms); w[sig.index[-q:]] = 1.0 / q; w[sig.index[:q]] = -1.0 / q
        W.iloc[me[k]] = w.values
        rv = pd.Series(lv, index=cols).sort_values()
        w2 = pd.Series(0.0, index=cur_syms); w2[rv.index[:q]] = 1.0 / q; w2[rv.index[-q:]] = -1.0 / q
        Wr.iloc[me[k]] = w2.values
    return W.ffill().fillna(0), Wr.ffill().fillna(0)


def weights_run(W, costs, exec_mode="open"):
    bps = {s: costs.bps(s, dv.get(s, 1e9)) for s in W.columns}
    net, gross, cost, to = L.weights_returns(W, panel, bps, costs.borrow, exec_mode)
    first = dates[month_starts[0]]
    return net.loc[first:], gross.loc[first:], to.loc[first:]

# ------------------------------------------------------------------ variant grid (IS selection)
grid = []
for k in (1.5, 2.0, 2.5):
    for npairs in (10, 20):
        for grouping in ("sector", "subind"):
            grid.append(dict(method="ggr", k=k, npairs=npairs, grouping=grouping))
for entry in (1.5, 2.0, 2.5):
    for stop in (3.5, 4.0):
        for rank in ("tstat", "ssd"):
            for hedge in ("dollar", "beta"):
                grid.append(dict(method="coint", entry=entry, exit=0.5, stop=stop, rank=rank, hedge=hedge, npairs=20))
for delta in (1e-4, 1e-5):
    for entry in (1.5, 2.0):
        grid.append(dict(method="kalman", delta=delta, entry=entry, exit=0.5, rank="tstat", npairs=20))
for (en, xs_) in ((1.25, 0.75), (1.5, 0.5)):
    for lo in (False, True):
        grid.append(dict(method="al", entry=en, exit_short=xs_, exit_long=0.5, long_only=lo))
# literature-spec cointegration (coordinator request): same industry, p<0.01, HL 5-30, z 2/0.5, stop 3.5 + ban,
# time stop min(2*HL, 30)
for rank in ("tstat", "ssd"):
    for hedge in ("dollar", "beta"):
        for grouping in ("sector", "subind"):
            grid.append(dict(method="coint", entry=2.0, exit=0.5, stop=3.5, rank=rank, hedge=hedge, npairs=20,
                             alpha=0.01, hl_min=5, tcap=30, grouping=grouping))
grid.append(dict(method="chen", npeers=50))
# earnings-avoidance overlays on the canonical GGR and literature cointegration
grid.append(dict(method="ggr", k=2.0, npairs=20, grouping="sector", earn_filter=True))
grid.append(dict(method="coint", entry=2.0, exit=0.5, stop=3.5, rank="tstat", hedge="dollar", npairs=20,
                 alpha=0.01, hl_min=5, tcap=30, grouping="subind", earn_filter=True))

import os
if os.environ.get("QUICK"):
    grid = [grid[8], grid[12], grid[-3], grid[-2]]


def vid(p):
    return "_".join(f"{k}={v}" for k, v in p.items())


def split(r):
    return r.loc[IS_START:IS_END], r.loc[OOS_START:]

variant_rows, store = [], {}
for p in grid:
    t1 = time.time()
    if p["method"] in ("al", "chen"):
        if p["method"] == "al":
            W = al_weights(entry=p["entry"], exit_short=p["exit_short"], exit_long=p["exit_long"], long_only=p["long_only"])
        else:
            W, W_rev1m = chen_weights(p["npeers"])
        r, rg, to = weights_run(W, COSTS)
        if p["method"] == "chen":
            first_act = W.index[(W.abs().sum(1) > 0).values][0]
            r, rg, to = r.loc[first_act:], rg.loc[first_act:], to.loc[first_act:]
        r2, _, _ = weights_run(W, COSTS2)
        rc, _, _ = weights_run(W, COSTS, "close")
        tdf = None
        store[vid(p)] = dict(p=p, r=r, r_gross=rg, r_2x=r2, r_close=rc, tdf=None, turnover=to, W=W,
                             r_flat5=weights_run(W, FLAT5)[0])
        if p["method"] == "chen":
            store[vid(p)]["r_rev1m"] = weights_run(W_rev1m, COSTS)[0]
    else:
        r, tdf, built = run_pairs(p)
        rg, _, _ = run_pairs(p, L.ZERO, built=built)
        r2, _, _ = run_pairs(p, COSTS2, built=built)
        rc, tdfc, _ = run_pairs(p, COSTS, "close", built=built)
        store[vid(p)] = dict(p=p, r=r, r_gross=rg, r_2x=r2, r_close=rc, tdf=tdf, built=built,
                             r_flat5=run_pairs(p, FLAT5, built=built)[0])
    ri, ro = split(r)
    mi, mo = L.metrics(ri, spy_r), L.metrics(ro, spy_r)
    variant_rows.append(dict(id=vid(p), method=p["method"], is_sharpe=mi["sharpe"], is_cagr=mi["cagr"],
                             is_mdd=mi["max_drawdown"], oos_sharpe=mo["sharpe"], oos_cagr=mo["cagr"],
                             oos_mdd=mo["max_drawdown"],
                             is_gross_sharpe=L.metrics(split(store[vid(p)]["r_gross"])[0])["sharpe"],
                             trades=0 if tdf is None else len(tdf)))
    print(f"{vid(p):70s} IS SR {mi['sharpe']:5.2f} CAGR {mi['cagr']:6.1%} | OOS SR {mo['sharpe']:5.2f} CAGR {mo['cagr']:6.1%}  ({time.time()-t1:.0f}s)", flush=True)

vdf = pd.DataFrame(variant_rows)
if os.environ.get("QUICK"):
    pickle.dump({k: {kk: vv for kk, vv in v.items() if kk != "W"} for k, v in store.items()}, open(L.CACHE / "quick.pkl", "wb")); raise SystemExit
vdf.to_csv(R / "variants_all.csv", index=False)

# ------------------------------------------------------------------ IS selection (frozen for OOS)
best_by_method = vdf.loc[vdf.groupby("method").is_sharpe.idxmax()].set_index("method")
best_overall = vdf.loc[vdf.is_sharpe.idxmax()]
print("best overall (IS):", best_overall.id)
# GGR canonical (paper) configuration reported regardless of IS rank
canon = {"ggr": vid(dict(method="ggr", k=2.0, npairs=20, grouping="sector")),
         "coint": vid(dict(method="coint", entry=2.0, exit=0.5, stop=4.0, rank="tstat", hedge="dollar", npairs=20)),
         "kalman": vid(dict(method="kalman", delta=1e-4, entry=2.0, exit=0.5, rank="tstat", npairs=20)),
         "al": vid(dict(method="al", entry=1.25, exit_short=0.75, exit_long=0.5, long_only=False)),
         "al_longonly": vid(dict(method="al", entry=1.25, exit_short=0.75, exit_long=0.5, long_only=True)),
         "coint_lit": vid(dict(method="coint", entry=2.0, exit=0.5, stop=3.5, rank="tstat", hedge="dollar", npairs=20,
                               alpha=0.01, hl_min=5, tcap=30, grouping="subind")),
         "chen": vid(dict(method="chen", npeers=50))}

is_sharpes = vdf.is_sharpe.values
yrs = lambda r: len(r) / L.TD


def full_report(key):
    s = store[key]
    out = {}
    for lab, ser in (("net", s["r"]), ("gross", s["r_gross"]), ("net_2x_costs", s["r_2x"]), ("net_same_close", s["r_close"]),
                     ("net_flat_10bp_rt", s.get("r_flat5", s["r"]))):
        ri, ro = split(ser)
        out[lab] = dict(IS=L.metrics(ri, spy_r), OOS=L.metrics(ro, spy_r), FULL=L.metrics(ser, spy_r))
    ri, ro = split(s["r"])
    out["bootstrap_IS"] = L.block_bootstrap(ri)
    out["bootstrap_OOS"] = L.block_bootstrap(ro)
    out["deflated_IS"] = L.deflated_sharpe(ri, is_sharpes, len(is_sharpes))
    out["psr_OOS"] = L.deflated_sharpe(ro, [0.0], 1)
    out["cost_drag_cagr"] = dict(IS=out["gross"]["IS"]["cagr"] - out["net"]["IS"]["cagr"],
                                 OOS=out["gross"]["OOS"]["cagr"] - out["net"]["OOS"]["cagr"])
    tdf = s.get("tdf")
    if tdf is not None and len(tdf):
        ent = pd.to_datetime(tdf.entry)
        ti, to_ = tdf[(ent >= IS_START) & (ent <= IS_END)], tdf[ent >= OOS_START]
        out["trades_IS"] = L.trade_stats(ti, 5)
        out["trades_OOS"] = L.trade_stats(to_, yrs(ro))
        # annual turnover in units of committed capital: sum of traded notional / n_slots / active portfolios(6)
        out["turnover_x_per_year"] = float(tdf.turnover.sum() / s["p"]["npairs"] / 6 / (len(s["r"]) / L.TD))
    else:
        to = s.get("turnover")
        out["turnover_x_per_year"] = float(to.sum() / (len(to) / L.TD)) if to is not None else None
    return out

reports = {}
keys = set(best_by_method.id) | set(canon.values())
for k in keys:
    reports[k] = full_report(k)

# ------------------------------------------------------------------ ETF and classic pairs, survivorship
extra = {}
NEAR_DUP = {("SPY", "IVV"), ("SPY", "VOO"), ("IVV", "VOO"), ("QQQ", "QQQM"), ("VTI", "ITOT"), ("IJH", "MDY"),
            ("GLD", "IAU"), ("EFA", "VEA"), ("EEM", "VWO"), ("HYG", "JNK"), ("AGG", "BND"), ("XLE", "VDE"),
            ("VNQ", "IYR"), ("XLRE", "VNQ"), ("XLK", "VGT"), ("XLF", "VFH"), ("XLV", "VHT"), ("XLY", "VCR"),
            ("XLP", "VDC"), ("XLI", "VIS"), ("XLB", "VAW"), ("XLU", "VPU"), ("LQD", "VCIT")}
ETF_DISTINCT = [p for p in ETF_PAIRS if p not in NEAR_DUP]
for lab, pairs in (("etf", ETF_PAIRS), ("etf_distinct", ETF_DISTINCT), ("classic", CLASSIC)):
    for p in (dict(method="ggr", k=2.0, npairs=10 if lab.startswith("etf") else 15, grouping="pair"),
              dict(method="coint", entry=2.0, exit=0.5, stop=4.0, rank="tstat", hedge="dollar", npairs=10 if lab.startswith("etf") else 15)):
        key = f"{lab}_{p['method']}"
        r, tdf, built = run_pairs(p, pairs=pairs)
        rg, _, _ = run_pairs(p, L.ZERO, pairs=pairs, built=built)
        r2, _, _ = run_pairs(p, COSTS2, pairs=pairs, built=built)
        store[key] = dict(p=p, r=r, r_gross=rg, r_2x=r2, r_close=run_pairs(p, COSTS, "close", pairs=pairs, built=built)[0], tdf=tdf, built=built)
        reports[key] = full_report(key)
        vdf.loc[len(vdf)] = dict(id=key, method=key, is_sharpe=reports[key]["net"]["IS"]["sharpe"], is_cagr=reports[key]["net"]["IS"]["cagr"],
                                 is_mdd=reports[key]["net"]["IS"]["max_drawdown"], oos_sharpe=reports[key]["net"]["OOS"]["sharpe"],
                                 oos_cagr=reports[key]["net"]["OOS"]["cagr"], oos_mdd=reports[key]["net"]["OOS"]["max_drawdown"],
                                 is_gross_sharpe=reports[key]["gross"]["IS"]["sharpe"], trades=len(tdf))
        print(key, "IS SR", round(reports[key]["net"]["IS"]["sharpe"], 2), "OOS SR", round(reports[key]["net"]["OOS"]["sharpe"], 2), flush=True)

# per classic pair breakdown (GGR rules, one pair at a time is what the 15-pair book trades)
cl = store["classic_ggr"]["tdf"].copy()
cl["pair"] = cl.a + "/" + cl.b
cl["period"] = np.where(pd.to_datetime(cl.entry) < OOS_START, "IS", "OOS")
cls = cl.groupby(["pair", "period"]).agg(trades=("net", "size"), net_sum=("net", "sum"), gross_sum=("gross", "sum"),
                                         win_rate=("net", lambda x: (x > 0).mean()), avg_days=("days", "mean"),
                                         pct_conv=("reason", lambda x: (x == "converge").mean())).reset_index()
cls.to_csv(R / "classic_pairs_breakdown.csv", index=False)
etf = store["etf_ggr"]["tdf"].copy(); etf["pair"] = etf.a + "/" + etf.b
etf.groupby("pair").agg(trades=("net", "size"), net_sum=("net", "sum"), gross_sum=("gross", "sum"),
                        win_rate=("net", lambda x: (x > 0).mean())).reset_index().to_csv(R / "etf_pairs_breakdown.csv", index=False)

# survivorship: canonical GGR and coint on the point-in-time universe
surv = {}
for m_ in ("ggr", "coint", "coint_lit", "kalman"):
    p = store[canon[m_]]["p"]
    r_pit, tdf_pit, _ = run_pairs(p, universe="pit")
    store[f"pit_{m_}"] = dict(p=p, r=r_pit, tdf=tdf_pit)
    a_i, a_o = split(store[canon[m_]]["r"]); b_i, b_o = split(r_pit)
    surv[m_] = dict(current_IS=L.metrics(a_i), pit_IS=L.metrics(b_i), current_OOS=L.metrics(a_o), pit_OOS=L.metrics(b_o),
                    pit_delist_trades=int((tdf_pit.reason == "delist").sum()), pit_trades=int(len(tdf_pit)))
    print("survivorship", m_, "current IS/OOS SR", round(surv[m_]["current_IS"]["sharpe"], 2), round(surv[m_]["current_OOS"]["sharpe"], 2),
          "| PIT", round(surv[m_]["pit_IS"]["sharpe"], 2), round(surv[m_]["pit_OOS"]["sharpe"], 2), flush=True)
# eligible-universe size by formation, current vs PIT
sizes = []
for t0 in month_starts[::6]:
    _, _, gc = groups_for(t0, "sector", "current"); _, _, gp = groups_for(t0, "sector", "pit")
    nc, npt = sum(map(len, gc.values())), sum(map(len, gp.values()))
    inter = len(set(sum(gc.values(), [])) & set(sum(gp.values(), [])))
    sizes.append(dict(date=dates[t0].date(), current=nc, pit=npt, overlap=inter))
pd.DataFrame(sizes).to_csv(R / "survivorship_universe_sizes.csv", index=False)

# ------------------------------------------------------------------ benchmarks
Wrev = reversal_weights()
rev, rev_g, rev_to = weights_run(Wrev, COSTS)
spy = spy_r.loc[dates[month_starts[0]]:]
store["bench_spy"] = dict(r=spy); store["bench_reversal"] = dict(r=rev, r_gross=rev_g)
bench = {}
for k_, ser in (("SPY", spy), ("reversal_5d_net", rev), ("reversal_5d_gross", rev_g)):
    i_, o_ = split(ser)
    bench[k_] = dict(IS=L.metrics(i_, spy_r), OOS=L.metrics(o_, spy_r))

with open(L.CACHE / "run_state.pkl", "wb") as f:
    pickle.dump(dict(store={k: {kk: vv for kk, vv in v.items() if kk not in ("W",)} for k, v in store.items()},
                     reports=reports, vdf=vdf, best_by_method=best_by_method.reset_index().to_dict("records"),
                     best_overall=best_overall.to_dict(), canon=canon, surv=surv, bench=bench,
                     month_starts=month_starts, dv=dv), f)
print(f"done in {time.time()-t_start:.0f}s")
