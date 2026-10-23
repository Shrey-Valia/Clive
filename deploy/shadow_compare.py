#!/usr/bin/env python3
"""shadow_compare.py -- did the VM's shadow day decide what the Mac really did?

Compares one ET trading day of the VM in shadow mode (dry runs, no --execute) with the Mac's
real run, from the bots' own log files only. Read-only: it opens log and JSON files and
nothing else. Standard library only, so it runs on the Mac's python3 and in the VM venv.

    deploy/shadow_compare.sh [DATE]          on the Mac: pulls the VM's logs, then runs this
    python3 deploy/shadow_compare.py --mac ROOT --vm ROOT [--date YYYY-MM-DD] [--json]

ROOT is a folder laid out like the repo: "Stock Shorting Bot/logs/", "Options Bot/live/logs/",
"events/logs/", "health/logs/". --mac defaults to this repo.

What it compares for DATE (each row is MATCH, DIFF-tolerated, MISMATCH, MISSING,
NOT-COMPARABLE or N/A; any MISMATCH or MISSING = FAIL, exit 1; --strict also fails on
NOT-COMPARABLE):
  jobs     every scheduled job ran on both sides; the VM's run-job.sh exit codes are 0
  scan     the 15:45 scan: TradingView universe, picks for the next open (names, order, size),
           the picks JSON (when both were saved for DATE)
  open     the 09:31 entry: picks read, planned shorts and skips, leftover covers, sleeve sells
  close    the 15:55 cover-all (what was short) and the long-sleeve buys
  options  the 15:45 decision (kill switch, open-spread actions, entry per symbol with the
           chosen legs, condor sleeve) and the 09:45 sweep / intraday exit decisions
  errors   ERROR lines that only the VM logged (informational)

Live data differs a little between two machines a few seconds apart, so some differences are
tolerated (see the --min-overlap / --qty-tol / --strike-tol flags), and the races that shadow
mode creates on purpose (the VM reads the same Alpaca accounts while the Mac trades) are
recognised and marked DIFF-tolerated.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import re
import sys

MATCH, TOL, MISMATCH, MISSING, NC, NA = "MATCH", "DIFF-tolerated", "MISMATCH", "MISSING", "NOT-COMPARABLE", "N/A"
FAILING = {MISMATCH, MISSING}
RANK = {NA: 0, MATCH: 1, NC: 2, TOL: 3, MISSING: 4, MISMATCH: 5}

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$")
MARK_START_RE = re.compile(r"^=== clive-job (\S+) \| mode=(\w+) \| (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")
MARK_END_RE = re.compile(r"^=== clive-job (\S+) (?:exit (-?\d+)|TIMED OUT after \S+ \(rc=(\d+)\)) \| "
                         r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")


# ---------------------------------------------------------------------------------- reading
class Line:
    __slots__ = ("ts", "level", "msg")

    def __init__(self, ts: dt.datetime, level: str, msg: str):
        self.ts, self.level, self.msg = ts, level, msg

    def __repr__(self):
        return f"Line({self.ts:%H:%M:%S} {self.level} {self.msg!r})"


def _ts(day: str, hms: str) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{day}T{hms}")


def read_lines(path: str | None, day: str) -> list[Line]:
    """The timestamped log lines of `path` dated `day` (tracebacks and other bare lines are dropped)."""
    out: list[Line] = []
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            m = LINE_RE.match(raw.rstrip("\r\n"))
            if m and m.group(1) == day:
                out.append(Line(_ts(m.group(1), m.group(2)), m.group(3), m.group(4)))
    return out


def read_markers(paths: list[str], day: str) -> dict[str, list[dict]]:
    """run-job.sh's start/end markers: {job: [{"start": ts, "mode": m, "rc": int|None}]}."""
    jobs: dict[str, list[dict]] = {}
    for p in paths:
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                s = raw.rstrip("\r\n")
                m = MARK_START_RE.match(s)
                if m and m.group(3) == day:
                    jobs.setdefault(m.group(1), []).append(
                        {"start": _ts(m.group(3), m.group(4)), "mode": m.group(2), "rc": None})
                    continue
                m = MARK_END_RE.match(s)
                if m and m.group(4) == day:
                    rc = int(m.group(2) if m.group(2) is not None else m.group(3))
                    runs = jobs.get(m.group(1)) or []
                    for r in reversed(runs):
                        if r["rc"] is None:
                            r["rc"] = rc
                            break
    return jobs


def _first_file(*paths: str) -> str | None:
    for p in paths:
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
    return None


def _load_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


class Side:
    """One machine's logs for one day. kind 'mac' = real (--execute) run, 'vm' = shadow dry run."""

    def __init__(self, root: str, day: str, kind: str):
        self.root, self.day, self.kind = root, day, kind
        sb = os.path.join(root, "Stock Shorting Bot", "logs")
        ob = os.path.join(root, "Options Bot", "live", "logs")
        self.short_file = _first_file(os.path.join(sb, "shortingbot.log"), os.path.join(sb, "cron.log"))
        self.opt_file = _first_file(os.path.join(ob, f"{day}.log"), os.path.join(ob, "cron.log"))
        self.short = read_lines(self.short_file, day)
        self.opt = read_lines(self.opt_file, day)
        self.opt_cron = read_lines(os.path.join(ob, "cron.log"), day)
        self.markers = read_markers([os.path.join(sb, "cron.log"), os.path.join(ob, "cron.log"),
                                     os.path.join(root, "events", "logs", "sync.log"),
                                     os.path.join(root, "health", "logs", "check.log")], day)
        picks = "next_open_picks.json" if kind == "mac" else "next_open_picks.dry_run.json"
        self.picks_json = _load_json(os.path.join(sb, picks))

    @property
    def empty(self) -> bool:
        return not self.short and not self.opt and not self.markers


# ---------------------------------------------------------------------------------- helpers
def at(day: str, hm: str) -> dt.datetime:
    return dt.datetime.fromisoformat(f"{day}T{hm}:00" if len(hm) == 5 else f"{day}T{hm}")


def window(lines: list[Line], lo: dt.datetime, hi: dt.datetime) -> list[Line]:
    return [x for x in lines if lo <= x.ts <= hi]


def parse_dict(s: str) -> dict | None:
    s = s.strip()
    if s in ("{}", ""):
        return {}
    try:
        d = ast.literal_eval(s)
        return {str(k): float(v) for k, v in d.items()} if isinstance(d, dict) else None
    except (ValueError, SyntaxError, TypeError, AttributeError):
        return None


def rel_diff(a: float, b: float) -> float:
    a, b = abs(a), abs(b)
    return 0.0 if a == b else abs(a - b) / max(a, b)


def worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: RANK[s]) if statuses else NA


def fmt_list(xs) -> str:
    return ", ".join(xs) if xs else "none"


def fmt_dict(d: dict | None) -> str:
    if d is None:
        return "-"
    return "{}" if not d else " ".join(f"{k} {abs(v):g}" for k, v in sorted(d.items()))


def reason_kind(reason: str) -> str:
    """A coarse category for a skip / exclusion reason, so wording or numbers don't count."""
    r = (reason or "").lower()
    for key in ("not shortable", "hard to borrow", "no price", "below", "gapped up", "already held",
                "overnight long", "gross cap", "too small", "pdt", "earnings", "industry cap",
                "still short", "already long", "entered as a short", "news", "no quote", "halted",
                "score", "adv", "liquidity", "data error"):
        if key in r:
            return key
    return re.sub(r"[\d.$%()\-+]+", "", r).strip()[:40]


RACE_REASONS = {"already held", "already long", "still short", "overnight long"}


# ---------------------------------------------------------------------------------- shorting
HEADER_RE = re.compile(r"^=== daily (open|close|cover-leftovers) run (\S+) \| (\w+)(.*?)===\s*$")
HALF_RE = re.compile(r"^half day: close (\d\d:\d\d) ET, scan (\d\d:\d\d), cover (\d\d:\d\d)")
BOARD_ROW_RE = re.compile(r"^\s*(\d+)\s+([A-Z][A-Z0-9.\-]*)\s+(.*)\s(-?\d+\.\d)(?:\s\s(.*))?$")
SHORT_PICK_RE = re.compile(r"SHORT (\d+) \(([\d.]+)%\)")
PICKS_SAVED_RE = re.compile(r"^picks saved for (\S+): (.*?) \((\d+)% gross of \$([\d.]+)\) -> (\S+)")
OPEN_PICKS_RE = re.compile(r"^open: (\d+) picks from the (\S+) scan \(run [^)]*\): (.*)$")
SKIP_RE = re.compile(r"^  skip (\S+): (.*)$")
PLAN_SELL_RE = re.compile(r"^  plan SELL (\S+) (\d+) @ ~\$([\d.]+) \(([\d.]+)%\)")
LONG_BLOCK_RE = re.compile(r"^long sleeve \((\w+)\): (\d+) of (\d+) picks")
LONG_NONE_RE = re.compile(r"^long sleeve: (.*)$")
LONG_SKIP_RE = re.compile(r"^  long skip (\S+): (.*)$")
PLAN_BUY_RE = re.compile(r"^  plan BUY (\S+) (\d+) @ ~\$([\d.]+) \(([\d.]+)%\)")
COVER_RE = re.compile(r"^cover: (\d+) shorts open (.*)$")
LEFTOVER_RE = re.compile(r"^leftover check \(([^)]*)\): (?:(\d+) shorts from an earlier session (\{.*\}) - covering"
                         r"|(no shorts from an earlier session))")
LONG_EXIT_RE = re.compile(r"^long exit: (\d+) longs open (.*)$")
NO_PICKS_RE = re.compile(r"ALERT (no_picks|stale_picks): (.*)$")
NO_ENTRY_ALERT_RE = re.compile(r"ALERT (pdt_block|pdt_limit|daily_loss_halt|drawdown_halt): (.*)$")


def session_times(day: str, *sides: Side) -> dict:
    """Close-job times for the day: regular (15:40 job, 15:45 scan, 15:55 cover) or a half day."""
    for s in sides:
        for x in s.short:
            m = HALF_RE.match(x.msg)
            if m:
                scan, cover = at(day, m.group(2)), at(day, m.group(3))
                return {"half": True, "start": scan - dt.timedelta(minutes=5), "scan": scan, "cover": cover,
                        "fallback": scan + dt.timedelta(minutes=5)}
    return {"half": False, "start": at(day, "15:40"), "scan": at(day, "15:45"), "cover": at(day, "15:55"),
            "fallback": at(day, "15:50")}


def headers(lines: list[Line], kind: str) -> list[tuple[Line, re.Match]]:
    out = []
    for x in lines:
        m = HEADER_RE.match(x.msg)
        if m and m.group(1) == kind:
            out.append((x, m))
    return out


def close_header(side: Side, t: dict) -> Line | None:
    lo, hi = t["start"] - dt.timedelta(minutes=5), t["start"] + dt.timedelta(minutes=12)
    for x, m in headers(side.short, "close"):
        if lo <= x.ts <= hi and "fallback" not in m.group(4):
            return x
    return None


def parse_scan(side: Side, t: dict) -> dict | None:
    """The main 15:40 job's scan: board rows and the picks-saved line. None when no scan ran."""
    hdr = close_header(side, t)
    lo = hdr.ts if hdr else t["start"]
    lines = window(side.short, lo, t["cover"] + dt.timedelta(minutes=10))
    board, saved, failed = [], None, None
    i = 0
    while i < len(lines):
        x = lines[i]
        if not board and "PICK / WHY NOT" in x.msg:
            j = i + 1
            while j < len(lines):
                m = BOARD_ROW_RE.match(lines[j].msg)
                if not m:
                    if lines[j].msg.startswith("monitor"):
                        j += 1
                        continue
                    break
                pick = (m.group(5) or "").strip()
                sm = SHORT_PICK_RE.search(pick)
                board.append({"rank": int(m.group(1)), "ticker": m.group(2), "score": float(m.group(4)),
                              "qty": int(sm.group(1)) if sm else None,
                              "pct": float(sm.group(2)) if sm else None,
                              "reason": "" if sm else pick})
                j += 1
            i = j
            continue
        if saved is None:
            m = PICKS_SAVED_RE.match(x.msg)
            if m:
                tick = [] if m.group(2).strip() == "none" else [s.strip() for s in m.group(2).split(",")]
                saved = {"trade_date": m.group(1), "tickers": tick, "gross_pct": int(m.group(3)),
                         "equity": float(m.group(4)), "file": m.group(5)}
        if failed is None and x.level == "ERROR" and (x.msg.startswith("scan failed") or x.msg.startswith("scan timed out")):
            failed = x.msg.split("\n")[0][:120]
        i += 1
    if not board and saved is None and failed is None:
        return None
    if saved is not None:
        picks = saved["tickers"]
    else:
        picks = [r["ticker"] for r in board if r["qty"]]
    qty = {r["ticker"]: r["qty"] for r in board if r["qty"]}
    return {"board": board, "picks": picks, "qty": qty, "saved": saved, "failed": failed}


