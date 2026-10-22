#!/usr/bin/env python3
"""Market events calendar sync -> Supabase `market_events` (dashboard migration 005).

Collects macro releases, Fed events, market-structure days and company events
for the last 30 and next 90 days (ET) and upserts them. Idempotent: the unique
key is (kind, ticker_key, event_date, title), so re-running only refreshes rows.
Nothing is ever deleted.

    python3 sync_events.py --dry-run            # fetch + print counts, write nothing
    python3 sync_events.py                      # fetch + upsert
    python3 sync_events.py --json out.json      # also dump the rows

Every source is independent: one failing source logs a status line and the
rest still sync. Needs only `requests`. Credentials are read from
"../Stock Shorting Bot/.env" (SUPABASE_URL, SUPABASE_SERVICE_KEY,
APCA_API_KEY_ID, APCA_API_SECRET_KEY) and are never printed.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import html
import json
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ENV_FILE = ROOT / "Stock Shorting Bot" / ".env"
OPTIONS_CONFIG = ROOT / "Options Bot" / "live" / "config.py"
MANUAL_CSV = HERE / "manual_events.csv"
CACHE = HERE / ".cache"
ET = ZoneInfo("America/New_York")

# Generic agents only. Nasdaq's API rejects non-browser agents, so it gets a
# stock desktop Chrome string; everything else gets a plain tool name.
UA_BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
UA_TOOL = "clive-events/1.0 (market calendar sync)"
TIMEOUT = 25

STATIC_TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "NFLX"]
MEGA = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "NFLX"}
ETFS = {"SPY", "QQQ", "IWM"}

KINDS = ["FOMC", "FOMC_PRESSER", "FOMC_MINUTES", "FED_SPEECH", "CPI", "PPI", "NFP", "PCE", "GDP", "RETAIL_SALES",
         "JOBLESS_CLAIMS", "TREASURY_AUCTION", "OPEX", "QUAD_WITCH", "VIX_EXPIRY", "HOLIDAY", "HALF_DAY",
         "EARNINGS", "EARNINGS_CALL", "INVESTOR_DAY", "CONFERENCE_TALK", "DIVIDEND_EX"]

# Fixed titles for macro kinds, so the same release from two sources collapses to one row.
MACRO_TITLE = {"CPI": "CPI", "PPI": "PPI", "NFP": "Employment report (NFP)", "PCE": "PCE / personal income",
               "GDP": "GDP", "RETAIL_SALES": "Retail sales", "JOBLESS_CLAIMS": "Initial jobless claims"}
MACRO_IMP = {"CPI": 3, "NFP": 3, "PCE": 2, "PPI": 2, "GDP": 2, "RETAIL_SALES": 2, "JOBLESS_CLAIMS": 1}
MACRO_URL = {"CPI": "https://www.bls.gov/cpi/", "PPI": "https://www.bls.gov/ppi/", "NFP": "https://www.bls.gov/ces/",
             "PCE": "https://www.bea.gov/data/personal-consumption-expenditures-price-index",
             "GDP": "https://www.bea.gov/data/gdp/gross-domestic-product",
             "RETAIL_SALES": "https://www.census.gov/retail/index.html",
             "JOBLESS_CLAIMS": "https://www.dol.gov/ui/data.pdf"}

# Fallback BLS dates for when bls.gov blocks scripts (it returns 403 to any
# non-browser client). 2026 entries are from BLS's published 2026 release
# schedule; the ones inside the Nasdaq feed's horizon were cross-checked live
# (CPI 10/14, 11/10; NFP 10/02, 11/06; PPI 10/15). Nasdaq rows for the same
# kind within 7 days supersede these. 2027 is not listed until BLS publishes it.
BLS_FALLBACK = {
    "CPI": ["2026-10-14", "2026-11-10", "2026-12-10"],
    "NFP": ["2026-10-02", "2026-11-06", "2026-12-04"],
    "PPI": ["2026-10-15"],
}

# FOMC fallback (decision days) if federalreserve.gov can't be parsed. From the 2026 calendar.
FOMC_FALLBACK = {"2026-01-28": False, "2026-03-18": True, "2026-04-29": False, "2026-06-17": True,
                 "2026-07-29": False, "2026-09-16": True, "2026-10-28": False, "2026-12-09": True}

# NYSE holidays, used to name Alpaca's missing sessions and as the fallback calendar.
NYSE_HOLIDAYS = {
    "2026-01-01": "New Year's Day", "2026-01-19": "Martin Luther King Jr. Day", "2026-02-16": "Presidents' Day",
    "2026-04-03": "Good Friday", "2026-05-25": "Memorial Day", "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day (observed)", "2026-09-07": "Labor Day", "2026-11-26": "Thanksgiving Day",
    "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day", "2027-01-18": "Martin Luther King Jr. Day", "2027-02-15": "Presidents' Day",
    "2027-03-26": "Good Friday", "2027-05-31": "Memorial Day", "2027-06-18": "Juneteenth (observed)",
    "2027-07-05": "Independence Day (observed)", "2027-09-06": "Labor Day", "2027-11-25": "Thanksgiving Day",
    "2027-12-24": "Christmas Day (observed)",
}
NYSE_HALF = {"2026-11-27": "Day after Thanksgiving", "2026-12-24": "Christmas Eve",
             "2027-11-26": "Day after Thanksgiving"}

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

STATUS: list[tuple[str, str, int, str]] = []   # (source, OK/FALLBACK/FAIL/SKIP, rows, note)


def status(src: str, state: str, n: int, note: str = "") -> None:
    STATUS.append((src, state, n, note))
    print(f"[{state:8}] {src:28} {n:5d} rows  {note}", flush=True)


# ---------------------------------------------------------------- helpers
def load_env() -> dict:
    env: dict[str, str] = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def iso(d: dt.date) -> str:
    return d.isoformat()


def parse_d(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def daterange(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def to_24h(t: str | None) -> str | None:
    """'2:00 p.m.' / '08:30 AM' / '13:00' -> '14:00' etc."""
    if not t:
        return None
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*([ap])?\.?\s*m?\.?", t.strip().lower())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if ap == "p" and h != 12:
        h += 12
    if ap == "a" and h == 12:
        h = 0
    return f"{h:02d}:{mi:02d}"


def nasdaq_time(d: dt.date, t: str | None) -> str | None:
    """Nasdaq's economic-calendar `gmt` field is really a fixed UTC-4 clock (08:30 EST
    shows as 09:30 after the November DST switch). Convert it to the ET wall clock."""
    hm = to_24h(t)
    if not hm:
        return None
    h, m = map(int, hm.split(":"))
    at = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
    return at.astimezone(ET).strftime("%H:%M")


class Http:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.day_cache = CACHE / dt.datetime.now(ET).date().isoformat()

    def get(self, url: str, *, params=None, browser=False, headers=None, cache_key: str | None = None, as_json=False):
        """GET with an optional same-day disk cache (keeps reruns cheap on Nasdaq)."""
        path = self.day_cache / cache_key if cache_key else None
        if path and path.exists():
            txt = path.read_text(encoding="utf-8")
            return json.loads(txt) if as_json else txt
        h = {"User-Agent": UA_BROWSER if browser else UA_TOOL, "Accept": "application/json, text/html;q=0.9, */*;q=0.5"}
        if headers:
            h.update(headers)
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self.s.get(url, params=params, headers=h, timeout=TIMEOUT)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                r.raise_for_status()
                txt = r.content.decode("utf-8-sig", errors="replace")
                if path:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(txt, encoding="utf-8")
                return json.loads(txt) if as_json else txt
            except (requests.RequestException, ValueError) as e:
                last = e
                if isinstance(e, requests.HTTPError) and "HTTP 4" in str(e):
                    break
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"{url}: {last}")


def row(kind: str, date: dt.date | str, title: str, *, time_: str | None = None, ticker: str | None = None,
        importance: int = 2, source: str, url: str | None = None) -> dict:
    assert kind in KINDS, kind
    return {"event_date": date if isinstance(date, str) else iso(date), "event_time": time_, "tz": "America/New_York",
            "kind": kind, "ticker": ticker, "ticker_key": ticker or "", "title": title[:300],
            "importance": max(1, min(3, int(importance))), "source": source, "url": url}


# ---------------------------------------------------------------- calendar (Alpaca)
class Calendar:
    """Trading sessions. Alpaca's /v2/calendar when keys work, else the NYSE table above."""

    def __init__(self, http: Http, env: dict, start: dt.date, end: dt.date) -> None:
        self.holidays: dict[str, str] = {}
        self.half: dict[str, str] = {}
        self.source = "fallback"
        self.start, self.end = start, end
        key, sec = env.get("APCA_API_KEY_ID"), env.get("APCA_API_SECRET_KEY")
        try:
            if not (key and sec):
                raise RuntimeError("no Alpaca keys in .env")
            r = http.s.get("https://paper-api.alpaca.markets/v2/calendar",
                           params={"start": iso(start), "end": iso(end)},
                           headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec, "User-Agent": UA_TOOL},
                           timeout=TIMEOUT)
            r.raise_for_status()
            sessions = {s["date"]: s for s in r.json()}
            if not sessions:
                raise RuntimeError("empty calendar")
            last = max(sessions)
            for d in daterange(start, parse_d(last)):
                k = iso(d)
                if d.weekday() < 5 and k not in sessions:
                    self.holidays[k] = NYSE_HOLIDAYS.get(k, "Market holiday")
                elif k in sessions and sessions[k]["close"] != "16:00":
                    self.half[k] = f"{NYSE_HALF.get(k, 'Early close')} · close {sessions[k]['close']}"
            # Alpaca only knows sessions it has published; extend with the table beyond that.
            for k, v in NYSE_HOLIDAYS.items():
                if k > last:
                    self.holidays.setdefault(k, v)
            for k, v in NYSE_HALF.items():
                if k > last:
                    self.half.setdefault(k, f"{v} · close 13:00")
            self.source = "alpaca"
        except Exception as e:  # noqa: BLE001
            self.err = str(e)
            self.holidays = dict(NYSE_HOLIDAYS)
            self.half = {k: f"{v} · close 13:00" for k, v in NYSE_HALF.items()}

    def is_session(self, d: dt.date) -> bool:
        return d.weekday() < 5 and iso(d) not in self.holidays

    def prev_session(self, d: dt.date) -> dt.date:
        d -= dt.timedelta(days=1)
        while not self.is_session(d):
            d -= dt.timedelta(days=1)
        return d


