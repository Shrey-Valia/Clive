"""Earnings report dates from SEC EDGAR (8-K Item 2.02), 2015 -> today.

Method (research/event_strategies.md section 1):
  * GET https://data.sec.gov/submissions/CIK{10-digit}.json (+ older files[] pages)
  * keep form 8-K (not 8-K/A) whose items contain "2.02"
  * acceptanceDateTime carries a 'Z' but is shifted: true ET = to_ET(ts) + utcoffset (verified)
  * drop TSLA delivery-report 2.02s (day-of-month <= 5 in Jan/Apr/Jul/Oct, or before 12:00)
  * AMC if hour >= 16 -> D = filing date; BMO if < 09:30 -> D = previous trading day
    (D is always "the last close before the news").

Output: tuning/events/earnings_dates.csv  (ticker, cik, filing_date, accepted_et, session, D)
Raw JSON cached in tuning/events/raw/.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
RAW.mkdir(exist_ok=True)
UA = {"User-Agent": "OptionsBacktester/1.0 research-contact@example.com",
      "Accept-Encoding": "gzip, deflate"}
CIKS = {"AAPL": 320193, "MSFT": 789019, "NVDA": 1045810, "AMZN": 1018724, "GOOGL": 1652044,
        "META": 1326801, "TSLA": 1318605, "NFLX": 1065280}


def get_json(url: str, name: str) -> dict:
    p = RAW / name
    if p.exists():
        return json.loads(p.read_text())
    time.sleep(0.25)
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    p.write_text(r.text)
    return r.json()


def filings(cik: int) -> pd.DataFrame:
    c10 = f"{cik:010d}"
    js = get_json(f"https://data.sec.gov/submissions/CIK{c10}.json", f"CIK{c10}.json")
    frames = [pd.DataFrame(js["filings"]["recent"])]
    for f in js["filings"].get("files", []):
        older = get_json(f"https://data.sec.gov/submissions/{f['name']}", f["name"])
        frames.append(pd.DataFrame(older))
    df = pd.concat(frames, ignore_index=True)
    return df[["form", "items", "filingDate", "acceptanceDateTime", "accessionNumber"]]


def main():
    from optionsbot.runner import client
    spy = client().stock_bars("SPY", "2015-01-01", "2026-10-20")
    tdays = spy.index
    rows = []
    for tk, cik in CIKS.items():
        df = filings(cik)
        df = df[(df["form"] == "8-K") & df["items"].fillna("").str.contains("2.02", regex=False)]
        for _, r in df.iterrows():
            # 'Z' is not true UTC (verified against filing index pages, research note s.1):
            # true_et = to_ET(json_ts) + utcoffset(to_ET(json_ts))
            j = pd.Timestamp(r["acceptanceDateTime"]).tz_convert("America/New_York")
            et = (j + j.utcoffset()).tz_localize(None)
            fd = pd.Timestamp(r["filingDate"])
            if fd < pd.Timestamp("2016-01-06"):  # SIP stock bars start 2016-01-04
                continue
            if tk == "TSLA" and ((et.month in (1, 4, 7, 10) and et.day <= 5) or et.hour < 12):
                continue
            hm = et.hour + et.minute / 60
            session = "AMC" if hm >= 16 else ("BMO" if hm < 9.5 else "RTH")
            day = et.normalize()
            if session == "AMC":
                D = tdays[tdays <= day][-1]
            else:  # news before/inside the session -> last close before it is the prior day
                D = tdays[tdays < day][-1]
            rows.append({"ticker": tk, "cik": cik, "filing_date": str(fd.date()),
                         "accepted_et": str(et), "session": session, "D": str(D.date()),
                         "accession": r["accessionNumber"]})
    out = pd.DataFrame(rows).sort_values(["ticker", "D"])
    # All regular reports for these 8 names are AMC; BMO/RTH 2.02s are pre-announcements or
    # delivery/other updates (NVDA 2019-01-28, 2022-08-08; TSLA 2017-07-07) -> drop.
    # Two AMC 2.02s within 40 days: the later is the regular release (GOOGL 2023-04-20 vs 04-25,
    # NFLX 2019-01-02 vs 01-17, AAPL 2019-01-02 guidance cut, NFLX 2019-12-16) -> keep the later.
    out = out[out["session"] == "AMC"].reset_index(drop=True)
    drop = set()
    for i in range(len(out) - 1):
        a, b = out.iloc[i], out.iloc[i + 1]
        if a["ticker"] == b["ticker"] and (pd.Timestamp(b["D"]) - pd.Timestamp(a["D"])).days < 40:
            drop.add(i)
    dropped = out.loc[sorted(drop)]
    out = out.drop(index=sorted(drop))
    out.to_csv(HERE / "earnings_dates.csv", index=False)
    print(out.groupby("ticker").size())
    print(out["session"].value_counts())
    print("dropped near-duplicates:\n", dropped)
    print(out[out["D"] >= "2024-01-01"].groupby("ticker")["D"].apply(list).to_string())


if __name__ == "__main__":
    main()