def compare_names(m: list[str], v: list[str], min_overlap: float, what: str = "names") -> tuple[str, str]:
    if m == v:
        return MATCH, f"same {len(m)} {what}, same order" if m else f"no {what} on either side"
    sm, sv = set(m), set(v)
    denom = max(len(sm), len(sv)) or 1
    common = len(sm & sv)
    only_m, only_v = sorted(sm - sv), sorted(sv - sm)
    extra = (f"; Mac only {fmt_list(only_m)}" if only_m else "") + (f"; VM only {fmt_list(only_v)}" if only_v else "")
    if sm == sv:
        return TOL, f"same {len(sm)} {what}, different order"
    if common / denom >= min_overlap:
        return TOL, f"{common}/{denom} {what} in common (>= {min_overlap:.0%}){extra}"
    return MISMATCH, f"{common}/{denom} {what} in common (< {min_overlap:.0%}){extra}"


def compare_qty(m: dict, v: dict, tol: float, what: str = "size") -> tuple[str, str]:
    common = sorted(set(m) & set(v))
    if not common:
        return NA, f"no common names to compare {what}"
    bad, off = [], []
    for k in common:
        d = rel_diff(m[k], v[k])
        if d > tol:
            bad.append(f"{k} {m[k]:g} vs {v[k]:g}")
        elif d > 0:
            off.append(f"{k} {m[k]:g} vs {v[k]:g}")
    if bad:
        return MISMATCH, f"{what} off by > {tol:.0%}: " + ", ".join(bad)
    if off:
        return TOL, f"{what} within {tol:.0%}: " + ", ".join(off)
    return MATCH, f"{what} identical for {len(common)} names"


def scan_items(mac: Side, vm: Side, t: dict, a) -> list[dict]:
    out = []
    ms, vs = parse_scan(mac, t), parse_scan(vm, t)
    label = f"{t['scan']:%H:%M} scan"
    if ms is None or vs is None:
        who = "either side" if ms is None and vs is None else ("the Mac" if ms is None else "the VM")
        out.append(item("scan", f"{label}: picks", MISSING,
                        "no scan" if ms is None else fmt_list(ms["picks"]),
                        "no scan" if vs is None else fmt_list(vs["picks"]),
                        f"no scan logged on {who} for {mac.day}"))
        return out
    if ms["failed"] or vs["failed"]:
        st = MATCH if ms["failed"] and vs["failed"] else MISMATCH
        out.append(item("scan", f"{label}: ran", st, ms["failed"] or "ok", vs["failed"] or "ok",
                        "scan failed on " + ("both" if st == MATCH else ("the VM" if vs["failed"] else "the Mac"))))
    mu = [r["ticker"] for r in ms["board"]]
    vu = [r["ticker"] for r in vs["board"]]
    if mu and vu:
        st, note = compare_names(sorted(mu), sorted(vu), a.min_universe_overlap, "gainers")
        out.append(item("scan", f"{label}: TradingView universe", st, f"{len(mu)} names", f"{len(vu)} names", note))
        mr = {r["ticker"]: ("SHORT" if r["qty"] else reason_kind(r["reason"])) for r in ms["board"]}
        vr = {r["ticker"]: ("SHORT" if r["qty"] else reason_kind(r["reason"])) for r in vs["board"]}
        diff = [f"{k} {mr[k] or 'ok'}/{vr[k] or 'ok'}" for k in sorted(set(mr) & set(vr)) if mr[k] != vr[k]]
        out.append(item("scan", f"{label}: gate per name (info)", TOL if diff else MATCH,
                        f"{len(mr)} judged", f"{len(vr)} judged",
                        ("Mac/VM: " + "; ".join(diff[:8]) + (" ..." if len(diff) > 8 else "")) if diff
                        else "same pick/exclusion category for every common name"))
    else:
        out.append(item("scan", f"{label}: TradingView universe", NC, f"{len(mu)} rows", f"{len(vu)} rows",
                        "board not logged on one side"))
    st, note = compare_names(ms["picks"], vs["picks"], a.min_overlap, "picks")
    out.append(item("scan", f"{label}: picks for the next open", st, fmt_list(ms["picks"]), fmt_list(vs["picks"]), note))
    st, note = compare_qty(ms["qty"], vs["qty"], a.qty_tol, "share qty")
    out.append(item("scan", f"{label}: pick size", st, fmt_dict(ms["qty"]), fmt_dict(vs["qty"]), note))

    mj, vj = mac.picks_json, vm.picks_json
    if mj and vj and mj.get("scan_date") == mac.day and vj.get("scan_date") == mac.day:
        mt, vt = list(mj.get("tickers") or []), list(vj.get("tickers") or [])
        st, note = compare_names(mt, vt, a.min_overlap, "picks")
        note += " (the VM's dry-run file can be rewritten by its 15:50 dry fallback)"
        out.append(item("scan", "picks file for the next open", st, fmt_list(mt), fmt_list(vt), note))
    else:
        why = []
        if not mj or mj.get("scan_date") != mac.day:
            why.append(f"Mac next_open_picks.json is for {mj.get('scan_date') if mj else 'nothing'}")
        if not vj or vj.get("scan_date") != mac.day:
            why.append(f"VM next_open_picks.dry_run.json is for {vj.get('scan_date') if vj else 'nothing'}")
        out.append(item("scan", "picks file for the next open", NC, "-", "-",
                        "; ".join(why) + " (run the compare the evening of the shadow day)"))
    return out


def parse_open(side: Side) -> dict | None:
    day = side.day
    hdrs = [x for x, _ in headers(side.short, "open") if at(day, "09:20") <= x.ts <= at(day, "10:00")]
    if not hdrs:
        return None
    h = hdrs[0]
    lines = window(side.short, h.ts, h.ts + dt.timedelta(minutes=30))
    o = {"picks": None, "no_picks": None, "skips": {}, "entries": {}, "leftovers": None, "long_exit": None,
         "halt": None, "dry": False}
    for x in lines:
        msg = x.msg
        if HEADER_RE.match(msg) and x is not h:
            break
        m = OPEN_PICKS_RE.match(msg)
        if m and o["picks"] is None:
            o["picks"] = [] if m.group(3).strip() == "none" else [s.strip() for s in m.group(3).split(",")]
            continue
        m = NO_PICKS_RE.search(msg)
        if m and o["no_picks"] is None:
            o["no_picks"] = m.group(1)
            continue
        m = NO_ENTRY_ALERT_RE.search(msg)
        if m and o["halt"] is None:
            o["halt"] = m.group(1)
            continue
        m = SKIP_RE.match(msg)
        if m:
            o["skips"][m.group(1)] = m.group(2)
            continue
        m = PLAN_SELL_RE.match(msg)
        if m:
            o["entries"][m.group(1)] = int(m.group(2))
            continue
        m = LEFTOVER_RE.match(msg)
        if m and o["leftovers"] is None and "safety net" not in m.group(1):
            o["leftovers"] = {} if m.group(4) else parse_dict(m.group(3))
            continue
        m = LONG_EXIT_RE.match(msg)
        if m and o["long_exit"] is None:
            o["long_exit"] = parse_dict(m.group(2))
            continue
        if msg.startswith("DRY RUN - no orders sent"):
            o["dry"] = True
        if msg.startswith("done:") or msg.startswith("DRY RUN - no orders sent"):
            break
    return o


