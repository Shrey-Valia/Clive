"""Historical headlines (Alpaca/Benzinga) for each day's 15:45 top-20, classified with the
live bot's own keyword lists (imported read-only from shortingbot.news).

Window = scan time minus NEWS_LOOKBACK_HOURS (36h) up to the 15:45 scan.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import BOT_ROOT, CACHE, alpaca_get  # noqa: E402

sys.path.insert(0, BOT_ROOT)
from shortingbot import config as botcfg  # noqa: E402
from shortingbot.news import DILUTION, FLUFF, STRONG  # noqa: E402

ET = ZoneInfo("America/New_York")
NEWS = "https://data.alpaca.markets/v1beta1/news"
D = os.path.join(CACHE, "news")


def day_job(args):
    date, close, syms = args
    path = os.path.join(D, f"{date}.parquet")
    if os.path.exists(path):
        return date
    hh, mm = map(int, close.split(":"))
    scan = dt.datetime.fromisoformat(date).replace(hour=hh, minute=mm, tzinfo=ET) - dt.timedelta(minutes=15)
    start = scan - dt.timedelta(hours=botcfg.NEWS_LOOKBACK_HOURS)
    fmt = lambda x: x.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"symbols": ",".join(sorted(syms)), "start": fmt(start), "end": fmt(scan),
              "limit": 50, "sort": "desc", "include_content": "false"}
    out, pages = [], 0
    while True:
        j = alpaca_get(NEWS, params)
        for n in j.get("news", []):
            out.append((n["id"], n["created_at"], ",".join(n.get("symbols") or []), n.get("headline") or "",
                        n.get("summary") or "", n.get("source") or ""))
        pages += 1
        if not j.get("next_page_token") or pages >= 10:
            break
        params["page_token"] = j["next_page_token"]
    pd.DataFrame(out, columns=["id", "created_at", "symbols", "headline", "summary", "source"]).to_parquet(path)
    return date


def classify_text(items: list[str]) -> tuple[str, float, int, int, int]:
    """Same decision tree as shortingbot.news.classify."""
    if not items:
        return "no_news", 0.75, 0, 0, 0
    text = " ".join(items).lower()
    s = [p for p in STRONG if re.search(p, text)]
    f = [p for p in FLUFF if re.search(p, text)]
    d = [p for p in DILUTION if re.search(p, text)]
    if s and not d:
        kind, score = "real_catalyst", 0.0
    elif d:
        kind, score = "dilution", 1.0
    elif f:
        kind, score = "hype", 0.9
    else:
        kind, score = "unclear", 0.5
    return kind, score, len(s), len(f), len(d)


def main():
    os.makedirs(D, exist_ok=True)
    scan = pd.read_parquet(os.path.join(CACHE, "scan_top20.parquet"), columns=["symbol", "date"])
    cal = pd.read_csv(os.path.join(CACHE, "calendar.csv")).set_index("date").close.to_dict()
    jobs = [(d, cal[d], set(g.symbol)) for d, g in scan.groupby("date")]
    with ThreadPoolExecutor(10) as pool:
        for n, d in enumerate(pool.map(day_job, jobs)):
            if n % 200 == 0:
                print(n, d, flush=True)
    # classify per (symbol, date)
    rows = []
    for d, g in scan.groupby("date"):
        news = pd.read_parquet(os.path.join(D, f"{d}.parquet"))
        if news.empty:
            news = pd.DataFrame(columns=["id", "created_at", "symbols", "headline", "summary", "source"])
        news["symset"] = news.symbols.astype(str).str.split(",")
        for s in g.symbol:
            m = news[news.symset.apply(lambda x: s in x).astype(bool)] if len(news) else news
            items = [f"{h} {sm}" for h, sm in zip(m.headline, m.summary)]
            kind, score, ns, nf, nd = classify_text(items)
            rows.append({"symbol": s, "date": d, "catalyst": kind, "catalyst_score": score,
                         "n_news": len(items), "n_strong": ns, "n_fluff": nf, "n_dilution": nd,
                         "headline0": m.headline.iloc[0] if len(m) else ""})
    pd.DataFrame(rows).to_parquet(os.path.join(CACHE, "catalyst.parquet"))
    print(pd.DataFrame(rows).catalyst.value_counts())


if __name__ == "__main__":
    main()
