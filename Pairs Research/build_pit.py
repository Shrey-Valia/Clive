"""Point-in-time S&P 500 membership from yearly Wikipedia snapshots (first revision on/before Jan 1 of
each year), with the GICS sector as listed at that time. Fetches Alpaca bars for any snapshot names not
already cached (data GETs only)."""
import pandas as pd
from pathlib import Path
import fetch_data as fd

C = fd.CACHE
rows = []
for y in range(2016, 2027):
    t = pd.read_html(C / "wiki" / f"sp500_{y}.html")[0]
    sym = [c for c in t.columns if "ymbol" in c][0]
    sub = [c for c in t.columns if c.startswith("GICS Sub")][0]
    d = pd.DataFrame({"year": y, "symbol": t[sym].astype(str).str.replace("-", ".", regex=False).str.strip(),
                      "sector": t["GICS Sector"], "sub_industry": t[sub]})
    rows.append(d)
pit = pd.concat(rows)
pit.to_csv(C / "pit_membership.csv", index=False)
have = {p.stem for p in fd.BARS.glob("*.csv")}
need = sorted(set(pit["symbol"]) - have)
print("PIT union symbols", pit["symbol"].nunique(), "missing bars", len(need))
if need:
    fd.fetch_bars(need, fd.keys())
cov = []
for s in sorted(set(pit["symbol"])):
    f = fd.BARS / f"{s}.csv"
    n = sum(1 for _ in open(f)) - 1 if f.exists() else 0
    cov.append((s, n))
cov = pd.DataFrame(cov, columns=["symbol", "nbars"])
cov.to_csv(C / "pit_coverage.csv", index=False)
print("symbols with zero bars:", (cov.nbars == 0).sum())