def compare_positions(m: dict | None, v: dict | None, what: str) -> tuple[str, str]:
    """Positions read by both sides at the same moment. The VM reading a little later than the Mac's
    own orders (it sees less) is the shadow race; the VM seeing more, or other names, is a mismatch."""
    if m is None or v is None:
        return (MISSING if (m is None) != (v is None) else NC), f"{what} not logged on " + \
            ("either side" if m is None and v is None else ("the Mac" if m is None else "the VM"))
    if m == v:
        return MATCH, f"same {what}" if m else f"no {what} on either side"
    extra = set(v) - set(m)
    more = [k for k in set(v) & set(m) if abs(v[k]) > abs(m[k])]
    if not extra and not more:
        lacks = [k for k in sorted(m) if k not in v] + \
                [f"{k} {abs(m[k]) - abs(v[k]):g}" for k in sorted(m) if k in v and abs(m[k]) != abs(v[k])]
        return TOL, "VM saw less (it read after the Mac's own orders): VM lacks " + ", ".join(lacks)
    return MISMATCH, f"VM saw {what} the Mac did not: " + ", ".join(sorted(extra) + more)


def compare_plans(m_entries: dict, m_skips: dict, v_entries: dict, v_skips: dict, names: list[str],
                  tol: float, side_word: str) -> tuple[str, str]:
    notes, st = [], []
    for k in names:
        me, ve = k in m_entries, k in v_entries
        if me and ve:
            d = rel_diff(m_entries[k], v_entries[k])
            if d > tol:
                st.append(MISMATCH); notes.append(f"{k} qty {m_entries[k]} vs {v_entries[k]}")
            elif d:
                st.append(TOL); notes.append(f"{k} qty {m_entries[k]} vs {v_entries[k]}")
            else:
                st.append(MATCH)
        elif me and not ve:
            r = v_skips.get(k)
            if r is not None and reason_kind(r) in RACE_REASONS:
                st.append(TOL); notes.append(f"{k}: Mac {side_word}, VM skipped '{r}' (read after the Mac's order)")
            else:
                st.append(MISMATCH); notes.append(f"{k}: Mac {side_word}, VM " + (f"skipped '{r}'" if r else "did not plan it"))
        elif ve and not me:
            r = m_skips.get(k)
            st.append(MISMATCH); notes.append(f"{k}: VM would {side_word.split()[0]}, Mac " + (f"skipped '{r}'" if r else "did not"))
        else:
            mr, vr = m_skips.get(k), v_skips.get(k)
            if mr is not None and vr is not None and reason_kind(mr) != reason_kind(vr):
                st.append(TOL); notes.append(f"{k}: both skipped ('{mr}' / '{vr}')")
            else:
                st.append(MATCH)
    return worst(*st) if st else MATCH, "; ".join(notes) or "same decision for every name"


def open_items(mac: Side, vm: Side, a) -> list[dict]:
    out = []
    mo, vo = parse_open(mac), parse_open(vm)
    if mo is None or vo is None:
        out.append(item("open", "09:31 open entry", MISSING, "ran" if mo else "no run logged",
                        "ran" if vo else "no run logged", "the 09:31 job left no header on " +
                        ("either side" if mo is None and vo is None else ("the Mac" if mo is None else "the VM"))))
        return out
    st, note = compare_positions(mo["leftovers"], vo["leftovers"], "leftover shorts")
    out.append(item("open", "09:31 leftover shorts to cover", st, fmt_dict(mo["leftovers"]), fmt_dict(vo["leftovers"]), note))
    if mo["long_exit"] is not None or vo["long_exit"] is not None:
        st, note = compare_positions(mo["long_exit"], vo["long_exit"], "sleeve longs")
        out.append(item("open", "09:31 sleeve longs to sell", st, fmt_dict(mo["long_exit"]), fmt_dict(vo["long_exit"]), note))
    mp, vp = mo["picks"], vo["picks"]
    if mp is None and vp is None:
        same = mo["no_picks"] == vo["no_picks"]
        out.append(item("open", "09:31 picks read", MATCH if same else TOL, mo["no_picks"] or "-", vo["no_picks"] or "-",
                        "no picks on either side"))
        return out
    if vp is None:
        out.append(item("open", "09:31 picks read", NC, fmt_list(mp), vo["no_picks"] or "none",
                        "VM had no dry-run picks from the previous evening (first shadow day?)"))
        return out
    if mp is None:
        out.append(item("open", "09:31 picks read", MISMATCH, mo["no_picks"] or "none", fmt_list(vp),
                        "the VM entered from picks the Mac did not have"))
        return out
    st, note = compare_names(mp, vp, a.min_overlap, "picks")
    out.append(item("open", "09:31 picks read", st, fmt_list(mp), fmt_list(vp),
                    note + " (VM picks = its own dry scan of the previous evening)"))
    if mo["halt"] or vo["halt"]:
        out.append(item("open", "09:31 halt / PDT block", MATCH if mo["halt"] == vo["halt"] else MISMATCH,
                        mo["halt"] or "none", vo["halt"] or "none", "an entry block alert fired"))
    common = [k for k in mp if k in set(vp)]
    st, note = compare_plans(mo["entries"], mo["skips"], vo["entries"], vo["skips"], common, a.qty_tol, "shorted")
    out.append(item("open", "09:31 planned shorts", st, fmt_dict(mo["entries"]), fmt_dict(vo["entries"]),
                    f"{len(common)} common picks; " + note))
    return out