# ---------------------------------------------------------------- sources
def src_market_days(cal: Calendar, a: dt.date, b: dt.date) -> list[dict]:
    out = []
    url = "https://www.nyse.com/markets/hours-calendars"
    for k, name in cal.holidays.items():
        if iso(a) <= k <= iso(b):
            out.append(row("HOLIDAY", k, f"Market closed · {name}", importance=2, source=cal.source, url=url))
    for k, name in cal.half.items():
        if iso(a) <= k <= iso(b):
            out.append(row("HALF_DAY", k, f"Half day · {name}", time_="13:00", importance=2, source=cal.source, url=url))
    status("market calendar", "OK" if cal.source == "alpaca" else "FALLBACK", len(out),
           "Alpaca /v2/calendar" if cal.source == "alpaca" else f"hardcoded NYSE table ({getattr(cal, 'err', '')})")
    return out


def third_friday(y: int, m: int) -> dt.date:
    d = dt.date(y, m, 15)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def src_expiries(cal: Calendar, a: dt.date, b: dt.date) -> list[dict]:
    out = []
    months = sorted({(d.year, d.month) for d in daterange(a - dt.timedelta(days=40), b + dt.timedelta(days=40))})
    for y, m in months:
        f = third_friday(y, m)
        exp = f if cal.is_session(f) else cal.prev_session(f)
        if a <= exp <= b:
            if m in (3, 6, 9, 12):
                out.append(row("QUAD_WITCH", exp, "Quarterly opex · quad witching", importance=3, source="rule",
                               url="https://www.cboe.com/about/hours/us-options/"))
            else:
                out.append(row("OPEX", exp, "Monthly opex", importance=2, source="rule",
                               url="https://www.cboe.com/about/hours/us-options/"))
    # VIX: the Wednesday 30 days before the next month's standard SPX expiry.
    for y, m in months:
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        spx = third_friday(ny, nm)
        if not cal.is_session(spx):
            spx = cal.prev_session(spx)
        vix = spx - dt.timedelta(days=30)
        if not cal.is_session(vix):
            vix = cal.prev_session(vix)
        if a <= vix <= b:
            out.append(row("VIX_EXPIRY", vix, "VIX futures & options expiry", time_="09:30", importance=1, source="rule",
                           url="https://www.cboe.com/tradable_products/vix/"))
    status("opex / vix (rules)", "OK", len(out), "3rd-Friday rule, holiday-adjusted")
    return out


