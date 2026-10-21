"""Load cached CBOE index CSVs + FRED DTB3 into one aligned daily panel."""
from pathlib import Path
import pandas as pd
import numpy as np

CACHE = Path(__file__).resolve().parent / ".cache"
SYMS = ["VIX", "VIX3M", "VIX9D", "VIX6M", "VVIX", "SKEW", "SPX",
        "PUT", "BXM", "BXMD", "CNDR", "WPUT", "PPUT", "CLL", "BFLY"]


def load_cboe(sym: str) -> pd.Series:
    df = pd.read_csv(CACHE / f"{sym}.csv")
    df.columns = [c.strip().upper() for c in df.columns]
    df["DATE"] = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
    col = "CLOSE" if "CLOSE" in df.columns else sym
    s = pd.to_numeric(df.set_index("DATE")[col], errors="coerce").dropna()
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s = s[s > 0]
    if sym == "VVIX":
        s = s[s >= 50]
    s.name = sym
    return s


def load_tbill() -> pd.Series:
    df = pd.read_csv(CACHE / "DTB3.csv")
    df.columns = ["DATE", "DTB3"]
    df["DATE"] = pd.to_datetime(df["DATE"])
    s = pd.to_numeric(df.set_index("DATE")["DTB3"], errors="coerce")
    return s.ffill()


def panel(start="2006-01-01") -> pd.DataFrame:
    sp = load_cboe("SPX")
    idx = sp[sp.index >= pd.Timestamp(start) - pd.Timedelta(days=400)].index
    out = {}
    for s in SYMS:
        try:
            out[s] = load_cboe(s).reindex(idx)
        except FileNotFoundError:
            pass
    df = pd.DataFrame(out, index=idx)
    # forward fill only signal/vol series by at most 3 days (holiday mismatches)
    df = df.ffill(limit=3)
    tb = load_tbill().reindex(idx, method="ffill")
    df["TB3"] = tb
    # daily cash return: discount-basis yield -> approx simple annual, accrue by calendar days
    days = df.index.to_series().diff().dt.days.fillna(1)
    df["CASH_RET"] = (df["TB3"].shift(1).fillna(df["TB3"]) / 100.0) * days / 360.0
    return df


if __name__ == "__main__":
    df = panel()
    for c in SYMS:
        s = load_cboe(c)
        s07 = s[s.index >= "2007-01-01"]
        gaps = s07.index.to_series().diff().dt.days
        stale = (s07.diff() == 0).mean()
        print(f"{c:6s} first={s.index[0].date()} n07={len(s07)} maxgap={gaps.max()} "
              f"stale%={stale:.3f}")