def parse_close(side: Side, t: dict) -> dict:
    lo, hi = t["cover"] - dt.timedelta(seconds=40), t["cover"] + dt.timedelta(minutes=6)
    cover = None
    for x in window(side.short, lo, hi):
        m = COVER_RE.match(x.msg)
        if m:
            cover = parse_dict(m.group(2))
            break
    # long sleeve: the LAST block between the scan and cover+10 (a dry fallback's block comes earlier)
    lines = window(side.short, t["scan"] - dt.timedelta(minutes=1), t["cover"] + dt.timedelta(minutes=10))
    sleeve = None
    for i, x in enumerate(lines):
        m = LONG_BLOCK_RE.match(x.msg)
        if m:
            sleeve = {"kind": m.group(1), "entries": {}, "skips": {}, "none": None, "ts": x.ts}
            for y in lines[i + 1:]:
                if (y.ts - x.ts).total_seconds() > 15:
                    break
                ms = LONG_SKIP_RE.match(y.msg)
                if ms:
                    sleeve["skips"][ms.group(1)] = ms.group(2)
                    continue
                mb = PLAN_BUY_RE.match(y.msg)
                if mb:
                    sleeve["entries"][mb.group(1)] = int(mb.group(2))
            continue
        m = LONG_NONE_RE.match(x.msg)
        if m:
            sleeve = {"kind": None, "entries": {}, "skips": {}, "none": m.group(1)[:80], "ts": x.ts}
    return {"cover": cover, "sleeve": sleeve}


def close_items(mac: Side, vm: Side, t: dict, a) -> list[dict]:
    out = []
    mc, vc = parse_close(mac, t), parse_close(vm, t)
    label = f"{t['cover']:%H:%M}"
    if mc["cover"] is None and vc["cover"] is None:
        out.append(item("close", f"{label} cover-all: shorts to cover", MISSING, "-", "-",
                        "no cover line on either side (did the close job run?)"))
    else:
        st, note = compare_positions(mc["cover"], vc["cover"], "shorts")
        out.append(item("close", f"{label} cover-all: shorts to cover", st, fmt_dict(mc["cover"]), fmt_dict(vc["cover"]), note))
    ms, vs = mc["sleeve"], vc["sleeve"]
    if ms is None and vs is None:
        out.append(item("close", f"{label} long sleeve buys", NA, "-", "-", "sleeve not logged on either side (off?)"))
    elif ms is None or vs is None:
        out.append(item("close", f"{label} long sleeve buys", MISSING, "logged" if ms else "-", "logged" if vs else "-",
                        f"only the {'Mac' if ms else 'VM'} logged a long-sleeve decision"))
    elif ms["none"] or vs["none"]:
        same = bool(ms["none"]) == bool(vs["none"]) and reason_kind(ms["none"] or "") == reason_kind(vs["none"] or "")
        out.append(item("close", f"{label} long sleeve buys", MATCH if same else MISMATCH,
                        ms["none"] or fmt_dict(ms["entries"]), vs["none"] or fmt_dict(vs["entries"]), "no-longs message"))
    else:
        names = sorted(set(ms["entries"]) | set(ms["skips"]) | set(vs["entries"]) | set(vs["skips"]))
        st, note = compare_plans(ms["entries"], ms["skips"], vs["entries"], vs["skips"], names, a.qty_tol, "bought")
        out.append(item("close", f"{label} long sleeve buys ({ms['kind']})", st, fmt_dict(ms["entries"]),
                        fmt_dict(vs["entries"]), note))
    return out


# ---------------------------------------------------------------------------------- options
MODE_RE = re.compile(r"^MODE (EXECUTE|DRY RUN) \| equity \$([\d,.]+)")
KILL_RE = re.compile(r"^kill switch: (ok|BLOCK) -> (.*)$")
OPEN_SPREADS_RE = re.compile(r"^-- open spreads: (\d+)")
SPREAD_ROW_RE = re.compile(r"^   (\S+)\s+x(\d+)\s+credit .* -> (.+)$")
ENTRY_ROW_RE = re.compile(r"^   ([A-Z]{1,6})\s+close (\S+) 200DMA (\S+) RSI2 (\S+) IBS (\S+) \[([^\]]*)\] -> (.+)$")
LEGS_RE = re.compile(r"^\s+(\S+) (\d{4}-\d{2}-\d{2}) DTE (\d+): sell (\S+) \(.*?\) buy (\S+) \(.*?\) width (\S+) mid")
CONDOR_HDR_RE = re.compile(r"^-- condor sleeve \(([^)]*)\): open (\d+)")
CONDOR_ENTRY_RE = re.compile(r"^   ([A-Z]{1,6})\s+spot (\S+) -> (.+)$")
CONDOR_LEG_RE = re.compile(r"^\s+\S+ \S+ DTE \d+: (put|call)\s+sell (\S+) .*? buy (\S+) ")
SIZING_RE = re.compile(r"sizing: (\d+)x")
OCC_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
EXIT_LINE_RE = re.compile(r"^(intraday|sweep) (?:condor )?(\S+)\s+mid (\S+) credit (\S+) -> (.+)$")
NO_SPREADS_RE = re.compile(r"^(intraday|sweep): no open spreads")


def occ(sym: str) -> tuple | None:
    m = OCC_RE.match(sym or "")
    return (m.group(1), m.group(2), m.group(3), int(m.group(4)) / 1000) if m else None


def parse_mode_blocks(lines: list[Line]) -> list[dict]:
    blocks = []
    cur, section = None, None
    for x in lines:
        m = MODE_RE.match(x.msg)
        if m:
            cur = {"ts": x.ts, "mode": m.group(1), "equity": float(m.group(2).replace(",", "")),
                   "kill": None, "spreads": {}, "entries": {}, "condor": None}
            blocks.append(cur)
            section = "head"
            continue
        if cur is None or (x.ts - cur["ts"]).total_seconds() > 10:
            cur = None
            continue
        msg = x.msg
        if msg.startswith("=" * 20):
            continue
        m = KILL_RE.match(msg)
        if m:
            cur["kill"] = m.group(1)
            continue
        if OPEN_SPREADS_RE.match(msg):
            section = "spreads"
            continue
        if msg.startswith("-- entry signals"):
            section = "entries"
            continue
        m = CONDOR_HDR_RE.match(msg)
        if m:
            section = "condor"
            cur["condor"] = {"state": m.group(1).split(";")[0], "open": int(m.group(2)), "exits": {},
                             "entry": None, "legs": {}, "size": None}
            continue
        if section == "spreads":
            m = SPREAD_ROW_RE.match(msg)
            if m:
                cur["spreads"][m.group(1)] = m.group(3).strip()
        elif section == "entries":
            m = ENTRY_ROW_RE.match(msg)
            if m:
                cur["entries"][m.group(1)] = {"action": m.group(7).strip(), "rsi2": m.group(4), "ibs": m.group(5),
                                              "legs": None}
                cur["_last"] = m.group(1)
                continue
            m = LEGS_RE.match(msg)
            if m and cur.get("_last"):
                cur["entries"][cur["_last"]]["legs"] = {"expiry": m.group(2), "short": m.group(4),
                                                        "long": m.group(5), "width": float(m.group(6))}
        elif section == "condor":
            c = cur["condor"]
            m = CONDOR_ENTRY_RE.match(msg)
            if m:
                c["entry"] = {"symbol": m.group(1), "action": m.group(3).strip()}
                continue
            m = SPREAD_ROW_RE.match(msg)
            if m:
                c["exits"][m.group(1)] = m.group(3).strip()
                continue
            m = CONDOR_LEG_RE.match(msg)
            if m:
                c["legs"][m.group(1)] = (m.group(2), m.group(3))
                continue
            m = SIZING_RE.search(msg)
            if m:
                c["size"] = int(m.group(1))
    for b in blocks:
        b.pop("_last", None)
    return blocks


