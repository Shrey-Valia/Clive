"""Family F5: intraday momentum / opening-range breakout on SPY, QQQ, IWM (1-min SIP bars, 2016-2026).

Hypotheses
- Market intraday momentum (Gao, Han, Li & Zhou 2018 JFE): the first half-hour return (prior close ->
  10:00) predicts the last half-hour return (15:30 -> 16:00), driven by late-day hedging / informed
  trading. Trade at 15:30 in the sign of r1, exit MOC.
- Noise-area intraday momentum (Zarattini, Barbon & Aziz 2024, SSRN 4824172): breakouts beyond a
  time-of-day "noise band" around the open persist; check every 30 min, trail with VWAP, flat at close.
  Trend-following, so positively convex on high-volatility days (the review's diversifier ask).
- Opening-range breakout (Zarattini & Aziz 2023, SSRN 4416622; Crabel 1990): trade in the direction of the
  first 5-minute candle, stop at its other extreme, flat at the close.
Costs: 1.5 bp per side (SPY/QQQ spreads are ~0.2-0.5 bp; the rest is slippage), plus sell fees.
IS 2016-2021, OOS 2022-2026.
"""
import glob
import os

import numpy as np
import pandas as pd

from common import CACHE, RES, SELL_FEES, by_year, corr_block, register, split_perf
from lib import alpaca_etf

CS = float(os.environ.get("F5_CS", "1.5e-4"))
TAG = os.environ.get("F5_TAG", "")
SELL_FEES = SELL_FEES if not TAG else 0.0


def load(sym):
    fs = sorted(glob.glob(os.path.join(CACHE, "minute_etf", f"{sym}_*.parquet")))
    m = pd.concat([pd.read_parquet(f) for f in fs])
    t = pd.to_datetime(m.ts, utc=True).dt.tz_convert("America/New_York")
    m["day"] = t.dt.tz_localize(None).dt.normalize()
    m["mod"] = t.dt.hour * 60 + t.dt.minute - 570   # minute of day, 0 = 09:30 bar
    m = m[(m["mod"] >= 0) & (m["mod"] < 390)]
    Cl = m.pivot_table(index="day", columns="mod", values="c", aggfunc="last").reindex(columns=range(390)).ffill(axis=1)
    Op = m.pivot_table(index="day", columns="mod", values="o", aggfunc="first").reindex(columns=range(390))
    Hi = m.pivot_table(index="day", columns="mod", values="h", aggfunc="max").reindex(columns=range(390))
    Lo = m.pivot_table(index="day", columns="mod", values="l", aggfunc="min").reindex(columns=range(390))
    V = m.pivot_table(index="day", columns="mod", values="v", aggfunc="sum").reindex(columns=range(390)).fillna(0)
    VW = (m.assign(pv=m.vw * m.v).pivot_table(index="day", columns="mod", values="pv", aggfunc="sum")
          .reindex(columns=range(390)).fillna(0))
    d = alpaca_etf(sym)
    keep = Cl.index.intersection(d.index)
    # drop half days (no bars after 13:00)
    full = Cl.loc[keep].notna().sum(axis=1) > 0
    return Cl.loc[keep], Op.loc[keep], Hi.loc[keep], Lo.loc[keep], V.loc[keep], VW.loc[keep], d.loc[keep]


rows, series = [], {}


def add(key, r, params, extra=None):
    r = r.fillna(0)
    if TAG:
        key = key + TAG
    else:
        register(key, "F5_intraday", r, params)
    series[key] = r
    rows.append(dict(name=key, days_traded=(r != 0).mean(), mean_bp_traded=r[r != 0].mean() * 1e4, **params,
                     **(extra or {}), **split_perf(r), **corr_block(r)))


