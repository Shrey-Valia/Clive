"""Load the round-1 scan events (read-only) and attach the round-3 intraday arrays and features.

    python3 data.py      # builds .cache/r3_events.parquet and .cache/r3_bars.npz

Every feature here is restricted to information available at its decision time:
  * scan-day (T) features: up to 15:45 on T (day0 bars stop at close-15m)
  * premarket features: T+1 04:00 .. 09:29 (known before a 09:31 entry)
  * open features (gap at open, opening range): only used by rules that enter after them
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from r3common import CACHE, LAST_DAY, SRC

NMIN = 390


def load_events() -> pd.DataFrame:
    """events.parquet + the same derived columns as round 1 (analyze.load / engine.prep)."""
    ev = pd.read_parquet(os.path.join(SRC, "events.parquet"))
    cal = pd.read_csv(os.path.join(SRC, "calendar.csv"))
    days = pd.to_datetime(cal.date)
    nxt_gap = (days.shift(-1) - days).dt.days
    ev["cal_gap"] = ev.ti.map(dict(enumerate(nxt_gap))).fillna(1)
    ev["weekday"] = pd.to_datetime(ev.date).dt.weekday
    nxt = dict(zip(cal.date[:-1], cal.date[1:]))
    close = dict(zip(cal.date, cal.close))
    ev["d1"] = ev.date.map(nxt)
    ev["close1_min"] = ev.d1.map(lambda d: (int(close[d][:2]) - 9) * 60 + int(close[d][3:]) - 30 if d in close else NMIN)
    for k in range(1, 6):
        ev[f"ok{k}"] = (ev[f"ti{k}"] - ev.ti) == k
    etf = pd.read_parquet(os.path.join(SRC, "etf_daily.parquet"))
    for s in ("IWM", "SPY"):
        x = etf[etf.symbol == s].set_index("date")
        ev[f"{s.lower()}_sma50"] = ev.date.map((x.c / x.c.rolling(50).mean() - 1).to_dict())     # T close: known at 15:45 ~
        ev[f"{s.lower()}_sma200"] = ev.date.map((x.c / x.c.rolling(200).mean() - 1).to_dict())
        ev[f"{s.lower()}_ret_T"] = ev.date.map((x.c / x.c.shift(1) - 1).to_dict())
        ev[f"{s.lower()}_ret20"] = ev.date.map((x.c / x.c.shift(20) - 1).to_dict())
        ev[f"{s.lower()}_ret1"] = ev.date.map((x.c.shift(-1) / x.c - 1).to_dict())             # T+1 (hedge only)
    vix = pd.read_csv(os.path.join(SRC, "vix.csv"))
    vix["date"] = pd.to_datetime(vix.DATE).dt.strftime("%Y-%m-%d")
    vs = vix.set_index("date").CLOSE
    ev["vix"] = ev.date.map(vs.shift(1).to_dict())          # prior close (round-1 convention)
    ev["vix_T"] = ev.date.map(vs.to_dict())                 # T close: known at T+1 09:31
    ev["vix_ma20"] = ev.date.map(vs.rolling(20).mean().to_dict())
    ev["ext20"] = ev.p1545 / ev.sma20 - 1
    ev["gap_open_T"] = ev.o / ev.pc - 1
    ev["intraday_T"] = ev.p1545 / ev.o - 1
    ev["off_high"] = ev.p1545 / ev.hi - 1
    ev["logcap"] = np.log10(ev.mcap)
    ev["dollar_vol1545"] = ev.vol1545 * ev.p1545          # adjusted price x adjusted volume = raw dollars
    ev["float_rot"] = ev.vol1545 / ev.adj_ratio.where(ev.adj_ratio > 0, 1) / ev.shares   # shares turned over by 15:45 (raw shares)
    ev["year"] = ev.date.str[:4].astype(int)
    ev["month"] = ev.date.str[5:7].astype(int)
    ev = ev[ev.date <= LAST_DAY].reset_index(drop=True)
    return ev


def build():
    ev = load_events()
    strict = (ev.p1545_raw >= 10) & (ev.adv20 >= 10e6)
    ev = ev[strict].reset_index(drop=True)
    n = len(ev)
    O = np.full((n, NMIN), np.nan, np.float32)
    H, L, C, V, VW = (O.copy() for _ in range(5))
    pm = np.full((n, 6), np.nan)          # pm_last, pm_high, pm_low, pm_vol, pm_vwap, pm_first
    d0f = np.full((n, 5), np.nan)         # vwap_1545, vol_by_1545 (bars), hod_minute, last30_ret, vwap_last60
    etf1 = {}
    idx = {k: i for i, k in enumerate(zip(ev.symbol, ev.date))}
    for d, g in ev.groupby("date"):
        p1 = os.path.join(CACHE, "day1", f"{d}.parquet")
        p0 = os.path.join(CACHE, "day0", f"{d}.parquet")
        if os.path.exists(p1):
            m = pd.read_parquet(p1)
            for s, b in m.groupby("symbol"):
                if s in ("SPY", "IWM", "QQQ"):
                    reg = b[(b.t >= 0) & (b.t < NMIN)]
                    pre = b[b.t < 0]
                    etf1[(s, d)] = (reg.o.iloc[0] if len(reg) and reg.t.iloc[0] == 0 else np.nan,
                                    pre.c.iloc[-1] if len(pre) else np.nan)
                    continue
                i = idx.get((s, d))
                if i is None:
                    continue
                reg = b[(b.t >= 0) & (b.t < NMIN)]
                t = reg.t.values
                O[i, t], H[i, t], L[i, t], C[i, t], V[i, t] = reg.o, reg.h, reg.l, reg.c, reg.v
                VW[i, t] = reg.vw.fillna((reg.h + reg.l + reg.c) / 3)
                pre = b[b.t < 0]
                if len(pre):
                    pv = pre.v.sum()
                    pm[i] = (pre.c.iloc[-1], pre.h.max(), pre.l.min(), pv,
                             (pre.vw.fillna(pre.c) * pre.v).sum() / pv if pv > 0 else np.nan, pre.o.iloc[0])
                else:
                    pm[i, 3] = 0
        if os.path.exists(p0):
            m = pd.read_parquet(p0)
            for s, b in m.groupby("symbol"):
                i = idx.get((s, d))
                if i is None or not len(b):
                    continue
                vv = b.v.values
                pv = vv.sum()
                vw = (b.vw.fillna(b.c).values * vv).sum() / pv if pv > 0 else np.nan
                l60 = b[b.t >= b.t.max() - 59]
                v60 = l60.v.sum()
                last = b.c.iloc[-1]
                b30 = b[b.t <= b.t.max() - 30]
                d0f[i] = (vw, pv, b.t.values[np.argmax(b.h.values)],
                          last / b30.c.iloc[-1] - 1 if len(b30) else np.nan,
                          (l60.vw.fillna(l60.c) * l60.v).sum() / v60 if v60 > 0 else np.nan)
    # Splits after the round-1 cache was built (e.g. HCAI 1:20, 2026) leave the fresh minute bars on a
    # different adjustment basis than the round-1 daily bars. Rescale those events to the daily basis.
    first = np.array([r[np.isfinite(r)][0] if np.isfinite(r).any() else np.nan for r in O])
    k = first / ev.o1.values
    fix = np.isfinite(k) & ((k > 1.4) | (k < 0.7))
    for A in (O, H, L, C, VW):
        A[fix] /= k[fix, None]
    V[fix] *= k[fix, None]
    pm[fix, :3] /= k[fix, None]; pm[fix, 4:] /= k[fix, None]
    d0f[fix, 0] /= k[fix]; d0f[fix, 4] /= k[fix]
    print("rescaled for later splits:", list(zip(ev.symbol[fix], ev.date[fix], np.round(k[fix], 2))))
    ev["has_day1"] = np.isfinite(O).any(axis=1)
    ev["has_day0"] = np.isfinite(d0f[:, 0])
    for j, c in enumerate(["pm_last", "pm_high", "pm_low", "pm_vol", "pm_vwap", "pm_first"]):
        ev[c] = pm[:, j]
    for j, c in enumerate(["vwap1545", "vol0_bars", "hod_min", "last30_ret", "vwap_last60"]):
        ev[c] = d0f[:, j]
    ev["spy_o1"] = [etf1.get(("SPY", d), (np.nan, np.nan))[0] for d in ev.date]
    ev["spy_pm"] = [etf1.get(("SPY", d), (np.nan, np.nan))[1] for d in ev.date]
    ev["iwm_o1"] = [etf1.get(("IWM", d), (np.nan, np.nan))[0] for d in ev.date]
    ev.to_parquet(os.path.join(CACHE, "r3_events.parquet"))
    np.savez_compressed(os.path.join(CACHE, "r3_bars.npz"), O=O, H=H, L=L, C=C, V=V, VW=VW)
    print(n, "events | day1 coverage %.3f | day0 coverage %.3f" % (ev.has_day1.mean(), ev.has_day0.mean()))


def load():
    ev = pd.read_parquet(os.path.join(CACHE, "r3_events.parquet"))
    z = np.load(os.path.join(CACHE, "r3_bars.npz"))
    return ev, {k: z[k] for k in z.files}


if __name__ == "__main__":
    build()
