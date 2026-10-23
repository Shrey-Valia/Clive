#!/usr/bin/env python3
"""Clive health probe: a read-only check of every bot, account, data source and host.

    python3 health/check.py --dry-run          print the check table, write nothing
    python3 health/check.py                    write one health_checks row per check (Supabase, 008)
    python3 health/check.py --dry-run --now 2026-10-22T16:30   judge the schedule as of another ET time
    python3 health/check.py --host vps-1       tag the rows with this host (default: the hostname)

It never trades. Alpaca is only ever sent GETs; Supabase gets inserts into health_checks and
alerts (bot_id 'health'). Every check is wrapped: one that raises becomes a 'fail' row with the
error, and the probe carries on. Secrets are never printed (error text is scrubbed of them).

Components (health_checks.component):
  scheduler   expected vs observed runs per bot, today and the last 5 sessions (Alpaca calendar,
              cron schedule below; observed from the local cron logs and Supabase)
  positions   shorting: leftover shorts (v2 never holds a short overnight), longs outside the
              long-sleeve window, gross vs the config caps, open orders after 15:58.
              options: spreads with <= 1 trading day to expiry still open after 15:00, unpaired legs
  accounts    both Alpaca paper accounts: reachable, ACTIVE, not blocked, shorting / options level
  data        TradingView, FINRA, SEC, Yahoo RSS, FMP, Alpaca data, Cboe VIX, Nasdaq, Fed calendar
  state       guard files, saved picks, stale locks, the long sleeve file, the options account pin
  supabase    reachable, migrations 004-008 applied, market_events fresh, the dashboard site up
  host        disk, clock, Python, macOS sleep / wake schedule, cron entries, uptime / sleep log
  health      'alive' heartbeat;  summary: the worst status per component

Alerting: a check that is 'fail' now but was not failing in this host's previous run raises an
alerts row (bot_id 'health') plus an ntfy push when NTFY_TOPIC is set; an open health alert is
resolved when its check passes again.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import email.utils
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

VERSION = "1.0"
ROOT = Path(__file__).resolve().parent.parent
SHORT = ROOT / "Stock Shorting Bot"
OPT = ROOT / "Options Bot"
EVENTS = ROOT / "events"
HEALTH = ROOT / "health"
STATE_FILE = HEALTH / "logs" / "last_status.json"
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
TRADE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"
DASHBOARD_URL = os.environ.get("HEALTH_DASHBOARD_URL", "https://clive-terminal.netlify.app")
UA = "clive-health/1.0 (read-only probe)"
UA_BROWSER = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = 12
RANK = {"ok": 0, "unknown": 1, "warn": 2, "fail": 3}
COMPONENTS = ["scheduler", "positions", "accounts", "data", "state", "supabase", "host"]


# ====================================================================== env + helpers
def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


SHORT_ENV = read_env(SHORT / ".env")
OPT_ENV = read_env(OPT / ".env")
_SECRETS = sorted({v for e in (SHORT_ENV, OPT_ENV) for k, v in e.items()
                   if len(v) >= 8 and any(s in k for s in ("KEY", "SECRET", "TOKEN", "TOPIC"))}, key=len, reverse=True)


def scrub(text) -> str:
    """Error text with every known secret value and apikey= parameter blanked."""
    s = str(text)
    for v in _SECRETS:
        s = s.replace(v, "***")
    return re.sub(r"(apikey|api_key|token)=[^&\s'\"]+", r"\1=***", s, flags=re.I)


def env_get(name: str, *envs: dict) -> str:
    """Process env wins when set (even empty: run-job.sh blanks NTFY_TOPIC in shadow mode)."""
    if name in os.environ:
        return os.environ[name]
    for e in envs:
        if e.get(name):
            return e[name]
    return ""


def worst(statuses) -> str:
    st = [s for s in statuses if s]
    return max(st, key=lambda s: RANK.get(s, 1)) if st else "ok"


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(UTC).isoformat() if t else None


def et_str(t: dt.datetime | None, date=True) -> str:
    if not t:
        return "-"
    t = t.astimezone(ET)
    return t.strftime("%Y-%m-%d %H:%M ET" if date else "%H:%M")


def parse_ts(s) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except ValueError:
        return None


def http(method: str, url: str, **kw) -> tuple[requests.Response, int]:
    kw.setdefault("timeout", TIMEOUT)
    h = {"User-Agent": UA}
    h.update(kw.pop("headers", None) or {})
    t0 = time.monotonic()
    r = requests.request(method, url, headers=h, **kw)
    return r, int((time.monotonic() - t0) * 1000)


def fmt_usd(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


# ====================================================================== Alpaca (GET only)
class Alpaca:
    def __init__(self, key: str, secret: str, label: str):
        self.h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.ok = bool(key and secret)
        self.label = label

    def get(self, path: str, params=None, data=False):
        if not self.ok:
            raise RuntimeError(f"{self.label}: Alpaca keys not set")
        r, ms = http("GET", (DATA_URL if data else TRADE_URL) + path, params=params, headers=self.h)
        if r.status_code >= 300:
            raise RuntimeError(f"GET {path}: HTTP {r.status_code} {scrub(r.text[:160])}")
        return r.json()


SHORT_API = Alpaca(SHORT_ENV.get("APCA_API_KEY_ID", ""), SHORT_ENV.get("APCA_API_SECRET_KEY", ""), "shorting")
OPT_API = Alpaca(OPT_ENV.get("OPT_APCA_API_KEY_ID", ""), OPT_ENV.get("OPT_APCA_API_SECRET_KEY", ""), "options")


# ====================================================================== Supabase
# PostgREST / Postgres codes that really mean "this table or column does not exist" (a migration
# not applied). Anything else - 5xx, 429, timeouts, DNS, connection resets - is transient and
# must never be reported as a missing migration (false alarm 2026-10-23 07:15 ET).
SCHEMA_MISSING_CODES = ("42703", "42P01", "PGRST204", "PGRST205", "PGRST200")
TRANSIENT_HTTP = {0, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 527, 530}


class SupaError(RuntimeError):
    def __init__(self, msg: str, status: int = 0, body: str = ""):
        super().__init__(msg)
        self.status, self.body = status, body or ""


def schema_missing(e: Exception) -> bool:
    """True only for a definite 'relation/column does not exist' answer from PostgREST."""
    if not isinstance(e, SupaError) or e.status not in (400, 404):
        return False
    b = e.body
    return any(c in b for c in SCHEMA_MISSING_CODES) or ("does not exist" in b) or ("Could not find" in b)


def transient(e: Exception) -> bool:
    """Network trouble or a 5xx/429: retry, and report 'unknown'/'warn' rather than a real fault."""
    if isinstance(e, SupaError):
        return e.status in TRANSIENT_HTTP
    return isinstance(e, (requests.ConnectionError, requests.Timeout, TimeoutError, ConnectionError, OSError))


def with_retry(fn, tries: int = 3, pause: float = 1.5):
    """fn() with up to `tries` attempts on transient errors; the last error is re-raised."""
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if not transient(e) or i == tries - 1:
                raise
            time.sleep(pause * (i + 1))


class Supa:
    def __init__(self):
        self.url = (env_get("SUPABASE_URL", SHORT_ENV) or "").rstrip("/")
        key = env_get("SUPABASE_SERVICE_KEY", SHORT_ENV)
        self.ok = bool(self.url and key)
        self.h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def get(self, table: str, params: str = "", timeout=TIMEOUT):
        if not self.ok:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        try:
            r, ms = http("GET", f"{self.url}/rest/v1/{table}?{params}", headers=self.h, timeout=timeout)
        except requests.RequestException as e:
            raise SupaError(f"{table}: {type(e).__name__}: {scrub(e)[:160]}", 0) from e
        if r.status_code >= 300:
            raise SupaError(f"{table}: HTTP {r.status_code} {scrub(r.text[:200])}", r.status_code, r.text[:500])
        try:
            return r.json()
        except ValueError as e:                   # an HTML error page from a proxy: transient
            raise SupaError(f"{table}: non-JSON answer (HTTP {r.status_code})", 502, r.text[:200]) from e

    def try_get(self, table: str, params: str = "", default=None):
        try:
            return self.get(table, params)
        except Exception:
            return default

    def insert(self, table: str, rows):
        r, _ = http("POST", f"{self.url}/rest/v1/{table}", headers={**self.h, "Prefer": "return=minimal"},
                    data=json.dumps(rows, default=str))
        if r.status_code >= 300:
            raise SupaError(f"insert {table}: HTTP {r.status_code} {scrub(r.text[:200])}", r.status_code, r.text[:500])

    def patch(self, table: str, params: str, body: dict):
        r, _ = http("PATCH", f"{self.url}/rest/v1/{table}?{params}", headers={**self.h, "Prefer": "return=minimal"},
                    data=json.dumps(body, default=str))
        if r.status_code >= 300:
            raise SupaError(f"patch {table}: HTTP {r.status_code} {scrub(r.text[:200])}", r.status_code, r.text[:500])


SB = Supa()


# ====================================================================== calendar
class Session:
    def __init__(self, date: dt.date, open_: str, close: str):
        self.date = date
        oh, om = map(int, open_.split(":"))
        ch, cm = map(int, close.split(":"))
        self.open = dt.datetime.combine(date, dt.time(oh, om), ET)
        self.close = dt.datetime.combine(date, dt.time(ch, cm), ET)
        self.close_min = ch * 60 + cm

    def at(self, minutes: int) -> dt.datetime:
        return dt.datetime.combine(self.date, dt.time(minutes // 60, minutes % 60), ET)

    @property
    def half(self) -> bool:
        return self.close_min < 16 * 60


class Calendar:
    """Alpaca /v2/calendar (half days included); Mon-Fri fallback if Alpaca is unreachable."""

    def __init__(self, today: dt.date):
        self.source = "alpaca"
        start, end = today - dt.timedelta(days=21), today + dt.timedelta(days=10)
        rows = None
        for api in (SHORT_API, OPT_API):
            try:
                rows = api.get("/v2/calendar", {"start": start.isoformat(), "end": end.isoformat()})
                break
            except Exception:
                continue
        if rows is None:
            self.source = "weekday fallback (Alpaca calendar unreachable)"
            rows, d = [], start
            while d <= end:
                if d.weekday() < 5:
                    rows.append({"date": d.isoformat(), "open": "09:30", "close": "16:00"})
                d += dt.timedelta(days=1)
        self.sessions = [Session(dt.date.fromisoformat(r["date"]), r["open"], r["close"]) for r in rows]
        self.by_date = {s.date: s for s in self.sessions}

    def get(self, d: dt.date) -> Session | None:
        return self.by_date.get(d)

    def last_n(self, today: dt.date, n: int) -> list[Session]:
        """The n sessions before today, plus today's when it is one; oldest first."""
        past = [s for s in self.sessions if s.date < today][-n:]
        return past + ([self.by_date[today]] if today in self.by_date else [])

    def prev(self, d: dt.date) -> Session | None:
        p = [s for s in self.sessions if s.date < d]
        return p[-1] if p else None

    def next(self, d: dt.date) -> Session | None:
        n = [s for s in self.sessions if s.date > d]
        return n[0] if n else None

    def latest_at_or_before(self, d: dt.date) -> Session | None:
        p = [s for s in self.sessions if s.date <= d]
        return p[-1] if p else None


# ====================================================================== probe
class Probe:
    def __init__(self, now: dt.datetime, host: str):
        self.now = now
        self.today = now.date()
        self.host = host
        self.results: list[dict] = []
        self.cal: Calendar | None = None
        self.cache: dict = {}
        self.write = False

    def add(self, component, name, status, message, detail=None, expected_at=None, observed_at=None):
        self.results.append({"component": component, "check_name": name, "status": status,
                             "message": scrub(message)[:900], "detail": detail,
                             "expected_at": iso(expected_at), "observed_at": iso(observed_at)})

    def guard(self, component: str, name: str, fn, *a):
        """Run one check group; a crash becomes a 'fail' row (the probe itself never crashes)."""
        try:
            fn(*a)
        except Exception as e:
            self.add(component, name, "fail", f"check crashed: {type(e).__name__}: {scrub(e)}",
                     {"traceback": scrub(traceback.format_exc()[-1500:])})

    # shared reads, cached --------------------------------------------------------------
    def short_positions(self):
        if "spos" not in self.cache:
            self.cache["spos"] = SHORT_API.get("/v2/positions")
        return self.cache["spos"]

    def short_account(self):
        if "sacct" not in self.cache:
            self.cache["sacct"] = SHORT_API.get("/v2/account")
        return self.cache["sacct"]

    def opt_account(self):
        if "oacct" not in self.cache:
            self.cache["oacct"] = OPT_API.get("/v2/account")
        return self.cache["oacct"]


# ====================================================================== config peeks (no import)
def config_value(name: str, default=None):
    """A literal from shortingbot/config.py, read as text (importing it is not needed and other
    work may be mid-edit there)."""
    try:
        src = (SHORT / "shortingbot" / "config.py").read_text()
    except OSError:
        return default
    m = re.search(rf"^{name}\s*=\s*(.+?)(#.*)?$", src, re.M)
    if not m:
        return default
    raw = m.group(1).strip()
    if raw.startswith("os.environ.get("):           # OVERNIGHT_LONG_ENABLED = os.environ.get("X", "1")...
        em = re.match(r'os\.environ\.get\("(\w+)",\s*"([^"]*)"\)', raw)
        if em:
            v = os.environ.get(em.group(1), SHORT_ENV.get(em.group(1), em.group(2)))
            return v.strip().lower() in ("1", "true", "yes")
    if re.fullmatch(r"[-+]?[\d_]+(\.\d+)?", raw):
        return float(raw.replace("_", ""))
    if raw in ("True", "False", "None"):
        return {"True": True, "False": False, "None": None}[raw]
    return raw.strip('"').strip("'")


