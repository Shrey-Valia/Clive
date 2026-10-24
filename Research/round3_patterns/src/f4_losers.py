"""Family F4: top losers and gap-downs on LIQUID names (raw px >= $10 and 20d ADV >= $20M, both lagged).

Hypotheses
- Short-term reversal / liquidity provision (Jegadeesh 1990; Lehmann 1990; Nagel 2012 "Evaporating
  liquidity"): large one-day losers bounce as liquidity providers are paid; the premium rises with VIX.
- Overnight-vs-intraday clientele (Lou, Polk & Skouras 2019; Berkman et al. 2012): retail attention buying
  arrives at the open, so a loser bought at the close may bounce overnight.
- Losers continuation for news-driven drops (Chan 2003; Savor 2012): large drops on information drift.
- Gap fills: opening gaps on liquid names partially revert intraday (practitioner; Plastun et al. 2019).

Mirror of the gainer study (which the other agent owns): same day-0 ranking, opposite tail.
Day-0 rank uses the full-day close (a 15:50 proxy is validated later on minute bars if anything passes).
Sleeve sizing: equal weight across up to K names, 100% of sleeve capital per day when signals exist.
"""
import os

import numpy as np
import pandas as pd

from common import (BORROW_GC, CACHE, RES, SELL_FEES, by_year, corr_block, register, split_perf,
                    stock_cost_side)

P = pd.read_parquet(os.path.join(CACHE, "panel_liquid.parquet"))
P = P.sort_values(["symbol", "date"])
g = P.groupby("symbol", sort=False)
for k in (1, 2, 5):
    P[f"c{k}"] = g.c.shift(-k)
P["o1"] = g.o.shift(-1); P["h1"] = g.h.shift(-1); P["l1"] = g.l.shift(-1)
P["d1"] = g.date.shift(-1)
P["cs"] = stock_cost_side(P.adv20_l.fillna(0).values)
elig = (P.px_l >= 10) & (P.adv20_l >= 2e7) & P.r_cc.notna() & ~P.bad_win
E = P[elig].copy()
DAYS = pd.DatetimeIndex(sorted(pd.to_datetime(P.date.unique())))


def to_daily(t: pd.DataFrame, ret_col: str, date_col="date", K=5):
    """t: one row per trade with net return; sleeve weight 1/K per name (idle capital earns 0)."""
    s = t.groupby(date_col)[ret_col].sum() / K
    s.index = pd.to_datetime(s.index)
    return s.reindex(DAYS).fillna(0)


rows, series, trades = [], {}, {}


def add(key, daily, t, params):
    register(key, "F4_losers", daily, params)
    series[key] = daily; trades[key] = t
    rows.append(dict(name=key, n_trades=len(t), trades_per_yr=len(t) / 10.8, mean_net_bp=t.net.mean() * 1e4,
                     mean_gross_bp=t.gross.mean() * 1e4, win=(t.net > 0).mean(), **params,
                     **split_perf(daily), **corr_block(daily)))


K = 5
for thr, amin in ((-0.05, 2e7), (-0.10, 2e7), (-0.05, 2e8)):
    L = E[(E.r_cc <= thr) & (E.adv20_l >= amin)].copy()
    L["rk"] = L.groupby("date").r_cc.rank(method="first")
    L = L[L.rk <= K]
    cs = L.cs
    variants = {
        "L1_close_to_open": (L.o1 / L.c - 1, 2 * cs, "date"),
        "L2_close_to_close1": (L.c1 / L.c - 1, 2 * cs, "date"),
        "L3_open1_to_close1": (L.c1 / L.o1 - 1, 2 * 1.5 * cs, "d1"),
        "L4_short_open1_close1": (-(L.c1 / L.o1 - 1), 2 * 1.5 * cs + BORROW_GC / 252, "d1"),
        "L5_close_to_close5": (L.c5 / L.c - 1, 2 * cs, "date"),
    }
    for name, (gross, cost, dcol) in variants.items():
        t = L[["symbol", "date", "d1", "r_cc", "adv20_l"]].copy()
        t["gross"] = gross.values; t["net"] = (gross - cost - SELL_FEES).values
        t = t.dropna(subset=["gross"])
        # L5 holds 5 days: spread its return over the holding window approximately by booking on entry day
        # and dividing capital by 5 overlapping cohorts (K*5 slots)
        kk = K * 5 if name.startswith("L5") else K
        daily = to_daily(t, "net", dcol, kk)
        if amin > 2e7 and name not in ("L1_close_to_open", "L4_short_open1_close1"):
            continue
        sfx = "" if amin == 2e7 else "_adv200m"
        add(f"F4_{name}_thr{int(-thr*100)}{sfx}", daily, t, {"thr": thr, "K": K, "adv_min": amin})

# gap-downs (known at the open) -> buy at the open, (a) exit close, (b) limit at prior close else close
for thr in (-0.03, -0.06):
    G = P[(P.px_l >= 10) & (P.adv20_l >= 2e7) & (P.r_co <= thr) & ~P.bad_win].copy()
    G["rk"] = G.groupby("date").r_co.rank(method="first")
    G = G[G.rk <= K]
    cs = 1.5 * G.cs
    fill_target = G.h >= G.pc
    gross_a = G.c / G.o - 1
    gross_b = np.where(fill_target, G.pc / G.o - 1, G.c / G.o - 1)
    for name, gross, cost in [("G1_gapdown_long_open_close", gross_a, 2 * cs),
                              ("G2_gapdown_long_fill_target", gross_b, cs + G.cs),
                              ("G3_gapdown_short_open_close", -gross_a, 2 * cs + BORROW_GC / 252)]:
        t = G[["symbol", "date", "r_co", "adv20_l"]].copy()
        t["gross"] = np.asarray(gross); t["net"] = t.gross - np.asarray(cost) - SELL_FEES
        t["d1"] = t.date
        daily = to_daily(t, "net", "date", K)
        add(f"F4_{name}_thr{int(-thr*100)}", daily, t, {"thr": thr, "K": K})

res = pd.DataFrame(rows)
res.to_csv(os.path.join(RES, "f4_losers.csv"), index=False)
pd.DataFrame(series).to_parquet(os.path.join(RES, "f4_series.parquet"))
pd.to_pickle(trades, os.path.join(RES, "f4_trades.pkl"))
pd.DataFrame({k: by_year(v) for k, v in series.items()}).to_csv(os.path.join(RES, "f4_by_year.csv"))
pd.set_option("display.width", 250)
cols = ["name", "trades_per_yr", "mean_gross_bp", "mean_net_bp", "win", "is_sharpe", "oos_sharpe", "is_cagr",
        "oos_cagr", "all_maxdd", "corr_spy", "corr_shortbot", "corr_shortbot_active", "ret_on_bot_worst20", "ret_on_spy_worst20"]
print(res[cols].round(3).to_string())
