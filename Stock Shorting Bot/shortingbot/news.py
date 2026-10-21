"""Checklist item 5 - is the move backed by real de-risking news, or by hype?

Headlines come from Financial Modeling Prep when FMP_API_KEY is set,
otherwise from yfinance. They are classified with keyword rules, because the
question is narrow: did something happen that justifies a re-rating?
"""
from __future__ import annotations

import datetime as dt
import os
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

import requests

from . import config

FMP_NEWS_URL = "https://financialmodelingprep.com/stable/news/stock"
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

# Real de-risking. Fading these is how shorts get run over.
STRONG = [
    r"\bfda (approv|clear|grant)", r"\b(fda|ema|regulatory|marketing) approval\b", r"phase (3|iii)\b.*(met|success|positive|achiev)",
    r"\b(to be |will be )?acquired\b", r"\bacquisition of\b.*\bby\b", r"\bmerger agreement\b",
    r"\bbuyout\b", r"\btake[- ]private\b", r"\bdefinitive agreement\b", r"\btender offer\b",
    r"\b(raises|raised|increases|boosts) (full[- ]year |fy|annual )?(guidance|outlook)\b",
    r"\bbeats?\b.*\b(estimates|expectations)\b", r"\brecord (revenue|quarter)\b",
    r"\$\d+(\.\d+)?\s?(billion|bn)\b.*\b(contract|order|award)\b",
    r"\b(agree[sd]? to|to) (sell|divest)\b.*\bfor \$\d", r"\bdivest(iture|s|ed)?\b",
    r"\b(sells?|sold) (its )?\w+( \w+)? (business|unit|division|segment)\b",
]
# Hype: thin, vague or promotional. These tend to fade.
FLUFF = [
    r"\bletter of intent\b", r"\bloi\b", r"\bmou\b", r"\bmemorandum of understanding\b",
    r"\bpartnership\b", r"\bcollaborat", r"\bstrategic (alliance|review)\b", r"\bexplor",
    r"\b(ai|artificial intelligence)\b", r"\bblockchain\b", r"\bcrypto\b", r"\bbitcoin\b",
    r"\btreasury (strategy|reserve)\b", r"\bpilot\b", r"\bshowcase\b", r"\bto present\b",
    r"\bconference\b", r"\bwebinar\b", r"\binterview\b", r"\bunusual (market|trading) activity\b",
]
# Dilution: supply is coming, which helps a short.
DILUTION = [
    r"\b(public|registered direct|private placement|at[- ]the[- ]market|atm) offering\b",
    r"\bprices? .*offering\b", r"\bwarrants?\b", r"\bshelf registration\b", r"\breverse split\b",
    r"\bconvertible notes?\b", r"\bgoing concern\b", r"\bdelist",
]


def _matches(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text)]


def _fmp(ticker: str, key: str) -> list[dict]:
    try:
        r = requests.get(FMP_NEWS_URL, params={"symbols": ticker, "limit": 20, "apikey": key},
                         timeout=config.REQUEST_TIMEOUT)
        r.raise_for_status()
        items = r.json() or []
    except Exception:
        return []
    out = []
    for it in items:
        try:
            ts = dt.datetime.fromisoformat(str(it.get("publishedDate")).replace(" ", "T"))
        except ValueError:
            ts = None
        out.append({"title": it.get("title") or "", "summary": it.get("text") or "",
                    "published": ts, "source": it.get("site") or it.get("publisher")})
    return out


def _yahoo(ticker: str) -> list[dict] | None:
    """Yahoo Finance's per-ticker RSS feed. yfinance's own .news comes back empty."""
    root = None
    for attempt in range(4):                     # the feed rate-limits bursts with 429
        try:
            r = requests.get(YAHOO_RSS_URL, params={"s": ticker, "region": "US", "lang": "en-US"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=config.REQUEST_TIMEOUT)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            root = ET.fromstring(r.content)
            break
        except Exception:
            return None
    if root is None:
        return None
    out = []
    for it in root.iter("item"):
        try:
            ts = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            ts = None
        out.append({"title": it.findtext("title") or "", "summary": it.findtext("description") or "",
                    "published": ts, "source": "yahoo"})
    return out


def headlines(ticker: str) -> tuple[list[dict] | None, str]:
    """(recent items, source). Items is None when no source could be reached."""
    key = os.environ.get("FMP_API_KEY")
    items, source = (_fmp(ticker, key), "fmp") if key else ([], "fmp")
    if not items:
        items, source = _yahoo(ticker), "yahoo_rss"
    if items is None:
        return None, source
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=config.NEWS_LOOKBACK_HOURS)

    def recent(it):
        ts = it["published"]
        if ts is None:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone(dt.timedelta(hours=-5)))   # FMP times are ET
        return ts >= cutoff

    return [it for it in items if recent(it)], source


def classify(ticker: str) -> dict:
    items, source = headlines(ticker)
    if items is None:      # feed unreachable: the catalyst block drops out of the score
        return {"catalyst": "news_unavailable", "catalyst_score": None, "headlines": [], "news_source": source}
    text = " ".join(f"{i['title']} {i['summary']}" for i in items).lower()
    strong, fluff, dilution = _matches(STRONG, text), _matches(FLUFF, text), _matches(DILUTION, text)

    if not items:
        kind, score = "no_news", 0.75          # a big move on no news usually fades
    elif strong and not dilution:
        kind, score = "real_catalyst", 0.0
    elif dilution:
        kind, score = "dilution", 1.0
    elif fluff:
        kind, score = "hype", 0.9
    else:
        kind, score = "unclear", 0.5
    return {
        "catalyst": kind,
        "catalyst_score": score,
        "headlines": [i["title"] for i in items[:5]],
        "news_source": source,
    }


def enrich(tickers: list[str]) -> dict[str, dict]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        return dict(zip(tickers, pool.map(classify, tickers)))