# ====================================================================== 1. scheduler
# (bot, job, label, minutes-of-day from the session close or a fixed time, late grace, missed
#  after, search window) -- all minutes. A job's run counts when its first log line / Supabase
#  row lands in [expected - 2, expected + window]; later than grace is LATE, nothing by
#  "missed after" is MISSED.
BOT_NAME = {"shorting": "Shorting bot", "options": "Options bot", "events": "Events sync"}


def expected_slots(s: Session, extra: set[str]) -> list[dict]:
    c = s.close_min
    out = []

    def slot(bot, job, label, minutes, grace=3, missed=5, window=30, repeat=False):
        out.append({"bot": bot, "job": job, "label": label, "date": s.date.isoformat(),
                    "exp": s.at(minutes), "grace": grace, "missed": missed, "window": window, "repeat": repeat})

    slot("events", "sync", "07:00 events sync", 7 * 60, grace=15, missed=30, window=120)
    slot("shorting", "open", "09:31 open entry", 9 * 60 + 31, window=19)
    m = 9 * 60 + 45
    while m <= min(15 * 60 + 45, c - 15):
        slot("shorting", "monitor", f"{m // 60:02d}:{m % 60:02d} monitor", m, window=14, repeat=True)
        m += 15
    slot("shorting", "close", f"{(c - 20) // 60:02d}:{(c - 20) % 60:02d} close/cover", c - 20, window=30)
    if "fallback" in extra:
        slot("shorting", "fallback", f"{(c - 10) // 60:02d}:{(c - 10) % 60:02d} fallback", c - 10, window=20)
    if "leftovers" in extra:
        for h in (10, 12, 14):
            if h * 60 < c:
                slot("shorting", "leftovers", f"{h:02d}:00 cover-leftovers", h * 60, window=14, repeat=True)
    slot("shorting", "sync", "16:15 sync", 16 * 60 + 15, grace=10, missed=15, window=45)
    slot("options", "sweep", "09:45 expiry sweep", 9 * 60 + 45, window=14)
    m = 10 * 60
    while m <= 15 * 60 + 30:
        slot("options", "intraday", f"{m // 60:02d}:{m % 60:02d} intraday", m, window=14, repeat=True)
        m += 15
    slot("options", "daily", f"{(c - 15) // 60:02d}:{(c - 15) % 60:02d} daily decision", c - 15, window=25)
    slot("options", "sync", "16:15 sync", 16 * 60 + 15, grace=10, missed=15, window=45)
    return out


LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.\d]* (\w+)\s+(.*)$")


def _log_lines(path: Path, since: dt.datetime):
    """(time ET, level, message) per timestamped line; untimestamped lines (tracebacks) carry the
    previous line's time with level TRACE."""
    out, last_t = [], None
    try:
        with open(path, errors="replace") as fh:
            for raw in fh:
                raw = raw.rstrip("\n")
                m = LOG_LINE.match(raw)
                if m:
                    last_t = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET)
                    if last_t >= since:
                        out.append((last_t, m.group(2).upper(), m.group(3)))
                elif last_t and last_t >= since and raw.strip():
                    out.append((last_t, "TRACE", raw.strip()))
    except OSError:
        return []
    return out


# multi-line runs: the job that owns the untagged lines after its first line, for this many minutes
RUN_SPAN = {("shorting", "open"): 25, ("shorting", "close"): 22, ("shorting", "fallback"): 15,
            ("shorting", "leftovers"): 10, ("options", "daily"): 20, ("options", "sweep"): 30}


def classify_short(msg: str) -> str | None:
    if msg.startswith("=== daily open run"):
        return "open"
    if msg.startswith("=== daily cover-leftovers run"):
        return "leftovers"
    if "--fallback" in msg:
        return "fallback"
    if msg.startswith("--half-day-only"):
        return "halfday_noop"
    if re.match(r"=== daily (close )?run", msg):
        return "close"
    if msg.startswith("monitor"):
        return "monitor"
    if msg.startswith("sync") or "sync failed" in msg:
        return "sync"
    return None


def classify_opt(msg: str) -> str | None:
    if re.match(r"sweep\b", msg):
        return "sweep"
    if re.match(r"intraday\b", msg):
        return "intraday"
    if msg.startswith("sync"):
        return "sync"
    if (msg.startswith("decision time today") or msg.startswith("market data via") or msg.startswith("MODE ")
            or msg.startswith("no options account")):
        return "daily"
    return None


def log_evidence(bot: str, path: Path, since: dt.datetime) -> list[dict]:
    """Evidence rows {bot, job, t, error, text, source} from one cron log."""
    classify = classify_short if bot == "shorting" else classify_opt
    ev, run = [], None          # run = (job, start time) of the current multi-line run
    for t, level, msg in _log_lines(path, since):
        job = classify(msg)
        is_err = level in ("ERROR", "CRITICAL") or (level == "TRACE" and msg.startswith("Traceback"))
        if job == "halfday_noop":
            continue
        span = lambda r: 60 * RUN_SPAN[(bot, r[0])]
        if job and (bot, job) in RUN_SPAN:
            if job == "close" and run and run[0] == "fallback" and (t - run[1]).total_seconds() < 120:
                job = "fallback"                     # the fallback's own close-run header
            elif not (run and run[0] == job and (t - run[1]).total_seconds() < span(run)):
                run = (job, t)
        elif job is None and run and (t - run[1]).total_seconds() < span(run):
            job = run[0]                             # untagged line inside a multi-line run
            if not is_err:
                continue
        if job is None and not is_err:
            continue
        ev.append({"bot": bot, "job": job, "t": t, "error": is_err, "text": msg[:300], "source": "log"})
    return ev


def _et_min(t: dt.datetime) -> int:
    t = t.astimezone(ET)
    return t.hour * 60 + t.minute


def supabase_evidence(since: dt.datetime) -> tuple[list[dict], list[str]]:
    ev, notes = [], []
    if not SB.ok:
        return ev, ["Supabase not configured"]
    since_s = since.astimezone(UTC).isoformat()
    runs = SB.try_get("short_runs", "select=id,mode,status,started_at,scan_at,entry_at,exit_at,message"
                                    f"&started_at=gte.{since_s}&order=id.desc&limit=60")
    if runs is None:
        runs = SB.try_get("short_runs", "select=id,mode,status,started_at,message&order=id.desc&limit=60", []) or []
        notes.append("short_runs: 004 columns unavailable")
    for r in runs:
        if r.get("mode") != "execute":
            continue
        st = parse_ts(r.get("started_at"))
        if st:
            mins = _et_min(st)
            job = "open" if 9 * 60 <= mins < 11 * 60 else "close" if 12 * 60 <= mins < 17 * 60 else None
            if job:
                ev.append({"bot": "shorting", "job": job, "t": st, "error": r.get("status") == "failed",
                           "text": f"short_runs #{r['id']} {r.get('status')}: {r.get('message') or ''}"[:300], "source": "supabase"})
        for col, job, off in (("scan_at", "close", 5), ("entry_at", "open", 0), ("exit_at", "close", 15)):
            t = parse_ts(r.get(col))
            if t:
                ev.append({"bot": "shorting", "job": job, "t": t - dt.timedelta(minutes=off), "error": False,
                           "text": f"short_runs #{r['id']} {col}", "source": "supabase"})
    for r in SB.try_get("sync_runs", f"select=bot_id,at,status,message&at=gte.{since_s}&order=at.desc&limit=60", []) or []:
        t = parse_ts(r.get("at"))
        if t and r.get("bot_id") in ("shorting", "options"):
            ev.append({"bot": r["bot_id"], "job": "sync", "t": t - dt.timedelta(minutes=2), "error": r.get("status") not in ("ok", None),
                       "text": f"sync_runs: {r.get('message') or r.get('status')}"[:300], "source": "supabase"})
    for r in SB.try_get("options_live_runs", f"select=id,kind,mode,status,started_at,message&started_at=gte.{since_s}"
                                             "&order=id.desc&limit=200", []) or []:
        t = parse_ts(r.get("started_at"))
        if t and r.get("mode") == "execute":
            job = {"daily": "daily", "intraday": "intraday", "sweep": "sweep"}.get(r.get("kind") or "daily")
            if job:
                ev.append({"bot": "options", "job": job, "t": t, "error": r.get("status") == "failed",
                           "text": f"options_live_runs #{r['id']} {r.get('status')}: {r.get('message') or ''}"[:300], "source": "supabase"})
    for r in SB.try_get("run_events", f"select=bot_id,at,level,step,message&at=gte.{since_s}&level=in.(ERROR,CRITICAL)"
                                      "&order=at.desc&limit=200", []) or []:
        t = parse_ts(r.get("at"))
        if t:
            step = (r.get("step") or "").lower()
            ev.append({"bot": r.get("bot_id") or "shorting", "job": "monitor" if "monitor" in step else None, "t": t,
                       "error": True, "text": f"run_events [{step}] {r.get('message') or ''}"[:300], "source": "supabase"})
    for b in SB.try_get("bots", "select=id,last_run_at,last_run_status,last_sync_at,last_sync_status", []) or []:
        if b.get("id") == "shorting":
            t = parse_ts(b.get("last_run_at"))
            if t and t >= since:
                mins = _et_min(t)
                job = "open" if 9 * 60 <= mins < 11 * 60 else "close" if 12 * 60 <= mins < 17 * 60 else None
                if job:
                    ev.append({"bot": "shorting", "job": job, "t": t, "error": b.get("last_run_status") == "error",
                               "text": f"bots.last_run_at ({b.get('last_run_status')})", "source": "supabase", "end_only": True})
    me = SB.try_get("market_events", "select=updated_at&order=updated_at.desc&limit=1")
    if me:
        t = parse_ts(me[0].get("updated_at"))
        if t:
            ev.append({"bot": "events", "job": "sync", "t": t - dt.timedelta(minutes=1), "error": False,
                       "text": "market_events.updated_at (latest run only)", "source": "supabase"})
    return ev, notes


def events_log_evidence() -> list[dict]:
    """sync.log has no timestamps: its mtime is the last run's finish."""
    p = EVENTS / "logs" / "sync.log"
    try:
        t = dt.datetime.fromtimestamp(p.stat().st_mtime, ET)
        tail = p.read_text(errors="replace")[-3000:]
    except OSError:
        return []
    err = "Traceback" in tail.split("market events sync")[-1] or "ERROR" in tail.split("market events sync")[-1]
    return [{"bot": "events", "job": "sync", "t": t - dt.timedelta(minutes=1), "error": err,
             "text": "events/logs/sync.log last write", "source": "log"}]


def crontab_text() -> str | None:
    if shutil.which("crontab") is None:
        return None
    try:
        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return None


def systemd_timers() -> str | None:
    if platform.system() != "Linux" or shutil.which("systemctl") is None:
        return None
    try:
        r = subprocess.run(["systemctl", "list-timers", "clive-*", "--all", "--no-pager"], capture_output=True, text=True, timeout=10)
        return r.stdout
    except Exception:
        return None


def optional_jobs() -> set[str]:
    """The shorting safety-net jobs that are scheduled on this host (cron line or systemd timer)."""
    ct = crontab_text() or ""
    st = systemd_timers() or ""
    out = set()
    if re.search(r"^[^#\n]*daily\.py[^\n]*--fallback", ct, re.M) or "clive-short-fallback.timer" in st:
        out.add("fallback")
    if re.search(r"^[^#\n]*daily\.py[^\n]*--cover-leftovers", ct, re.M) or "clive-short-cover-leftovers.timer" in st:
        out.add("leftovers")
    return out


def first_scheduled(jobs: set[str], now: dt.datetime, write: bool) -> dict[str, dt.datetime]:
    """When this probe first saw each optional job scheduled (health/logs/last_status.json). A job
    added to cron mid-day is only expected from then on; slots before it are not counted."""
    st = _load_json(STATE_FILE) or {}
    seen = st.get("_scheduled_since", {})
    out = {}
    for j in jobs:
        out[j] = parse_ts(seen.get(j)) or now
        seen.setdefault(j, iso(now))
    for j in list(seen):
        if j not in jobs:
            seen.pop(j)
    if write:
        st["_scheduled_since"] = seen
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(st, indent=1))
        except OSError:
            pass
    return out


def evaluate_slot(slot: dict, ev: list[dict], now: dt.datetime) -> dict:
    exp = slot["exp"]
    lo, hi = exp - dt.timedelta(minutes=2), exp + dt.timedelta(minutes=slot["window"])
    mine = [e for e in ev if e["bot"] == slot["bot"] and e.get("job") == slot["job"] and lo <= e["t"] <= hi]
    errs = [e for e in mine if e["error"]] + slot.get("orphans", [])
    seen = sorted(mine + slot.get("orphans", []), key=lambda e: e["t"])
    res = {k: slot[k] for k in ("bot", "job", "label", "date")}
    res.update(expected_at=iso(exp), observed_at=None, status="PENDING", delay_min=None, note="", sources=[])
    if seen:
        first = seen[0]["t"]
        delay = (first - exp).total_seconds() / 60
        res.update(observed_at=iso(first), delay_min=round(delay, 1), sources=sorted({e["source"] for e in seen}))
        if errs:
            res.update(status="FAILED", note=errs[0]["text"])
        elif delay > slot["grace"]:
            res.update(status="LATE", note=f"started {delay:.0f} min late")
        else:
            res.update(status="OK", note=seen[-1]["text"][:160])
    elif now > exp + dt.timedelta(minutes=slot["missed"]):
        res.update(status="MISSED", note="no log line and no Supabase row")
    elif now >= exp:
        res.update(status="PENDING", note="due now")
    return res


