"""Earnings gate: no pick reports after the scan day's close or in the next EARNINGS_SESSIONS
sessions (the trade date and the one after).

Two sources, both read-only:
  1. Supabase market_events (kind EARNINGS), written daily by events/sync_events.py. It only
     covers the names in the latest picks/positions, so it can miss a fresh gainer.
  2. The Nasdaq earnings calendar (one call per date, every company that day), with a browser
     User-Agent, cached per date for the day in .cache/earnings/<today>/.

A hit in either source excludes the name. A name is cleared only when Nasdaq answered, or
market_events covers it (has an EARNINGS row dated on or after the scan day, so the sync tracks
it); otherwise it is excluded (fail closed) and logged. EARNINGS_REQUIRE_NASDAQ False restores
the older rule (either source answering clears every name). Nothing here raises.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from zoneinfo import ZoneInfo

import requests

from . import config, db

log = logging.getLogger("shortingbot")
AFTER_CLOSE = ("AMC", None)          # an event on the scan day counts only after the close (or time unknown)


def reason_hit(day: dt.date) -> str:
    return f"earnings within {config.EARNINGS_SESSIONS} sessions ({day.isoformat()})"


def reason_unknown() -> str:
    return ("earnings calendar unavailable (Nasdaq failed and market_events does not cover the name) - "
            "excluded, fail closed")


# ---------------------------------------------------------------- sources
def from_market_events(tickers: list[str], start: dt.date, end: dt.date) -> tuple[dict[str, list], bool]:
    """({ticker: [(date, time)]}, ok). ok False when Supabase is off or the read failed."""
    if not tickers or not db.enabled():
        return {}, False
    params = {"select": "ticker,event_date,event_time", "kind": "eq.EARNINGS",
              "ticker": f"in.({','.join(sorted(set(tickers)))})",
              "and": f"(event_date.gte.{start.isoformat()},event_date.lte.{end.isoformat()})"}
    try:
        r = db._send("GET", "market_events", None, params, "")
        if r is None:
            return {}, False
        rows = r.json()
        if not isinstance(rows, list):
            return {}, False
    except Exception as e:
        log.warning("earnings: market_events read failed: %s", e)
        return {}, False
    out: dict[str, list] = {}
    for row in rows:
        try:
            out.setdefault(row["ticker"], []).append((dt.date.fromisoformat(row["event_date"][:10]),
                                                      row.get("event_time")))
        except (KeyError, TypeError, ValueError):
            continue
    return out, True


def market_events_coverage(tickers: list[str], start: dt.date) -> set[str]:
    """The tickers market_events tracks: any EARNINGS row dated on or after `start`. Empty when
    Supabase is off or the read failed (no name counts as covered)."""
    if not tickers or not db.enabled():
        return set()
    params = {"select": "ticker", "kind": "eq.EARNINGS", "ticker": f"in.({','.join(sorted(set(tickers)))})",
              "event_date": f"gte.{start.isoformat()}"}
    try:
        r = db._send("GET", "market_events", None, params, "")
        rows = r.json() if r is not None else None
        if not isinstance(rows, list):
            return set()
        return {row["ticker"] for row in rows if isinstance(row, dict) and row.get("ticker")}
    except Exception as e:
        log.warning("earnings: market_events coverage read failed: %s", e)
        return set()


def _nasdaq_cache(day: dt.date) -> str:
    today = dt.datetime.now(ZoneInfo(config.MARKET_TZ)).date().isoformat()
    return os.path.join(config.CACHE_DIR, "earnings", today, f"nasdaq_{day.isoformat()}.json")


def nasdaq_day(day: dt.date) -> list[dict]:
    """Every row of the Nasdaq earnings calendar for `day`. Raises when the call fails.
    A good answer is cached for the rest of the day; a failure is not."""
    path = _nasdaq_cache(day)
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        pass
    r = requests.get(config.EARNINGS_NASDAQ_URL, params={"date": day.isoformat()}, timeout=config.REQUEST_TIMEOUT,
                     headers={"User-Agent": config.USER_AGENT, "Accept": "application/json, text/plain, */*",
                              "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
    r.raise_for_status()
    j = r.json()
    if not isinstance(j, dict) or not isinstance(j.get("data"), dict):
        raise RuntimeError(f"unexpected Nasdaq answer for {day}: {str(j)[:200]}")
    rows = j["data"].get("rows") or []          # rows None = no earnings that day
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            json.dump(rows, fh)
        os.replace(path + ".tmp", path)
    except OSError as e:
        log.warning("earnings: could not cache %s: %s", os.path.basename(path), e)
    return rows


def from_nasdaq(tickers: list[str], days: list[dt.date]) -> tuple[dict[str, list], bool]:
    """({ticker: [(date, time)]}, ok). ok False when any day could not be read."""
    want, out, ok = set(tickers), {}, True
    for d in days:
        try:
            rows = nasdaq_day(d)
        except Exception as e:
            log.warning("earnings: Nasdaq calendar for %s failed: %s", d, e)
            ok = False
            continue
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            if sym in want:
                t = row.get("time")
                when = "BMO" if t == "time-pre-market" else "AMC" if t == "time-after-hours" else None
                out.setdefault(sym, []).append((d, when))
    return out, ok


# ---------------------------------------------------------------- the gate
def window(scan_date: dt.date, next_sessions: list[dt.date]) -> list[dt.date]:
    """The scan day plus the next EARNINGS_SESSIONS sessions."""
    return [scan_date] + list(next_sessions)[:config.EARNINGS_SESSIONS]


def blocks(tickers: list[str], scan_date: dt.date, next_sessions: list[dt.date]) -> dict[str, str | None]:
    """{ticker: exclude reason, or None when clear}."""
    days = window(scan_date, next_sessions)
    me, me_ok = from_market_events(tickers, days[0], days[-1])
    nq, nq_ok = from_nasdaq(tickers, days)
    if config.EARNINGS_REQUIRE_NASDAQ:
        covered = market_events_coverage(tickers, scan_date) if (me_ok and not nq_ok) else set()
        decided = lambda t: nq_ok or t in covered
    else:
        decided = lambda t: me_ok or nq_ok
    log.info("earnings: %s %s..%s, market_events %s, Nasdaq %s", len(tickers), days[0], days[-1],
             "ok" if me_ok else "unavailable", "ok" if nq_ok else "failed", extra={"milestone": True, "step": "source"})
    out = {}
    for t in tickers:
        hits = sorted({d for d, when in me.get(t, []) + nq.get(t, [])
                       if d in days and (d != scan_date or when in AFTER_CLOSE)})
        if hits:
            out[t] = reason_hit(hits[0])
        elif not decided(t):
            out[t] = reason_unknown()
            log.warning("earnings: no calendar answered for %s - excluded (fail closed)", t)
        else:
            out[t] = None
    return out


def annotate(ranked: list[dict], scan_date: dt.date, next_sessions: list[dt.date]) -> int:
    """Set r["earnings_block"] on each row the gate excludes. Returns how many. Off: 0."""
    if not config.EARNINGS_GATE or not ranked:
        return 0
    try:
        res = blocks([r["ticker"] for r in ranked], scan_date, next_sessions)
    except Exception as e:                          # never: blocks() catches its own; fail closed anyway
        log.error("earnings gate failed: %s - every name excluded", e)
        res = {r["ticker"]: reason_unknown() for r in ranked}
    n = 0
    for r in ranked:
        if res.get(r["ticker"]):
            r["earnings_block"] = res[r["ticker"]]
            n += 1
    return n
