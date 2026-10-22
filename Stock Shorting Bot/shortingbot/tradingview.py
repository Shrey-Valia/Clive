"""Step 1 - today's top gainers.

Posts to the scanner endpoint that backs
https://www.tradingview.com/markets/stocks-usa/market-movers-gainers/ instead
of scraping the rendered page: same data, no JavaScript, no bot challenge.
"""
from __future__ import annotations

import requests

from . import config

SCANNER_URL = "https://scanner.tradingview.com/america/scan"

# Order matters - it maps positionally onto each row's "d" array.
COLUMNS = [
    "name", "description", "open", "high", "low", "close", "VWAP",
    "change", "market_cap_basic", "volume", "relative_volume_10d_calc",
    "average_volume_10d_calc", "float_shares_outstanding", "RSI", "SMA20",
    "sector", "exchange", "industry",
]


def _payload() -> dict:
    return {
        "filter": [
            {"left": "market_cap_basic", "operation": "greater", "right": config.MIN_MARKET_CAP},
            {"left": "change", "operation": "greater", "right": 0},
            {"left": "exchange", "operation": "in_range", "right": config.EXCHANGES},
            {"left": "is_primary", "operation": "equal", "right": True},
            {"left": "typespecs", "operation": "has_none_of", "right": ["preferred"]},
        ],
        "options": {"lang": "en"},
        "markets": ["america"],
        "symbols": {"query": {"types": ["stock"]}, "tickers": []},
        "columns": COLUMNS,
        "sort": {"sortBy": "change", "sortOrder": "desc"},
        "range": [0, config.SCAN_POOL],
    }


def prev_close(close: float | None, change_pct: float | None) -> float | None:
    if close is None or change_pct is None or change_pct <= -100:
        return None
    return close / (1 + change_pct / 100.0)


def fetch_gainers() -> list[dict]:
    """Top gainers with market cap > $25M and previous close > $3, best first."""
    resp = requests.post(
        SCANNER_URL,
        json=_payload(),
        headers={"User-Agent": config.USER_AGENT, "Content-Type": "application/json"},
        timeout=config.REQUEST_TIMEOUT,
    )
    resp.raise_for_status()

    out = []
    for row in resp.json().get("data", []):
        rec = dict(zip(COLUMNS, row["d"]))
        rec["ticker"] = rec.pop("name")
        rec["company"] = rec.pop("description")
        rec["market_cap"] = rec.pop("market_cap_basic")
        rec["rvol"] = rec.pop("relative_volume_10d_calc")
        rec["avg_volume"] = rec.pop("average_volume_10d_calc")
        rec["prev_close"] = prev_close(rec.get("close"), rec.get("change"))
        if rec["prev_close"] is None or rec["prev_close"] <= config.MIN_PREV_CLOSE:
            continue
        out.append(rec)
        if len(out) == config.TOP_N:
            break
    return out
