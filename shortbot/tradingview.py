"""Step 1 - pull today's top gainers from TradingView.

Uses the JSON endpoint that backs the market-movers-gainers page rather than
scraping the rendered HTML. Same data, no JS rendering, no bot challenge.
"""
from __future__ import annotations

import requests

from . import config

SCANNER_URL = "https://scanner.tradingview.com/america/scan"

# Order matters - it maps positionally onto the response "d" array.
COLUMNS = [
    "name", "description", "open", "high", "low", "close", "VWAP",
    "change", "market_cap_basic", "volume",
    "relative_volume_10d_calc", "RSI", "SMA20", "SMA50", "price_52_week_high",
    "average_volume_10d_calc", "float_shares_outstanding", "ATR", "gap",
    "sector", "exchange",
]


def _payload(limit: int) -> dict:
    flt = [
            {"left": "close", "operation": "greater", "right": config.MIN_PRICE},
            {"left": "market_cap_basic", "operation": "greater",
             "right": config.MIN_MARKET_CAP},
            {"left": "exchange", "operation": "in_range", "right": config.EXCHANGES},
            {"left": "is_primary", "operation": "equal", "right": True},
            {"left": "typespecs", "operation": "has_none_of", "right": ["preferred"]},
    ]
    if config.MAX_PRICE is not None:
        flt.append({"left": "close", "operation": "less", "right": config.MAX_PRICE})
    if config.MAX_MARKET_CAP is not None:
        flt.append({"left": "market_cap_basic", "operation": "less",
                    "right": config.MAX_MARKET_CAP})
    return {
        "filter": flt,
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": ["stock"]}, "tickers": []},
        "columns": COLUMNS,
        "sort": {"sortBy": "change", "sortOrder": "desc"},
        "range": [0, limit],
    }


def fetch_gainers(limit: int | None = None) -> list[dict]:
    """Return the top `limit` gainers passing the price / market-cap screen."""
    limit = limit or config.TOP_N
    resp = requests.post(
        SCANNER_URL,
        json=_payload(limit),
        headers={"User-Agent": config.USER_AGENT, "Content-Type": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])

    out = []
    for row in rows:
        rec = dict(zip(COLUMNS, row["d"]))
        rec["ticker"] = rec.pop("name")
        rec["company"] = rec.pop("description")
        out.append(rec)
    return out
