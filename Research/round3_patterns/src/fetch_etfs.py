"""ETF daily bars. Long history from Yahoo (yfinance, adjusted OHLC = total return), cross-checked against
Alpaca SIP (adjustment=all) over 2016-2026. Alpaca bars are also kept for the bot-era tests."""
import os
import sys

import pandas as pd

from common import CACHE, bars

ETFS = {
    "us_eq": ["SPY", "QQQ", "IWM", "DIA", "MDY"],
    "sector": ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY", "XLRE", "XLC"],
    "country": ["EFA", "EEM", "EWJ", "EWG", "EWU", "EWC", "EWA", "EWZ", "FXI", "EWY", "EWT", "EWH",
                "EWS", "EWW", "EWQ", "EWL", "EWP", "EWI", "INDA"],
    "bond": ["TLT", "IEF", "SHY", "LQD", "HYG", "TIP", "AGG", "EMB"],
    "cmdty": ["GLD", "SLV", "DBC", "USO", "DBA", "UNG"],
    "fx": ["UUP", "FXE", "FXY"],
    "reit": ["VNQ", "IYR"],
}
ALL = sorted({s for v in ETFS.values() for s in v})

if __name__ == "__main__":
    yp = os.path.join(CACHE, "etf_yahoo.parquet")
    if not os.path.exists(yp):
        import yfinance as yf
        d = yf.download(ALL, start="1993-01-01", end="2026-10-21", auto_adjust=True, progress=False,
                        group_by="ticker", threads=True)
        rows = []
        for s in ALL:
            x = d[s].dropna(how="all").reset_index()
            x.columns = [c.lower() for c in x.columns]
            x["symbol"] = s
            rows.append(x.rename(columns={"open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"}))
        y = pd.concat(rows)
        y["date"] = pd.to_datetime(y.date).dt.strftime("%Y-%m-%d")
        y[["symbol", "date", "o", "h", "l", "c", "v"]].to_parquet(yp)
    y = pd.read_parquet(yp)
    print(y.groupby("symbol").date.agg(["min", "max", "count"]).to_string())
    ap = os.path.join(CACHE, "etf_alpaca.parquet")
    if not os.path.exists(ap):
        a = bars(ALL, "2016-01-01", "2026-10-21", "1Day", "all")
        a["date"] = a.ts.str[:10]
        a.drop(columns="ts").to_parquet(ap)
    a = pd.read_parquet(ap)
    # cross-check daily close-to-close returns
    m = y.merge(a, on=["symbol", "date"], suffixes=("_y", "_a"))
    m = m.sort_values(["symbol", "date"])
    m["ry"] = m.groupby("symbol").c_y.pct_change(); m["ra"] = m.groupby("symbol").c_a.pct_change()
    m["ryo"] = m.o_y / m.groupby("symbol").c_y.shift() - 1; m["rao"] = m.o_a / m.groupby("symbol").c_a.shift() - 1
    print(m.groupby("symbol").apply(lambda x: pd.Series({"corr_cc": x.ry.corr(x.ra), "corr_co": x.ryo.corr(x.rao),
                                                         "mad_cc_bp": (x.ry - x.ra).abs().median() * 1e4})).round(4).to_string())