def pick_block(side: Side, lo: dt.datetime, hi: dt.datetime, want: str) -> dict | None:
    bl = [b for b in parse_mode_blocks(side.opt) if lo <= b["ts"] <= hi]
    pref = [b for b in bl if b["mode"] == want]
    return (pref or bl or [None])[-1]


def parse_action(s: str) -> tuple:
    m = re.match(r"^OPEN (\d+)x", s)
    if m:
        return ("open", int(m.group(1)))
    m = re.match(r"^skip: (.*)$", s)
    if m:
        return ("skip", m.group(1))
    return (s, None)


def compare_legs(m: dict | None, v: dict | None, tol: float) -> tuple[str, str]:
    if not m or not v:
        return NC, "legs not logged"
    if (m["short"], m["long"]) == (v["short"], v["long"]):
        return MATCH, f"same legs {m['short']}/{m['long']}"
    ms, ml, vs, vl = occ(m["short"]), occ(m["long"]), occ(v["short"]), occ(v["long"])
    if not all((ms, ml, vs, vl)):
        return MISMATCH, f"legs {m['short']}/{m['long']} vs {v['short']}/{v['long']}"
    same_exp = ms[1] == vs[1] and ms[2] == vs[2]
    dshort, dlong = abs(ms[3] - vs[3]), abs(ml[3] - vl[3])
    if same_exp and dshort <= tol and dlong <= tol:
        return TOL, f"strikes {ms[3]:g}/{ml[3]:g} vs {vs[3]:g}/{vl[3]:g} (within ${tol:g})"
    return MISMATCH, f"legs {m['short']}/{m['long']} vs {v['short']}/{v['long']}"


def options_items(mac: Side, vm: Side, t: dict, a) -> list[dict]:
    out = []
    day = mac.day
    if t["half"]:
        lo, hi = t["start"] - dt.timedelta(minutes=2), t["start"] + dt.timedelta(minutes=35)
    else:
        lo, hi = at(day, "15:40"), at(day, "16:15")
    mb, vb = pick_block(mac, lo, hi, "EXECUTE"), pick_block(vm, lo, hi, "DRY RUN")
    label = "options decision"
    if mb is None or vb is None:
        out.append(item("options", label, MISSING, "logged" if mb else "no MODE block", "logged" if vb else "no MODE block",
                        "the daily decision printout is missing on " +
                        ("both sides" if mb is None and vb is None else ("the Mac" if mb is None else "the VM"))))
    else:
        if mb["mode"] != "EXECUTE" or vb["mode"] != "DRY RUN":
            out.append(item("options", f"{label}: modes", TOL, mb["mode"], vb["mode"],
                            "expected Mac EXECUTE and VM DRY RUN"))
        out.append(item("options", f"{label}: kill switch", MATCH if mb["kill"] == vb["kill"] else MISMATCH,
                        mb["kill"] or "-", vb["kill"] or "-", "VIX / VIX3M gate"))
        if mb["spreads"] or vb["spreads"]:
            st = MATCH if mb["spreads"] == vb["spreads"] else MISMATCH
            diff = [f"{k}: {mb['spreads'].get(k, '-')} / {vb['spreads'].get(k, '-')}"
                    for k in sorted(set(mb["spreads"]) | set(vb["spreads"])) if mb["spreads"].get(k) != vb["spreads"].get(k)]
            out.append(item("options", f"{label}: open spread exits", st, f"{len(mb['spreads'])} spreads",
                            f"{len(vb['spreads'])} spreads", "; ".join(diff) or "same action for every open spread"))
        else:
            out.append(item("options", f"{label}: open spread exits", MATCH, "0 spreads", "0 spreads", "nothing open"))
        for sym in sorted(set(mb["entries"]) | set(vb["entries"])):
            me, ve = mb["entries"].get(sym), vb["entries"].get(sym)
            if not me or not ve:
                out.append(item("options", f"{label}: {sym} entry", MISSING, me["action"] if me else "-",
                                ve["action"] if ve else "-", "signal row on one side only"))
                continue
            ma, va = parse_action(me["action"]), parse_action(ve["action"])
            if ma[0] == "skip" and va[0] == "skip":
                st = MATCH if ma[1] == va[1] else (MATCH if reason_kind(ma[1]) == reason_kind(va[1]) else TOL)
                note = "both skip" + ("" if ma[1] == va[1] else " (different reason)")
            elif ma[0] == "open" and va[0] == "open":
                st, note = compare_legs(me["legs"], ve["legs"], a.strike_tol)
                if ma[1] != va[1]:
                    st = worst(st, TOL if st == TOL else MISMATCH)
                    note += f"; size {ma[1]}x vs {va[1]}x"
            else:
                st, note = MISMATCH, "one side opens, the other does not"
            note += f" | RSI2 {me['rsi2']}/{ve['rsi2']} IBS {me['ibs']}/{ve['ibs']}"
            out.append(item("options", f"{label}: {sym} entry", st, me["action"][:40], ve["action"][:40], note))
        mc, vc = mb["condor"], vb["condor"]
        if mc or vc:
            if not mc or not vc:
                out.append(item("options", f"{label}: condor sleeve", MISSING, mc["state"] if mc else "-",
                                vc["state"] if vc else "-", "condor sleeve printed on one side only"))
            else:
                sts, notes = [], []
                sts.append(MATCH if mc["state"] == vc["state"] else MISMATCH)
                if mc["state"] != vc["state"]:
                    notes.append(f"state {mc['state']} vs {vc['state']}")
                if mc["exits"] != vc["exits"]:
                    sts.append(MISMATCH); notes.append(f"exits {mc['exits']} vs {vc['exits']}")
                ma = parse_action(mc["entry"]["action"]) if mc["entry"] else None
                va = parse_action(vc["entry"]["action"]) if vc["entry"] else None
                if (ma and ma[0]) != (va and va[0]):
                    sts.append(MISMATCH); notes.append(f"entry {mc['entry']} vs {vc['entry']}")
                elif ma and ma[0] == "open":
                    for wing in ("put", "call"):
                        ml, vl = mc["legs"].get(wing), vc["legs"].get(wing)
                        if ml and vl:
                            s2, n2 = compare_legs({"short": ml[0], "long": ml[1]}, {"short": vl[0], "long": vl[1]},
                                                  a.strike_tol)
                            sts.append(s2); notes.append(f"{wing}: {n2}")
                    if mc["size"] != vc["size"]:
                        sts.append(MISMATCH); notes.append(f"size {mc['size']}x vs {vc['size']}x")
                out.append(item("options", f"{label}: condor sleeve", worst(*sts),
                                f"{mc['state']}, {mc['entry']['action'][:30] if mc['entry'] else 'no entry row'}",
                                f"{vc['state']}, {vc['entry']['action'][:30] if vc['entry'] else 'no entry row'}",
                                "; ".join(notes) or "same sleeve state and decision"))
    out += exit_items(mac, vm)
    return out


