"""Family F6-F8: cross-sectional stock signals on the liquid universe (px >= $10, ADV20 >= $20M; the top
500 by ADV for reversal). Weekly or monthly rebalance at the close (MOC).

F6 Residual short-term reversal (Blitz, Huij, Lansdorp & Verbeek 2013; de Groot, Huij & Zhou 2012; the
   institutional review's recommended re-test). Residual = stock return minus beta x its sector-SPDR return
   (sector = the SPDR with the highest prior-year correlation; beta from the prior year). Long the
   bottom decile of past residual return, short the top decile; 1-week and 1-month formation.
F7 52-week-high momentum (George & Hwang 2004): nearness = close / max(close, 252d). Long top decile,
   monthly; long-short vs bottom decile and long-only minus SPY.
F8 Short interest (Asquith, Pathak & Ritter 2005; Boehmer, Huszar & Jordan 2010; Diether, Lee & Werner
   2009): short the top SI-to-shares decile with negative 12-1 momentum (borrow 5%/yr), and long the
   lowest-SI decile (minus SPY). FINRA SI is used from its publication date (settlement + 8 business days).
"""
import os

import numpy as np
import pandas as pd

from common import (BORROW_GC, BORROW_HTB, CACHE, RES, SCACHE, SELL_FEES, by_year, corr_block, register,
                    split_perf, stock_cost_side)
from lib import yahoo

P = pd.read_parquet(os.path.join(CACHE, "panel_liquid.parquet"),
                    columns=["symbol", "date", "c", "r_cc", "adv20_l", "px_l", "bad_win"])
P["date"] = pd.to_datetime(P.date)
P.loc[P.bad_win, "r_cc"] = 0.0   # neutralise bad prints (rare) rather than trade them
C = P.pivot(index="date", columns="symbol", values="c")
R = P.pivot(index="date", columns="symbol", values="r_cc")
ADV = P.pivot(index="date", columns="symbol", values="adv20_l")
PX = P.pivot(index="date", columns="symbol", values="px_l")
BAD = P.pivot(index="date", columns="symbol", values="bad_win").fillna(False).astype(bool)
del P
idx = C.index
SPY = yahoo("SPY").c.pct_change().reindex(idx).fillna(0)
Y = yahoo()
SECT = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
SR = Y[Y.symbol.isin(SECT)].pivot(index="date", columns="symbol", values="c").pct_change().reindex(idx)
CS = pd.DataFrame(stock_cost_side(ADV.fillna(0).values), index=idx, columns=C.columns)
LIQ = (PX >= 10) & (ADV >= 2e7)


def sector_resid():
    """Daily residual returns, sector + beta fixed per calendar year from the prior year's data."""
    res = pd.DataFrame(np.nan, index=idx, columns=C.columns)
    for yr in range(2017, 2027):
        tr = (idx.year == yr - 1); te = (idx.year == yr)
        Rt = R[tr]
        best_c = pd.Series(-1.0, index=C.columns); best_s = pd.Series(None, index=C.columns, dtype=object)
        for s in SECT:
            cc = Rt.corrwith(SR.loc[tr, s])
            upd = cc > best_c
            best_c[upd] = cc[upd]; best_s[upd] = s
        ok = best_s.notna() & (Rt.notna().sum() >= 150)
        for s in SECT:
            cols = best_s.index[ok & (best_s == s)]
            if not len(cols):
                continue
            x = SR.loc[tr, s]
            beta = Rt[cols].apply(lambda y: y.cov(x)) / x.var()
            res.loc[te, cols] = R.loc[te, cols].values - np.outer(SR.loc[te, s].fillna(0).values, beta.values)
    return res


def rebal_dates(freq):
    s = pd.Series(idx, index=idx)
    if freq == "W":
        g = s.groupby(idx.to_period("W-FRI")).last()
    else:
        g = s.groupby(idx.to_period("M")).last()
    return pd.DatetimeIndex(g.values)


def book(score, elig, freq, q=0.1, long_low=True, min_names=50):
    """Equal-weight long and short books formed at rebalance closes, held to the next rebalance."""
    wl = pd.DataFrame(np.nan, index=idx, columns=C.columns); ws = wl.copy()
    for d in rebal_dates(freq):
        s = score.loc[d].where(elig.loc[d]).dropna()
        if len(s) < min_names:
            continue
        lo = s[s <= s.quantile(q)].index; hi = s[s >= s.quantile(1 - q)].index
        L, S = (lo, hi) if long_low else (hi, lo)
        a = pd.Series(0.0, index=C.columns); a[L] = 1 / len(L); wl.loc[d] = a
        b = pd.Series(0.0, index=C.columns); b[S] = 1 / len(S); ws.loc[d] = b
    wl = wl.ffill().fillna(0); ws = ws.ffill().fillna(0)
    return wl, ws


def leg(w, borrow=0.0):
    held = w.shift(1).fillna(0)
    gross = (held * R.fillna(0)).sum(axis=1)
    # drift-adjusted turnover approximated by the change in target weights at rebalance dates
    dw = (w - w.shift(1).fillna(0)).abs()
    cost = (dw * CS).sum(axis=1) + (dw * SELL_FEES / 2).sum(axis=1)
    return gross, cost, held.sum(axis=1) * borrow / 252, dw.sum(axis=1)


rows, series = [], {}


