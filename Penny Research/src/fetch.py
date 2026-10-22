"""Resumable GET-only fetchers, one worker, ~60 req/min (common.get).

  fetch.py late    15:40-15:59 minute bars (same 'mtc' format as the shorting cache) for penny
                   gainers on T and T-1 that the shorting cache does not already have
  fetch.py orb     09:30-10:59 minute bars for the day's top-20 penny gap-ups ('mso' = minutes since open)
  fetch.py news_g  Alpaca/Benzinga headlines, 36h before the 15:45 scan, penny top-25 gainers
  fetch.py news_l  headlines over the 5 calendar days before the 15:45 scan, penny top losers
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

from common import CACHE, LAST_DAY, SCACHE, get

ET = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
NEWS = "https://data.alpaca.markets/v1beta1/news"
Z = lambda x: x.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cal():
    c = pd.read_csv(os.path.join(SCACHE, "calendar.csv"))
    return c[(c.date >= "2016-01-04") & (c.date <= LAST_DAY)]


def bars(syms, start, end, ref, mode):
    out = []
    syms = sorted(syms)
    for i in range(0, len(syms), 100):
        p = {"symbols": ",".join(syms[i:i + 100]), "timeframe": "1Min", "start": Z(start), "end": Z(end),
             "feed": "sip", "adjustment": "all", "limit": 10000}
        while True:
            j = get(BARS, p)
            for s, bs in (j.get("bars") or {}).items():
                for b in bs:
                    t = dt.datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                    m = int((ref - t).total_seconds() // 60) if mode == "mtc" else int((t - ref).total_seconds() // 60)
                    out.append((s, m, b["o"], b["h"], b["l"], b["c"], b["v"]))
            if not j.get("next_page_token"):
                break
            p["page_token"] = j["next_page_token"]
    return pd.DataFrame(out, columns=["symbol", mode, "o", "h", "l", "c", "v"])


def job_late():
    d = os.path.join(CACHE, "minute_late"); os.makedirs(d, exist_ok=True)
    cg = pd.read_parquet(os.path.join(CACHE, "cand_gain.parquet"))
    by = cg.groupby("date").symbol.apply(set).to_dict()
    prev = set()
    rows = list(cal().itertuples(index=False))
    for n, r in enumerate(rows):
        cur = by.get(r.date, set())
        want = cur | prev
        prev = cur
        path = os.path.join(d, f"{r.date}.parquet")
        if not want or os.path.exists(path):
            continue
        have = set()
        sp = os.path.join(SCACHE, "minute", f"{r.date}.parquet")
        if os.path.exists(sp):
            have = set(pd.read_parquet(sp, columns=["symbol"]).symbol)
        need = want - have
        hh, mm = map(int, r.close.split(":"))
        c = dt.datetime.fromisoformat(r.date).replace(hour=hh, minute=mm, tzinfo=ET)
        df = bars(need, c - dt.timedelta(minutes=20), c - dt.timedelta(minutes=1), c, "mtc") if need else \
            pd.DataFrame(columns=["symbol", "mtc", "o", "h", "l", "c", "v"])
        df.to_parquet(path)
        if n % 100 == 0:
            print("late", n, r.date, len(need), flush=True)


def job_orb():
    d = os.path.join(CACHE, "minute_orb"); os.makedirs(d, exist_ok=True)
    cp = pd.read_parquet(os.path.join(CACHE, "cand_gap.parquet"))
    cp = cp[cp.groupby("date").gap.rank(ascending=False, method="first") <= 20]
    by = cp.groupby("date").symbol.apply(set).to_dict()
    for n, r in enumerate(cal().itertuples(index=False)):
        syms = by.get(r.date)
        path = os.path.join(d, f"{r.date}.parquet")
        if not syms or os.path.exists(path):
            continue
        o = dt.datetime.fromisoformat(r.date).replace(hour=9, minute=30, tzinfo=ET)
        bars(syms, o, o + dt.timedelta(minutes=89), o, "mso").to_parquet(path)
        if n % 100 == 0:
            print("orb", n, r.date, len(syms), flush=True)


def job_open30():
    """09:30-09:59 minute bars on T+1 for the T top-30 penny gainers (Night Owl exit C, hardened fade entry)."""
    d = os.path.join(CACHE, "minute_open30"); os.makedirs(d, exist_ok=True)
    cg = pd.read_parquet(os.path.join(CACHE, "cand_gain.parquet"))
    cg = cg[cg.rk <= 30] if "rk" in cg else cg[cg.groupby("date").chg.rank(ascending=False, method="first") <= 30]
    by = cg.groupby("date").symbol.apply(set).to_dict()
    rows = list(cal().itertuples(index=False))
    for n, (r0, r1) in enumerate(zip(rows[:-1], rows[1:])):
        syms = by.get(r0.date)
        path = os.path.join(d, f"{r1.date}.parquet")       # file named by the trading day (T+1)
        if not syms or os.path.exists(path):
            continue
        o = dt.datetime.fromisoformat(r1.date).replace(hour=9, minute=30, tzinfo=ET)
        bars(syms, o, o + dt.timedelta(minutes=29), o, "mso").to_parquet(path)
        if n % 100 == 0:
            print("open30", n, r1.date, len(syms), flush=True)


def news(kind, syms, start, end, path, max_pages):
    p = {"symbols": ",".join(sorted(syms)), "start": Z(start), "end": Z(end), "limit": 50, "sort": "desc",
         "include_content": "false"}
    out, pages, trunc = [], 0, False
    while True:
        j = get(NEWS, p)
        for x in j.get("news", []):
            out.append((x["id"], x["created_at"], ",".join(x.get("symbols") or []), x.get("headline") or "",
                        x.get("summary") or ""))
        pages += 1
        if not j.get("next_page_token"):
            break
        if pages >= max_pages:
            trunc = True
            break
        p["page_token"] = j["next_page_token"]
    df = pd.DataFrame(out, columns=["id", "created_at", "symbols", "headline", "summary"])
    df["truncated"] = trunc
    df.to_parquet(path)


def job_news(kind):
    d = os.path.join(CACHE, f"news_{kind}"); os.makedirs(d, exist_ok=True)
    if kind == "g":
        c = pd.read_parquet(os.path.join(CACHE, "cand_gain.parquet"))
        c = c[c.groupby("date").chg.rank(ascending=False, method="first") <= 25]
        hours, max_pages = 36, 4
    else:
        c = pd.read_parquet(os.path.join(CACHE, "cand_lose.parquet"))
        keep = None
        for k in (1, 3, 5):
            m = c.groupby("date")[f"r{k}"].rank(ascending=True, method="first") <= 6
            keep = m if keep is None else (keep | m)
        c = c[keep]
        hours, max_pages = 72, 4
    by = c.groupby("date").symbol.apply(set).to_dict()
    for n, r in enumerate(cal().itertuples(index=False)):
        syms = by.get(r.date)
        path = os.path.join(d, f"{r.date}.parquet")
        if not syms or os.path.exists(path):
            continue
        hh, mm = map(int, r.close.split(":"))
        scan = dt.datetime.fromisoformat(r.date).replace(hour=hh, minute=mm, tzinfo=ET) - dt.timedelta(minutes=15)
        news(kind, syms, scan - dt.timedelta(hours=hours), scan, path, max_pages)
        if n % 100 == 0:
            print("news", kind, n, r.date, len(syms), flush=True)


if __name__ == "__main__":
    for a in sys.argv[1:]:
        {"late": job_late, "orb": job_orb, "open30": job_open30, "news_g": lambda: job_news("g"), "news_l": lambda: job_news("l")}[a]()
        print("done", a, flush=True)