for sym in ["SPY", "QQQ", "IWM"]:
    Cl, Op, Hi, Lo, V, VW, D = load(sym)
    pc = D.c.shift(1)
    o = Op[0].fillna(D.o)
    close = D.c                        # official close (MOC exit)
    half = Cl.iloc[:, 209].isna() | (Cl.notna().sum(axis=1) < 300)   # early-close days
    # ---------------- F5a Gao et al.
    r1 = Cl[29] / pc - 1                       # prior close -> 10:00 (bar 09:59 close)
    r12 = Cl[359] / Cl[329] - 1                # 15:00 -> 15:30
    last = close / Cl[359] - 1                 # 15:30 -> close
    for v in ["r1", "r1_and_r12"]:
        sig = np.sign(r1) if v == "r1" else np.where(np.sign(r1) == np.sign(r12), np.sign(r1), 0)
        sig = pd.Series(sig, index=Cl.index).where(~half, 0)
        r = sig * last - (sig != 0) * (2 * CS + SELL_FEES)
        add(f"F5a_GHLZ_{v}_{sym}", r, {"sym": sym, "rule": v})
    # ---------------- F5b noise-area momentum
    move = (Cl.div(o, axis=0) - 1).abs()
    sigma = move.rolling(14, min_periods=10).mean().shift(1)          # past 14 days, per minute of day
    ub = np.maximum(o, pc).values[:, None] * (1 + sigma.values)
    lb = np.minimum(o, pc).values[:, None] * (1 - sigma.values)
    vwap = (VW.cumsum(axis=1) / V.cumsum(axis=1).replace(0, np.nan)).values
    px = Cl.values
    checks = list(range(29, 390, 30))   # 10:00, 10:30, ..., 15:30 (bar closes at :59/:29)
    dvol = D.c.pct_change().rolling(14).std().shift(1)
    for lev_mode in ["lev1", "voltgt"]:
        out = np.zeros(len(Cl))
        ntr = np.zeros(len(Cl))
        for i in range(len(Cl)):
            if half.iloc[i] or np.isnan(sigma.values[i, 29]):
                continue
            pos = 0; entry = np.nan; pnl = 0.0; trades = 0
            for j in checks:
                p = px[i, j]
                if np.isnan(p):
                    continue
                if pos == 1 and p < max(ub[i, j], vwap[i, j]):
                    pnl += p / entry - 1; pos = 0; trades += 1
                elif pos == -1 and p > min(lb[i, j], vwap[i, j]):
                    pnl += -(p / entry - 1); pos = 0; trades += 1
                if pos == 0 and j < 389:
                    if p > ub[i, j]:
                        pos = 1; entry = p; trades += 1
                    elif p < lb[i, j]:
                        pos = -1; entry = p; trades += 1
            if pos != 0:
                pnl += pos * (close.iloc[i] / entry - 1); trades += 1
            lev = 1.0 if lev_mode == "lev1" else float(np.clip(0.02 / dvol.iloc[i], 0, 2)) if dvol.iloc[i] > 0 else 0
            out[i] = lev * (pnl - trades * CS - (trades / 2) * SELL_FEES)
            ntr[i] = trades
        r = pd.Series(out, index=Cl.index)
        add(f"F5b_NOISE_{lev_mode}_{sym}", r, {"sym": sym, "lev": lev_mode}, {"avg_sides_per_day": ntr.mean()})
    # ---------------- F5c 5-min ORB
    o5 = Op[0]; c5 = Cl[4]; h5 = Hi.iloc[:, :5].max(axis=1); l5 = Lo.iloc[:, :5].min(axis=1)
    out = np.zeros(len(Cl))
    hi_after = Hi.iloc[:, 5:].values; lo_after = Lo.iloc[:, 5:].values
    ent = Op[5].values
    for i in range(len(Cl)):
        if half.iloc[i] or np.isnan(ent[i]) or c5.iloc[i] == o5.iloc[i]:
            continue
        d = 1 if c5.iloc[i] > o5.iloc[i] else -1
        stop = l5.iloc[i] if d == 1 else h5.iloc[i]
        R = abs(ent[i] - stop)
        if R <= 0:
            continue
        tgt = ent[i] + d * 10 * R
        ex = close.iloc[i]
        for j in range(hi_after.shape[1]):
            h, l = hi_after[i, j], lo_after[i, j]
            if np.isnan(h):
                continue
            if d == 1 and l <= stop:
                ex = stop * (1 - 1e-4); break           # stop fill with 1 bp slip
            if d == -1 and h >= stop:
                ex = stop * (1 + 1e-4); break
            if d == 1 and h >= tgt:
                ex = tgt; break
            if d == -1 and l <= tgt:
                ex = tgt; break
        out[i] = d * (ex / ent[i] - 1) - 2 * CS - SELL_FEES
    add(f"F5c_ORB5_{sym}", pd.Series(out, index=Cl.index), {"sym": sym})

res = pd.DataFrame(rows)
res.to_csv(os.path.join(RES, f"f5_intraday{TAG}.csv"), index=False)
pd.DataFrame(series).to_parquet(os.path.join(RES, f"f5_series{TAG}.parquet"))
pd.DataFrame({k: by_year(v) for k, v in series.items()}).to_csv(os.path.join(RES, f"f5_by_year{TAG}.csv"))
pd.set_option("display.width", 250)
cols = ["name", "days_traded", "mean_bp_traded", "avg_sides_per_day", "is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr",
        "all_maxdd", "oos_maxdd", "corr_spy", "corr_shortbot", "ret_on_bot_worst20", "ret_on_spy_worst20"]
print(res.reindex(columns=cols).round(3).to_string())
