"""Steps 2 & 3 - short interest, days to cover, borrow fee.

Fintel serves a Cloudflare challenge to non-browser clients (hard 403), so we
go to the sources it republishes instead:

  Yahoo Finance  -> short % of float, days to cover, shares short, float
  FINRA          -> official bi-monthly short interest + days to cover

FINRA is the system of record: brokers report to it, and Fintel/Ortex resell
it. Yahoo is faster and carries float, so it leads and FINRA backfills.

Borrow fee is deliberately absent. No free source publishes it reliably -
it is a paid feed (Fintel Pro / Ortex / IBKR). The column exists and stays
empty rather than being faked.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config

FINRA_URL = (
    "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
)

# What every provider must return, so callers never guess at keys.
EMPTY = {
    "short_pct_float": None,
    "days_to_cover": None,
    "shares_short": None,
    "float_shares": None,
    "short_interest_date": None,
    "borrow_fee_pct": None,      # paid-feed only; see module docstring
    "short_data_source": None,
    "move_since_si_pct": None,
}


# ----------------------------------------------------------------- Yahoo
def _from_yahoo(ticker: str) -> dict:
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
    except Exception:
        return {}

    pct = info.get("shortPercentOfFloat")
    date = info.get("dateShortInterest")

    rec = {
        "short_pct_float": round(pct * 100, 2) if pct else None,
        "days_to_cover": info.get("shortRatio"),
        "shares_short": info.get("sharesShort"),
        "float_shares": info.get("floatShares"),
        "short_interest_date": (
            dt.datetime.utcfromtimestamp(date).date().isoformat() if date else None
        ),
    }
    if rec["short_pct_float"] is not None or rec["days_to_cover"] is not None:
        rec["short_data_source"] = "yahoo"
    return {k: v for k, v in rec.items() if v is not None}


# ----------------------------------------------------------------- FINRA
def _from_finra(tickers: list[str]) -> dict[str, dict]:
    """One batched call covering every ticker. Returns the newest row each."""
    lookback = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    try:
        resp = requests.post(
            FINRA_URL,
            json={
                "limit": 5000,
                "compareFilters": [
                    {"fieldName": "settlementDate",
                     "fieldValue": lookback, "compareType": "GTE"}
                ],
                "domainFilters": [
                    {"fieldName": "symbolCode", "values": list(tickers)}
                ],
            },
            headers={"Content-Type": "application/json"},
            timeout=config.REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception:
        return {}

    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {}

    header = [h.strip('"') for h in lines[0].split(",")]
    newest: dict[str, dict] = {}
    for line in lines[1:]:
        cells = [c.strip('"') for c in line.split(",")]
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells))
        sym, date = row.get("symbolCode"), row.get("settlementDate")
        if not sym or not date:
            continue
        # Rows arrive oldest-first; keep only the most recent per symbol.
        if sym not in newest or date > newest[sym]["settlementDate"]:
            newest[sym] = row

    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        sym: {
            "shares_short": _num(r.get("currentShortPositionQuantity")),
            "days_to_cover": _num(r.get("daysToCoverQuantity")),
            "short_interest_date": r.get("settlementDate"),
            "short_data_source": "finra",
        }
        for sym, r in newest.items()
    }


# ----------------------------------------------------------------- merge
def _staleness(date_str: str | None) -> int | None:
    if not date_str:
        return None
    try:
        d = dt.date.fromisoformat(date_str)
    except ValueError:
        return None
    return (dt.date.today() - d).days


def _move_since(ticker: str, since: str | None) -> float | None:
    """Price change since the short interest was measured.

    Short interest is a photograph of a date, not a live number. If the stock
    has ripped since that date, a chunk of the short base has probably already
    covered INTO the move - the fuel the squeeze score is counting may be
    spent. This is the single biggest trap in trading stale short data.
    """
    if not since:
        return None
    try:
        import yfinance as yf

        start = (dt.date.fromisoformat(since) - dt.timedelta(days=4)).isoformat()
        h = yf.Ticker(ticker).history(start=start)
        if h.empty:
            return None
        on_or_before = h.loc[h.index.strftime("%Y-%m-%d") <= since, "Close"]
        if on_or_before.empty:
            return None
        return round(100 * (h["Close"].iloc[-1] / on_or_before.iloc[-1] - 1), 1)
    except Exception:
        return None


def enrich(tickers: list[str]) -> dict[str, dict]:
    """Fetch short data for every ticker. Yahoo leads, FINRA backfills gaps."""
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        yahoo = dict(zip(tickers, pool.map(_from_yahoo, tickers)))

    finra = _from_finra(tickers)

    merged: dict[str, dict] = {}
    for t in tickers:
        rec = dict(EMPTY)
        y, f = yahoo.get(t) or {}, finra.get(t) or {}

        rec.update(f)          # official baseline
        rec.update(y)          # Yahoo wins where present (it carries float)

        if y and f:
            rec["short_data_source"] = "yahoo+finra"
        # Prefer FINRA's days-to-cover when Yahoo didn't supply one.
        if rec["days_to_cover"] is None and f.get("days_to_cover") is not None:
            rec["days_to_cover"] = f["days_to_cover"]

        rec["short_data_age_days"] = _staleness(rec["short_interest_date"])
        merged[t] = rec

    # Price move since each ticker's own SI measurement date.
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as pool:
        moves = pool.map(
            lambda t: _move_since(t, merged[t]["short_interest_date"]), tickers
        )
    for t, mv in zip(tickers, moves):
        merged[t]["move_since_si_pct"] = mv
    return merged
