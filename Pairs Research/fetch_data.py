"""Data fetch for the pairs research (Alpaca market-data GETs only; no order/account endpoints).

- Reads APCA keys from ../Stock Shorting Bot/.env at runtime (never printed).
- Builds the universe: current S&P 500 (Wikipedia GICS sector + sub-industry, cross-checked with
  SSGA SPDR sector-ETF holdings), plus names removed from the index since 2016 (survivorship test),
  plus an ETF universe and classic pairs.
- Downloads daily bars (adjustment=all, feed=sip, from 2016-01-01) in symbol batches, one worker,
  throttled to ~90 req/min. Caches per-symbol parquet-like CSVs under .cache/bars/.
"""
import json, os, re, sys, time
from pathlib import Path
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
BARS = CACHE / "bars"
BARS.mkdir(parents=True, exist_ok=True)
ENV = ROOT.parent / "Stock Shorting Bot" / ".env"

ETFS = ["SPY", "IVV", "VOO", "QQQ", "QQQM", "DIA", "IWM", "IJR", "MDY", "IJH", "VTI", "ITOT",
        "XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC",
        "VGT", "VFH", "VHT", "VCR", "VDC", "VDE", "VIS", "VAW", "VPU", "VNQ", "IYR",
        "XOP", "OIH", "GLD", "IAU", "GDX", "GDXJ", "SLV", "SIL", "USO", "BNO",
        "SMH", "SOXX", "XBI", "IBB", "KRE", "KBE", "ITB", "XHB", "XRT",
        "EFA", "VEA", "EEM", "VWO", "EWA", "EWC", "EWG", "EWQ", "EWU", "EWJ", "EWZ", "EWW",
        "EWH", "EWS", "EWY", "EWT", "FXI", "MCHI", "TLT", "IEF", "HYG", "JNK", "LQD", "VCIT", "AGG", "BND"]
ETF_PAIRS = [("SPY", "IVV"), ("SPY", "VOO"), ("IVV", "VOO"), ("QQQ", "QQQM"), ("VTI", "ITOT"),
             ("IJH", "MDY"), ("GLD", "IAU"), ("EFA", "VEA"), ("EEM", "VWO"), ("HYG", "JNK"),
             ("AGG", "BND"), ("XLE", "XOP"), ("XLE", "VDE"), ("XOP", "OIH"), ("GLD", "GDX"),
             ("GDX", "GDXJ"), ("SLV", "SIL"), ("GLD", "SLV"), ("USO", "BNO"), ("SMH", "SOXX"),
             ("XBI", "IBB"), ("KRE", "KBE"), ("ITB", "XHB"), ("VNQ", "IYR"), ("XLRE", "VNQ"),
             ("XLK", "VGT"), ("XLF", "VFH"), ("XLV", "VHT"), ("XLY", "VCR"), ("XLP", "VDC"),
             ("XLI", "VIS"), ("XLB", "VAW"), ("XLU", "VPU"), ("EWA", "EWC"), ("EWG", "EWQ"),
             ("EWU", "EWG"), ("EWH", "EWS"), ("EWY", "EWT"), ("FXI", "MCHI"), ("TLT", "IEF"),
             ("LQD", "VCIT"), ("SPY", "DIA"), ("IWM", "IJR"), ("EWZ", "EWW")]
CLASSIC = [("KO", "PEP"), ("V", "MA"), ("HD", "LOW"), ("XOM", "CVX"), ("GOOGL", "GOOG"),
           ("UPS", "FDX"), ("JPM", "BAC"), ("MCD", "YUM"), ("WMT", "TGT"), ("CAT", "DE"),
           ("MRK", "PFE"), ("BRK.B", "JPM"), ("COP", "XOM"), ("GS", "MS"), ("LMT", "NOC")]
SECTOR_ETF = {"Information Technology": "XLK", "Financials": "XLF", "Health Care": "XLV",
              "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE",
              "Industrials": "XLI", "Materials": "XLB", "Utilities": "XLU", "Real Estate": "XLRE",
              "Communication Services": "XLC"}


