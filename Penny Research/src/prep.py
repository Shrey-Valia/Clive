"""Slim penny panel from the shorting research's panel.parquet (read-only) + candidate lists to fetch.

Writes
  .cache/pp.parquet        rows with raw price <= $13 (prev close or close), 2016-01-04..; adjusted
                           returns, raw price levels, split flags, delisting facts
  .cache/cand_gain.parquet top-40 daily gainers in the penny range (for the 15:45 scan)
  .cache/cand_gap.parquet  top-30 gap-ups at the open in the penny range (for ORB / day-2)
  .cache/cand_lose.parquet top losers over 1/3/5 days in the penny range (for reversal)
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from common import CACHE, SCACHE

COLS = ["symbol", "date", "o", "h", "l", "c", "v", "c_raw", "v_raw", "ti", "pc", "pc_raw", "chg", "nhist",
        "dv_raw", "avgv10", "adv20", "atr14", "sma20", "ret5_prior", "missing_prev",
        "ti1", "o1", "h1", "l1", "c1", "ti2", "c2", "o2", "ti3", "c3", "o3", "ti4", "c4", "ti5", "c5", "o5",
        "l2", "l3", "l4", "l5"]


def main():
    t = pq.read_table(os.path.join(SCACHE, "panel.parquet"), columns=COLS)
    df = t.to_pandas()
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = df.groupby("symbol", sort=False)
    df["ratio"] = df.c_raw / df.c                                  # raw / adjusted
    pr = g.ratio.shift(1)
    jump = df.ratio / pr - 1
    df["rsplit"] = jump > 0.25                                     # raw jumps vs adjusted: reverse split today
    df["fsplit"] = jump < -0.2
    # reverse split in the prior 20 sessions (known at T)
    df["rs_recent20"] = g.rsplit.transform(lambda s: s.rolling(20, min_periods=1).max()).astype(bool)
    # reverse split in the next 5 sessions (look-ahead; used only as a diagnostic)
    df["rs_next5"] = g.rsplit.transform(lambda s: s[::-1].rolling(5, min_periods=1).max()[::-1].shift(-1)).fillna(0).astype(bool)
    df["ret3_prior"] = df.pc / g.c.shift(3) - 1                    # ending at T-1 close
    for k in (1, 3, 5):
        df[f"r{k}"] = df.c / g.c.shift(k) - 1                      # ending at T close (k-day loss/gain)
    df["gap"] = df.o / df.pc - 1
    df["o_raw"] = df.o * df.ratio
    df["last_date"] = g.date.transform("max")
    df["n_after"] = g.cumcount(ascending=False)                    # bars left after this one
    df["c_raw_prev2"] = g.c_raw.shift(2)
    df = df[(df.date >= "2016-01-04") & ((df.c_raw <= 13) | (df.pc_raw <= 13))].copy()
    u = pd.read_csv(os.path.join(SCACHE, "universe.csv"))
    df = df.merge(u[["symbol", "exchange", "status", "shortable", "easy_to_borrow"]], on="symbol", how="left")
    df = df[df.exchange.isin(["NASDAQ", "NYSE", "AMEX"])]
    df.to_parquet(os.path.join(CACHE, "pp.parquet"))
    print("penny panel rows", len(df), "symbols", df.symbol.nunique())

    base = (df.nhist >= 20) & ~df.missing_prev & (df.adv20 >= 500_000)
    # gainers: prev close and close within a slack band around $1-$10
    gm = base & (df.pc_raw >= 0.75) & (df.c_raw <= 13) & (df.c_raw >= 0.8) & (df.chg > 0)
    cg = df[gm].copy()
    cg["rk"] = cg.groupby("date").chg.rank(ascending=False, method="first")
    cg = cg[cg.rk <= 40][["symbol", "date", "chg", "c_raw", "pc_raw"]]
    cg.to_parquet(os.path.join(CACHE, "cand_gain.parquet"))
    # gap-ups at the open (ex-ante at 09:30)
    gp = base & (df.pc_raw >= 0.9) & (df.o_raw >= 1.0) & (df.o_raw <= 10) & (df.gap >= 0.04)
    cp = df[gp].copy()
    cp["rk"] = cp.groupby("date").gap.rank(ascending=False, method="first")
    cp = cp[cp.rk <= 30][["symbol", "date", "gap", "o_raw"]]
    cp.to_parquet(os.path.join(CACHE, "cand_gap.parquet"))
    # losers at the close
    lm = base & (df.c_raw >= 1.0) & (df.c_raw <= 10)
    cl = df[lm].copy()
    keep = np.zeros(len(cl), bool)
    for k in (1, 3, 5):
        rk = cl.groupby("date")[f"r{k}"].rank(ascending=True, method="first")
        keep |= (rk <= 12).values
    cl = cl[keep][["symbol", "date", "r1", "r3", "r5", "c_raw"]]
    cl.to_parquet(os.path.join(CACHE, "cand_lose.parquet"))
    print("cand gain", len(cg), "gap", len(cp), "lose", len(cl))


if __name__ == "__main__":
    main()
