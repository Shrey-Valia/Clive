"""Family F2: overnight vs intraday ("night returns", Cliff-Cooper-Gulen 2008; Kelly & Clark 2011;
Lou, Polk & Skouras 2019 "A tug of war").

F2a  ETF overnight long: buy MOC (close t), sell MOO (open t+1). Long-history Yahoo, IS<=2015, OOS 2016+.
     Costs per side 0.5 / 1.5 / 3 bp (auction prints have ~0 spread; the band covers imbalance + fees).
F2b  ETF intraday long (open->close) for contrast.
F2c  Cross-section, liquid stocks (ADV>=$100M, px>=$10): Lou-Polk-Skouras say momentum is earned
     overnight. Hold the top-decile 12-1 momentum names overnight only (equal weight, rebalanced monthly,
     still trading in/out every day) vs. the same basket close-to-close. Stock costs from common.
F2d  "Tug of war" persistence (Akbas, Boehmer, Jiang & Koch 2022, JFE): within the past 21 days, count days
     with overnight>0 and intraday<0 ("NOS" fight days). High count -> higher future returns. Monthly
     rebalanced long top decile / short bottom decile, plus long-only top decile minus SPY.
"""
import os

import numpy as np
import pandas as pd

from common import (BORROW_GC, CACHE, IS_END, LH_IS_END, LH_OOS_START, OOS_START, RES, SELL_FEES, by_year,
                    corr_block, register, split_perf, stock_cost_side)
from lib import yahoo

rows, series = [], {}

# ---------------- F2a/b ETFs
for sym in ["SPY", "QQQ", "IWM"]:
    px = yahoo(sym); px = px[px.index >= "1994-01-01"]
    on = (px.o / px.c.shift(1) - 1)  # overnight return booked on day t (held from close t-1)
    intr = px.c / px.o - 1
    for cs in [0.5e-4, 1.5e-4, 3e-4]:
        r = (on - 2 * cs - SELL_FEES).fillna(0)
        key = f"F2a_ON_{sym}_c{cs*1e4:.1f}"
        register(key, "F2_overnight", r, {"sym": sym, "cost_side": cs})
        series[key] = r
        rows.append(dict(name=key, gross_mean_bp=on.mean() * 1e4, **split_perf(r, LH_IS_END, LH_OOS_START),
                         **corr_block(r[r.index >= "2016"])))
    r = (intr - 2 * 1.5e-4 - SELL_FEES).fillna(0)
    key = f"F2b_ID_{sym}_c1.5"
    register(key, "F2_overnight", r, {"sym": sym})
    series[key] = r
    rows.append(dict(name=key, gross_mean_bp=intr.mean() * 1e4, **split_perf(r, LH_IS_END, LH_OOS_START),
                     **corr_block(r[r.index >= "2016"])))

# ---------------- stocks
P = pd.read_parquet(os.path.join(CACHE, "panel_liquid.parquet"),
                    columns=["symbol", "date", "o", "c", "r_cc", "r_co", "r_oc", "adv20_l", "px_l"])
P["date"] = pd.to_datetime(P.date)
C = P.pivot(index="date", columns="symbol", values="c")
RCC = P.pivot(index="date", columns="symbol", values="r_cc")
RCO = P.pivot(index="date", columns="symbol", values="r_co")
ROC = P.pivot(index="date", columns="symbol", values="r_oc")
ADV = P.pivot(index="date", columns="symbol", values="adv20_l")
PX = P.pivot(index="date", columns="symbol", values="px_l")
del P
mon_end = C.groupby(C.index.to_period("M")).apply(lambda x: x.index[-1]).values
mon_end = pd.DatetimeIndex(mon_end)