def keys():
    kv = {}
    for line in ENV.read_text().splitlines():
        m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*)\s*$", line)
        if m:
            kv[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return {"APCA-API-KEY-ID": kv["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": kv["APCA_API_SECRET_KEY"]}


_last = [0.0]


def get(url, params, hdr):
    for attempt in range(8):
        dt = time.time() - _last[0]
        if dt < 0.67:  # ~90 req/min, single worker
            time.sleep(0.67 - dt)
        _last[0] = time.time()
        r = requests.get(url, params=params, headers=hdr, timeout=60)
        if r.status_code == 429:
            time.sleep(5 * (attempt + 1)); continue
        if r.status_code >= 500:
            time.sleep(3 * (attempt + 1)); continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"failed after retries: {url}")


def build_universe():
    cur = pd.read_html(CACHE / "sp500_wiki.html")[0]
    cur = cur.rename(columns={"Symbol": "symbol", "Security": "name", "GICS Sector": "sector",
                              "GICS Sub-Industry": "sub_industry", "Date added": "date_added"})
    cur["symbol"] = cur["symbol"].str.replace("-", ".", regex=False)  # BRK-B style -> BRK.B (Alpaca)
    cur = cur[["symbol", "name", "sector", "sub_industry", "date_added"]]
    # SSGA cross-check
    ss = []
    for f in (CACHE / "sectors").glob("*.xlsx"):
        d = pd.read_excel(f, header=4)
        d = d[d["Ticker"].notna() & (d["Ticker"].astype(str).str.len() <= 6)]
        ss.append(pd.DataFrame({"symbol": d["Ticker"].astype(str).str.replace("-", ".", regex=False).str.replace("/", ".", regex=False),
                                "ssga_etf": f.stem.upper()}))
    ss = pd.concat(ss).drop_duplicates("symbol")
    cur = cur.merge(ss, on="symbol", how="left")
    cur["sector_etf"] = cur["sector"].map(SECTOR_ETF)
    # changes table (Feb-2026 revision): removed names since 2016
    ch = pd.read_html(CACHE / "sp500_wiki_old.html")[1]
    ch.columns = ["date", "add_t", "add_s", "rem_t", "rem_s", "reason"]
    ch["date"] = pd.to_datetime(ch["date"], errors="coerce")
    ch.to_csv(CACHE / "sp500_changes.csv", index=False)
    rem = ch[(ch["date"] >= "2016-01-01") & ch["rem_t"].notna()]
    rem = rem[~rem["rem_t"].isin(cur["symbol"])]
    removed = sorted(set(rem["rem_t"].astype(str).str.replace("-", ".", regex=False)))
    return cur, removed, ch


def fetch_bars(symbols, hdr):
    todo = [s for s in symbols if not (BARS / f"{s}.csv").exists()]
    print(f"bars: {len(symbols)} symbols, {len(todo)} to fetch", flush=True)
    url = "https://data.alpaca.markets/v2/stocks/bars"
    nreq = 0
    for i in range(0, len(todo), 40):
        batch = todo[i:i + 40]
        acc = {s: [] for s in batch}
        tok = None
        while True:
            p = {"symbols": ",".join(batch), "timeframe": "1Day", "adjustment": "all", "feed": "sip",
                 "start": "2016-01-01", "end": "2026-10-20", "limit": 10000, "sort": "asc"}
            if tok:
                p["page_token"] = tok
            j = get(url, p, hdr); nreq += 1
            for s, bars in (j.get("bars") or {}).items():
                acc.setdefault(s, []).extend(bars)
            tok = j.get("next_page_token")
            if not tok:
                break
        for s in batch:
            b = acc.get(s, [])
            df = pd.DataFrame(b)
            if len(df):
                df = df.rename(columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vw": "vwap", "n": "trades"})
                df["date"] = pd.to_datetime(df["date"]).dt.tz_convert("America/New_York").dt.date
                df = df[["date", "open", "high", "low", "close", "volume", "vwap"]]
            else:
                df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "vwap"])
            df.to_csv(BARS / f"{s}.csv", index=False)
        print(f"  {i + len(batch)}/{len(todo)} symbols, {nreq} requests", flush=True)


def fetch_assets(hdr):
    f = CACHE / "assets.json"
    if f.exists():
        return json.loads(f.read_text())
    j = get("https://paper-api.alpaca.markets/v2/assets", {"asset_class": "us_equity"}, hdr)
    f.write_text(json.dumps(j))
    return j


if __name__ == "__main__":
    hdr = keys()
    cur, removed, ch = build_universe()
    cur.to_csv(CACHE / "universe_current.csv", index=False)
    pd.Series(removed, name="symbol").to_csv(CACHE / "universe_removed.csv", index=False)
    print("current", len(cur), "removed since 2016", len(removed))
    agree = (cur["ssga_etf"] == cur["sector_etf"]).mean()
    print(f"Wikipedia-GICS vs SSGA sector agreement: {agree:.1%}  (missing in SSGA: {cur['ssga_etf'].isna().sum()})")
    syms = sorted(set(cur["symbol"]) | set(ETFS) | {s for p in CLASSIC for s in p} | set(removed))
    fetch_bars(syms, hdr)
    assets = fetch_assets(hdr)
    print("assets", len(assets))