def check_scheduler(p: Probe):
    cal = p.cal
    sessions = cal.last_n(p.today, 5)
    if not sessions:
        p.add("scheduler", "timeline", "unknown", "no sessions in the calendar window")
        return
    since = dt.datetime.combine(sessions[0].date, dt.time(0, 0), ET)
    ev = []
    ev += log_evidence("shorting", SHORT / "logs" / "cron.log", since)
    ev += log_evidence("options", OPT / "live" / "logs" / "cron.log", since)
    ev += events_log_evidence()
    sb_ev, notes = supabase_evidence(since)
    ev += sb_ev
    ev = [e for e in ev if e["t"] <= p.now + dt.timedelta(minutes=1)]     # --now in the past: ignore later evidence
    extra = optional_jobs()
    fb = "fallback" in extra
    since_opt = first_scheduled(extra, p.now, p.write)
    slots = [s for sess in sessions for s in expected_slots(sess, extra)]
    # untagged errors: give each to the latest slot of that bot that started before it
    for e in [e for e in ev if e.get("job") is None]:
        cands = [s for s in slots if s["bot"] == e["bot"] and s["exp"] - dt.timedelta(minutes=2) <= e["t"] <= s["exp"] + dt.timedelta(minutes=s["window"])]
        if cands:
            s = max(cands, key=lambda s: s["exp"])
            s.setdefault("orphans", []).append(e)
    # tracking start: a job's slots count from its first ever observation (or its bot's, if never seen)
    first_bot, first_job = {}, {}
    for e in ev:
        first_bot[e["bot"]] = min(first_bot.get(e["bot"], e["t"]), e["t"])
        k = (e["bot"], e.get("job"))
        first_job[k] = min(first_job.get(k, e["t"]), e["t"])
    rows = []
    for s in slots:
        start = first_job.get((s["bot"], s["job"])) or first_bot.get(s["bot"])
        if s["job"] in since_opt and (s["bot"], s["job"]) not in first_job:
            start = since_opt[s["job"]]                 # optional job never seen running: from when it was scheduled
        if start is None or s["exp"] + dt.timedelta(minutes=s["window"]) < start:
            continue                                  # before this job was ever observed: not tracked
        if s["exp"] > p.now + dt.timedelta(hours=12):
            continue
        rows.append(evaluate_slot(s, ev, p.now))
    for r in rows:
        if r["status"] == "PENDING" and parse_ts(r["expected_at"]) > p.now:
            r["note"] = "not due yet"
    today_s, prev_s = p.today.isoformat(), (cal.prev(p.today).date.isoformat() if cal.prev(p.today) else "")
    jobs = []
    for bot, job in [("shorting", "open"), ("shorting", "monitor"), ("shorting", "close"), ("shorting", "fallback"),
                     ("shorting", "leftovers"), ("shorting", "sync"), ("options", "sweep"), ("options", "intraday"), ("options", "daily"),
                     ("options", "sync"), ("events", "sync")]:
        mine = sorted([r for r in rows if r["bot"] == bot and r["job"] == job], key=lambda r: r["expected_at"])
        name = f"{bot}.{job}"
        if not mine:
            if job == "leftovers" and "leftovers" not in extra:
                p.add("scheduler", name, "ok", "cover-leftovers job not scheduled (optional)")
            elif job == "fallback" and not fb:
                p.add("scheduler", name, "warn", "15:50 fallback job is not scheduled (no cron line / timer): a missed 15:40 run has no safety net",
                      {"fix": "add the --fallback cron line (Stock Shorting Bot README)"})
            elif bot in first_bot:
                p.add("scheduler", name, "ok", "no slot due yet")
            else:
                p.add("scheduler", name, "unknown", f"no runs of the {BOT_NAME[bot]} observed on this host or in Supabase")
            continue
        done = [r for r in mine if r["status"] != "PENDING"]
        bad = [r for r in done if r["status"] in ("MISSED", "FAILED")]
        late = [r for r in done if r["status"] == "LATE" and r["date"] in (today_s, prev_s)]
        latest = done[-1] if done else None
        repeat = job in ("monitor", "intraday", "leftovers")
        detail = {"slots": len(mine), "missed": sum(r["status"] == "MISSED" for r in mine),
                  "failed": sum(r["status"] == "FAILED" for r in mine), "late": sum(r["status"] == "LATE" for r in mine),
                  "latest": latest}
        if latest and latest["status"] in ("MISSED", "FAILED"):
            # the most recent run is bad: fail. For a repeating job one blip in a row is a warn.
            streak = 0
            for r in reversed(done):
                if r["status"] in ("MISSED", "FAILED"):
                    streak += 1
                else:
                    break
            first_bad = done[len(done) - streak]
            status = "fail" if (not repeat or streak >= 2) else "warn"
            msg = slot_sentence(first_bad, streak if repeat else 1)
            p.add("scheduler", name, status, msg, {**detail, "bad": first_bad, "streak": streak},
                  parse_ts(first_bad["expected_at"]), parse_ts(first_bad["observed_at"]))
        elif bad:
            b = bad[-1]
            p.add("scheduler", name, "warn", f"recovered; earlier: {slot_sentence(b, 1)}", {**detail, "bad": b},
                  parse_ts(b["expected_at"]), parse_ts(b["observed_at"]))
        elif late:
            b = late[-1]
            p.add("scheduler", name, "warn", f"{BOT_NAME[bot]} {b['label']} ran {b['delay_min']:.0f} min late on {b['date']}",
                  {**detail, "bad": b}, parse_ts(b["expected_at"]), parse_ts(b["observed_at"]))
        elif not done:
            nx = mine[0]
            p.add("scheduler", name, "ok", f"next run due {nx['date']} {nx['label'].split(' ')[0]} ET", detail, parse_ts(nx["expected_at"]))
        else:
            lt = latest or mine[-1]
            p.add("scheduler", name, "ok", f"{len(done)} run{"s" if len(done) != 1 else ""} on time" + (f", last {lt['date']} {et_str(parse_ts(lt['observed_at']), False)} ET" if lt.get("observed_at") else ""),
                  detail, parse_ts(lt["expected_at"]), parse_ts(lt.get("observed_at")))
        jobs.append(name)
    sched = [r for r in p.results if r["component"] == "scheduler" and r["check_name"] != "timeline"]
    n = {k: sum(r["status"] == k for r in rows) for k in ("MISSED", "FAILED", "LATE", "OK", "PENDING")}
    for r in rows:
        r.pop("sources", None) if not r.get("sources") else None
    p.add("scheduler", "timeline", worst(r["status"] for r in sched),
          f"{n['MISSED']} missed, {n['FAILED']} failed, {n['LATE']} late, {n['OK']} ok over {len(sessions)} sessions",
          {"sessions": [s.date.isoformat() for s in sessions], "half_days": [s.date.isoformat() for s in sessions if s.half],
           "today": p.today.isoformat(), "calendar": cal.source, "fallback_scheduled": fb, "notes": notes,
           "first_seen": {k: iso(v) for k, v in first_bot.items()}, "slots": rows})


def slot_sentence(r: dict, streak: int) -> str:
    bot = BOT_NAME[r["bot"]]
    hhmm = r["label"].split(" ")[0]
    what = " ".join(r["label"].split(" ")[1:])
    when = f"{r['date']} {hhmm} ET"
    more = f" (and {streak - 1} more in a row)" if streak > 1 else ""
    if r["status"] == "MISSED":
        return f"{bot} missed the {hhmm} {what} run at {when}{more}"
    return f"{bot} {hhmm} {what} run at {when} failed: {r['note'][:200]}{more}"


# ====================================================================== 2. positions
def _fills_today(api: Alpaca, day: dt.date) -> list[dict]:
    try:
        return api.get("/v2/account/activities/FILL", {"date": day.isoformat()}) or []
    except Exception:
        return []


def check_positions_shorting(p: Probe):
    pos = p.short_positions()
    acct = p.short_account()
    equity = float(acct.get("equity") or 0)
    version = config_value("STRATEGY_VERSION", "intraday_v2")
    sess = p.cal.get(p.today)
    now = p.now
    shorts = [x for x in pos if x.get("asset_class", "us_equity") == "us_equity" and float(x["qty"]) < 0]
    longs = [x for x in pos if x.get("asset_class", "us_equity") == "us_equity" and float(x["qty"]) > 0]
    syms = [x["symbol"] for x in shorts]
    notional = sum(abs(float(x["market_value"])) for x in shorts)
    detail = {"strategy_version": version, "count": len(shorts), "symbols": syms, "notional": round(notional, 2),
              "positions": [{"symbol": x["symbol"], "qty": float(x["qty"]), "market_value": float(x["market_value"]),
                             "unrealized_pl": float(x.get("unrealized_pl") or 0)} for x in shorts]}
    # 2a leftover shorts --------------------------------------------------------------
    if version != "intraday_v2":
        p.add("positions", "shorting.leftover_shorts", "ok", f"{version}: overnight shorts are intended ({len(shorts)} open)", detail)
    elif not shorts:
        p.add("positions", "shorting.leftover_shorts", "ok", "no shorts open", detail)
    else:
        in_session = sess and sess.open + dt.timedelta(minutes=1) <= now < sess.close
        lst = ", ".join(syms)
        if in_session:
            opened = {f.get("symbol") for f in _fills_today(SHORT_API, p.today) if f.get("side") == "sell_short"}
            left = [s for s in syms if s not in opened]
            detail["opened_today"] = sorted(opened)
            if not left:
                p.add("positions", "shorting.leftover_shorts", "ok", f"{len(shorts)} shorts open, all opened today ({lst})", detail)
            else:
                st = "warn" if now < sess.at(10 * 60) else "fail"
                detail.update(count=len(left), symbols=left)
                p.add("positions", "shorting.leftover_shorts", st,
                      f"{len(left)} shorts held overnight (not opened today): {', '.join(left)}; the cover was missed", detail)
        else:
            last = p.cal.latest_at_or_before(p.today if (sess and now >= sess.close) else p.today - dt.timedelta(days=1))
            when = f"after the {last.date} close" if last else "outside market hours"
            p.add("positions", "shorting.leftover_shorts", "fail",
                  f"{len(shorts)} shorts held overnight {when}: {lst} ({fmt_usd(notional)} short)", detail)
    # 2b longs outside the sleeve window ------------------------------------------------
    enabled = config_value("OVERNIGHT_LONG_ENABLED", False)
    lsyms = [x["symbol"] for x in longs]
    lnot = sum(abs(float(x["market_value"])) for x in longs)
    ldetail = {"sleeve_enabled": bool(enabled), "count": len(longs), "symbols": lsyms, "notional": round(lnot, 2)}
    if not longs:
        p.add("positions", "shorting.longs_window", "ok", "no longs open", ldetail)
    elif not enabled:
        p.add("positions", "shorting.longs_window", "fail", f"{len(longs)} longs open but the long sleeve is off: {', '.join(lsyms)}", ldetail)
    else:
        outside = sess and sess.at(9 * 60 + 50) <= now < sess.close - dt.timedelta(minutes=11)
        if outside:
            st = "warn" if now < sess.at(10 * 60 + 15) else "fail"
            p.add("positions", "shorting.longs_window", st,
                  f"{len(longs)} overnight longs still open after the 09:50 exit deadline: {', '.join(lsyms)}", ldetail)
        else:
            p.add("positions", "shorting.longs_window", "ok", f"{len(longs)} longs inside the overnight window ({', '.join(lsyms)})", ldetail)
    # 2c gross vs caps --------------------------------------------------------------------
    cap_s = float(config_value("MAX_GROSS_PCT", 50.0) or 50.0)
    cap_l = float(config_value("OVERNIGHT_LONG_MAX_GROSS", 25.0) or 25.0)
    gs = 100 * notional / equity if equity else 0.0
    gl = 100 * lnot / equity if equity else 0.0
    st = "ok"
    if gs > cap_s * 1.5 or gl > cap_l * 1.5:
        st = "fail"
    elif gs > cap_s * 1.02 or gl > cap_l * 1.02:
        st = "warn"
    p.add("positions", "shorting.gross", st,
          f"short gross {gs:.1f}% of {fmt_usd(equity)} (cap {cap_s:.0f}%), long gross {gl:.1f}% (cap {cap_l:.0f}%)",
          {"short_gross_pct": round(gs, 2), "long_gross_pct": round(gl, 2), "short_cap": cap_s, "long_cap": cap_l, "equity": equity})
    # 2d open orders after 15:58 -------------------------------------------------------------
    orders = SHORT_API.get("/v2/orders", {"status": "open", "limit": 100})
    after = (not sess) or now >= sess.close - dt.timedelta(minutes=2) or now < sess.open - dt.timedelta(minutes=30)
    od = {"count": len(orders), "orders": [{"symbol": o.get("symbol"), "side": o.get("side"), "type": o.get("type"),
                                            "tif": o.get("time_in_force"), "qty": o.get("qty"), "status": o.get("status")} for o in orders[:20]]}
    planned = [o for o in orders if o.get("time_in_force") in ("opg", "cls")]
    stray = [o for o in orders if o not in planned]
    if not orders:
        p.add("positions", "shorting.open_orders", "ok", "no open orders", od)
    elif after and stray:
        p.add("positions", "shorting.open_orders", "fail",
              f"{len(stray)} open orders after the 15:58 cutoff: " + ", ".join(f"{o['side']} {o['symbol']}" for o in stray[:6]), od)
    elif after:
        p.add("positions", "shorting.open_orders", "warn", f"{len(planned)} open opening/closing-auction orders queued", od)
    else:
        p.add("positions", "shorting.open_orders", "ok", f"{len(orders)} working orders in session", od)


OCC = re.compile(r"^([A-Z.]{1,6})(\d{6})([CP])(\d{8})$")


