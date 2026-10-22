"""Shared engine: costs by price bucket, borrow, book construction, stats, DSR, bootstrap.

Trade table convention (one row per position):
  date      realisation date of the trade's P&L (exit day for 1-day holds; entry+h for multi-day)
  sig_date  signal date (for bookkeeping)
  symbol, side (+1 long / -1 short), gross (gross return of the position, in the side's direction)
  px        raw price at entry ($) -> spread bucket;  adv (20d ADV $);  sig (daily vol proxy, fraction)
  hold      holding period in trading days (1 for overnight / intraday), cal_days for borrow
  rank      ordering key within the day (lower = better), score (optional)
A Book picks up to n positions per entry day at w per name (gross cap), applies costs,
and returns a daily series indexed by realisation date. Multi-day holds use h staggered
sub-accounts (same convention as the shorting research engine).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as sst

from common import IS_END, OOS_START

# ------------------------------------------------------------------ costs
# one-way half-spread + fees (bps) by raw price; sub-$5 spreads are wide
SPREAD_BPS = [(2, 100), (3, 70), (5, 50), (10, 25), (20, 12), (50, 8), (1e9, 5)]


def one_way_bps(px, adv):
    px = np.asarray(px, float)
    adv = np.asarray(adv, float)
    b = np.full(len(px), 5.0)
    for hi, v in reversed(SPREAD_BPS):
        b = np.where(px < hi, v, b)
    b += np.select([adv < 2e6, adv < 10e6], [20, 10], 0)
    return b


def impact(order_usd, adv, sig, y=1.0):
    """Square-root impact, fraction of price: y * sigma_daily * sqrt(Q / ADV)."""
    q = np.clip(np.asarray(order_usd, float) / np.maximum(np.asarray(adv, float), 1.0), 0, 1)
    return y * np.asarray(sig, float) * np.sqrt(q)


# ------------------------------------------------------------------ borrow
# share of recent penny top-gainers flagged easy-to-borrow on Alpaca today (assets.json, 2026-10-21,
# July-Oct 2026 gainers): $1-3 22%, $3-5 34%, $5-10 43%, $10-13 58%  (see results/borrow_calibration.csv)
ETB_P = [(3, 0.22), (5, 0.34), (10, 0.43), (1e9, 0.80)]


def etb_prob(px):
    px = np.asarray(px, float)
    p = np.full(len(px), 0.8)
    for hi, v in reversed(ETB_P):
        p = np.where(px < hi, v, p)
    return p


def u01(keys) -> np.ndarray:
    """Deterministic uniform(0,1) per key (symbol|date|seed) so availability draws are reproducible."""
    return np.array([int(hashlib.md5(k.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF for k in keys])


PDT_END = "2026-06-04"


@dataclass
class Book:
    name: str
    n: int = 5
    w: float = 0.10                 # per-name weight (fraction of equity)
    gross_cap: float = 0.50
    hold: int = 1                   # trading days per position (sub-accounts)
    equity: float = 100_000.0
    cost_mult: float = 1.0
    impact_y: float = 0.5           # square-root impact coefficient (x2 in the cost stress)
    # shorts
    borrow_mode: str = "alpaca"     # alpaca: only ETB names (random draw at ETB_P), fee 0 intraday / 15%/yr overnight
                                    # locate: every name, HTB fee + locate charge
    htb_fee: float = 1.00           # annual, for locate mode on sub-$10 names
    locate_bps: float = 0.0         # per-trade locate charge for locate mode (bps of notional)
    seed: int = 0
    pdt: bool = False               # <$25k margin: at most 3 intraday round trips per rolling 5 sessions
    intraday: bool = False          # position opened and closed the same session (PDT applies)
    extra: dict = field(default_factory=dict)


def select(tr: pd.DataFrame, b: Book, days=None) -> pd.DataFrame:
    t = tr.sort_values(["entry_date", "rank"]).copy()
    if (t.side < 0).any() and b.borrow_mode == "alpaca":
        keys = (t.symbol + "|" + t.entry_date + "|" + str(b.seed)).tolist()
        avail = u01(keys) < etb_prob(t.px.values)
        t = t[(t.side > 0) | avail]
    t = t.groupby("entry_date", sort=True).head(b.n)
    if b.pdt and b.intraday:
        keep, hist = [], []          # hist: (day index) of each day trade taken
        cal_idx = {d: i for i, d in enumerate(days)}
        for d, g in t.groupby("entry_date", sort=True):
            i = cal_idx[d]
            used = sum(1 for j in hist if i - j < 5)
            k = max(0, 3 - used) if d < PDT_END else len(g)      # PDT rule eliminated 2026-06-04
            keep.append(g.head(k))
            hist += [i] * min(k, len(g))
        t = pd.concat(keep) if keep else t.iloc[:0]
    return t


def net(t: pd.DataFrame, b: Book) -> pd.DataFrame:
    t = t.copy()
    cnt = t.groupby("entry_date").symbol.transform("size")
    w = np.minimum(b.w, b.gross_cap / np.maximum(cnt, 1))
    t["w"] = w
    order = t.w * b.equity / max(b.hold, 1) if b.hold > 1 else t.w * b.equity
    c1 = one_way_bps(t.px.values, t.adv.values) / 1e4 * b.cost_mult + impact(order, t.adv.values, t.sig.values, b.impact_y) * b.cost_mult
    # exit leg cost scales with the exit price relative to entry (gross in side's direction)
    exit_rel = np.where(t.side > 0, 1 + t.gross, 1 - t.gross)
    c2 = c1 * np.clip(exit_rel, 0, None)
    borrow = np.zeros(len(t))
    sh = (t.side < 0).values
    if sh.any():
        cal = t.cal_days.values.astype(float)
        if b.borrow_mode == "alpaca":
            rate = np.where(t.px.values < 10, 0.30, 0.15) * b.extra.get("borrow_mult", 1.0)
            borrow = np.where(sh, rate * cal / 360, 0.0)          # cal = 0 for intraday shorts on Alpaca
        else:
            rate = np.where(t.px.values < 10, b.htb_fee, 0.15) * b.extra.get("borrow_mult", 1.0)
            borrow = np.where(sh, rate * np.maximum(cal, 1) / 360 + b.locate_bps / 1e4 * b.extra.get("borrow_mult", 1.0), 0.0)
    t["cost"] = c1 + c2 + borrow
    t["net"] = t.gross - t.cost
    t["contrib"] = t.w * t.net
    return t


def daily(t: pd.DataFrame, b: Book, days: list[str]) -> pd.DataFrame:
    """Book return per realisation date. Multi-day: h staggered sub-accounts keyed on entry day."""
    if b.hold <= 1:
        d = t.groupby("date").agg(ret=("contrib", "sum"), n=("symbol", "size"))
        d = d.reindex(days).fillna({"ret": 0.0, "n": 0})
    else:
        e = t.groupby("entry_date").agg(ret=("contrib", "sum"), n=("symbol", "size"))
        ent_days = days
        e = e.reindex(ent_days).fillna({"ret": 0.0, "n": 0})
        r = e.ret.values
        eq = np.ones(b.hold)
        curve = []
        for i, x in enumerate(r):
            eq[i % b.hold] *= 1 + x
            curve.append(eq.mean())
        curve = np.array(curve)
        ret = np.r_[curve[0] - 1, curve[1:] / curve[:-1] - 1]
        # entry-day indexed sub-account curve shifted forward by the hold to the realisation date
        s = pd.Series(ret, index=ent_days).shift(b.hold).fillna(0.0)
        d = pd.DataFrame({"ret": s.values, "n": e.n.shift(b.hold).fillna(0).values}, index=ent_days)
    d.index.name = "date"
    return d.reset_index()


def run(tr: pd.DataFrame, b: Book, days: list[str]):
    t = net(select(tr, b, days), b)
    return daily(t, b, days), t


# ------------------------------------------------------------------ stats
def stats(d: pd.DataFrame, t: pd.DataFrame | None = None) -> dict:
    r = d.ret.values.astype(float)
    if len(r) < 2:
        return {}
    eq = np.cumprod(np.maximum(1 + r, 0))
    yrs = len(r) / 252
    sd = r.std(ddof=1)
    dd = eq / np.maximum.accumulate(np.maximum(eq, 1e-300)) - 1
    neg = r[r < 0]
    dsd = math.sqrt((np.minimum(r, 0) ** 2).mean())
    o = {"days": len(r), "active_days": int((d.n > 0).sum()),
         "cagr": eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1.0, "total_return": eq[-1] - 1,
         "vol": sd * math.sqrt(252), "sharpe": r.mean() / sd * math.sqrt(252) if sd > 0 else 0.0,
         "sortino": r.mean() / dsd * math.sqrt(252) if dsd > 0 else 0.0,
         "max_drawdown": dd.min(), "worst_day": r.min(), "best_day": r.max()}
    if t is not None and len(t):
        x = t.net
        pos, ng = x[x > 0].sum(), -x[x < 0].sum()
        o.update({"trades": int(len(x)), "trades_per_year": len(x) / yrs, "mean_trade": x.mean(),
                  "median_trade": x.median(), "win_rate": (x > 0).mean(),
                  "profit_factor": pos / ng if ng > 0 else np.inf, "mean_gross": t.gross.mean(),
                  "mean_cost": t.cost.mean(), "q01": x.quantile(0.01), "q05": x.quantile(0.05),
                  "q95": x.quantile(0.95), "q99": x.quantile(0.99), "worst_trade": x.min(), "best_trade": x.max(),
                  "p_lt_-10": (x < -0.10).mean(), "p_lt_-25": (x < -0.25).mean()})
    else:
        o["trades"] = 0
    return o


def split(d, t=None):
    di, do = d[d.date <= IS_END], d[d.date >= OOS_START]
    if t is None:
        return (di, None), (do, None)
    return (di, t[t.date <= IS_END]), (do, t[t.date >= OOS_START])


def sr0(n_trials, var_sr):
    g = 0.5772156649
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - g) * sst.norm.ppf(1 - 1 / n_trials) + g * sst.norm.ppf(1 - 1 / (n_trials * math.e)))


def dsr(r, n_trials, var_sr_daily):
    """Deflated Sharpe (Bailey & Lopez de Prado 2014), per-period Sharpe."""
    r = np.asarray(r, float)
    if len(r) < 10 or r.std() == 0:
        return np.nan
    sr = r.mean() / r.std(ddof=1)
    s0 = sr0(n_trials, var_sr_daily)
    sk, ku = pd.Series(r).skew(), pd.Series(r).kurt() + 3
    den = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr ** 2))
    return float(sst.norm.cdf((sr - s0) * math.sqrt(len(r) - 1) / den))


def block_boot(r, block=10, reps=2000, seed=0):
    """Stationary (Politis-Romano) bootstrap of the annualised Sharpe and mean daily return."""
    r = np.asarray(r, float)
    n = len(r)
    if n < 30 or r.std() == 0:
        return {"sharpe_lo": np.nan, "sharpe_hi": np.nan, "cagr_lo": np.nan, "cagr_hi": np.nan}
    rng = np.random.default_rng(seed)
    nb = int(math.ceil(n / block))
    starts = rng.integers(0, n, size=(reps, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n      # circular moving blocks
    x = r[idx.reshape(reps, -1)[:, :n]]
    s = x.std(axis=1, ddof=1)
    shs = np.where(s > 0, x.mean(axis=1) / np.where(s > 0, s, 1) * math.sqrt(252), 0)
    lg = np.log(np.maximum(1 + x, 1e-12)).sum(axis=1)
    cg = np.exp(lg * 252 / n) - 1
    return {"sharpe_lo": float(np.percentile(shs, 2.5)), "sharpe_hi": float(np.percentile(shs, 97.5)),
            "cagr_lo": float(np.percentile(cg, 2.5)), "cagr_hi": float(np.percentile(cg, 97.5))}


def boot_trades(x, reps=2000, seed=0):
    x = np.asarray(x, float)
    if len(x) < 10:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    m = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(reps)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))