def monthly_basket(score: pd.DataFrame, elig: pd.DataFrame, q_hi=0.9, q_lo=None):
    """Form at each month-end close; hold through next month-end. Returns long and short weight frames
    (daily, forward-filled, equal weight)."""
    wl = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    ws = wl.copy()
    for d in mon_end:
        s = score.loc[d].where(elig.loc[d]).dropna()
        if len(s) < 50:
            continue
        top = s[s >= s.quantile(q_hi)].index
        row = pd.Series(0.0, index=C.columns); row[top] = 1 / len(top)
        wl.loc[d] = row
        if q_lo is not None:
            bot = s[s <= s.quantile(q_lo)].index
            row2 = pd.Series(0.0, index=C.columns); row2[bot] = 1 / len(bot)
            ws.loc[d] = row2
    # weights decided at close d apply to returns from d+1 onward
    return wl.ffill().shift(1).fillna(0), ws.ffill().shift(1).fillna(0)


def basket_returns(w, ret, adv, turnover_daily_both_sides=False):
    g = (w * ret.fillna(0)).sum(axis=1)
    cs = pd.DataFrame(stock_cost_side(adv.fillna(0).values), index=adv.index, columns=adv.columns)
    if turnover_daily_both_sides:  # in at close, out at open every day: 2 sides on full weight each day
        cost = (w * cs * 2).sum(axis=1) + (w * SELL_FEES).sum(axis=1)
    else:
        dw = w.diff().abs().fillna(w.abs())
        cost = (dw * cs).sum(axis=1) + (dw * SELL_FEES / 2).sum(axis=1)
    return g, cost


elig = (ADV >= 1e8) & (PX >= 10)
mom = C.shift(21) / C.shift(252) - 1   # 12-1 momentum known at close t
wl, _ = monthly_basket(mom, elig & mom.notna(), 0.9)
g_on, c_on = basket_returns(wl, RCO, ADV, True)
g_cc, c_cc = basket_returns(wl, RCC, ADV, False)
g_id = (wl * ROC.fillna(0)).sum(axis=1)
for key, g, c in [("F2c_MOM_overnight_only", g_on, c_on), ("F2c_MOM_close_to_close", g_cc, c_cc)]:
    r = (g - c)[g.index >= "2017-01-01"]
    register(key, "F2_overnight", r)
    series[key] = r
    rows.append(dict(name=key, gross_mean_bp=g[g.index >= "2017"].mean() * 1e4, cost_bp=c.mean() * 1e4,
                     **split_perf(r), **corr_block(r)))
rows.append(dict(name="F2c_MOM_intraday_gross_info", gross_mean_bp=g_id[g_id.index >= "2017"].mean() * 1e4))

# tug of war
fight = ((RCO > 0) & (ROC < 0)).astype(float).where(RCO.notna())
nos = fight.rolling(21, min_periods=18).sum()
elig2 = (ADV >= 2e7) & (PX >= 10) & nos.notna()
wl, ws = monthly_basket(nos + 1e-6 * np.random.default_rng(0).standard_normal(nos.shape), elig2, 0.9, 0.1)
gl, cl = basket_returns(wl, RCC, ADV); gs, csh = basket_returns(ws, RCC, ADV)
spy = yahoo("SPY").c.pct_change().reindex(C.index).fillna(0)
borrow = BORROW_GC / 252
ls = (gl - cl) - (gs + csh) - borrow * ws.sum(axis=1)
lo = (gl - cl) - spy
for key, r in [("F2d_TUG_LS", ls), ("F2d_TUG_long_minus_SPY", lo)]:
    r = r[r.index >= "2016-03-01"]
    register(key, "F2_overnight", r)
    series[key] = r
    rows.append(dict(name=key, gross_mean_bp=r.mean() * 1e4, **split_perf(r), **corr_block(r)))

res = pd.DataFrame(rows)
res.to_csv(os.path.join(RES, "f2_overnight.csv"), index=False)
pd.DataFrame(series).to_parquet(os.path.join(RES, "f2_series.parquet"))
pd.DataFrame({k: by_year(v) for k, v in series.items()}).to_csv(os.path.join(RES, "f2_by_year.csv"))
pd.set_option("display.width", 250)
cols = ["name", "gross_mean_bp", "cost_bp", "is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr", "oos_maxdd", "all_maxdd",
        "corr_spy", "corr_shortbot", "ret_on_bot_worst20"]
print(res.reindex(columns=cols).round(3).to_string())
