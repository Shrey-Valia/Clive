"""FINRA short interest history (2017-12 onward), SEC fails-to-deliver files, CBOE VIX."""
from __future__ import annotations

import io
import os
import re
import sys
import time
import zipfile

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE  # noqa: E402

SI_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
SEC_UA = {"User-Agent": "StockShortingBot/1.0 (personal research)"}
FIELDS = ["symbolCode", "settlementDate", "currentShortPositionQuantity",
          "averageDailyVolumeQuantity", "daysToCoverQuantity", "marketClassCode"]


def finra_dates():
    r = requests.post(SI_URL, json={"limit": 5000, "fields": ["settlementDate"],
                                    "compareFilters": [{"fieldName": "symbolCode", "fieldValue": "AAPL",
                                                        "compareType": "EQUAL"}]},
                      headers={"Content-Type": "application/json", "Accept": "application/json"}, timeout=60)
    return sorted({x["settlementDate"] for x in r.json()})


def finra_si():
    path = os.path.join(CACHE, "finra_si.parquet")
    if os.path.exists(path):
        return
    d_dir = os.path.join(CACHE, "finra_si")
    os.makedirs(d_dir, exist_ok=True)
    for d in finra_dates():
        p = os.path.join(d_dir, f"{d}.parquet")
        if os.path.exists(p):
            continue
        rows, off = [], 0
        while True:
            for i in range(6):
                try:
                    r = requests.post(SI_URL, json={
                        "limit": 5000, "offset": off, "fields": FIELDS,
                        "compareFilters": [{"fieldName": "settlementDate", "fieldValue": d, "compareType": "EQUAL"}]},
                        headers={"Content-Type": "application/json", "Accept": "application/json"}, timeout=90)
                    if r.status_code in (200, 204):
                        break
                except requests.RequestException:
                    pass
                time.sleep(3 * (i + 1))
            chunk = r.json() if r.status_code == 200 and r.text.strip() else []
            rows += chunk
            if len(chunk) < 5000:
                break
            off += 5000
        pd.DataFrame(rows).to_parquet(p)
        print("finra", d, len(rows), flush=True)
    df = pd.concat([pd.read_parquet(os.path.join(d_dir, f)) for f in sorted(os.listdir(d_dir))])
    df.to_parquet(path)


def sec_ftd():
    path = os.path.join(CACHE, "ftd.parquet")
    if os.path.exists(path):
        return
    page = requests.get("https://www.sec.gov/data-research/sec-markets-data/fails-deliver-data",
                        headers=SEC_UA, timeout=60).text
    links = sorted(set(re.findall(r'href="([^"]*cnsfails(\d{6})([ab])\.zip)"', page)))
    d_dir = os.path.join(CACHE, "ftd")
    os.makedirs(d_dir, exist_ok=True)
    frames = []
    for href, ym, half in links:
        if ym < "201510":
            continue
        p = os.path.join(d_dir, f"{ym}{half}.parquet")
        if not os.path.exists(p):
            url = href if href.startswith("http") else "https://www.sec.gov" + href
            for i in range(5):
                r = requests.get(url, headers=SEC_UA, timeout=120)
                if r.status_code == 200 and r.content[:2] == b"PK":
                    break
                time.sleep(2 * (i + 1))
            z = zipfile.ZipFile(io.BytesIO(r.content))
            raw = z.open(z.namelist()[0]).read().decode("latin-1").splitlines()
            out = []
            for line in raw[1:]:
                q = line.split("|")
                if len(q) >= 4 and q[0].isdigit():
                    try:
                        out.append((q[0], q[2], float(q[3])))
                    except ValueError:
                        pass
            pd.DataFrame(out, columns=["date", "symbol", "qty"]).to_parquet(p)
            print("ftd", ym, half, len(out), flush=True)
            time.sleep(0.3)
        df = pd.read_parquet(p)
        df["period"] = ym + half
        frames.append(df)
    pd.concat(frames).to_parquet(path)


def vix():
    r = requests.get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv", timeout=60)
    open(os.path.join(CACHE, "vix.csv"), "w").write(r.text)


if __name__ == "__main__":
    vix()
    sec_ftd()
    finra_si()
    print("done")