def add(key, fam, r, params, turnover=None):
    r = r[r.index >= "2017-02-01"]
    register(key, fam, r, params)
    series[key] = r
    rows.append(dict(name=key, family=fam, **params,
                     ann_turnover=(turnover[turnover.index >= "2017-02-01"].sum() / 9.7) if turnover is not None else np.nan,
                     **split_perf(r), **corr_block(r)))


def ls_and_lo(prefix, fam, score, elig, freq, long_low, params, short_borrow=BORROW_GC):
    wl, ws = book(score, elig, freq, 0.1, long_low)
    gl, cl, _, tl = leg(wl); gs, cs_, bs, ts = leg(ws, short_borrow)
    ls = (gl - cl) - (gs + cs_) - bs
    add(f"{prefix}_LS", fam, ls, params, tl + ts)
    add(f"{prefix}_LminusSPY", fam, (gl - cl) - SPY, params, tl)
    return gl - cl, gs + cs_ + bs


# ---------------- F6 residual reversal
RES_D = sector_resid()
top500 = ADV.rank(axis=1, ascending=False) <= 500
elig = top500 & LIQ & ~BAD
for form, freq in [(5, "W"), (21, "M"), (21, "W")]:
    rr = RES_D.rolling(form, min_periods=int(form * 0.8)).sum()
    raw = R.rolling(form, min_periods=int(form * 0.8)).sum()
    ls_and_lo(f"F6_RESREV_f{form}_{freq}", "F6_reversal", rr, elig & rr.notna(), freq, True,
              {"form": form, "freq": freq, "resid": True})
    if form == 5:
        ls_and_lo(f"F6_RAWREV_f{form}_{freq}", "F6_reversal", raw, elig & raw.notna(), freq, True,
                  {"form": form, "freq": freq, "resid": False})

# ---------------- F7 52-week high
hi52 = C.rolling(252, min_periods=240).max()
near = C / hi52
elig7 = LIQ & near.notna() & ~BAD
ls_and_lo("F7_52WH_M", "F7_52wh", near, elig7, "M", False, {"freq": "M"})
mom = C.shift(21) / C.shift(252) - 1
ls_and_lo("F7_MOM121_M", "F7_52wh", mom, LIQ & mom.notna() & ~BAD, "M", False, {"freq": "M", "signal": "12-1"})

# ---------------- F8 short interest
si = pd.read_parquet(os.path.join(SCACHE, "finra_si.parquet"),
                     columns=["symbolCode", "currentShortPositionQuantity", "settlementDate"])
si["avail"] = pd.to_datetime(si.settlementDate) + pd.offsets.BDay(8)
sh = pd.read_parquet(os.path.join(SCACHE, "shares_out.parquet"))
sh["end"] = pd.to_datetime(sh.end); sh["avail"] = sh.end + pd.Timedelta(days=45)   # 10-Q lag
si = si.rename(columns={"symbolCode": "symbol", "currentShortPositionQuantity": "ss"})
si = si[si.symbol.isin(C.columns)].sort_values("avail")
sh = sh[sh.symbol.isin(C.columns)].sort_values("avail")
SS = si.pivot_table(index="avail", columns="symbol", values="ss", aggfunc="last").reindex(idx.union(si.avail.unique())).ffill().reindex(idx)
SH = sh.pivot_table(index="avail", columns="symbol", values="shares", aggfunc="last").reindex(idx.union(sh.avail.unique())).ffill().reindex(idx)
SIR = (SS / SH.reindex(columns=SS.columns)).reindex(columns=C.columns)
SIR = SIR.where((SIR > 0) & (SIR < 1.5))
elig8 = LIQ & SIR.notna() & mom.notna() & ~BAD
# (a) short high SI & negative momentum: score combines both; equal-weight top decile SI among mom<0
score_a = SIR.where(mom < 0)
wl, ws = book(score_a, elig8 & (mom < 0), "M", 0.1, long_low=False)   # wl = top-SI decile among losers
g, c, b, t = leg(wl, 0)
short_hi = -(g) - c - wl.shift(1).fillna(0).sum(axis=1) * BORROW_HTB / 252
add("F8_SHORT_hiSI_negmom_plusSPY", "F8_si", short_hi + SPY, {"borrow": BORROW_HTB}, t)
# (b) long lowest-SI decile minus SPY; and LS low-SI minus high-SI
wl, ws = book(SIR, elig8, "M", 0.1, long_low=True)
gl, cl, _, tl = leg(wl); gs, cs_, bs, ts = leg(ws, BORROW_HTB)
add("F8_LONG_loSI_minusSPY", "F8_si", (gl - cl) - SPY, {}, tl)
add("F8_LS_loSI_hiSI", "F8_si", (gl - cl) - (gs + cs_) - bs, {"borrow_short": BORROW_HTB}, tl + ts)

res = pd.DataFrame(rows)
res.to_csv(os.path.join(RES, "f6_xsection.csv"), index=False)
pd.DataFrame(series).to_parquet(os.path.join(RES, "f6_series.parquet"))
pd.DataFrame({k: by_year(v) for k, v in series.items()}).to_csv(os.path.join(RES, "f6_by_year.csv"))
pd.set_option("display.width", 250)
cols = ["name", "ann_turnover", "is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr", "all_maxdd", "corr_spy",
        "corr_shortbot", "ret_on_bot_worst20", "ret_on_spy_worst20"]
print(res[cols].round(3).to_string())