def exit_slots(side: Side) -> dict[tuple, dict]:
    slots: dict[tuple, dict] = {}
    for x in side.opt:
        m = EXIT_LINE_RE.match(x.msg)
        n = NO_SPREADS_RE.match(x.msg)
        if not m and not n:
            continue
        kind = (m or n).group(1)
        slot = x.ts.replace(minute=x.ts.minute - x.ts.minute % 15, second=0)
        d = slots.setdefault((kind, f"{slot:%H:%M}"), {})
        if m:
            d[m.group(2)] = m.group(5).strip()
    return slots


def exit_items(mac: Side, vm: Side) -> list[dict]:
    ms, vs = exit_slots(mac), exit_slots(vm)
    common = sorted(set(ms) & set(vs))
    if not ms and not vs:
        return [item("options", "sweep / intraday exit decisions", NC, "-", "-", "no sweep/intraday lines on either side")]
    if not common:
        return [item("options", "sweep / intraday exit decisions", MISSING, f"{len(ms)} slots", f"{len(vs)} slots",
                     "no 15-minute slot logged on both sides")]
    bad = [f"{k[0]} {k[1]}: {ms[k]} / {vs[k]}" for k in common if ms[k] != vs[k]]
    return [item("options", "sweep / intraday exit decisions", MISMATCH if bad else MATCH,
                 f"{len(ms)} slots", f"{len(vs)} slots",
                 ("; ".join(bad[:4]) if bad else f"same decision in all {len(common)} common slots")
                 + (f"; slots only on the Mac: {len(set(ms) - set(vs))}" if set(ms) - set(vs) else ""))]


