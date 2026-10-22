"""Ex-ante daily variance-premium signal for every SPY session (test 3).

  RV_e(T)   = sum of squared 1-min log returns of SPY from entry time e to 16:00 on day T
  HARhat_e  = HAR(1,5,22) forecast of RV_e(T), fit on an expanding window of days < T
  f_e       = IS-average share of the close-to-close variance that falls after e
  VRP_vix_e = (VIX1D close of T-1 / 100)^2 / 252 * f_e - HARhat_e        (primary)
  VRP_std_e = (straddle-implied 1-sigma move at e / S)^2 - HARhat_e      (secondary; added in analyze)
  gap_z     = |open_T / close_{T-1} - 1| / (VIX1D_{T-1}/100/sqrt(252))
Output: signals.csv (one row per session).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import zdata as z  # noqa: E402

SPLIT = pd.Timestamp("2025-07-01")
ENTRIES = ["10:00", "12:00", "14:00"]


def main():
    spy = z.spy_all()
    ses = pd.read_csv(HERE / ".cache" / "sessions.csv", index_col=0, parse_dates=True)
    rth = spy.between_time("09:30", "15:59")
    rows = []
    for d, g in rth.groupby(rth.index.date):
        d = pd.Timestamp(d)
        if d not in ses.index:
            continue
        px = np.concatenate([[g["o"].iloc[0]], g["c"].to_numpy()])
        lr = np.diff(np.log(px))
        t = g.index.strftime("%H:%M")
        row = {"date": d, "open": px[0], "close": px[-1], "rv_full": float((lr ** 2).sum())}
        for ent in ENTRIES:
            row[f"rv_{ent}"] = float((lr[t >= ent] ** 2).sum())
        rows.append(row)
    s = pd.DataFrame(rows).set_index("date").sort_index()
    s["on_ret"] = np.log(s.open / s.close.shift(1))
    s["tv"] = s.rv_full + s.on_ret ** 2
    vix = pd.read_csv(HERE / ".cache" / "VIX1D_History.csv", parse_dates=["DATE"]).set_index("DATE")
    s["vix1d_prev"] = vix["CLOSE"].reindex(s.index).shift(1)
    s["vix1d_open"] = vix["OPEN"].reindex(s.index)
    s["iv_day"] = (s.vix1d_prev / 100) ** 2 / 252
    s["gap_z"] = s.on_ret.abs() / np.sqrt(s.iv_day)
    is_mask = s.index < SPLIT
    for ent in ENTRIES:
        y = s[f"rv_{ent}"]
        f_e = (y[is_mask] / s.tv[is_mask]).replace([np.inf, -np.inf], np.nan).median()
        s[f"f_{ent}"] = f_e
        X = pd.DataFrame({"l1": y.shift(1), "l5": y.shift(1).rolling(5).mean(),
                          "l22": y.shift(1).rolling(22).mean()})
        fc = np.full(len(s), np.nan)
        for i in range(len(s)):
            if i < 60:
                continue
            Xi, yi = X.iloc[22:i], y.iloc[22:i]
            m = Xi.notna().all(1) & yi.notna()
            if m.sum() < 30 or not X.iloc[i].notna().all():
                continue
            A = np.column_stack([np.ones(m.sum()), Xi[m].to_numpy()])
            b, *_ = np.linalg.lstsq(A, yi[m].to_numpy(), rcond=None)
            fc[i] = max(float(b[0] + X.iloc[i].to_numpy() @ b[1:]), 0.25 * float(X.iloc[i].l22))
        s[f"har_{ent}"] = fc
        s[f"vrp_vix_{ent}"] = s.iv_day * f_e - fc
    s.to_csv(HERE / "signals.csv")
    print(s[[c for c in s.columns if c.startswith(("vrp", "har", "f_"))]].describe().T)


if __name__ == "__main__":
    main()