def check_positions_options(p: Probe):
    pos = OPT_API.get("/v2/positions")
    sess = p.cal.get(p.today)
    legs, stocks = [], []
    for x in pos:
        m = OCC.match(x["symbol"])
        if not m:
            stocks.append(x)
            continue
        exp = dt.datetime.strptime(m.group(2), "%y%m%d").date()
        legs.append({"symbol": x["symbol"], "und": m.group(1), "exp": exp, "right": m.group(3),
                     "strike": int(m.group(4)) / 1000, "qty": float(x["qty"])})
    sdays = [s.date for s in p.cal.sessions]
    near = []
    for lg in legs:
        lg["tdte"] = sum(1 for d in sdays if p.today < d <= lg["exp"])
        if lg["tdte"] <= 1:
            near.append(lg)
    detail = {"legs": [{**lg, "exp": lg["exp"].isoformat()} for lg in legs], "stocks": [s["symbol"] for s in stocks]}
    if not legs:
        p.add("positions", "options.expiry", "ok", "no option positions", detail)
    elif near:
        late = sess is not None and p.now >= sess.at(15 * 60)
        names = ", ".join(sorted({f"{lg['und']} {lg['exp']:%m/%d} {lg['right']}" for lg in near}))
        p.add("positions", "options.expiry", "fail" if late or (sess is None) else "warn",
              f"{len(near)} legs with <= 1 trading day to expiry still open{' after 15:00' if late else ''}: {names}", detail)
    else:
        p.add("positions", "options.expiry", "ok", f"{len(legs)} legs, nearest expiry in {min(lg['tdte'] for lg in legs)} sessions", detail)
    groups: dict = {}
    for lg in legs:
        g = groups.setdefault((lg["und"], lg["exp"], lg["right"]), {"short": 0.0, "long": 0.0})
        g["short" if lg["qty"] < 0 else "long"] += abs(lg["qty"])
    unpaired = [f"{k[0]} {k[1]:%m/%d} {k[2]} short {v['short']:.0f} / long {v['long']:.0f}" for k, v in groups.items() if v["short"] != v["long"]]
    if stocks:
        p.add("positions", "options.unpaired", "fail", f"stock in the options account (assignment?): {', '.join(s['symbol'] + ' ' + s['qty'] for s in stocks)}", detail)
    elif unpaired:
        p.add("positions", "options.unpaired", "fail", f"unpaired legs: {'; '.join(unpaired)}", detail)
    else:
        p.add("positions", "options.unpaired", "ok", f"{len(groups)} spread sides, every short leg covered" if groups else "no legs", detail)


# ====================================================================== 3. accounts
def check_accounts(p: Probe):
    for name, api, getter in (("shorting", SHORT_API, p.short_account), ("options", OPT_API, p.opt_account)):
        try:
            t0 = time.monotonic()
            a = getter()
            ms = int((time.monotonic() - t0) * 1000)
        except Exception as e:
            p.add("accounts", f"{name}.account", "fail", f"{name} Alpaca paper account unreachable: {scrub(e)}")
            continue
        d = {"status": a.get("status"), "trading_blocked": a.get("trading_blocked"), "account_blocked": a.get("account_blocked"),
             "shorting_enabled": a.get("shorting_enabled"), "options_level": a.get("options_trading_level"),
             "equity": float(a.get("equity") or 0), "buying_power": float(a.get("buying_power") or 0),
             "options_buying_power": float(a.get("options_buying_power") or 0) if a.get("options_buying_power") else None,
             "account_tail": str(a.get("account_number", ""))[-4:], "paper": str(a.get("account_number", "")).startswith("PA"),
             "latency_ms": ms}
        problems = []
        if a.get("status") != "ACTIVE":
            problems.append(f"status {a.get('status')}")
        if a.get("trading_blocked") or a.get("account_blocked"):
            problems.append("trading blocked")
        if name == "shorting" and not a.get("shorting_enabled"):
            problems.append("shorting disabled")
        if name == "options" and int(a.get("options_trading_level") or 0) < 3:
            problems.append(f"options level {a.get('options_trading_level')} (needs 3)")
        if not d["paper"]:
            problems.append("not a paper account")
        if problems:
            p.add("accounts", f"{name}.account", "fail", f"{name} account …{d['account_tail']}: " + ", ".join(problems), d)
        else:
            p.add("accounts", f"{name}.account", "ok",
                  f"…{d['account_tail']} ACTIVE, equity {fmt_usd(d['equity'])}, buying power {fmt_usd(d['buying_power'])}"
                  + (f", options L{d['options_level']}" if name == "options" else ", shorting on"), d)


# ====================================================================== 4. data sources
def in_market(p: Probe) -> bool:
    s = p.cal.get(p.today)
    return bool(s and s.open <= p.now < s.close)


def data_checks(p: Probe) -> list:
    today = p.today
    prev = p.cal.prev(today + dt.timedelta(days=1) if p.cal.get(today) and p.now.hour >= 18 else today)

    def tradingview():
        body = {"filter": [{"left": "market_cap_basic", "operation": "greater", "right": 25_000_000}],
                "markets": ["america"], "symbols": {"query": {"types": ["stock"]}, "tickers": []},
                "columns": ["name", "change"], "sort": {"sortBy": "change", "sortOrder": "desc"}, "range": [0, 1]}
        r, ms = http("POST", "https://scanner.tradingview.com/america/scan", json=body)
        r.raise_for_status()
        n = r.json().get("totalCount")
        return "ok", f"scanner up, {n} stocks match", {"latency_ms": ms, "total": n}

    def finra_si():
        body = {"limit": 20, "compareFilters": [{"fieldName": "settlementDate", "fieldValue": (today - dt.timedelta(days=40)).isoformat(),
                                                 "compareType": "GTE"}], "domainFilters": [{"fieldName": "symbolCode", "values": ["AAPL"]}]}
        r, ms = http("POST", "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", json=body,
                     headers={"Accept": "application/json"})
        r.raise_for_status()
        rows = r.json() if r.text.strip().startswith("[") else []
        sd = max((x.get("settlementDate") for x in rows if x.get("settlementDate")), default=None)
        age = (today - dt.date.fromisoformat(sd)).days if sd else None
        st = "ok" if age is not None and age <= 35 else "warn"
        return st, f"API up, latest settlement {sd or '?'}" + (f" ({age} d old)" if age is not None else ""), {"latency_ms": ms, "settlement": sd, "age_days": age}

    def finra_shvol():
        d = prev.date if prev else today
        for _ in range(3):
            r, ms = http("GET", f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d:%Y%m%d}.txt", stream=True)
            r.close()
            if r.status_code == 200:
                return "ok", f"file for {d} present", {"latency_ms": ms, "date": d.isoformat()}
            s = p.cal.prev(d)
            if not s:
                break
            missing, d = d, s.date
        return "warn", f"no short-volume file for the last session (latest tried {d})", {"latency_ms": ms}

    def sec_ftd():
        y, m = today.year, today.month
        for _ in range(4):
            for half in ("b", "a"):
                r, ms = http("HEAD", f"https://www.sec.gov/files/data/fails-deliver-data/cnsfails{y}{m:02d}{half}.zip",
                             headers={"User-Agent": "StockShortingBot/1.0 (personal research)"}, allow_redirects=True)
                if r.status_code == 200:
                    age_m = (today.year - y) * 12 + today.month - m
                    return ("ok" if age_m <= 2 else "warn"), f"latest file cnsfails{y}{m:02d}{half}", {"latency_ms": ms, "period": f"{y}{m:02d}{half}"}
                if r.status_code in (403, 429):
                    return "warn", f"sec.gov refused the probe (HTTP {r.status_code})", {"latency_ms": ms}
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return "fail", "no FTD file found in the last 4 months", {}

    def yahoo():
        r, ms = http("GET", "https://feeds.finance.yahoo.com/rss/2.0/headline", params={"s": "SPY", "region": "US", "lang": "en-US"},
                     headers={"User-Agent": UA_BROWSER})
        if r.status_code == 429:
            return "warn", "reachable but rate-limited (HTTP 429); the bot retries and falls back", {"latency_ms": ms, "http": 429}
        r.raise_for_status()
        n = r.text.count("<item>")
        return ("ok" if n else "warn"), f"{n} SPY headlines", {"latency_ms": ms, "items": n}

    def fmp():
        key = env_get("FMP_API_KEY", SHORT_ENV)
        if not key:
            return "ok", "not configured (FMP_API_KEY unset; Yahoo RSS is used)", {"configured": False}
        r, ms = http("GET", "https://financialmodelingprep.com/stable/news/stock", params={"symbols": "AAPL", "limit": 1, "apikey": key})
        return ("ok" if r.status_code == 200 else "fail"), f"HTTP {r.status_code}", {"latency_ms": ms}

    def alpaca_bars():
        t0 = time.monotonic()
        js = SHORT_API.get("/v2/stocks/SPY/bars/latest", {"feed": "iex"}, data=True)
        ms = int((time.monotonic() - t0) * 1000)
        t = parse_ts(js.get("bar", {}).get("t"))
        age = (p.now - t).total_seconds() / 60 if t else None
        st = "ok"
        if in_market(p) and (age is None or age > 15):
            st = "fail" if age is None or age > 45 else "warn"
        return st, f"latest SPY bar {et_str(t)} ({age:.0f} min old)" if age is not None else "no SPY bar", {"latency_ms": ms, "age_min": round(age, 1) if age else None}

    def alpaca_options():
        api = OPT_API if OPT_API.ok else SHORT_API
        t0 = time.monotonic()
        js = api.get("/v1beta1/options/snapshots/SPY", {"feed": "indicative", "limit": 5}, data=True)
        ms = int((time.monotonic() - t0) * 1000)
        n = len(js.get("snapshots") or {})
        return ("ok" if n else "fail"), f"SPY chain snapshot: {n} contracts in the first page", {"latency_ms": ms, "contracts": n}

    def cboe():
        out, worst_age = {}, 0.0
        for sym in ("VIX", "VIX3M"):
            r, ms = http("GET", f"https://cdn.cboe.com/api/global/delayed_quotes/quotes/_{sym}.json", headers={"User-Agent": UA_BROWSER})
            r.raise_for_status()
            d = r.json().get("data", {})
            ts = d.get("last_trade_time") or r.json().get("timestamp")
            t = None
            if ts:
                try:
                    t = dt.datetime.fromisoformat(str(ts).replace("Z", "")).replace(tzinfo=ET)
                except ValueError:
                    t = None
            age = (p.now - t).total_seconds() / 60 if t else None
            out[sym] = {"last": d.get("current_price"), "time": ts, "age_min": round(age, 1) if age is not None else None, "latency_ms": ms}
            worst_age = max(worst_age, age if age is not None else 999)
        st = "ok"
        if in_market(p) and worst_age > 25:
            st = "fail" if worst_age > 60 else "warn"
        return st, f"VIX {out['VIX']['last']} / VIX3M {out['VIX3M']['last']}, oldest quote {worst_age:.0f} min" + ("" if in_market(p) else " (market closed)"), out

    def nasdaq(kind):
        def f():
            url = {"earnings": "https://api.nasdaq.com/api/calendar/earnings", "econ": "https://api.nasdaq.com/api/calendar/economicevents"}[kind]
            r, ms = http("GET", url, params={"date": today.isoformat()},
                         headers={"User-Agent": UA_BROWSER, "Accept": "application/json", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
            r.raise_for_status()
            data = r.json().get("data") or {}
            rows = (data.get("rows") or []) if isinstance(data, dict) else []
            return "ok", f"API up, {len(rows)} rows for {today}", {"latency_ms": ms, "rows": len(rows)}
        return f

    def fed():
        r, ms = http("GET", "https://www.federalreserve.gov/json/calendar.json")
        r.raise_for_status()
        js = json.loads(r.content.decode("utf-8-sig"))
        n = len(js.get("events", []))
        return ("ok" if n else "warn"), f"{n} calendar events", {"latency_ms": ms, "events": n}

    return [("tradingview", tradingview), ("finra_si", finra_si), ("finra_shvol", finra_shvol), ("sec_ftd", sec_ftd),
            ("yahoo_rss", yahoo), ("fmp", fmp), ("alpaca_bars", alpaca_bars), ("alpaca_options", alpaca_options),
            ("cboe_vix", cboe), ("nasdaq_earnings", nasdaq("earnings")), ("nasdaq_econ", nasdaq("econ")), ("fed_calendar", fed)]


def check_data(p: Probe):
    checks = data_checks(p)
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fn): name for name, fn in checks}
        res = {}
        for f in cf.as_completed(futs, timeout=60):
            name = futs[f]
            try:
                res[name] = f.result()
            except Exception as e:
                res[name] = ("fail", f"unreachable: {type(e).__name__}: {scrub(e)[:200]}", {})
    for name, _ in checks:
        st, msg, d = res.get(name, ("fail", "timed out", {}))
        p.add("data", name, st, msg, d)