# ---------------------------------------------------------------------------------- jobs
def _count_slots(lines: list[Line], pred, lo: dt.datetime, hi: dt.datetime, minutes: int = 15) -> int:
    seen = set()
    for x in window(lines, lo, hi):
        if pred(x.msg):
            seen.add((x.ts.hour, x.ts.minute // minutes))
    return len(seen)


def content_runs(side: Side, job: str, t: dict) -> int | None:
    """How many times the job ran on `side`, judged from the bots' own log lines. None = cannot tell."""
    day, s, o = side.day, side.short, side.opt
    if job == "short-open":
        return len([1 for x, _ in headers(s, "open") if at(day, "09:20") <= x.ts <= at(day, "10:00")])
    if job == "short-close":
        return 1 if close_header(side, t) else 0
    if job == "short-fallback":
        lo, hi = t["fallback"] - dt.timedelta(minutes=2), t["fallback"] + dt.timedelta(minutes=15)
        return 1 if any(x.msg.startswith("--fallback:") or (HEADER_RE.match(x.msg) and "fallback" in x.msg)
                        for x in window(s, lo, hi)) else 0
    if job == "short-cover-leftovers":
        return len(headers(s, "cover-leftovers"))
    if job == "short-monitor":
        return _count_slots(s, lambda m: m.startswith("monitor"), at(day, "09:00"), at(day, "15:59"))
    if job == "short-sync":
        return 1 if any(x.msg.startswith("sync:") for x in window(s, at(day, "16:10"), at(day, "17:30"))) else 0
    if job == "opt-sweep":
        return 1 if any(x.msg.startswith("sweep") for x in window(o, at(day, "09:44"), at(day, "10:05"))) else 0
    if job == "opt-intraday":
        return _count_slots(o, lambda m: m.startswith("intraday"), at(day, "10:00"), at(day, "15:40"))
    if job == "opt-daily":
        lo, hi = (t["start"] + dt.timedelta(minutes=4), t["start"] + dt.timedelta(minutes=30)) if t["half"] \
            else (at(day, "15:44"), at(day, "16:10"))
        return 1 if window(o, lo, hi) else 0
    if job == "opt-sync":
        return 1 if any(x.msg.startswith("sync:") for x in window(side.opt_cron, at(day, "16:10"), at(day, "17:30"))) else 0
    return None


# job, expected runs on a regular day (None = any), skipped in shadow (VM never runs it)
JOBS = [
    ("short-open", 1, False), ("short-cover-leftovers", 3, False), ("short-close", 1, False),
    ("short-fallback", 1, False), ("short-halfday", None, False), ("short-halfday-fallback", None, False),
    ("short-monitor", None, True), ("short-sync", 1, True),
    ("opt-sweep", 1, False), ("opt-intraday", None, False), ("opt-halfday", None, False),
    ("opt-daily", 1, False), ("opt-sync", 1, True),
    ("events-sync", 1, False), ("health-check", None, False),
]


# judged on the VM's run-job markers only (no timestamped Mac log line to compare with)
VM_ONLY = {"short-halfday", "short-halfday-fallback", "opt-halfday", "events-sync", "health-check"}


def job_items(mac: Side, vm: Side, t: dict) -> list[dict]:
    out = []
    have_markers = bool(vm.markers)
    for job, expect, shadow_skip in JOBS:
        mc = content_runs(mac, job, t)
        if shadow_skip:
            out.append(item("jobs", job, NA, f"{mc} runs" if mc is not None else "-", "skipped in shadow",
                            "only writes to Supabase or can trade, so run-job.sh skips it in shadow mode"))
            continue
        runs = vm.markers.get(job, [])
        if have_markers:
            vn = len(runs)
            bad = [r for r in runs if r["rc"] not in (0, None)]
            unfinished = [r for r in runs if r["rc"] is None]
            vtxt = f"{vn} runs" + (f", rc {','.join(str(r['rc']) for r in bad)}" if bad else "")
            if bad:
                out.append(item("jobs", job, MISMATCH, f"{mc} runs" if mc is not None else "-", vtxt,
                                "VM job exited non-zero (124/137 = timed out); see journalctl -u clive-job@" + job))
                continue
            if unfinished:
                vtxt += f", {len(unfinished)} without an exit line"
        else:
            vn = content_runs(vm, job, t)
            vtxt = f"{vn} runs (log lines)" if vn is not None else "-"
        mtxt = f"{mc} runs" if mc is not None else "-"
        if vn is None:
            out.append(item("jobs", job, NC, mtxt, vtxt, "no run-job markers and no log lines to judge by"))
            continue
        if job in VM_ONLY:
            if job in ("short-halfday", "short-halfday-fallback", "opt-halfday"):
                st = MATCH if vn or not t["half"] else MISSING
            else:
                st = MATCH if vn else MISSING
            out.append(item("jobs", job, st, mtxt, vtxt,
                            "VM-side check (ran, exit 0)" if st == MATCH else "the VM never ran it"))
            continue
        need = expect if expect is not None else (mc or 0)
        if vn == 0 and (need or mc):
            out.append(item("jobs", job, MISSING, mtxt, vtxt,
                            "did not run on either side" if not mc else "the VM never ran it"))
        elif mc is not None and vn < mc:
            out.append(item("jobs", job, MISMATCH, mtxt, vtxt, f"the VM ran it {mc - vn} times fewer than the Mac"))
        elif mc is not None and mc < (expect or 0) and vn >= (expect or 0):
            out.append(item("jobs", job, TOL, mtxt, vtxt, "the Mac missed runs the VM made"))
        elif mc is not None and vn > mc:
            out.append(item("jobs", job, TOL, mtxt, vtxt, "the VM ran it more often (Mac log has fewer lines)"))
        else:
            out.append(item("jobs", job, MATCH, mtxt, vtxt, "ran on both" if vn else "did not run on either"))
    return out


def error_items(mac: Side, vm: Side) -> list[dict]:
    def norm(msg: str) -> str:
        m = msg.split("\n")[0]
        m = re.sub(r"0x[0-9a-f]+|[0-9a-f]{8}-[0-9a-f-]{27}|\d+(\.\d+)?", "#", m)
        return m[:90]
    me = {norm(x.msg) for x in mac.short + mac.opt if x.level in ("ERROR", "CRITICAL")}
    ve = [norm(x.msg) for x in vm.short + vm.opt if x.level in ("ERROR", "CRITICAL")]
    only = sorted({e for e in ve if e not in me})
    return [item("errors", "ERROR lines only on the VM (info)", TOL if only else MATCH, f"{len(me)} kinds",
                 f"{len(set(ve))} kinds", "; ".join(only[:5]) + (" ..." if len(only) > 5 else "") if only
                 else "none beyond the Mac's")]


# ---------------------------------------------------------------------------------- output
def item(section: str, name: str, status: str, mac: str, vm: str, note: str = "") -> dict:
    return {"section": section, "item": name, "status": status, "mac": str(mac), "vm": str(vm), "note": note}


def market_closed(side: Side) -> bool:
    return any(x.msg.startswith("market closed today") for x in side.short)


def compare(mac: Side, vm: Side, a) -> list[dict]:
    if market_closed(mac) and market_closed(vm):
        return [item("jobs", "market session", NA, "market closed", "market closed",
                     f"{mac.day} is not a trading day on either side - nothing to compare")]
    t = session_times(mac.day, mac, vm)
    items = job_items(mac, vm, t)
    items += scan_items(mac, vm, t, a)
    items += open_items(mac, vm, a)
    items += close_items(mac, vm, t, a)
    items += options_items(mac, vm, t, a)
    items += error_items(mac, vm)
    return items


def verdict(items: list[dict], strict: bool) -> bool:
    bad = FAILING | ({NC} if strict else set())
    return not any(i["status"] in bad for i in items)


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "~"


def print_table(items: list[dict], day: str, ok: bool, out=None) -> None:
    out = out or sys.stdout
    print(f"shadow compare {day}: Mac (real) vs VM (shadow dry run)", file=out)
    w_item = min(44, max(len(i["item"]) for i in items))
    sec = None
    for i in items:
        if i["section"] != sec:
            sec = i["section"]
            print(f"\n[{sec}]", file=out)
        print(f"  {i['status']:<15} {_cut(i['item'], w_item):<{w_item}}  Mac: {_cut(i['mac'], 34):<34}  "
              f"VM: {_cut(i['vm'], 34):<34}", file=out)
        if i["note"]:
            print(f"  {'':<15} {'':<{w_item}}  {i['note']}", file=out)
    counts = {}
    for i in items:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    summary = ", ".join(f"{counts[k]} {k}" for k in sorted(counts, key=lambda k: -RANK[k]))
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}  ({summary})", file=out)


def default_day() -> str:
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def main(argv=None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mac", default=os.path.dirname(here), help="Mac root (default: this repo)")
    ap.add_argument("--vm", required=True, help="folder with the VM's logs, laid out like the repo")
    ap.add_argument("--date", default=None, help="ET trading day YYYY-MM-DD (default: today ET)")
    ap.add_argument("--min-overlap", type=float, default=0.8,
                    help="picks: minimum share of names in common to tolerate a difference (default 0.8 = 4 of 5)")
    ap.add_argument("--min-universe-overlap", type=float, default=0.6,
                    help="TradingView gainers: minimum share in common (default 0.6)")
    ap.add_argument("--qty-tol", type=float, default=0.10, help="share/contract qty tolerance, relative (default 0.10)")
    ap.add_argument("--strike-tol", type=float, default=2.0, help="option strike tolerance in $ (default 2)")
    ap.add_argument("--strict", action="store_true", help="NOT-COMPARABLE rows fail too")
    ap.add_argument("--json", action="store_true", help="print the rows and the verdict as JSON")
    a = ap.parse_args(argv)
    day = a.date or default_day()
    try:
        dt.date.fromisoformat(day)
    except ValueError:
        ap.error(f"bad --date {day!r}")
    mac, vm = Side(a.mac, day, "mac"), Side(a.vm, day, "vm")
    if mac.empty or vm.empty:
        print(f"no log lines for {day} on the {'Mac' if mac.empty else 'VM'} side "
              f"({a.mac if mac.empty else a.vm})", file=sys.stderr)
        return 2
    items = compare(mac, vm, a)
    ok = verdict(items, a.strict)
    if a.json:
        print(json.dumps({"date": day, "result": "PASS" if ok else "FAIL", "items": items}, indent=1))
    else:
        print_table(items, day, ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
