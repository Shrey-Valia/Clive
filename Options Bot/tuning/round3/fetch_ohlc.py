"""Fetch raw + split-adjusted daily SIP bars (2015-06..2026-10-22) for the ETF universe into
tuning/round3/ohlc/<SYM>.csv (columns: date, open, high, low, close = raw; s_* = split-adjusted).
Data endpoint only (optionsbot.data.AlpacaData asserts no order/position URLs); ~3 requests per
symbol, written to the scratch cache copy."""
import os
import sys

import alpaca_env
from common import CACHE, HERE

alpaca_env.load()
os.environ["OPTIONSBOT_MIN_INTERVAL"] = "1.0"
from optionsbot.data import AlpacaData  # noqa: E402

cl = AlpacaData(cache_dir=CACHE)
out = HERE / "ohlc"
out.mkdir(exist_ok=True)
syms = sys.argv[1:] or ["SPY", "IWM", "QQQ", "DIA", "XLF", "XLE", "XLK", "XLI", "XLV", "XLY", "XLP",
                        "XLU", "XLB", "SMH", "GLD", "TLT", "EEM", "EFA", "MDY", "IJR", "RSP"]
for s in syms:
    df = cl.stock_bars(s, "2015-06-01", "2026-10-22")
    if df.empty:
        print(s, "no data")
        continue
    k = df["split_close"] / df["close"]
    o = df[["open", "high", "low", "close"]].copy()
    for c in ("open", "high", "low", "close"):
        o["s_" + c] = df[c] * k
    o["volume"] = df["volume"]
    o.index.name = "date"
    o.round(4).to_csv(out / f"{s}.csv")
    print(s, len(o), o.index[0].date(), o.index[-1].date(), "requests", cl.n_requests, flush=True)