def src_fomc(http: Http, a: dt.date, b: dt.date, fed_json: dict | None) -> list[dict]:
    url = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    meetings: dict[str, bool] = {}   # decision date -> has SEP
    state, note = "OK", "fomccalendars.htm"
    try:
        page = http.get(url)
        for panel in re.split(r'<div class="panel panel-default">', page)[1:]:
            ym = re.search(r"(\d{4}) FOMC Meetings", panel)
            if not ym:
                continue
            year = int(ym.group(1))
            for mon, day in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<',
                                       panel, re.S):
                mon, day = mon.strip(), day.strip()
                if "notation" in day.lower() or "unscheduled" in day.lower() or "cancel" in day.lower():
                    continue
                dm = re.match(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?", day)
                if not dm:
                    continue
                end_mon = MONTHS.get(mon.split("/")[-1].strip()[:3].lower())
                if not end_mon:
                    continue
                end_day = int(dm.group(2) or dm.group(1))
                yr = year + 1 if (mon.lower().startswith("dec/") and end_mon == 1) else year
                meetings[iso(dt.date(yr, end_mon, end_day))] = "*" in day
        if not meetings:
            raise RuntimeError("no meetings parsed")
    except Exception as e:  # noqa: BLE001
        meetings, state, note = dict(FOMC_FALLBACK), "FALLBACK", f"hardcoded 2026 list ({e})"

    # Minutes: the Fed's own calendar JSON has the scheduled dates; else decision + 3 weeks.
    minutes: dict[str, str] = {}
    for e in (fed_json or {}).get("events", []):
        if e.get("type") == "FOMC" and "Minutes" in e.get("title", "") and e.get("month"):
            desc = clean(e.get("description", ""))
            mm = re.search(r"Meeting of ([A-Za-z]+)\s+(\d{1,2})(?:\s*-\s*(?:([A-Za-z]+)\s+)?(\d{1,2}))?", desc)
            try:
                rel = dt.date.fromisoformat(f"{e['month']}-{int(re.match(r'\d+', e['days']).group()):02d}")
            except Exception:  # noqa: BLE001
                continue
            if mm:
                em = MONTHS.get((mm.group(3) or mm.group(1))[:3].lower())
                ed = int(mm.group(4) or mm.group(2))
                y = rel.year if em <= rel.month else rel.year - 1
                minutes[iso(dt.date(y, em, ed))] = iso(rel)

    out = []
    for dec, sep in sorted(meetings.items()):
        d = parse_d(dec)
        if a <= d <= b:
            out.append(row("FOMC", d, "FOMC rate decision" + (" + SEP / dot plot" if sep else ""), time_="14:00",
                           importance=3, source="federalreserve.gov", url=url))
            out.append(row("FOMC_PRESSER", d, "FOMC press conference", time_="14:30", importance=3,
                           source="federalreserve.gov", url=url))
        rel = parse_d(minutes[dec]) if dec in minutes else d + dt.timedelta(days=21)
        if a <= rel <= b:
            out.append(row("FOMC_MINUTES", rel, f"FOMC minutes ({d.strftime('%b')} {d.day} meeting)", time_="14:00",
                           importance=2, source="federalreserve.gov" if dec in minutes else "rule (+21 days)",
                           url="https://www.federalreserve.gov/monetarypolicy/fomcminutes.htm"))
    status("fomc", state, len(out), note)
    return out


def src_fed_speeches(fed_json: dict | None, a: dt.date, b: dt.date) -> list[dict]:
    if fed_json is None:
        status("fed speeches", "FAIL", 0, "federalreserve.gov/json/calendar.json unavailable")
        return []
    out = []
    for e in fed_json.get("events", []):
        if e.get("type") not in ("Speeches", "Testimony") or not e.get("month"):
            continue
        title = clean(e.get("title", ""))
        who = re.search(r"(Chair(?:man)?|Vice Chair for Supervision|Vice Chair|Governor)\s+([A-Z][\w.'\- ]+)", title)
        if not who:
            continue   # Board members only (Chair, Vice Chairs, Governors)
        try:
            d = dt.date.fromisoformat(f"{e['month']}-{int(re.match(r'\d+', e['days']).group()):02d}")
        except Exception:  # noqa: BLE001
            continue
        if not (a <= d <= b):
            continue
        role = who.group(1)
        imp = 3 if role.startswith("Chair") else 2 if role.startswith("Vice Chair") else 1
        if e.get("type") == "Testimony":
            imp = max(imp, 2)
        topic = clean(e.get("description", ""))
        name = who.group(2).strip()
        kind = "Testimony" if e.get("type") == "Testimony" else title.split(" - ")[0].strip() or "Speech"
        out.append(row("FED_SPEECH", d, f"{kind} · {role} {name}" + (f" · {topic}" if topic else ""),
                       time_=to_24h(e.get("time")), importance=imp, source="federalreserve.gov",
                       url=e.get("live") or "https://www.federalreserve.gov/newsevents/calendar.htm"))
    status("fed speeches", "OK", len(out), "calendar.json · Board members only; lists ~1 week ahead")
    return out


NASDAQ_ECON_MAP = [
    (re.compile(r"^CPI$"), "CPI"), (re.compile(r"^PPI$"), "PPI"), (re.compile(r"^Nonfarm Payrolls$"), "NFP"),
    (re.compile(r"^PCE price index$", re.I), "PCE"), (re.compile(r"^GDP$"), "GDP"),
    (re.compile(r"^Retail Sales$"), "RETAIL_SALES"), (re.compile(r"^Initial Jobless Claims$"), "JOBLESS_CLAIMS"),
    (re.compile(r"^(10-Year Note|20-Year Bond|30-Year Bond) Auction$"), "TREASURY_AUCTION"),
]


def src_nasdaq_econ(http: Http, a: dt.date, b: dt.date) -> list[dict]:
    """Nasdaq economic calendar. Covers the past and ~3 weeks ahead. Its `date`
    parameter is one day ahead of the events it returns (date=Fri gives Thu's
    claims), so the offset is calibrated each run on jobless claims (always a Thursday)."""
    raw: list[tuple[dt.date, dict]] = []
    empty_run = 0
    today = dt.datetime.now(ET).date()
    for q in daterange(a, b + dt.timedelta(days=1)):
        try:
            j = http.get("https://api.nasdaq.com/api/calendar/economicevents", params={"date": iso(q)}, browser=True,
                         headers={"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"},
                         cache_key=f"nasdaq_econ_{iso(q)}.json", as_json=True)
        except Exception:  # noqa: BLE001
            continue
        rows = ((j or {}).get("data") or {}).get("rows") or []
        us = [r for r in rows if r.get("country") == "United States"]
        for r in us:
            raw.append((q, r))
        # stop once the feed's horizon is clearly passed
        empty_run = empty_run + 1 if (q > today and not us) else 0
        if empty_run >= 10:
            break
        time.sleep(0.2)
    if not raw:
        status("nasdaq econ calendar", "FAIL", 0, "no rows")
        return []
    thursdayish = lambda d: d.weekday() == 3 or (d.weekday() == 2 and iso(d + dt.timedelta(days=1)) in NYSE_HOLIDAYS)  # noqa: E731
    claims = [q for q, r in raw if r.get("eventName", "").strip() == "Initial Jobless Claims"]
    shift = max((0, -1, 1), key=lambda s: sum(thursdayish(q + dt.timedelta(days=s)) for q in claims)) if claims else -1
    out, seen = [], set()
    for q, r in raw:
        name = r.get("eventName", "").strip()
        d = q + dt.timedelta(days=shift)
        if not (a <= d <= b):
            continue
        for pat, kind in NASDAQ_ECON_MAP:
            if pat.match(name):
                if kind == "TREASURY_AUCTION":
                    title, imp, url = f"{name[:-8]} auction", 1, "https://www.treasurydirect.gov/auctions/upcoming/"
                else:
                    title, imp, url = MACRO_TITLE[kind], MACRO_IMP[kind], MACRO_URL[kind]
                k = (kind, iso(d), title)
                if k not in seen:
                    seen.add(k)
                    out.append(row(kind, d, title, time_=nasdaq_time(d, r.get("gmt")), importance=imp, source="nasdaq", url=url))
                break
    status("nasdaq econ calendar", "OK", len(out), f"date offset {shift:+d} day(s); horizon to {max(q for q, _ in raw)}")
    return out


def src_bea(http: Http, a: dt.date, b: dt.date) -> list[dict]:
    url = "https://www.bea.gov/news/schedule"
    try:
        page = http.get(url)
    except Exception as e:  # noqa: BLE001
        status("bea schedule (GDP, PCE)", "FAIL", 0, str(e)[:80])
        return []
    out, year = [], dt.date.today().year
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [clean(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        txt = " | ".join(c for c in cells if c)
        y = re.search(r"Year (\d{4})", txt)
        if y:
            year = int(y.group(1))
            continue
        m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2})\b.*?(\d{1,2}:\d{2}\s*[AP]M)", txt)
        if not m or not MONTHS.get(m.group(1)[:3].lower()):
            continue
        d = dt.date(year, MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        nonempty = [c for c in cells if c]
        title = nonempty[-1] if nonempty else ""
        kind = "GDP" if title.startswith("GDP (") else "PCE" if title.startswith("Personal Income and Outlays") else None
        if kind and a <= d <= b:
            out.append(row(kind, d, MACRO_TITLE[kind], time_=to_24h(m.group(3)), importance=MACRO_IMP[kind],
                           source="bea.gov", url=url))
    status("bea schedule (GDP, PCE)", "OK", len(out), "upcoming releases only")
    return out


def src_census_retail(http: Http, a: dt.date, b: dt.date) -> list[dict]:
    out, ok = [], 0
    for y in sorted({a.year, b.year}):
        url = f"https://www.census.gov/economic-indicators/calendar-listview-{y}.html"
        try:
            page = http.get(url)
            ok += 1
        except Exception:  # noqa: BLE001
            continue
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
            txt = clean(tr)
            if "Advance Monthly Sales for Retail and Food Services" not in txt:
                continue
            m = re.search(r"([A-Z][a-z]+) (\d{1,2}), (\d{4})\s+(\d{1,2}:\d{2} [AP]M)", txt)
            if not m:
                continue
            d = dt.date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
            if a <= d <= b:
                out.append(row("RETAIL_SALES", d, MACRO_TITLE["RETAIL_SALES"], time_=to_24h(m.group(4)), importance=2,
                               source="census.gov", url="https://www.census.gov/economic-indicators/"))
    status("census schedule (retail)", "OK" if ok else "FAIL", len(out), f"{ok} year page(s)")
    return out


def src_bls(http: Http, a: dt.date, b: dt.date) -> list[dict]:
    """bls.gov answers scripts with 403; try once, then fall back to the table."""
    try:
        http.get("https://www.bls.gov/schedule/news_release/cpi.htm")
        note = "bls.gov reachable but schedule parsing not implemented; using table"
    except Exception as e:  # noqa: BLE001
        note = f"bls.gov blocked ({str(e)[-12:].strip()}); hardcoded 2026 schedule"
    out = []
    times = {"CPI": "08:30", "NFP": "08:30", "PPI": "08:30"}
    for kind, dates in BLS_FALLBACK.items():
        for s in dates:
            d = parse_d(s)
            if a <= d <= b:
                out.append(row(kind, d, MACRO_TITLE[kind], time_=times[kind], importance=MACRO_IMP[kind],
                               source="bls-schedule (hardcoded)", url=MACRO_URL[kind]))
    status("bls (CPI, PPI, NFP)", "FALLBACK", len(out), note)
    return out


def src_claims_rule(cal: Calendar, a: dt.date, b: dt.date) -> list[dict]:
    out = []
    for d in daterange(a, b):
        if d.weekday() != 3:
            continue
        rel = d if iso(d) not in NYSE_HOLIDAYS and iso(d) not in cal.holidays else d - dt.timedelta(days=1)
        out.append(row("JOBLESS_CLAIMS", rel, MACRO_TITLE["JOBLESS_CLAIMS"], time_="08:30", importance=1,
                       source="rule (weekly, Thu)", url=MACRO_URL["JOBLESS_CLAIMS"]))
    status("jobless claims (rule)", "OK", len(out), "every Thursday, Wed if Thu is a holiday")
    return out


def src_treasury(http: Http, a: dt.date, b: dt.date) -> list[dict]:
    try:
        j = http.get("https://www.treasurydirect.gov/TA_WS/securities/upcoming", params={"format": "json"}, as_json=True)
    except Exception as e:  # noqa: BLE001
        status("treasurydirect auctions", "FAIL", 0, str(e)[:80])
        return []
    out = []
    for x in j or []:
        term, typ = x.get("securityTerm", ""), x.get("securityType", "")
        if typ not in ("Note", "Bond") or not re.match(r"(10|20|30)-Year", term):
            continue
        ds = x.get("auctionDate") or x.get("competitiveAuctionCloseDate")
        if not ds:
            continue
        d = parse_d(ds)
        reopen = " (reopening)" if x.get("reopening") == "Yes" else ""
        if a <= d <= b:
            out.append(row("TREASURY_AUCTION", d, f"{term} {typ} auction", time_=to_24h(x.get("competitiveAuctionCloseTime")),
                           importance=1, source="treasurydirect.gov" + reopen,
                           url="https://www.treasurydirect.gov/auctions/upcoming/"))
    status("treasurydirect auctions", "OK", len(out), "10/20/30-year only; announced ~1 week ahead")
    return out


def src_nasdaq_earnings(http: Http, tickers: set[str], a: dt.date, b: dt.date) -> list[dict]:
    out, fails, calls = [], 0, 0
    for d in daterange(a, b):
        if d.weekday() >= 5:
            continue
        calls += 1
        try:
            j = http.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": iso(d)}, browser=True,
                         headers={"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"},
                         cache_key=f"nasdaq_earn_{iso(d)}.json", as_json=True)
        except Exception:  # noqa: BLE001
            fails += 1
            continue
        for r in ((j or {}).get("data") or {}).get("rows") or []:
            sym = (r.get("symbol") or "").strip().upper()
            if sym not in tickers:
                continue
            t = r.get("time")
            when = "BMO" if t == "time-pre-market" else "AMC" if t == "time-after-hours" else None
            fq = (r.get("fiscalQuarterEnding") or "").strip()
            out.append(row("EARNINGS", d, f"{sym} earnings" + (f" · qtr ending {fq}" if fq else ""), time_=when, ticker=sym,
                           importance=3 if sym in MEGA else 2, source="nasdaq",
                           url=f"https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/earnings"))
        time.sleep(0.25)
    status("nasdaq earnings", "OK" if fails < calls else "FAIL", len(out), f"{calls} days queried, {fails} failed")
    return out


def src_nasdaq_dividends(http: Http, tickers: set[str], a: dt.date, b: dt.date) -> list[dict]:
    out, missing = [], []
    for sym in sorted(tickers):
        rows = None
        for ac in (["etf", "stocks"] if sym in ETFS else ["stocks", "etf"]):
            try:
                j = http.get(f"https://api.nasdaq.com/api/quote/{sym}/dividends", params={"assetclass": ac}, browser=True,
                             headers={"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"},
                             cache_key=f"nasdaq_div_{sym}_{ac}.json", as_json=True)
            except Exception:  # noqa: BLE001
                continue
            rows = (((j or {}).get("data") or {}).get("dividends") or {}).get("rows")
            if rows:
                break
        if not rows:
            missing.append(sym)
            continue
        for r in rows:
            try:
                d = dt.datetime.strptime(r.get("exOrEffDate", ""), "%m/%d/%Y").date()
            except ValueError:
                continue
            if a <= d <= b:
                amt = (r.get("amount") or "").strip()
                out.append(row("DIVIDEND_EX", d, f"{sym} ex-dividend" + (f" {amt}" if amt else ""), ticker=sym, importance=1,
                               source="nasdaq", url=f"https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/dividend-history"))
        time.sleep(0.2)
    status("nasdaq dividends", "OK", len(out), f"no history for: {' '.join(missing) or 'none'}")
    return out


def src_manual(a: dt.date, b: dt.date) -> list[dict]:
    if not MANUAL_CSV.exists():
        status("manual_events.csv", "SKIP", 0, "file missing")
        return []
    out, bad = [], 0
    lines = [ln for ln in MANUAL_CSV.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    for r in csv.DictReader(lines):
        try:
            d = parse_d(r["event_date"].strip())
            kind = r["kind"].strip().upper()
            if kind not in KINDS:
                raise ValueError(kind)
            if a <= d <= b:
                tk = (r.get("ticker") or "").strip().upper() or None
                out.append(row(kind, d, r["title"].strip(), time_=(r.get("event_time") or "").strip() or None, ticker=tk,
                               importance=int((r.get("importance") or "2").strip() or 2), source="manual",
                               url=(r.get("url") or "").strip() or None))
        except Exception:  # noqa: BLE001
            bad += 1
    status("manual_events.csv", "OK", len(out), f"{bad} bad row(s)" if bad else "")
    return out


# ---------------------------------------------------------------- scope
def ticker_scope(env: dict) -> set[str]:
    tickers = set(STATIC_TICKERS)
    try:
        m = re.search(r"^UNIVERSE\s*=\s*\[([^\]]*)\]", OPTIONS_CONFIG.read_text(), re.M)
        if m:
            tickers |= set(re.findall(r"[\"']([A-Z.]+)[\"']", m.group(1)))
    except OSError:
        pass
    url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_SERVICE_KEY")
    extra: set[str] = set()
    if url and key:
        base, h = url.rstrip("/") + "/rest/v1/", {"apikey": key, "Authorization": f"Bearer {key}", "User-Agent": UA_TOOL}
        try:
            runs = requests.get(base + "short_runs", params={"select": "id", "order": "id.desc", "limit": "3"},
                                headers=h, timeout=TIMEOUT).json()
            ids = ",".join(str(r["id"]) for r in runs if isinstance(r, dict))
            if ids:
                for r in requests.get(base + "short_candidates", params={"select": "ticker", "selected": "eq.true",
                                      "run_id": f"in.({ids})"}, headers=h, timeout=TIMEOUT).json():
                    extra.add(r["ticker"])
            for r in requests.get(base + "positions", params={"select": "ticker"}, headers=h, timeout=TIMEOUT).json():
                extra.add(r["ticker"])
            res = requests.get(base + "options_live_positions", params={"select": "underlying"}, headers=h, timeout=TIMEOUT)
            if res.ok:
                extra |= {r["underlying"] for r in res.json() if r.get("underlying")}
        except Exception as e:  # noqa: BLE001
            print(f"[WARN    ] ticker scope: Supabase unreachable ({type(e).__name__}); static list only")
    extra = {t.strip().upper() for t in extra if t and re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", t.strip().upper())}
    print(f"[INFO    ] ticker scope: {len(tickers | extra)} names ({len(extra - tickers)} from Supabase picks/positions)")
    return tickers | extra


# ---------------------------------------------------------------- merge + write
SOURCE_RANK = {"manual": 0, "federalreserve.gov": 1, "bea.gov": 1, "census.gov": 1, "treasurydirect.gov": 1,
               "alpaca": 1, "nasdaq": 2, "rule": 3}


def rank(src: str) -> int:
    for k, v in SOURCE_RANK.items():
        if src.startswith(k):
            return v
    return 4


def merge(rows: list[dict]) -> list[dict]:
    # Drop hardcoded/rule macro rows superseded by a live row of the same kind within 7 days
    # (e.g. a rescheduled release), then collapse exact-key duplicates keeping the best source.
    live = collections.defaultdict(list)
    for r in rows:
        if r["kind"] in MACRO_TITLE and not (r["source"].startswith("bls-schedule") or r["source"].startswith("rule")):
            live[r["kind"]].append(parse_d(r["event_date"]))
    kept = []
    for r in rows:
        if r["kind"] in MACRO_TITLE and (r["source"].startswith("bls-schedule") or r["source"].startswith("rule")):
            d = parse_d(r["event_date"])
            if any(abs((d - x).days) <= (3 if r["kind"] == "JOBLESS_CLAIMS" else 7) and x != d for x in live[r["kind"]]):
                continue
        kept.append(r)
    best: dict[tuple, dict] = {}
    for r in kept:
        k = (r["kind"], r["ticker_key"], r["event_date"], r["title"])
        if k not in best or rank(r["source"]) < rank(best[k]["source"]):
            best[k] = r
    return sorted(best.values(), key=lambda r: (r["event_date"], r["event_time"] or "99", r["kind"], r["ticker_key"]))


def upsert(env: dict, rows: list[dict]) -> int:
    url, key = env.get("SUPABASE_URL"), env.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY missing from Stock Shorting Bot/.env")
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA_TOOL,
         "Prefer": "resolution=merge-duplicates,return=minimal"}
    n = 0
    for i in range(0, len(rows), 500):
        chunk = [{**r, "updated_at": now} for r in rows[i:i + 500]]
        r = requests.post(url.rstrip("/") + "/rest/v1/market_events", params={"on_conflict": "kind,ticker_key,event_date,title"},
                          headers=h, data=json.dumps(chunk), timeout=60)
        if r.status_code >= 300:
            raise SystemExit(f"upsert failed: HTTP {r.status_code} {r.text[:300]}")
        n += len(chunk)
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="fetch and print, write nothing")
    ap.add_argument("--days-back", type=int, default=30)
    ap.add_argument("--days-ahead", type=int, default=90)
    ap.add_argument("--json", metavar="OUT", help="also write the merged rows to this JSON file")
    args = ap.parse_args()

    today = dt.datetime.now(ET).date()
    a, b = today - dt.timedelta(days=args.days_back), today + dt.timedelta(days=args.days_ahead)
    print(f"market events sync · window {a} → {b} (ET) · {'DRY RUN' if args.dry_run else 'UPSERT'}")
    env = load_env()
    http = Http()
    tickers = ticker_scope(env)
    cal = Calendar(http, env, a - dt.timedelta(days=45), b + dt.timedelta(days=60))
    try:
        fed_json = http.get("https://www.federalreserve.gov/json/calendar.json", as_json=True)
    except Exception:  # noqa: BLE001
        fed_json = None

    rows: list[dict] = []
    jobs = [
        lambda: src_fomc(http, a, b, fed_json),
        lambda: src_fed_speeches(fed_json, a, b),
        lambda: src_nasdaq_econ(http, a, b),
        lambda: src_bea(http, a, b),
        lambda: src_census_retail(http, a, b),
        lambda: src_bls(http, a, b),
        lambda: src_claims_rule(cal, a, b),
        lambda: src_treasury(http, a, b),
        lambda: src_market_days(cal, a, b),
        lambda: src_expiries(cal, a, b),
        lambda: src_nasdaq_earnings(http, tickers, a, b),
        lambda: src_nasdaq_dividends(http, tickers, a, b),
        lambda: src_manual(a, b),
    ]
    for job in jobs:
        try:
            rows += job()
        except Exception as e:  # noqa: BLE001  one bad source never stops the rest
            status(getattr(job, "__name__", "source"), "FAIL", 0, f"{type(e).__name__}: {e}"[:120])

    merged = merge(rows)
    counts = collections.Counter(r["kind"] for r in merged)
    print("\ncounts per kind:")
    for k in KINDS:
        if counts[k]:
            print(f"  {k:18} {counts[k]:4d}")
    print(f"  {'TOTAL':18} {len(merged):4d}  (from {len(rows)} raw rows)")

    if args.json:
        Path(args.json).write_text(json.dumps(merged, indent=1))
        print(f"wrote {args.json}")
    if args.dry_run:
        print("\nnext 10 days sample:")
        for r in [r for r in merged if iso(today) <= r["event_date"] <= iso(today + dt.timedelta(days=10))][:25]:
            print(f"  {r['event_date']} {r['event_time'] or '--':5} {r['kind']:16} {r['ticker'] or '':6} i{r['importance']} {r['title'][:70]}")
        print("\ndry run: nothing written")
        return 0
    n = upsert(env, merged)
    print(f"upserted {n} rows into market_events")
    return 0


if __name__ == "__main__":
    sys.exit(main())