# ====================================================================== 5. state files
def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def check_state(p: Probe):
    cal, now, logs = p.cal, p.now, SHORT / "logs"
    sess = cal.get(p.today)
    # last_execute.json (15:40 job) -----------------------------------------------
    close_passed = sess and now >= sess.close - dt.timedelta(minutes=10)
    exp_close = sess if close_passed else cal.prev(p.today)
    g = _load_json(logs / "last_execute.json")
    if g is None:
        p.add("state", "shorting.last_execute", "warn", "logs/last_execute.json missing (the 15:40 --execute job has never run here)")
    else:
        d = g.get("date")
        ok = exp_close is None or (d and d >= exp_close.date.isoformat())
        p.add("state", "shorting.last_execute", "ok" if ok else "fail",
              f"15:40 job guard dated {d}" + ("" if ok else f"; expected {exp_close.date} (that day's close job never started)"),
              {"date": d, "expected": exp_close.date.isoformat() if exp_close else None, "orders": len(g.get("orders") or [])},
              exp_close.at(exp_close.close_min - 20) if exp_close else None)
    # last_open.json (09:31 job; written only when it sends orders) ----------------------
    open_passed = sess and now >= sess.at(9 * 60 + 40)
    exp_open = sess if open_passed else cal.prev(p.today)
    g = _load_json(logs / "last_open.json")
    d = g.get("date") if g else None
    if exp_open and (not d or d < exp_open.date.isoformat()):
        p.add("state", "shorting.last_open", "warn",
              f"09:31 job guard {'missing' if not d else 'dated ' + d}; expected {exp_open.date} (written only when the open job sends orders: no picks, or it did not run)",
              {"date": d, "expected": exp_open.date.isoformat()})
    else:
        p.add("state", "shorting.last_open", "ok", f"09:31 job guard dated {d}", {"date": d})
    # next_open_picks.json -------------------------------------------------------------
    scan_done = sess and now >= sess.close - dt.timedelta(minutes=5)
    if scan_done:
        nxt = cal.next(p.today)
        want = nxt.date if nxt else None
    else:
        want = sess.date if sess else (cal.next(p.today).date if cal.next(p.today) else None)
    pk = _load_json(logs / "next_open_picks.json")
    if pk is None:
        st = "fail" if scan_done else "warn"
        p.add("state", "shorting.next_open_picks", st,
              f"logs/next_open_picks.json missing; the {want} open has no saved picks" + (" (today's 15:45 scan never ran)" if scan_done else ""),
              {"expected_trade_date": want.isoformat() if want else None})
    else:
        td = pk.get("trade_date")
        good = want is None or td == want.isoformat() or (not scan_done and td and td > want.isoformat())
        p.add("state", "shorting.next_open_picks", "ok" if good else "fail",
              f"picks for {td}: {', '.join(pk.get('tickers') or []) or 'none'}" + ("" if good else f"; the next session is {want} (stale picks)"),
              {"trade_date": td, "expected_trade_date": want.isoformat() if want else None, "tickers": pk.get("tickers"), "saved_at": pk.get("saved_at")})
    # stale locks ------------------------------------------------------------------------
    locks = []
    for f in sorted(logs.glob("*.lock")):
        raw = (f.read_text().strip() if f.exists() else "")
        pid = int(raw) if raw.isdigit() else None
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        age = (time.time() - f.stat().st_mtime) / 60
        locks.append({"file": f.name, "pid": pid, "alive": alive, "age_min": round(age, 1)})
    stale = [l for l in locks if not l["alive"] and l["age_min"] > 1]
    if stale:
        p.add("state", "shorting.locks", "warn", "stale lock files (dead pid): " + ", ".join(f"{l['file']} pid {l['pid']}" for l in stale), {"locks": locks})
    else:
        p.add("state", "shorting.locks", "ok", f"{len(locks)} lock files" + (", all held by live processes" if locks else ""), {"locks": locks})
    # overnight_longs.json vs positions ----------------------------------------------------
    ol = _load_json(logs / "overnight_longs.json")
    try:
        held = {x["symbol"] for x in p.short_positions() if float(x["qty"]) > 0}
    except Exception:
        held = None
    if ol is None:
        p.add("state", "shorting.overnight_longs", "ok" if not held else "warn",
              "no live overnight_longs.json" + (f", but longs are held: {', '.join(sorted(held))}" if held else ""), {"held": sorted(held or [])})
    else:
        open_state = ol.get("status") not in ("exited", "flattened") and any(o.get("status") != "rejected" for o in ol.get("orders") or [])
        names = {o.get("ticker") or o.get("symbol") for o in ol.get("orders") or [] if o.get("status") != "rejected"}
        d = {"status": ol.get("status"), "entry_date": ol.get("entry_date"), "exit_date": ol.get("exit_date"),
             "file": sorted(n for n in names if n), "held": sorted(held or [])}
        if held is None:
            p.add("state", "shorting.overnight_longs", "unknown", "positions unavailable", d)
        elif open_state and not held and ol.get("exit_date") and ol["exit_date"] < p.today.isoformat():
            p.add("state", "shorting.overnight_longs", "warn", f"file still says '{ol.get('status')}' for {', '.join(d['file'])} but no longs are held", d)
        elif held - names:
            p.add("state", "shorting.overnight_longs", "warn", f"longs held that the sleeve file does not know: {', '.join(sorted(held - names))}", d)
        elif held and ol.get("exit_date") and ol["exit_date"] < p.today.isoformat():
            p.add("state", "shorting.overnight_longs", "fail", f"longs {', '.join(sorted(held))} past their exit date {ol['exit_date']}", d)
        else:
            p.add("state", "shorting.overnight_longs", "ok", f"sleeve file '{ol.get('status')}' ({ol.get('entry_date')} -> {ol.get('exit_date')}) matches positions", d)
    # options account pin -------------------------------------------------------------------
    pin_path = OPT / "live" / "state" / "account_pin.json"
    pin = _load_json(pin_path)
    try:
        aid = str(p.opt_account().get("id") or "")
    except Exception as e:
        aid = ""
        p.add("state", "options.account_pin", "unknown", f"options account unavailable: {scrub(e)[:120]}")
        return
    olds = sorted(x.name for x in pin_path.parent.glob("account_pin.old-*.json"))
    if pin is None:
        p.add("state", "options.account_pin", "warn", "no live/state/account_pin.json yet; the next --execute run pins the account in use",
              {"old_pins": olds, "account": aid[:8]})
    elif pin.get("id") != aid:
        p.add("state", "options.account_pin", "fail",
              f"pinned account {str(pin.get('id'))[:8]}… is not the OPT keys' account {aid[:8]}…: the options bot refuses to trade",
              {"pinned": str(pin.get("id"))[:8], "account": aid[:8]})
    else:
        p.add("state", "options.account_pin", "ok", f"pin matches account {aid[:8]}…", {"account": aid[:8]})


# ====================================================================== 6. supabase + dashboard
MIGRATIONS = [
    ("004", [("bots", "last_run_at,last_sync_at"), ("short_runs", "strategy_version,trade_date,exit_at"), ("sync_runs", "id"),
             ("run_events", "id"), ("alerts", "id,resolved_at")]),
    ("005", [("market_events", "id,ticker_key")]),
    ("006", [("options_live_positions", "sleeve,call_short_symbol")]),
    ("007", [("short_orders", "sleeve,limit_price"), ("short_trades", "sleeve")]),
    ("008", [("health_checks", "id,host,expected_at"), ("health_latest", "id")]),
]


def migration_status(get=None) -> tuple[tuple[str, str], dict]:
    """((status, message), detail). A migration is 'missing' only on a definite PostgREST
    'does not exist' answer; a transient error (after retries) makes it 'unverified' -> unknown."""
    get = get or SB.get
    missing, unverified = {}, {}
    for mig, probes in MIGRATIONS:
        for table, cols in probes:
            try:
                with_retry(lambda: get(table, f"select={cols}&limit=0"))
            except Exception as e:
                if schema_missing(e):
                    missing.setdefault(mig, []).append(f"{table}({cols})")
                else:
                    unverified.setdefault(mig, []).append(f"{table}: {scrub(e)[:80]}")
    applied = [m for m, _ in MIGRATIONS if m not in missing and m not in unverified]
    detail = {"applied": applied, "missing": missing, "unverified": unverified}
    if missing:
        early = [m for m in missing if m != MIGRATIONS[-1][0]]
        msg = "not applied: " + "; ".join(f"{m} ({', '.join(v)})" for m, v in missing.items())
        return ("fail" if early else "warn", msg), detail
    if unverified:
        return ("unknown", "could not verify " + ", ".join(sorted(unverified)) + " (transient Supabase error, not a missing migration): "
                + "; ".join(v[0] for v in unverified.values())[:300]), detail
    return ("ok", f"migrations {MIGRATIONS[0][0]}-{MIGRATIONS[-1][0]} applied"), detail


def check_supabase(p: Probe):
    if not SB.ok:
        p.add("supabase", "supabase.reachable", "fail", "SUPABASE_URL / SUPABASE_SERVICE_KEY missing from Stock Shorting Bot/.env")
        return
    t0 = time.monotonic()
    try:
        with_retry(lambda: SB.get("bots", "select=id&limit=1"))
        p.add("supabase", "supabase.reachable", "ok", f"REST API up ({int((time.monotonic() - t0) * 1000)} ms)",
              {"latency_ms": int((time.monotonic() - t0) * 1000)})
    except Exception as e:
        # 3 tries over ~5 s. A flaky or down Supabase does not stop trading (the bots log and carry
        # on), so a transient error is a warn; a 401/403 (key changed) is a real fail.
        st = "warn" if transient(e) else "fail"
        p.add("supabase", "supabase.reachable", st, f"Supabase {'flaky / unreachable' if st == 'warn' else 'refused the probe'}: {scrub(e)[:200]}",
              {"transient": st == "warn", "http": getattr(e, "status", None)})
        p.add("supabase", "supabase.migrations", "unknown", "not checked: Supabase unreachable")
        return
    st_, notes = migration_status()
    p.add("supabase", "supabase.migrations", *st_, notes)
    try:
        r = SB.get("market_events", "select=updated_at&order=updated_at.desc&limit=1")
        t = parse_ts(r[0]["updated_at"]) if r else None
        age = (p.now - t).total_seconds() / 3600 if t else None
        st = "ok" if age is not None and age <= 26 else "fail"
        p.add("supabase", "supabase.market_events", st,
              f"market_events last updated {et_str(t)} ({age:.1f} h ago)" if t else "market_events is empty",
              {"updated_at": iso(t), "age_h": round(age, 2) if age is not None else None}, observed_at=t)
    except Exception as e:
        p.add("supabase", "supabase.market_events", "unknown" if transient(e) else "fail", f"market_events unreadable: {scrub(e)[:160]}")
    try:
        r, ms = http("GET", DASHBOARD_URL, allow_redirects=True)
        if r.status_code < 400:
            p.add("supabase", "dashboard.site", "ok", f"{DASHBOARD_URL} HTTP {r.status_code} ({ms} ms)", {"latency_ms": ms, "http": r.status_code, "url": DASHBOARD_URL})
        elif r.status_code in (401, 403):
            p.add("supabase", "dashboard.site", "ok", f"{DASHBOARD_URL} up, protected (HTTP {r.status_code}, Netlify team login)", {"latency_ms": ms, "http": r.status_code, "url": DASHBOARD_URL})
        else:
            p.add("supabase", "dashboard.site", "warn" if r.status_code in TRANSIENT_HTTP else "fail", f"{DASHBOARD_URL} HTTP {r.status_code}",
                  {"latency_ms": ms, "http": r.status_code, "url": DASHBOARD_URL})
    except Exception as e:
        p.add("supabase", "dashboard.site", "warn" if transient(e) else "fail", f"{DASHBOARD_URL} unreachable: {scrub(e)[:160]}")


# ====================================================================== 7. host
EXPECTED_CRON = [
    ("shorting.open", r"daily\.py\s+--open\s+--execute", "31 9"),
    ("shorting.monitor", r"monitor\.py", "*/15 9-15"),
    ("shorting.close", r"daily\.py\s+--execute\s*(>|$)", "40 15"),
    ("shorting.halfday", r"daily\.py\s+--execute\s+--half-day-only", "40 12"),
    ("shorting.fallback", r"daily\.py\s+--execute\s+--fallback|daily\.py\s+--fallback\s+--execute", "50 15"),
    ("shorting.leftovers", r"daily\.py\s+--cover-leftovers", "0 10,12,14"),
    ("shorting.sync", r"Stock Shorting Bot.*sync\.py", "15 16"),
    ("options.sweep", r"live/run\.py\s+--sweep\s+--execute", "45 9"),
    ("options.intraday", r"live/run\.py\s+--intraday\s+--execute", "*/15 10-14"),
    ("options.daily", r"live/run\.py\s+--execute\s*(>|$)", "45 15"),
    ("options.sync", r"live/sync\.py", "15 16"),
    ("events.sync", r"sync_events\.py", "0 7"),
    ("health.check", r"health.*check\.py", "*/15 9-16"),
]
EXPECTED_TIMERS = ["clive-short-open", "clive-short-monitor", "clive-short-close", "clive-short-halfday", "clive-short-fallback",
                   "clive-short-sync", "clive-opt-sweep", "clive-opt-intraday", "clive-opt-daily", "clive-opt-halfday",
                   "clive-opt-sync", "clive-events-sync", "clive-health-check"]


def check_host(p: Probe):
    du = shutil.disk_usage(ROOT)
    free_gb, pct = du.free / 1e9, 100 * du.free / du.total
    p.add("host", "disk", "fail" if free_gb < 3 or pct < 3 else "warn" if free_gb < 10 or pct < 8 else "ok",
          f"{free_gb:.0f} GB free ({pct:.0f}%)", {"free_gb": round(free_gb, 1), "free_pct": round(pct, 1)})
    try:
        t0 = time.time()
        r, ms = http("HEAD", "https://www.google.com", allow_redirects=False)
        t1 = time.time()
        server = email.utils.parsedate_to_datetime(r.headers["Date"]).timestamp()
        off = (t0 + t1) / 2 - server
        st = "ok" if abs(off) < 5 else "warn" if abs(off) < 60 else "fail"
        p.add("host", "clock", st, f"clock offset {off:+.1f} s vs an HTTP Date header", {"offset_s": round(off, 2), "rtt_ms": ms})
    except Exception as e:
        p.add("host", "clock", "unknown", f"could not compare the clock: {scrub(e)[:120]}")
    v = sys.version_info
    bot_py = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3" if platform.system() == "Darwin" else "/usr/bin/python3"
    p.add("host", "python", "ok" if v >= (3, 11) else "warn", f"Python {platform.python_version()} ({sys.executable})",
          {"version": platform.python_version(), "executable": sys.executable, "bot_python_present": os.path.exists(bot_py)})
    if platform.system() == "Darwin":
        host_macos(p)
    elif platform.system() == "Linux":
        host_linux(p)
    # cron / timers --------------------------------------------------------------------
    ct, st = crontab_text(), systemd_timers()
    if st is not None and st.strip():
        have = [t for t in EXPECTED_TIMERS if f"{t}.timer" in st]
        miss = [t for t in EXPECTED_TIMERS if t not in have]
        p.add("host", "schedule_entries", "warn" if miss else "ok",
              f"{len(have)}/{len(EXPECTED_TIMERS)} systemd timers" + (f"; missing: {', '.join(miss)}" if miss else ""), {"kind": "systemd", "missing": miss})
    elif ct is not None:
        lines = [l for l in ct.splitlines() if l.strip() and not l.strip().startswith("#")]
        found, miss = [], []
        for name, pat, when in EXPECTED_CRON:
            hit = [l for l in lines if re.search(pat, l)]
            (found if hit else miss).append(name)
        crit = [m for m in miss if m in ("shorting.open", "shorting.close", "shorting.sync", "options.daily", "options.sweep")]
        st_ = "fail" if crit else "warn" if miss else "ok"
        p.add("host", "schedule_entries", st_, f"{len(found)}/{len(EXPECTED_CRON)} cron jobs present" + (f"; missing: {', '.join(miss)}" if miss else ""),
              {"kind": "crontab", "missing": miss, "found": found})
    else:
        p.add("host", "schedule_entries", "unknown", "no crontab or systemd on this host")
    # uptime ------------------------------------------------------------------------------
    try:
        if platform.system() == "Darwin":
            raw = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
            boot = dt.datetime.fromtimestamp(int(re.search(r"sec = (\d+)", raw).group(1)), ET)
        else:
            boot = p.now - dt.timedelta(seconds=float(Path("/proc/uptime").read_text().split()[0]))
        up_d = (p.now - boot).total_seconds() / 86400
        p.add("host", "uptime", "ok", f"up {up_d:.1f} days (booted {et_str(boot)})", {"boot": iso(boot), "uptime_days": round(up_d, 2)})
    except Exception as e:
        p.add("host", "uptime", "unknown", f"uptime unavailable: {scrub(e)[:100]}")


