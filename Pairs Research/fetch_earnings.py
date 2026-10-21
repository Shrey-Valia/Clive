"""Earnings dates from SEC EDGAR (8-K with Item 2.02), current S&P 500 names. GETs only, <= 4 req/s,
generic research User-Agent. Output: .cache/earnings_dates.csv (symbol, filing_date, accept_raw)."""
import json, time
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
C = ROOT / ".cache"; D = C / "edgar"; D.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "PairsResearch academic research contact@example.com"}
_last = [0.0]


def get(url):
    f = D / (url.rsplit("/", 1)[-1])
    if f.exists():
        return json.loads(f.read_text())
    for k in range(5):
        dt = time.time() - _last[0]
        if dt < 0.25:
            time.sleep(0.25 - dt)
        _last[0] = time.time()
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200:
            f.write_text(r.text); return r.json()
        if r.status_code == 404:
            return None
        time.sleep(2 * (k + 1))
    return None


cur = pd.read_html(C / "sp500_wiki.html")[0]
rows = []
for sym, cik in zip(cur.Symbol, cur.CIK):
    sym = sym.replace("-", ".")
    j = get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    if not j:
        continue
    blocks = [j["filings"]["recent"]]
    for x in j["filings"].get("files", []):
        if x.get("filingTo", "9999") >= "2015-06-01":
            b = get(f"https://data.sec.gov/submissions/{x['name']}")
            if b:
                blocks.append(b)
    for b in blocks:
        for form, items, fd, acc in zip(b["form"], b["items"], b["filingDate"], b["acceptanceDateTime"]):
            if form == "8-K" and "2.02" in (items or "") and fd >= "2015-06-01":
                rows.append((sym, fd, acc))
out = pd.DataFrame(rows, columns=["symbol", "filing_date", "accept_raw"]).drop_duplicates(["symbol", "filing_date"])
out.to_csv(C / "earnings_dates.csv", index=False)
print("symbols", out.symbol.nunique(), "events", len(out))
