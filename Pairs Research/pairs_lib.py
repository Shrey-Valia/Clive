"""Core library for the pairs-trading research.

Conventions
- Prices: Alpaca daily bars, adjustment=all (split + dividend adjusted), feed=sip.
- Signals use closes of day t only. Execution either at the next open (default) or at the same close
  (optimistic bound).
- Pair returns are on committed capital, GGR style: each pair slot is committed $1 and holds $1 long and
  $1 short (dollar-neutral at entry, fixed shares afterwards). That is 2x gross per $1, i.e. exactly
  Reg T 50% initial margin on both legs. Returns scale linearly if you run lower gross.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from pathlib import Path
from statsmodels.tsa.adfvalues import mackinnoncrit
from scipy.stats import norm, skew, kurtosis

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
BARS = CACHE / "bars"
TD = 252

# --------------------------------------------------------------------------- data

def load_panel(symbols=None):
    f = CACHE / "panel.pkl"
    if f.exists() and symbols is None:
        return pd.read_pickle(f)
    files = sorted(BARS.glob("*.csv")) if symbols is None else [BARS / f"{s}.csv" for s in symbols]
    O, C, V = {}, {}, {}
    for p in files:
        d = pd.read_csv(p, parse_dates=["date"])
        if not len(d):
            continue
        d = d.drop_duplicates("date").set_index("date")
        O[p.stem], C[p.stem], V[p.stem] = d["open"], d["close"], d["volume"]
    O, C, V = pd.DataFrame(O), pd.DataFrame(C), pd.DataFrame(V)
    dates = C["SPY"].dropna().index
    O, C, V = O.reindex(dates), C.reindex(dates), V.reindex(dates)
    # Ticker-reuse / delisting guard: if a symbol's data has a gap of > 5 trading days, keep only the
    # longest contiguous block that contains its first observation run (treat the gap as a delisting).
    for s in C.columns:
        c = C[s]
        idx = np.where(c.notna().values)[0]
        if len(idx) < 2:
            continue
        gaps = np.where(np.diff(idx) > 5)[0]
        if len(gaps):
            cut = idx[gaps[0]]  # last valid index before the first big gap
            # if a later block is the long one (e.g. symbol reused by an IPO), keep the longest block
            blocks = np.split(idx, gaps + 1)
            best = max(blocks, key=len)
            keep = np.zeros(len(c), bool); keep[best[0]:best[-1] + 1] = True
            for M in (O, C, V):
                M.loc[~keep, s] = np.nan
    # forward-fill single missing bars inside a symbol's life (halts), not past its end
    for M in (O, C):
        first = M.apply(lambda x: x.first_valid_index())
        last = M.apply(lambda x: x.last_valid_index())
        filled = M.ffill(limit=3)
        for s in M.columns:
            if first[s] is None:
                continue
            m = (M.index < first[s]) | (M.index > last[s])
            filled.loc[m, s] = np.nan
        M.loc[:, :] = filled
    O = O.fillna(C)  # missing opens: use close
    panel = {"open": O, "close": C, "volume": V.fillna(0), "dates": dates}
    if symbols is None:
        pd.to_pickle(panel, f)
    return panel


def universe_current():
    return pd.read_csv(CACHE / "universe_current.csv")


def pit_members(year):
    pit = pd.read_csv(CACHE / "pit_membership.csv")
    return pit[pit.year == year]

# --------------------------------------------------------------------------- costs

class Costs:
    def __init__(self, stock_bps_liquid=3.0, stock_bps_other=5.0, etf_bps_major=2.0, etf_bps_other=8.0,
                 borrow=0.005, mult=1.0, etfs=()):
        self.sl, self.so, self.em, self.eo = stock_bps_liquid, stock_bps_other, etf_bps_major, etf_bps_other
        self.borrow, self.mult, self.etfs = borrow, mult, set(etfs)

    def bps(self, sym, dollar_vol):
        if sym in self.etfs:
            b = self.em if dollar_vol >= 1e9 else (4.0 if dollar_vol >= 1e8 else self.eo)
        else:
            b = self.sl if dollar_vol >= 5e8 else self.so
        return b * self.mult * 1e-4

    def scaled(self, mult, borrow_mult=None):
        return Costs(self.sl, self.so, self.em, self.eo, self.borrow * (borrow_mult if borrow_mult is not None else mult),
                     mult, self.etfs)

ZERO = Costs(0, 0, 0, 0, 0.0)

# --------------------------------------------------------------------------- formation / selection

def formation_eligible(C, V, f0, f1, allowed):
    """Symbols with a full price history and positive volume over [f0, f1] (inclusive) and a price at f1+1."""
    win = C.iloc[f0:f1 + 1]
    ok = win.notna().all() & (V.iloc[f0:f1 + 1] > 0).all() & C.iloc[min(f1 + 1, len(C) - 1)].notna()
    ok = ok[ok].index
    return [s for s in ok if s in allowed]


def ssd_pairs(C, f0, f1, groups: dict, top=20, min_corr=None):
    """GGR: SSD of normalized cumulative-return price paths within each group."""
    out = []
    for g, syms in groups.items():
        if len(syms) < 2:
            continue
        X = C.iloc[f0:f1 + 1][syms].values
        X = X / X[0]
        sq = (X ** 2).sum(0)
        G = X.T @ X
        S = sq[:, None] + sq[None, :] - 2 * G
        iu = np.triu_indices(len(syms), 1)
        sd = S[iu]
        for k in np.argsort(sd)[: top * 3]:
            i, j = iu[0][k], iu[1][k]
            out.append((syms[i], syms[j], float(sd[k]), g))
    df = pd.DataFrame(out, columns=["a", "b", "ssd", "group"]).sort_values("ssd")
    # a stock may appear in several pairs (as in GGR); just take the top N
    return df.head(top).reset_index(drop=True)


def coint_pairs(C, f0, f1, groups: dict, top=20, hl_min=1, hl_max=30, rank="tstat", alpha=0.05):
    """Engle-Granger on log prices, both orderings; ADF(1 lag) on OLS residuals vs MacKinnon EG critical
    value (2 variables, constant) at `alpha`; half-life filter; rank by ADF t-stat or by SSD."""
    T = f1 - f0 + 1
    crit = mackinnoncrit(N=2, regression="c", nobs=T - 2)
    crit = crit[{0.01: 0, 0.05: 1, 0.10: 2}[alpha]]
    res = []
    for g, syms in groups.items():
        n = len(syms)
        if n < 2:
            continue
        P = C.iloc[f0:f1 + 1][syms].values
        L = np.log(P)
        N = P / P[0]
        Lm = L - L.mean(0)
        cov = Lm.T @ Lm / T
        var = np.diag(cov)
        ii, jj = np.where(~np.eye(n, dtype=bool))  # ordered pairs (y=i, x=j)
        beta = cov[ii, jj] / var[jj]
        E = Lm[:, ii] - beta[None, :] * Lm[:, jj]  # T x m residuals (mean zero)
        dE = np.diff(E, axis=0)
        y = dE[1:]; x1 = E[1:-1]; x2 = dE[:-1]
        s11 = (x1 * x1).sum(0); s22 = (x2 * x2).sum(0); s12 = (x1 * x2).sum(0)
        s1y = (x1 * y).sum(0); s2y = (x2 * y).sum(0)
        det = s11 * s22 - s12 ** 2
        gam = (s22 * s1y - s12 * s2y) / det
        phi = (s11 * s2y - s12 * s1y) / det
        resid = y - gam * x1 - phi * x2
        dof = y.shape[0] - 2
        s2 = (resid ** 2).sum(0) / dof
        se = np.sqrt(s2 * s22 / det)
        t = gam / se
        # half-life from AR(1): dE_t = lam * E_{t-1}
        lam = (E[:-1] * dE).sum(0) / (E[:-1] ** 2).sum(0)
        hl = np.where(lam < 0, -np.log(2) / np.log1p(np.clip(lam, -0.999, -1e-12)), np.inf)
        ssd = ((N[:, ii] - N[:, jj]) ** 2).sum(0)
        sd = E.std(0)
        keep = (t < crit) & (hl >= hl_min) & (hl <= hl_max) & (beta > 0)
        for k in np.where(keep)[0]:
            res.append((syms[ii[k]], syms[jj[k]], float(beta[k]), float(t[k]), float(hl[k]), float(ssd[k]),
                        float(L[:, ii[k]].mean() - beta[k] * L[:, jj[k]].mean()), float(sd[k]), g))
    df = pd.DataFrame(res, columns=["a", "b", "beta", "tstat", "hl", "ssd", "alpha", "sd", "group"])
    if not len(df):
        return df
    df = df.sort_values("tstat" if rank == "tstat" else "ssd")
    # keep one ordering per unordered pair (the better-ranked one)
    df["key"] = [tuple(sorted(x)) for x in zip(df.a, df.b)]
    df = df.drop_duplicates("key").drop(columns="key")
    return df.head(top).reset_index(drop=True)

# --------------------------------------------------------------------------- signal generation
# Each generator returns a list of trade dicts with signal indices: es (entry signal close idx),
# xs (exit signal close idx), side (+1 = long a / short b, -1 = short a / long b), reason.

def ggr_signals(C, a, b, f0, f1, t_end, k=2.0):
    pa, pb = C[a].values, C[b].values
    na, nb = pa[f0:f1 + 1] / pa[f0], pb[f0:f1 + 1] / pb[f0]
    sig = np.std(na - nb)
    ra, rb = pa[f1], pb[f1]  # renormalize at the start of trading
    trades, pos = [], 0
    for t in range(f1 + 1, t_end + 1):
        if np.isnan(pa[t]) or np.isnan(pb[t]):  # delisted: force close at last close
            if pos:
                trades[-1].update(xs=t - 1, reason="delist")
                pos = 0
            break
        s = pa[t] / ra - pb[t] / rb
        if pos == 0:
            if t < t_end and abs(s) > k * sig:
                pos = -1 if s > 0 else 1  # a rich -> short a / long b
                trades.append(dict(a=a, b=b, es=t, side=pos, beta=1.0, sig=sig))
        else:
            if (pos == -1 and s <= 0) or (pos == 1 and s >= 0):
                trades[-1].update(xs=t, reason="converge"); pos = 0
            elif t == t_end:
                trades[-1].update(xs=t, reason="period_end"); pos = 0
    return trades


def coint_signals(C, row, f1, t_end, entry=2.0, exit=0.5, stop=4.0, time_mult=2.0, tcap=None):
    a, b, beta, alpha, sd, hl = row.a, row.b, row.beta, row.alpha, row.sd, row.hl
    la, lb = np.log(C[a].values), np.log(C[b].values)
    tmax = max(int(math.ceil(time_mult * hl)), 2)
    if tcap:
        tmax = min(tmax, tcap)
    trades, pos, t_open, blocked = [], 0, None, False
    for t in range(f1 + 1, t_end + 1):
        if np.isnan(la[t]) or np.isnan(lb[t]):
            if pos:
                trades[-1].update(xs=t - 1, reason="delist"); pos = 0
            break
        z = (la[t] - alpha - beta * lb[t]) / sd
        if pos == 0:
            if not blocked and t < t_end and entry < abs(z) < stop:
                pos = -1 if z > 0 else 1
                t_open = t
                trades.append(dict(a=a, b=b, es=t, side=pos, beta=beta, hl=hl))
        else:
            if abs(z) >= stop:
                trades[-1].update(xs=t, reason="stop"); pos = 0; blocked = True
            elif (pos == -1 and z < exit) or (pos == 1 and z > -exit):
                trades[-1].update(xs=t, reason="converge"); pos = 0
            elif t - t_open >= tmax:
                trades[-1].update(xs=t, reason="time_stop"); pos = 0
            elif t == t_end:
                trades[-1].update(xs=t, reason="period_end"); pos = 0
    return trades


def kalman_signals(C, row, f0, f1, t_end, delta=1e-4, ve=1e-3, entry=2.0, exit=0.5, time_mult=2.0):
    """Dynamic hedge ratio (state = [beta, alpha]) on log prices, filtered from formation start.
    z = one-step forecast error / its predicted std (Chan 2013)."""
    a, b = row.a, row.b
    ya, xb = np.log(C[a].values), np.log(C[b].values)
    Vw = delta / (1 - delta) * np.eye(2)
    th = np.zeros(2); P = np.eye(2)
    trades, pos, t_open = [], 0, None
    tmax = max(int(math.ceil(time_mult * row.hl)), 2)
    for t in range(f0, t_end + 1):
        if np.isnan(ya[t]) or np.isnan(xb[t]):
            if pos:
                trades[-1].update(xs=t - 1, reason="delist"); pos = 0
            break
        F = np.array([xb[t], 1.0])
        R = P + Vw
        yhat = F @ th
        Q = F @ R @ F + ve
        e = ya[t] - yhat
        K = R @ F / Q
        th = th + K * e
        P = R - np.outer(K, F) @ R
        if t <= f1:
            continue
        z = e / math.sqrt(Q)
        if pos == 0:
            if t < t_end and abs(z) > entry:
                pos = -1 if z > 0 else 1; t_open = t
                trades.append(dict(a=a, b=b, es=t, side=pos, beta=float(th[0]), hl=row.hl))
        else:
            if (pos == -1 and z < exit) or (pos == 1 and z > -exit):
                trades[-1].update(xs=t, reason="converge"); pos = 0
            elif t - t_open >= tmax:
                trades[-1].update(xs=t, reason="time_stop"); pos = 0
            elif t == t_end:
                trades[-1].update(xs=t, reason="period_end"); pos = 0
    return trades

# --------------------------------------------------------------------------- trade P&L

def trade_pnl(tr, O, C, dv, costs: Costs, exec_mode="open", hedge="dollar"):
    """Daily P&L (per $1 long-leg notional) of one pair trade. Returns (idx array, pnl array, stats).
    hedge='dollar': $1 long / $1 short. hedge='beta': $1 on a, $beta on b, scaled so gross = 2."""
    a, b, side = tr["a"], tr["b"], tr["side"]
    n = len(C)
    if exec_mode == "open":
        i0, i1 = tr["es"] + 1, tr["xs"] + 1
        if tr.get("reason") == "delist":  # cannot trade next open of a delisted name: exit at last close
            i1 = tr["xs"]; x_at_close = True
        else:
            x_at_close = False
        if i1 >= n:
            i1 = n - 1; x_at_close = True
        if i0 >= n or i0 > i1:
            return None
    else:
        i0, i1 = tr["es"], tr["xs"]; x_at_close = True
    Ca, Cb, Oa, Ob = C[a].values, C[b].values, O[a].values, O[b].values
    if hedge == "beta":
        wa = 2.0 / (1.0 + tr["beta"]); wb = 2.0 - wa
    else:
        wa = wb = 1.0
    ent_a = Oa[i0] if exec_mode == "open" else Ca[i0]
    ent_b = Ob[i0] if exec_mode == "open" else Cb[i0]
    if not (ent_a > 0 and ent_b > 0):
        return None
    qa = side * wa / ent_a   # +: long a
    qb = -side * wb / ent_b
    idx = np.arange(i0, i1 + 1)
    pa = Ca[i0:i1 + 1].copy(); pb = Cb[i0:i1 + 1].copy()
    if not x_at_close:
        pa[-1] = Oa[i1]; pb[-1] = Ob[i1]
    prev_a = np.r_[ent_a, pa[:-1]]; prev_b = np.r_[ent_b, pb[:-1]]
    if exec_mode != "open":
        prev_a[0], prev_b[0] = ent_a, ent_b
    pnl = qa * (pa - prev_a) + qb * (pb - prev_b)
    ba, bb = costs.bps(a, dv.get(a, 1e10)), costs.bps(b, dv.get(b, 1e10))
    ent_cost = abs(qa) * ent_a * ba + abs(qb) * ent_b * bb
    ex_cost = abs(qa) * pa[-1] * ba + abs(qb) * pb[-1] * bb
    # borrow on the short leg, charged per day held (overnight), on that day's short market value
    short_mv = np.where(qa < 0, -qa * pa, 0) + np.where(qb < 0, -qb * pb, 0)
    borrow = short_mv * costs.borrow / TD
    if len(borrow) > 1:
        borrow[-1] = 0.0  # no overnight on exit day
    pnl = pnl - borrow
    pnl[0] -= ent_cost
    pnl[-1] -= ex_cost
    gross = qa * (pa[-1] - ent_a) + qb * (pb[-1] - ent_b)
    stats = dict(i0=int(i0), i1=int(i1), days=int(i1 - i0), gross=float(gross), net=float(pnl.sum()),
                 cost=float(ent_cost + ex_cost + borrow.sum()),
                 turnover=float(abs(qa) * ent_a + abs(qb) * ent_b + abs(qa) * pa[-1] + abs(qb) * pb[-1]))
    return idx, pnl, stats

# --------------------------------------------------------------------------- portfolio engine

def run_portfolios(port_trades, panel, dv, costs, exec_mode="open", hedge="dollar", n_slots=20):
    """port_trades: list of (t_start, t_end, [trades]) — one per monthly-staggered portfolio.
    Daily strategy return = average over portfolios active that day of (sum pair pnl / n_slots).
    Returns (daily return Series, trade-level DataFrame)."""
    O, C, dates = panel["open"], panel["close"], panel["dates"]
    n = len(dates)
    tot = np.zeros(n)
    active = np.zeros(n)
    rows = []
    for (ts, te, trs) in port_trades:
        pr = np.zeros(n)
        for tr in trs:
            out = trade_pnl(tr, O, C, dv, costs, exec_mode, hedge)
            if out is None:
                continue
            idx, pnl, st = out
            pr[idx] += pnl / n_slots
            st.update(a=tr["a"], b=tr["b"], side=tr["side"], reason=tr.get("reason", "?"),
                      entry=dates[st["i0"]].date(), exit=dates[st["i1"]].date(), port=dates[ts].date())
            rows.append(st)
        end = min(te + 1, n - 1)  # include exit-day open execution
        tot[ts:end + 1] += pr[ts:end + 1]
        active[ts:end + 1] += 1
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(active > 0, tot / np.maximum(active, 1), 0.0)
    return pd.Series(r, index=dates), pd.DataFrame(rows)

# --------------------------------------------------------------------------- weights engine (AL / reversal)

def weights_returns(W, panel, bps_map, borrow, exec_mode="open"):
    """W: DataFrame (dates x symbols) of target weights decided at close t. Returns net daily returns,
    gross returns, cost series, turnover series."""
    O, C = panel["open"][W.columns], panel["close"][W.columns]
    W = W.fillna(0.0)
    Wv = W.values
    cl = C.values
    op = O.values
    rc = np.nan_to_num(cl[1:] / cl[:-1] - 1)
    ro = np.nan_to_num(op[1:] / cl[:-1] - 1)   # overnight
    ri = np.nan_to_num(cl[1:] / op[1:] - 1)    # intraday
    n = len(W)
    g = np.zeros(n); cost = np.zeros(n); to = np.zeros(n); bor = np.zeros(n)
    bps = np.array([bps_map.get(s, 5e-4) for s in W.columns])
    for d in range(1, n):
        w1 = Wv[d - 1]
        if exec_mode == "open":
            w2 = Wv[d - 2] if d >= 2 else np.zeros_like(w1)
            g[d] = w2 @ ro[d - 1] + w1 @ ri[d - 1]
            dw = np.abs(w1 - w2)
        else:
            w0 = Wv[d - 2] if d >= 2 else np.zeros_like(w1)
            g[d] = w1 @ rc[d - 1]
            dw = np.abs(w1 - w0)
        cost[d] = dw @ bps
        to[d] = dw.sum()
        bor[d] = np.clip(-w1, 0, None).sum() * borrow / TD
    idx = W.index
    return (pd.Series(g - cost - bor, idx), pd.Series(g, idx), pd.Series(cost + bor, idx), pd.Series(to, idx))

# --------------------------------------------------------------------------- metrics

def metrics(r: pd.Series, spy: pd.Series | None = None):
    r = r.dropna()
    if len(r) < 5:
        return {}
    eq = (1 + r).cumprod()
    yrs = len(r) / TD
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1.0
    vol = r.std() * math.sqrt(TD)
    sh = r.mean() / r.std() * math.sqrt(TD) if r.std() > 0 else 0.0
    dn = r[r < 0]
    so = r.mean() / math.sqrt((np.minimum(r, 0) ** 2).mean()) * math.sqrt(TD) if len(dn) else 0.0
    dd = eq / eq.cummax() - 1
    m = dict(cagr=float(cagr), total_return=float(eq.iloc[-1] - 1), vol=float(vol), sharpe=float(sh),
             sortino=float(so), max_drawdown=float(dd.min()), days=int(len(r)))
    if spy is not None:
        s = spy.reindex(r.index).fillna(0)
        m["corr_spy"] = float(np.corrcoef(r, s)[0, 1]) if r.std() > 0 else 0.0
        m["beta_spy"] = float(np.cov(r, s)[0, 1] / s.var()) if s.var() > 0 else 0.0
    return m


def trade_stats(tdf: pd.DataFrame, years: float):
    if tdf is None or not len(tdf):
        return dict(trades=0)
    wins = tdf.net > 0
    pf = tdf.net[wins].sum() / max(-tdf.net[~wins].sum(), 1e-12)
    return dict(trades=int(len(tdf)), win_rate=float(wins.mean()), profit_factor=float(pf),
                pct_converged=float((tdf.reason == "converge").mean()), avg_hold_days=float(tdf.days.mean()),
                reasons={k: int(v) for k, v in tdf.reason.value_counts().items()})


def block_bootstrap(r: pd.Series, n_boot=2000, block=20, seed=0):
    """Circular block bootstrap CIs for annualized Sharpe and CAGR."""
    x = r.dropna().values
    T = len(x)
    rng = np.random.default_rng(seed)
    nb = int(math.ceil(T / block))
    sh, cg = np.empty(n_boot), np.empty(n_boot)
    for k in range(n_boot):
        st = rng.integers(0, T, nb)
        ix = (st[:, None] + np.arange(block)[None, :]).ravel()[:T] % T
        y = x[ix]
        sh[k] = y.mean() / y.std() * math.sqrt(TD) if y.std() > 0 else 0
        cg[k] = np.prod(1 + y) ** (TD / T) - 1
    q = lambda v: [float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))]
    return dict(sharpe_ci95=q(sh), cagr_ci95=q(cg), p_sharpe_le_0=float((sh <= 0).mean()))


def deflated_sharpe(r: pd.Series, trial_sharpes_ann, n_trials=None):
    """Bailey & Lopez de Prado (2014) DSR. trial_sharpes_ann: annualized Sharpes of all variants tried."""
    x = r.dropna().values
    T = len(x)
    sr = x.mean() / x.std()
    sk, ku = skew(x), kurtosis(x, fisher=False)
    tr = np.asarray(trial_sharpes_ann) / math.sqrt(TD)
    N = n_trials or len(tr)
    v = tr.var(ddof=1) if len(tr) > 1 else 0.0
    g = 0.5772156649
    sr0 = math.sqrt(v) * ((1 - g) * norm.ppf(1 - 1 / N) + g * norm.ppf(1 - 1 / (N * math.e))) if N > 1 else 0.0
    z = (sr - sr0) * math.sqrt(T - 1) / math.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr ** 2, 1e-12))
    return dict(dsr=float(norm.cdf(z)), sr0_ann=float(sr0 * math.sqrt(TD)), n_trials=int(N),
                psr_vs_0=float(norm.cdf(sr * math.sqrt(T - 1) / math.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr ** 2, 1e-12)))))