def host_macos(p: Probe):
    run = lambda *a: subprocess.run(list(a), capture_output=True, text=True, timeout=25).stdout
    g = run("pmset", "-g")
    m = re.search(r"^\s*sleep\s+(\d+)(.*)$", g, re.M)
    sleep_min = int(m.group(1)) if m else None
    prevented = (m.group(2).strip() if m else "")
    sched = run("pmset", "-g", "sched")
    repeat = "Repeating power events" in sched
    rep_lines = [l.strip() for l in sched.split("Scheduled power events")[0].splitlines()[1:] if l.strip()] if repeat else []
    if sleep_min == 0:
        p.add("host", "sleep_setting", "ok", "system sleep disabled (pmset sleep 0)", {"sleep_min": 0})
    else:
        p.add("host", "sleep_setting", "warn",
              f"system sleep after {sleep_min} min {prevented}; a closed lid on battery still sleeps (Clamshell Sleep)",
              {"sleep_min": sleep_min, "prevented_by": prevented})
    try:
        info = mac_power(p)
        for name, st, msg in judge_power(info, market_window(p)):
            p.add("host", name, st, msg, {k: info.get(k) for k in ("source", "percent", "state", "lid_closed", "sleep_min", "sleep_disabled")})
    except Exception as e:
        p.add("host", "power_source", "unknown", f"pmset / ioreg unavailable: {scrub(e)[:100]}")
    p.add("host", "wake_schedule", "ok" if repeat else "warn",
          ("repeating wake: " + "; ".join(rep_lines)) if repeat else "no repeating wake scheduled (pmset repeat)", {"repeat": rep_lines})
    # sleep stretches today from the power log (Sleep ... next full Wake; dark wakes stay asleep)
    try:
        log = subprocess.run(["pmset", "-g", "log"], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:
        p.add("host", "sleep_today", "unknown", f"pmset log unavailable: {scrub(e)[:100]}")
        return
    day = p.today.isoformat()
    evs = []
    for line in log.splitlines():
        if not line.startswith(day):
            continue
        mm = re.match(r"^(\S+ \S+) ([-+]\d{4}) (Sleep|Wake|DarkWake)\s{2,}(.*)$", line)
        if mm:
            t = dt.datetime.strptime(mm.group(1) + " " + mm.group(2), "%Y-%m-%d %H:%M:%S %z").astimezone(ET)
            evs.append((t, mm.group(3), mm.group(4)))
    stretches, start, why = [], None, ""
    for t, kind, text in evs:
        if kind == "Sleep" and start is None:
            start = t
            why = "lid closed" if "Clamshell" in text else "idle sleep" if "Idle" in text else "sleep"
            why += ", on battery" if "Batt" in text else ""
        elif kind == "Wake" and start is not None:
            stretches.append((start, t, why))
            start = None
    if start is not None:
        stretches.append((start, None, why))
    sess = p.cal.get(p.today)
    lo = sess.open - dt.timedelta(minutes=10) if sess else None
    hi = (dt.datetime.combine(p.today, dt.time(16, 20), ET)) if sess else None
    hits = []
    for a, b, why in stretches:
        b2 = b or p.now
        if sess and b2 > lo and a < hi and (b2 - a).total_seconds() >= 300:
            hits.append({"from": iso(a), "to": iso(b), "min": round((min(b2, hi) - max(a, lo)).total_seconds() / 60), "why": why})
    last_wake = max((t for t, k, _ in evs if k == "Wake"), default=None)
    total = sum(h["min"] for h in hits)
    if not sess:
        p.add("host", "sleep_today", "ok", "market closed today" + (f"; last wake {et_str(last_wake, False)} ET" if last_wake else ""), {"last_wake": iso(last_wake)})
    elif hits:
        desc = ", ".join(f"{et_str(parse_ts(h['from']), False)}-{et_str(parse_ts(h['to']), False) if h['to'] else 'now'} ({h['why']})" for h in hits)
        p.add("host", "sleep_today", "fail" if total >= 15 else "warn", f"Mac asleep {total} min in market hours today: {desc}",
              {"stretches": hits, "total_min": total, "last_wake": iso(last_wake)})
    else:
        p.add("host", "sleep_today", "ok", "awake through market hours" + (f"; last wake {et_str(last_wake, False)} ET" if last_wake else ""),
              {"last_wake": iso(last_wake)})


# ====================================================================== 7b. power, memory, VM mode
def parse_pmset_batt(text: str) -> dict:
    """`pmset -g batt` -> {source: 'ac'|'battery'|'ups'|None, percent, state, has_battery}."""
    m = re.search(r"Now drawing from '([^']+)'", text or "")
    src = None
    if m:
        s = m.group(1).lower()
        src = "battery" if "battery" in s else "ups" if "ups" in s else "ac"
    pm = re.search(r"(\d{1,3})%;\s*([^;]+);", text or "")
    return {"source": src, "percent": int(pm.group(1)) if pm else None,
            "state": pm.group(2).strip() if pm else None, "has_battery": bool(pm)}


def parse_clamshell(text: str) -> bool | None:
    """`ioreg -r -k AppleClamshellState -d 1` -> True when the lid is closed, None if unknown."""
    m = re.search(r'"AppleClamshellState"\s*=\s*(Yes|No)', text or "")
    return None if not m else m.group(1) == "Yes"


def parse_pmset_g(text: str) -> dict:
    """`pmset -g` (settings for the CURRENT power source) -> sleep minutes, what prevents it, SleepDisabled."""
    m = re.search(r"^\s*sleep\s+(\d+)(.*)$", text or "", re.M)
    return {"sleep_min": int(m.group(1)) if m else None, "prevented_by": m.group(2).strip() if m else "",
            "sleep_disabled": bool(re.search(r"^\s*SleepDisabled\s+1", text or "", re.M))}


def mac_power(p: Probe) -> dict:
    if "power" not in p.cache:
        run = lambda *a: subprocess.run(list(a), capture_output=True, text=True, timeout=15).stdout
        info = {**parse_pmset_g(run("/usr/bin/pmset", "-g")), **parse_pmset_batt(run("/usr/bin/pmset", "-g", "batt"))}
        try:
            info["lid_closed"] = parse_clamshell(run("/usr/sbin/ioreg", "-r", "-k", "AppleClamshellState", "-d", "1"))
        except Exception:
            info["lid_closed"] = None
        p.cache["power"] = info
    return p.cache["power"]


def market_window(p: Probe) -> bool:
    """09:20 to 16:25 ET on a session (the bots' jobs from the open entry to the 16:15 sync)."""
    s = p.cal.get(p.today) if p.cal else None
    return bool(s and s.open - dt.timedelta(minutes=10) <= p.now <= dt.datetime.combine(p.today, dt.time(16, 25), ET))


def judge_power(info: dict, in_window: bool) -> list[tuple[str, str, str]]:
    """[(check_name, status, message)] for the Mac's power source and lid. Pure, for tests.
    A closed lid or battery power only matter in the market window (09:20-16:25 ET)."""
    out = []
    src, pct, lid = info.get("source"), info.get("percent"), info.get("lid_closed")
    no_sleep = info.get("sleep_disabled") or info.get("sleep_min") == 0
    prevented = info.get("prevented_by") or ""
    held = "caffeinate" in prevented or "sleep prevented" in prevented
    when = "in market hours" if in_window else "(outside market hours)"
    if not info.get("has_battery") and src in (None, "ac"):
        out.append(("power_source", "ok", "on AC power (no battery)"))
    elif src == "battery":
        p_s = f" at {pct}%" if pct is not None else ""
        if not in_window:
            out.append(("power_source", "ok", f"on battery{p_s} {when}"))
        elif pct is not None and pct < 20:
            out.append(("power_source", "fail", f"on battery{p_s} {when}: it will die or sleep before the close; plug it in"))
        elif not no_sleep and not held:
            out.append(("power_source", "fail", f"on battery{p_s} {when} with system sleep after {info.get('sleep_min')} min and no "
                                                "sleep assertion: the Mac can sleep through the 15:40 cover; plug it in"))
        elif not no_sleep:
            out.append(("power_source", "warn", f"on battery{p_s} {when}: idle sleep is held off ({prevented[:60]}), but a closed lid "
                                                "or a flat battery still stops every job; plug it in"))
        else:
            out.append(("power_source", "warn", f"on battery{p_s} {when} (sleep disabled, but plug it in)"))
    else:
        out.append(("power_source", "ok", f"on {'UPS' if src == 'ups' else 'AC'} power" + (f", battery {pct}%" if pct is not None else "")))
    if lid is None:
        out.append(("lid", "ok", "no lid / state unknown"))
    elif not lid:
        out.append(("lid", "ok", "lid open"))
    elif not in_window:
        out.append(("lid", "ok", f"lid closed {when}"))
    elif src == "battery" and not info.get("sleep_disabled"):
        out.append(("lid", "fail", f"lid closed on battery {when}: Clamshell Sleep will stop every job; open it, or plug in with disablesleep"))
    elif info.get("sleep_disabled"):
        out.append(("lid", "ok", f"lid closed {when}, sleep disabled (pmset disablesleep 1)"))
    else:
        out.append(("lid", "warn", f"lid closed on AC {when}: it stays awake only with an external display or `sudo pmset -a disablesleep 1`"
                                   + ("" if held else "; no caffeinate assertion seen")))
    return out


def parse_meminfo(text: str) -> dict:
    kb = {m.group(1): int(m.group(2)) for m in re.finditer(r"^(\w+):\s+(\d+) kB", text or "", re.M)}
    mb = lambda k: round(kb.get(k, 0) / 1024)
    return {"total_mb": mb("MemTotal"), "avail_mb": mb("MemAvailable"), "swap_total_mb": mb("SwapTotal"),
            "swap_free_mb": mb("SwapFree")}


def judge_memory(m: dict) -> list[tuple[str, str, str]]:
    """[(check_name, status, message)] for a Linux host. Thresholds suit a 1 GB droplet running
    up to four Python jobs at 15:40-15:55 ET (each ~100-250 MB with pandas)."""
    out = []
    avail, total = m["avail_mb"], m["total_mb"]
    st = "fail" if avail < 100 else "warn" if avail < 200 else "ok"
    out.append(("memory", st, f"{avail} MB of {total} MB available"))
    used = m["swap_total_mb"] - m["swap_free_mb"]
    if m["swap_total_mb"] == 0:
        out.append(("swap", "warn" if total < 2000 else "ok",
                    "no swap" + (" on a host under 2 GB: run deploy/bootstrap.sh (adds a 2 GB swapfile)" if total < 2000 else "")))
    else:
        pct = 100 * used / m["swap_total_mb"]
        out.append(("swap", "fail" if pct > 90 else "warn" if pct > 60 else "ok", f"swap {used} of {m['swap_total_mb']} MB used ({pct:.0f}%)"))
    return out


MODE_FILE = Path(os.environ.get("CLIVE_MODE_FILE", "/etc/clive/mode"))
MIGRATED_FILE = Path("/var/lib/clive/state-migrated")


def vm_mode() -> str | None:
    """The deploy kit's run mode (off / shadow / live), or None on a host without the kit (the Mac)."""
    try:
        return MODE_FILE.read_text().strip() or "off"
    except OSError:
        return None


def host_linux(p: Probe):
    try:
        m = parse_meminfo(Path("/proc/meminfo").read_text())
        for name, st, msg in judge_memory(m):
            p.add("host", name, st, msg, m)
    except OSError as e:
        p.add("host", "memory", "unknown", f"/proc/meminfo unreadable: {e}")
    mode = vm_mode()
    if mode is not None:
        migrated = MIGRATED_FILE.exists()
        st = "warn" if mode == "live" and not migrated else "ok"
        p.add("host", "vm_mode", st, f"VM mode {mode}" + ("; live without the state-migrated marker" if st == "warn" else "")
              + {"live": " (this VM trades)", "shadow": " (dry runs; the Mac trades)", "off": " (every job is a no-op)"}.get(mode, ""),
              {"mode": mode, "state_migrated": migrated})
    if shutil.which("systemctl"):
        try:
            r = subprocess.run(["systemctl", "list-units", "clive-*", "--state=failed", "--no-legend", "--plain"],
                               capture_output=True, text=True, timeout=10).stdout
            failed = [l.split()[0] for l in r.splitlines() if l.strip()]
            p.add("host", "failed_units", "warn" if failed else "ok",
                  f"failed units: {', '.join(failed)}" if failed else "no failed clive-* units", {"failed": failed})
        except Exception as e:
            p.add("host", "failed_units", "unknown", f"systemctl unavailable: {scrub(e)[:100]}")


# ====================================================================== 8. pre-close readiness
# The 15:30 ET run (12:30 on half days) asks: would anything stop the 15:40 close/cover and the
# 15:45 options decision? A fail pushes an urgent ntfy, leaving 10 minutes to react.
def readiness_due(p: Probe) -> bool:
    s = p.cal.get(p.today) if p.cal else None
    return bool(s and s.at(s.close_min - 35) <= p.now < s.at(s.close_min - 19))


def trading_role() -> dict:
    """Is THIS host the one that trades? vm-live and mac-cron trade; vm-shadow, vm-off and a Mac
    whose bot lines are parked (#CLIVE-ON-VM#, deploy/mac_cron.sh disable) do not."""
    mode = vm_mode()
    if mode is not None:
        return {"role": f"vm-{mode}", "trades": mode == "live"}
    ct = crontab_text()
    if ct is None:
        return {"role": "none", "trades": False}
    active = [l for l in ct.splitlines() if l.strip() and not l.lstrip().startswith("#") and re.search(r"daily\.py[^\n]*--execute", l)]
    if active:
        return {"role": "mac-cron", "trades": True}
    if "#CLIVE-ON-VM#" in ct:
        return {"role": "mac-parked", "trades": False}
    return {"role": "none", "trades": False}


def _lock_info(path: Path) -> dict | None:
    """A pid lock file (close.lock) -> {pid, alive, age_min}; None when absent."""
    try:
        raw = path.read_text().strip()
        age = (time.time() - path.stat().st_mtime) / 60
    except OSError:
        return None
    pid = int(raw) if raw.isdigit() else None
    alive = False
    if pid:
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
    return {"pid": pid, "alive": alive, "age_min": round(age, 1)}


def _flock_held(path: Path) -> bool | None:
    """The options bot's live/state/.lock is an flock: try a shared non-blocking lock (harmless)."""
    try:
        import fcntl
    except ImportError:
        return None
    try:
        with open(path, "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
    except OSError:
        return None


def check_readiness(p: Probe):
    sess = p.cal.get(p.today)
    close_at = sess.at(sess.close_min - 20) if sess else None
    hhmm = f"{close_at:%H:%M}" if close_at else "15:40"
    role = trading_role()
    rows: list[tuple] = []

    def add(n, st, msg, d=None):
        rows.append((n, st, msg, d))

    if not role["trades"]:
        # Not the trading host: make sure SOME other host is alive instead.
        others = SB.try_get("health_latest", "select=host,run_at,status&component=eq.health&check_name=eq.alive"
                                             f"&host=neq.{requests.utils.quote(p.host)}") if SB.ok else None
        fresh = [r for r in (others or []) if parse_ts(r.get("run_at")) and (p.now - parse_ts(r["run_at"])).total_seconds() < 35 * 60]
        if others is None:
            add("readiness.trading_host", "unknown", f"this host is {role['role']} (does not trade); the other hosts' heartbeats are unreadable",
                {"role": role["role"]})
        elif fresh:
            add("readiness.trading_host", "ok", f"this host is {role['role']} (does not trade); "
                f"{', '.join(sorted({r['host'] for r in fresh}))} reported in the last 35 min", {"role": role["role"], "others": [r["host"] for r in fresh]})
        else:
            add("readiness.trading_host", "fail", f"this host is {role['role']} (does not trade) and no other host has reported for 35 min: "
                f"nothing may run the {hhmm} cover. Re-enable the Mac cron (deploy/mac_cron.sh enable) or set the VM live.", {"role": role["role"]})
    else:
        # 1. the host stays awake -------------------------------------------------------
        if platform.system() == "Darwin":
            info = mac_power(p)
            bad = [(n, st, m) for n, st, m in judge_power(info, True) if st != "ok"]
            if not info.get("sleep_disabled") and info.get("sleep_min") not in (0, None) and "caffeinate" not in (info.get("prevented_by") or ""):
                bad.append(("sleep", "warn", f"system sleep after {info.get('sleep_min')} min and no caffeinate assertion"))
            add("readiness.host_awake", worst(b[1] for b in bad) if bad else "ok", "; ".join(b[2] for b in bad) if bad else
                f"on {info.get('source') or 'AC'} power, lid {'closed' if info.get('lid_closed') else 'open'}, sleep held off", info)
        else:
            mode = vm_mode()
            add("readiness.host_awake", "ok" if mode == "live" else "fail",
                f"VM mode {mode}" + (", always on" if mode == "live" else ": jobs will not trade"), {"mode": mode})
        # 2. guards: one already dated today makes the job refuse -------------------------
        today = p.today.isoformat()
        g = _load_json(SHORT / "logs" / "last_execute.json") or {}
        og = _load_json(OPT / "live" / "state" / "last_execute.json") or {}
        probs = []
        if g.get("date") == today:
            probs.append(f"logs/last_execute.json is already dated {today}: the {hhmm} close job will refuse ('already executed today')")
        if og.get("daily") == today:
            probs.append(f"Options live/state/last_execute.json daily={today}: the options decision will be skipped")
        add("readiness.guards", "fail" if probs else "ok", "; ".join(probs) or "close and options guards not set yet today",
            {"short": g.get("date"), "options": og.get("daily")})
        # 3. locks ------------------------------------------------------------------------
        cl = _lock_info(SHORT / "logs" / "close.lock")
        olock = OPT / "live" / "state" / ".lock"
        held = _flock_held(olock) if olock.exists() else False
        try:
            omt = (time.time() - olock.stat().st_mtime) / 60
        except OSError:
            omt = None
        st, msgs = "ok", []
        if cl and cl["alive"]:
            st = "fail" if cl["age_min"] > 20 else "warn"
            msgs.append(f"close.lock held by pid {cl['pid']} for {cl['age_min']:.0f} min" + (" (stuck? the close job will find it busy)" if st == "fail" else ""))
        elif cl:
            msgs.append(f"stale close.lock (dead pid {cl['pid']}); the bot clears it")
        if held and omt is not None and omt > 25:
            st = "fail"
            msgs.append(f"the options state lock has been held for {omt:.0f} min (a stuck live/run.py?)")
        elif held:
            msgs.append("options lock held (an intraday run in progress)")
        add("readiness.locks", st, "; ".join(msgs) or "no locks held", {"close_lock": cl, "options_lock_held": held})
        # 4. keys -------------------------------------------------------------------------
        miss = [k for k in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY") if not SHORT_ENV.get(k)]
        miss += [k for k in ("OPT_APCA_API_KEY_ID", "OPT_APCA_API_SECRET_KEY") if not OPT_ENV.get(k)]
        soft = [k for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY") if not SHORT_ENV.get(k)]
        add("readiness.keys", "fail" if miss else "warn" if soft else "ok",
            ("missing: " + ", ".join(miss + soft)) if miss or soft else "Alpaca keys for both bots and Supabase present", {"missing": miss + soft})
        # 5. schedule ---------------------------------------------------------------------
        if role["role"] == "mac-cron":
            lines = [l for l in (crontab_text() or "").splitlines() if l.strip() and not l.lstrip().startswith("#")]
            need = {"shorting.close": r"daily\.py\s+--execute\s*(>|$)", "options.daily": r"live/run\.py\s+--execute\s*(>|$)",
                    "shorting.fallback": r"daily\.py\s+(--execute\s+--fallback|--fallback\s+--execute)"}
            gone = [n for n, pat in need.items() if not any(re.search(pat, l) for l in lines)]
            add("readiness.schedule", "fail" if set(gone) - {"shorting.fallback"} else "warn" if gone else "ok",
                ("cron lines missing: " + ", ".join(gone)) if gone else "15:40 close, 15:50 fallback and 15:45 options cron lines active", {"missing": gone})
        else:
            timers = ["clive-short-close.timer", "clive-short-fallback.timer", "clive-opt-daily.timer"]
            try:
                r = subprocess.run(["systemctl", "is-active", *timers], capture_output=True, text=True, timeout=10).stdout.split()
            except Exception:
                r = []
            gone = [t for t, s in zip(timers, r + ["unknown"] * 3) if s != "active"]
            add("readiness.schedule", "fail" if gone else "ok", ("timers not active: " + ", ".join(gone)) if gone else
                "close, fallback and options timers active", {"inactive": gone})
        # 6. network + Alpaca -------------------------------------------------------------
        dns_bad = []
        for h in ("paper-api.alpaca.markets", "data.alpaca.markets", "scanner.tradingview.com"):
            try:
                socket.getaddrinfo(h, 443)
            except OSError:
                dns_bad.append(h)
        add("readiness.network", "fail" if dns_bad else "ok", ("DNS fails for " + ", ".join(dns_bad)) if dns_bad else
            "DNS resolves Alpaca and TradingView", {"dns_fail": dns_bad})
        probs, d = [], {}
        try:
            clk = with_retry(lambda: SHORT_API.get("/v2/clock"), tries=2)
            d["is_open"], d["next_close"] = clk.get("is_open"), clk.get("next_close")
            if not clk.get("is_open"):
                probs.append("Alpaca says the market is closed")
            a = p.short_account()
            if a.get("status") != "ACTIVE" or a.get("trading_blocked") or not a.get("shorting_enabled"):
                probs.append(f"shorting account {a.get('status')}, blocked={a.get('trading_blocked')}, shorting={a.get('shorting_enabled')}")
        except Exception as e:
            probs.append(f"shorting Alpaca unreachable: {scrub(e)[:120]}")
        try:
            o = p.opt_account()
            if o.get("status") != "ACTIVE" or o.get("trading_blocked"):
                probs.append(f"options account {o.get('status')}, blocked={o.get('trading_blocked')}")
        except Exception as e:
            probs.append(f"options Alpaca unreachable: {scrub(e)[:120]}")
        add("readiness.alpaca", "fail" if probs else "ok", "; ".join(probs) or "both Alpaca accounts ACTIVE, market open", d)
        # 7. resources --------------------------------------------------------------------
        du = shutil.disk_usage(ROOT)
        res, st = [f"{du.free / 1e9:.1f} GB disk free"], ("fail" if du.free < 1e9 else "ok")
        if platform.system() == "Linux":
            try:
                m = parse_meminfo(Path("/proc/meminfo").read_text())
                res.append(f"{m['avail_mb']} MB RAM + {m['swap_free_mb']} MB swap free")
                st = worst([st, "fail" if m["avail_mb"] + m["swap_free_mb"] < 300 else "warn" if m["avail_mb"] < 200 else "ok"])
            except OSError:
                pass
        add("readiness.resources", st, ", ".join(res))
    bad = [r for r in rows if r[1] == "fail"]
    for n, st, msg, d in rows:
        p.add("readiness", n, st, msg, d, expected_at=close_at)
    p.add("readiness", "readiness.summary", worst(r[1] for r in rows),
          (f"NOT READY for the {hhmm} close: " + "; ".join(r[2] for r in bad)) if bad else
          f"ready for the {hhmm} close and the options decision ({role['role']})", {"role": role["role"], "fails": len(bad)}, expected_at=close_at)


# ====================================================================== 9. event-alert auto-resolution
# The bots raise one-off EVENT alerts (cover_failed, run_failed, ...) that nothing resolves, so the
# dashboard would keep them open forever. The probe resolves one once the evidence says the problem
# is over. Idempotent, and safe next to the bots' own resolution: every PATCH is filtered on
# resolved_at=is.null, so an alert already resolved (by a bot or an earlier probe) is never touched.
BOT_OWNED_KINDS = {"position_loss", "book_loss", "long_loss", "long_outside_window", "drawdown_halt"}   # deduped; monitor.py resolves
SHORTS_CLEARED_KINDS = {"cover_failed", "close_missed", "close_fallback", "overnight_leftover", "leftover_covered",
                        "cover_blocked", "pdt_block", "vm_shorts_open"}
LONGS_CLEARED_KINDS = {"long_exit_failed", "long_exit_late"}
NEXT_SESSION_KINDS = {"run_failed", "scan_failed", "no_picks", "stale_picks", "calendar_failed", "job_locked", "stop_failed",
                      "long_entry_failed", "daily_loss_halt", "pdt_limit", "vm_job_failed", "vm_missed_run", "vm_timer_inactive",
                      "vm_healthcheck", "vm_heartbeat"}
STALE_AFTER_SESSIONS = 3


def plan_alert_resolution(alerts: list[dict], results: list[dict], cal: Calendar, now: dt.datetime) -> list[tuple[dict, str]]:
    """[(alert, reason)] to resolve. Pure: open alerts rows, this run's check rows, the calendar.
      shorts kinds   -> resolved when no short is left open (positions/shorting.leftover_shorts ok)
      longs kinds    -> resolved when no sleeve long is outside its window
      one-day kinds  -> resolved once a LATER session has run 30 min with no scheduler fail for that bot
      other kinds    -> resolved as stale after 3 later sessions; deduped condition kinds: left to the bot
    Nothing younger than 10 minutes is touched."""
    st = {f"{r['component']}/{r['check_name']}": r["status"] for r in results}
    shorts_ok = st.get("positions/shorting.leftover_shorts") == "ok"
    longs_ok = st.get("positions/shorting.longs_window") == "ok" and st.get("state/shorting.overnight_longs") in ("ok", None)
    sched_bad = {b: any(k.startswith(f"scheduler/{b}.") and v == "fail" for k, v in st.items()) for b in ("shorting", "options")}
    started = [s for s in cal.sessions if s.open + dt.timedelta(minutes=30) <= now]
    out = []
    for a in alerts:
        kind, bot = a.get("kind") or "", a.get("bot_id") or "shorting"
        at = parse_ts(a.get("at"))
        if not at or bot == "health" or kind in BOT_OWNED_KINDS or (now - at).total_seconds() < 600:
            continue
        later = [s for s in started if s.date > at.astimezone(ET).date()]
        if kind in SHORTS_CLEARED_KINDS:
            if shorts_ok:
                out.append((a, "no shorts left open"))
        elif kind in LONGS_CLEARED_KINDS:
            if longs_ok:
                out.append((a, "no sleeve longs outside the window"))
        elif kind in NEXT_SESSION_KINDS:
            if later and not sched_bad.get(bot, sched_bad["shorting"]):
                out.append((a, f"the {later[0].date} session ran without a scheduler failure"))
        elif len(later) >= STALE_AFTER_SESSIONS:
            out.append((a, f"stale: {len(later)} sessions old"))
    return out


def check_alert_resolution(p: Probe):
    if not SB.ok:
        p.add("alerts", "alerts.auto_resolve", "unknown", "Supabase not configured")
        return
    rows = SB.try_get("alerts", "select=id,bot_id,kind,level,at&resolved_at=is.null&or=(bot_id.is.null,bot_id.neq.health)"
                                "&order=at.desc&limit=500")
    if rows is None:
        p.add("alerts", "alerts.auto_resolve", "unknown", "open alerts unreadable (transient Supabase error)")
        return
    plan = plan_alert_resolution(rows, p.results, p.cal, p.now)
    done, failed = [], None
    if p.write and plan:
        stamp = iso(dt.datetime.now(UTC))
        for reason in sorted({r for _, r in plan}):
            ids = [str(a["id"]) for a, r in plan if r == reason]
            try:
                SB.patch("alerts", f"id=in.({','.join(ids)})&resolved_at=is.null", {"resolved_at": stamp})
                done += ids
            except Exception as e:
                failed = scrub(e)[:160]
    kinds = sorted({a.get("kind") or "?" for a, _ in plan})
    n = len(done) if p.write else len(plan)
    verb = "resolved" if p.write else "would resolve"
    detail = {"resolved": [{"id": a["id"], "kind": a.get("kind"), "reason": r} for a, r in plan][:50], "open_before": len(rows)}
    p.add("alerts", "alerts.auto_resolve", "warn" if failed else "ok",
          (f"{verb} {n} event alerts ({', '.join(kinds)}); {len(rows) - n} bot alerts still open" if plan else
           f"{len(rows)} open bot alerts, none cleared yet") + (f"; resolve failed: {failed}" if failed else ""), detail)


# ====================================================================== summary, write, alert
def link_findings(p: Probe):
    """Say the consequence in the cause's message: a missed cover run plus open shorts."""
    get = lambda c, n: next((r for r in p.results if r["component"] == c and r["check_name"] == n), None)
    close, left = get("scheduler", "shorting.close"), get("positions", "shorting.leftover_shorts")
    if close and left and close["status"] == "fail" and left["status"] == "fail":
        n = (left.get("detail") or {}).get("count")
        if n:
            close["message"] += f", {n} shorts held overnight"
            close["detail"] = {**(close.get("detail") or {}), "leftover_shorts": n}


def summarize(p: Probe, started: float):
    extra = [c for c in dict.fromkeys(r["component"] for r in p.results) if c not in COMPONENTS and c not in ("summary", "health")]
    for comp in COMPONENTS + extra:
        rows = [r for r in p.results if r["component"] == comp]
        st = worst(r["status"] for r in rows) if rows else "unknown"
        nf, nw = sum(r["status"] == "fail" for r in rows), sum(r["status"] == "warn" for r in rows)
        p.add("summary", comp, st, f"{nf} fail, {nw} warn of {len(rows)} checks", {"fail": nf, "warn": nw, "checks": len(rows)})
    n = len([r for r in p.results if r["component"] != "summary"])
    counts = {k: sum(r["status"] == k for r in p.results if r["component"] not in ("summary",)) for k in RANK}
    p.add("health", "alive", "ok", f"probe ran on {p.host} in {time.monotonic() - started:.1f} s, {n} checks",
          {"version": VERSION, "duration_s": round(time.monotonic() - started, 1), "counts": counts, "python": platform.python_version(),
           "platform": platform.platform(), "now": iso(p.now)})


def alert_key(r: dict, host: str) -> str:
    local = r["component"] in ("host", "state", "readiness")
    return f"health_{r['component']}_{r['check_name']}".replace(".", "_") + (f"@{host}" if local else "")


def previous_statuses(host: str) -> dict:
    if SB.ok:
        rows = SB.try_get("health_latest", f"select=component,check_name,status&host=eq.{requests.utils.quote(host)}")
        if rows is not None:
            return {f"{r['component']}/{r['check_name']}": r["status"] for r in rows}
    st = _load_json(STATE_FILE) or {}
    return st.get(host, {})


def push_ntfy(title: str, message: str, level: str):
    topic = env_get("NTFY_TOPIC", SHORT_ENV)
    if not topic:
        return False
    server = (env_get("NTFY_SERVER", SHORT_ENV) or "https://ntfy.sh").rstrip("/")
    try:
        requests.post(f"{server}/{topic}", data=message.encode(), timeout=10,
                      headers={"Title": title, "Priority": "urgent" if level == "critical" else "default", "Tags": "rotating_light"})
        return True
    except Exception:
        return False


def write_and_alert(p: Probe, run_at: dt.datetime) -> list[str]:
    notes = []
    prev = previous_statuses(p.host)
    rows = [{**r, "run_at": iso(run_at), "host": p.host} for r in p.results]
    if SB.ok:
        try:
            SB.insert("health_checks", rows)
            notes.append(f"wrote {len(rows)} health_checks rows")
        except Exception as e:
            hint = ": apply dashboard/supabase/migrations/008_health.sql" if schema_missing(e) else " (transient; next run retries)"
            notes.append(f"health_checks insert failed ({scrub(e)[:160]}){hint}")
    # local state: the fallback "previous run" when Supabase is unreachable
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        st = _load_json(STATE_FILE) or {}
        st[p.host] = {f"{r['component']}/{r['check_name']}": r["status"] for r in p.results}
        STATE_FILE.write_text(json.dumps(st, indent=1))
    except OSError:
        pass
    # alerts -------------------------------------------------------------------------------
    candidates = [r for r in p.results if r["component"] not in ("summary", "health") and r["check_name"] != "timeline"]
    open_alerts = {}
    if SB.ok:
        for a in SB.try_get("alerts", "select=id,kind&bot_id=eq.health&resolved_at=is.null&limit=500", []) or []:
            open_alerts.setdefault(a["kind"], []).append(a["id"])
    new_fails = []
    for r in candidates:
        key = alert_key(r, p.host)
        was = prev.get(f"{r['component']}/{r['check_name']}")
        if r["status"] == "fail":
            if was == "fail" or key in open_alerts:
                continue
            new_fails.append(r)
            if SB.ok:
                try:
                    SB.insert("alerts", [{"bot_id": "health", "level": "critical", "kind": key, "ticker": None,
                                          "message": f"[{p.host}] {r['message']}"[:900]}])
                except Exception as e:
                    notes.append(f"alert insert failed: {scrub(e)[:120]}")
        elif r["status"] in ("ok", "warn") and key in open_alerts:
            try:
                SB.patch("alerts", f"id=in.({','.join(str(i) for i in open_alerts[key])})", {"resolved_at": iso(dt.datetime.now(UTC))})
                notes.append(f"resolved alert {key}")
            except Exception as e:
                notes.append(f"alert resolve failed: {scrub(e)[:120]}")
    # readiness: its alerts rows go through the loop above; its push is separate, loud, once per host per day
    ready = next((r for r in p.results if r["check_name"] == "readiness.summary"), None)
    if ready is None:
        stale = [k for k in open_alerts if k.startswith("health_readiness_") and k.endswith(f"@{p.host}")]
        for k in stale:            # the window is over: these alerts have done their job
            try:
                SB.patch("alerts", f"id=in.({','.join(str(i) for i in open_alerts[k])})&resolved_at=is.null", {"resolved_at": iso(dt.datetime.now(UTC))})
                notes.append(f"resolved alert {k}")
            except Exception as e:
                notes.append(f"alert resolve failed: {scrub(e)[:120]}")
    elif ready["status"] == "fail":
        st = _load_json(STATE_FILE) or {}
        sent = st.get("_readiness_pushed", {})
        if sent.get(p.host) != p.today.isoformat():
            bad = [r for r in p.results if r["component"] == "readiness" and r["status"] == "fail" and r is not ready]
            body = "\n".join(f"- {r['message']}" for r in bad[:8]) + f"\nFix it before the close job runs. {DASHBOARD_URL}/health"
            if push_ntfy(f"Clive NOT READY on {p.host}: the close job is at risk", body, "critical"):
                notes.append("readiness ntfy push sent")
            sent[p.host] = p.today.isoformat()
            st["_readiness_pushed"] = sent
            try:
                STATE_FILE.write_text(json.dumps(st, indent=1))
            except OSError:
                pass
    new_fails = [r for r in new_fails if r["component"] != "readiness"]
    if new_fails:
        body = "\n".join(f"- {r['message']}" for r in new_fails[:8]) + (f"\n(+{len(new_fails) - 8} more)" if len(new_fails) > 8 else "")
        if push_ntfy(f"Clive health: {len(new_fails)} new FAIL on {p.host}", body + f"\n{DASHBOARD_URL}/health", "critical"):
            notes.append("ntfy push sent")
        notes.append(f"{len(new_fails)} new failures alerted")
    return notes


def print_table(p: Probe):
    w = shutil.get_terminal_size((160, 40)).columns
    mw = max(40, w - 52)
    print(f"\nCLIVE HEALTH  host={p.host}  now={et_str(p.now)}  calendar={p.cal.source if p.cal else '-'}")
    print(f"{'COMPONENT':<10} {'CHECK':<26} {'STATUS':<7} MESSAGE")
    print("-" * min(w, 200))
    for r in p.results:
        if r["component"] == "summary":
            continue
        msg = r["message"]
        print(f"{r['component']:<10} {r['check_name']:<26} {r['status'].upper():<7} {msg if len(msg) <= mw else msg[:mw - 1] + '…'}")
    print("-" * min(w, 200))
    for r in p.results:
        if r["component"] == "summary":
            print(f"summary    {r['check_name']:<26} {r['status'].upper():<7} {r['message']}")
    tl = next((r for r in p.results if r["check_name"] == "timeline"), None)
    if tl and tl.get("detail"):
        bad = [s for s in tl["detail"]["slots"] if s["status"] not in ("OK", "PENDING")]
        print(f"\nSCHEDULE  sessions {', '.join(tl['detail']['sessions'])}  ({len(tl['detail']['slots'])} slots, {len(bad)} not OK)")
        for s in bad:
            print(f"  {s['date']} {s['label']:<24} {s['bot']:<9} {s['status']:<7} obs {et_str(parse_ts(s['observed_at']), False) if s['observed_at'] else '-':<6} {s['note'][:90]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Clive read-only health probe")
    ap.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    ap.add_argument("--host", default=None, help="host tag for the rows (default: $HEALTH_HOST or the hostname)")
    ap.add_argument("--now", default=None, help="judge the schedule as of this ET time, e.g. 2026-10-22T16:30")
    ap.add_argument("--json", action="store_true", help="with --dry-run: print the rows as JSON")
    ap.add_argument("--only", default=None, help="comma list of components to run (testing)")
    ap.add_argument("--readiness", action="store_true",
                    help="run the pre-close readiness checks now (they run by themselves at 15:30 ET, 12:30 on half days)")
    a = ap.parse_args(argv)
    started = time.monotonic()
    host = a.host or os.environ.get("HEALTH_HOST") or os.environ.get("CLIVE_HOST") or socket.gethostname()
    host = host.removesuffix(".local")
    now = dt.datetime.now(ET)
    if a.now:
        now = dt.datetime.fromisoformat(a.now)
        now = now.replace(tzinfo=ET) if now.tzinfo is None else now.astimezone(ET)
    p = Probe(now, host)
    p.write = not a.dry_run
    try:
        p.cal = Calendar(p.today)
    except Exception as e:                       # cannot happen (fallback inside), but never crash
        p.add("scheduler", "calendar", "fail", f"calendar: {scrub(e)}")
        return 0
    only = set(a.only.split(",")) if a.only else None
    plan = [("scheduler", "timeline", check_scheduler), ("positions", "shorting", check_positions_shorting),
            ("positions", "options", check_positions_options), ("accounts", "accounts", check_accounts),
            ("data", "data", check_data), ("state", "state", check_state), ("supabase", "supabase", check_supabase),
            ("host", "host", check_host)]
    if a.readiness or readiness_due(p):
        plan.append(("readiness", "readiness.summary", check_readiness))
    plan.append(("alerts", "alerts.auto_resolve", check_alert_resolution))     # last: it reads the rows above
    for comp, name, fn in plan:
        if only and comp not in only:
            continue
        p.guard(comp, name, fn, p)
    link_findings(p)
    summarize(p, started)
    if a.dry_run:
        if a.json:
            print(json.dumps(p.results, indent=1, default=str))
        else:
            print_table(p)
            print("\n--dry-run: nothing written")
        return 0
    try:
        notes = write_and_alert(p, dt.datetime.now(UTC))
    except Exception as e:
        notes = [f"write failed: {scrub(e)[:200]}"]
    fails = [r for r in p.results if r["status"] == "fail" and r["component"] != "summary"]
    print(f"{dt.datetime.now(ET):%Y-%m-%d %H:%M:%S} health {host}: {len(p.results)} rows, {len(fails)} fail"
          + (": " + " | ".join(r["message"][:80] for r in fails[:4]) if fails else "") + ("; " + "; ".join(notes) if notes else ""))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:                       # the probe must never crash its cron line
        print(f"health: unexpected error: {scrub(e)}")
        sys.exit(0)
