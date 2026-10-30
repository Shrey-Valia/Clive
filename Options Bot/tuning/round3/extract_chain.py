"""Pull every cached Alpaca daily option bar for an ETF into one parquet-like pickle.

Output WORK/chain_<SYM>.pkl: DataFrame[date, expiry, strike, cp, close, volume, spot, dte].
Reads only the scratch copy of the cache (offline). Used to calibrate the IV-surface proxy.
"""
import json
import sys

import numpy as np
import pandas as pd

from common import WORK, engine_client
from optionsbot import runner

engine_client()
for sym in sys.argv[1:] or ["SPY", "IWM", "QQQ"]:
    m = runner.market(sym)
    cl = runner.client()
    rows = []
    ch = m.chain
    n_files = 0
    for (exp, cp), g in m._by_exp.items():
        start, end = m._bars_window(exp)
        for s, k in zip(g["symbol"].values, g["strike_price"].values):
            p = cl._cache_path("optbars", f"{s}|{start}|{end}")
            if not p.exists():
                continue
            n_files += 1
            for b in json.loads(p.read_text()):
                rows.append((b["t"][:10], exp, float(k), cp, float(b["c"]), float(b.get("v", 0) or 0)))
    df = pd.DataFrame(rows, columns=["date", "expiry", "strike", "cp", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    spot = m.stock["close"]
    df["spot"] = df["date"].map(spot)
    df = df.dropna(subset=["spot"])
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    df = df[df["dte"] > 0]
    df.to_pickle(WORK / f"chain_{sym}.pkl")
    print(sym, "files", n_files, "bars", len(df), "days", df.date.nunique(),
          "dte range", df.dte.min(), df.dte.max(), flush=True)
