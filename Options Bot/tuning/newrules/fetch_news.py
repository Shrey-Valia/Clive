#!/usr/bin/env python3
"""Fetch Alpaca (Benzinga) headlines for the index ETFs, month by month (GET only).

    python3 tuning/newrules/fetch_news.py                # SPY,QQQ,IWM,DIA market-wide headlines
    python3 tuning/newrules/fetch_news.py --symbols X,Y --tag hype   # per-name headlines (rule 5)

Uses created_at (first publication). Cached as JSON per (tag, month) under
tuning/newrules/.cache/news/. Keys are read by optionsbot.data.AlpacaData from
"Stock Shorting Bot/.env" and never printed. ~1 request/second.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from optionsbot.data import AlpacaData  # noqa: E402

NEWS = "https://data.alpaca.markets/v1beta1/news"
CACHE = HERE / ".cache" / "news"


def fetch_window(c: AlpacaData, symbols: str, start: str, end: str) -> list[dict]:
    params = {"symbols": symbols, "start": start, "end": end, "limit": 50, "sort": "asc",
              "include_content": "false"}
    out = []
    while True:
        js = c._get(NEWS, params)
        for n in js.get("news", []) or []:
            out.append({"id": n["id"], "created_at": n["created_at"], "updated_at": n.get("updated_at"),
                        "symbols": n.get("symbols") or [], "headline": n.get("headline") or "",
                        "summary": n.get("summary") or "", "source": n.get("source") or ""})
        tok = js.get("next_page_token")
        if not tok:
            return out
        params["page_token"] = tok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="SPY,QQQ,IWM,DIA")
    ap.add_argument("--tag", default="index")
    ap.add_argument("--start", default="2024-02-01")
    ap.add_argument("--end", default="2026-10-21")
    a = ap.parse_args()
    c = AlpacaData()
    c.min_interval = 1.0
    CACHE.mkdir(parents=True, exist_ok=True)
    months = pd.date_range(pd.Timestamp(a.start).replace(day=1), a.end, freq="MS")
    for m in months:
        f = CACHE / f"{a.tag}_{m:%Y-%m}.json"
        if f.exists():
            continue
        lo = m.strftime("%Y-%m-%dT00:00:00Z")
        hi = min(m + pd.offsets.MonthBegin(1), pd.Timestamp(a.end) + pd.Timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
        rows = fetch_window(c, a.symbols, lo, hi)
        f.write_text(json.dumps(rows))
        print(f"{a.tag} {m:%Y-%m}: {len(rows)} stories, requests so far {c.n_requests}", flush=True)


if __name__ == "__main__":
    main()
