"""Tests for deploy/shadow_compare.py, on synthetic log files written in the bots' real formats
(daily.py print_board / open / cover / long sleeve lines, live/run.py print_plan, run-job.sh markers)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import shadow_compare as sc  # noqa: E402

DAY = "2026-10-27"
PREV = "2026-10-26"
PICKS = [("AVX", 10.96, 412, 19.0), ("BNC", 13.10, 317, 18.0), ("NAKA", 8.40, 201, 17.0),
         ("ASST", 30.50, 51, 16.0), ("RCEL", 16.20, 96, 15.0)]
OTHERS = ["AARD", "ABSI", "SEI", "ROLR", "ATII"]


def L(hms, msg, level="INFO", day=DAY):
    return f"{day} {hms} {level} {msg}"


def board(picks, others, hms="15:45:09"):
    head = (f"{'#':>2} {'TICKER':<6} {'PRICE':>7} {'CHG%':>6} {'RVOL':>5} {'SI%':>5} {'DTC':>4} "
            f"{'ADV20':>7} {'CAT':<13} {'SCORE':>5}  PICK / WHY NOT")
    rows = [L(hms, head)]
    n = 0
    for t, px, qty, pct in picks:
        n += 1
        rows.append(L(hms, "%2d %-6s %7s %6s %5s %5s %4s %7s %-13s %5.1f  %s" % (
            n, t, f"{px:.2f}", "11.1", "1.4", "1.1", "0.3", "5.2M", "no_news", 60 - n, f"SHORT {qty} ({pct}%)")))
    for t in others:
        n += 1
        rows.append(L(hms, "%2d %-6s %7s %6s %5s %5s %4s %7s %-13s %5.1f  %s" % (
            n, t, "4.10", "12.0", "0.9", "-", "-", "-", "unclear", 40 - n, "not shortable on Alpaca")))
    return rows


def shorting_log(mode="execute", picks=PICKS, others=OTHERS, open_picks=PICKS, open_plan=None, open_skips=None,
                 cover=None, longs=None, fallback=False):
    """A full day of shortingbot.log. mode execute = the Mac, dry_run = the VM in shadow."""
    dry = mode == "dry_run"
    rows = [L("09:31:01", f"=== daily open run {DAY} | {mode} | short_intraday ==="),
            L("09:31:02", "leftover check (09:31): no shorts from an earlier session"),
            L("09:31:02", "long exit: 0 longs open {}"),
            L("09:31:02", f"open: {len(open_picks)} picks from the {PREV} scan (run None): "
                          + (", ".join(p[0] for p in open_picks) or "none"))]
    rows.append(L("09:31:03", "PDT cap off (PDT_ENFORCE=auto)"))
    for t, r in (open_skips or {}).items():
        rows.append(L("09:31:04", f"  skip {t}: {r}"))
    plan = open_plan if open_plan is not None else {p[0]: p[2] for p in open_picks}
    for t, q in plan.items():
        rows.append(L("09:31:04", f"  plan SELL {t} {q} @ ~$10.00 (17.0%), stop ~$11.50"))
    if dry:
        rows.append(L("09:31:04", "DRY RUN - no orders sent. Gross short exposure would be 85% of $10389.00."))
    else:
        for t, q in plan.items():
            rows.append(L("09:31:05", f"  SELL {t} {q} (short)"))
        rows.append(L("09:31:09", f"done: {len(plan)} entries, 0 skipped, 0 problems"))
    rows.append(L("09:45:02", "monitor: 5 positions, equity $10248.87, over the line: none, raised none, resolved none"))
    for hh in ("10", "12", "14"):
        rows += [L(f"{hh}:00:01", f"=== daily cover-leftovers run {DAY} | {mode} ==="),
                 L(f"{hh}:00:02", f"leftover check ({hh}:00 safety net): no shorts from an earlier session")]
    if not dry:
        rows.append(L("12:40:01", "--half-day-only: regular close 16:00 ET - nothing to do"))
    rows += [L("15:40:02", f"=== daily close run {DAY} | {mode} | short_intraday ==="),
             L("15:40:02", "waiting 297s for scan (15:45 ET)"),
             L("15:45:00", "scan started for trade date 2026-10-28 (run None)"),
             L("15:45:00", "tradingview: 20 gainers after filters"),
             L("15:45:00", "monitor: 5 positions, equity $10248.87, over the line: none, raised none, resolved none")]
    rows += board(picks, others)
    gross = sum(p[3] for p in picks)
    fname = "next_open_picks.dry_run.json" if dry else "next_open_picks.json"
    rows.append(L("15:45:09", f"picks saved for 2026-10-28: {', '.join(p[0] for p in picks) or 'none'} "
                              f"({gross:.0f}% gross of $10389.00) -> {fname}"))
    if fallback:  # the VM's dry fallback at 15:50 runs a whole dry close, interleaved
        rows += [L("15:50:01", f"=== daily close run {DAY} | {mode} | short_intraday | fallback ==="),
                 L("15:50:02", "cover: 5 shorts open {'AVX': -412, 'BNC': -317, 'NAKA': -201, 'ASST': -51, 'RCEL': -96}"),
                 L("15:50:02", "  DRY RUN would BUY AVX 412 (cover)")]
        rows += board([("ZZZ", 5.0, 100, 19.0)], [], hms="15:50:30")
        rows.append(L("15:50:30", "picks saved for 2026-10-28: ZZZ (19% gross of $10389.00) -> " + fname))
        rows += [L("15:50:31", "long sleeve (limit): 1 of 1 picks, 0.0% long gross already open"),
                 L("15:50:31", "  plan BUY ZZZ 100 @ ~$5.00 (5.0%), limit $5.03")]
    else:
        rows.append(L("15:50:01", "--fallback: today's close job already started or finished - nothing to do") if not dry
                    else L("15:50:01", "--fallback: today's close job already started or finished - nothing to do"))
    cover = cover if cover is not None else {p[0]: -p[2] for p in open_picks}
    rows.append(L("15:55:00", f"cover: {len(cover)} shorts open {cover}"))
    for t, q in cover.items():
        rows.append(L("15:55:00", f"  {'DRY RUN would BUY' if dry else 'BUY'} {t} {abs(q)} (cover)"))
    longs = longs if longs is not None else {"AVX": 95, "BNC": 79}
    rows.append(L("15:55:31", f"long sleeve (limit): {len(longs)} of 5 picks, 0.0% long gross already open"))
    for t, q in longs.items():
        rows.append(L("15:55:31", f"  plan BUY {t} {q} @ ~$10.96 (10.0%), limit $11.02"))
    rows.append(L("15:55:31", "  long skip NAKA: news: 'offering' (catalyst dilution)"))
    rows.append(L("15:55:40", f"done: 5 covers{'' if not dry else ' planned'}, 0 cover problems, 5 picks saved"
                              f"{'' if not dry else ' (DRY RUN)'}"))
    if not dry:
        rows.append(L("16:15:03", "sync: equity $10401.28, 5 positions, 6 fills -> 0 closed trades (0 with a run_id)"))
    return rows


def options_log(mode="EXECUTE", spy_action="OPEN 1x  (max loss $1,364/spread, budget $2,500)",
                legs=("SPY261106P00683000", "SPY261106P00668000"), condor=True):
    rows = [L("09:45:02", "sweep: no open spreads")]
    for hh in ("10", "11", "12", "13", "14"):
        for mm in ("00", "15", "30", "45"):
            rows.append(L(f"{hh}:{mm}:02", "intraday: no open spreads"))
    for mm in ("00", "15", "30"):
        rows.append(L(f"15:{mm}:02", "intraday: no open spreads"))
    rows += [L("15:55:00", "=" * 96),
             L("15:55:00", f"MODE {mode} | equity $10,000.00 | VIX cboe live"),
             L("15:55:00", "decision time 15:55 ET (DECIDE_MINUTES_BEFORE_CLOSE=5)"),
             L("15:55:00", "kill switch: ok -> VIX 17.00, VIX/VIX3M 0.895"),
             L("15:55:00", "-- open spreads: 1"),
             L("15:55:00", "   %-26s x%-3s credit %s (mid %s)  cost mid %s  nat %s  held %sd  tdte %s  close %s vs 5DMA %s -> %s"
               % ("SPY-20261030-P700/690", 1, "0.80", "0.80", "0.30", "0.35", 2, 3, "697.56", "690.10", "hold")),
             L("15:55:00", "-- entry signals"),
             L("15:55:00", "   %-5s close %s 200DMA %s RSI2 %s IBS %s [%s] -> %s"
               % ("SPY", "697.56", "663.38", "0.4", "0.06", "live", spy_action))]
    if spy_action.startswith("OPEN"):
        rows += [L("15:55:00", f"         SPY 2026-11-06 DTE 9: sell {legs[0]} (Δ-0.23 2.39/2.43) buy {legs[1]} "
                               f"(Δ-0.11 1.01/1.05) width 15 mid 1.38 nat 1.34 (9% of width)"),
                 L("15:55:00", "         limit credits (re-priced in order): 1.38, 1.37, 1.36; max loss $1,364")]
    rows += [L("15:55:00", "   %-5s close %s 200DMA %s RSI2 %s IBS %s [%s] -> %s"
               % ("QQQ", "600.10", "550.00", "45.0", "0.50", "live", "skip: RSI2 45.0 >= 10")),
             L("15:55:00", "=" * 96)]
    if condor:
        rows += [L("15:55:00", "-- condor sleeve (ENABLED): open 0"),
                 L("15:55:00", "   %-5s spot %s -> %s" % ("SPY", "697.56", "skip: VIX 17.00 below 18")),
                 L("15:55:00", "=" * 96)]
    if mode == "DRY RUN":
        rows.append(L("15:55:01", "DRY RUN: nothing sent. 0 exit(s) and 1 entry would be worked from 15:55 to 15:58 ET."))
    else:
        rows.append(L("15:55:20", "open SPY-20261106-P683/668: filled 1/1 @ 1.38"))
    return rows


def markers(jobs, rc=None):
    rc = rc or {}
    out = []
    for job, hms, n in jobs:
        for i in range(n):
            out.append(f"=== clive-job {job} | mode=shadow | {DAY} {hms} EDT | x.py ===")
            out.append(f"=== clive-job {job} exit {rc.get(job, 0)} | {DAY} {hms} EDT ===")
    return out


VM_SHORT_JOBS = [("short-open", "09:31:00", 1), ("short-cover-leftovers", "10:00:00", 3), ("short-halfday", "12:40:00", 1),
                 ("short-halfday-fallback", "12:50:00", 1), ("short-close", "15:40:00", 1), ("short-fallback", "15:50:00", 1)]
VM_OPT_JOBS = [("opt-sweep", "09:45:00", 1), ("opt-intraday", "10:00:00", 23), ("opt-halfday", "12:45:00", 1),
               ("opt-daily", "15:45:00", 1)]


def write_root(root, short_rows, opt_rows, short_markers=(), opt_markers=(), events=True, health=True,
               picks_json=None, kind="mac"):
    sb = root / "Stock Shorting Bot" / "logs"
    ob = root / "Options Bot" / "live" / "logs"
    sb.mkdir(parents=True)
    ob.mkdir(parents=True)
    (sb / "shortingbot.log").write_text("\n".join(short_rows) + "\n")
    (sb / "cron.log").write_text("\n".join(list(short_markers) + short_rows) + "\n")
    (ob / f"{DAY}.log").write_text("\n".join(opt_rows) + "\n")
    (ob / "cron.log").write_text("\n".join(list(opt_markers) + opt_rows +
                                           ([L("16:15:03", "sync: synced")] if kind == "mac" else [])) + "\n")
    if kind == "vm":
        (root / "events" / "logs").mkdir(parents=True)
        (root / "health" / "logs").mkdir(parents=True)
        if events:
            (root / "events" / "logs" / "sync.log").write_text("\n".join(markers([("events-sync", "07:00:00", 1)])) + "\n")
        if health:
            (root / "health" / "logs" / "check.log").write_text("\n".join(markers([("health-check", "09:00:00", 32)])) + "\n")
    if picks_json is not None:
        name = "next_open_picks.json" if kind == "mac" else "next_open_picks.dry_run.json"
        (sb / name).write_text(json.dumps(picks_json))
    return str(root)


def picks_file(tickers):
    return {"scan_date": DAY, "trade_date": "2026-10-28", "tickers": tickers, "picks": [{"ticker": t} for t in tickers]}


@pytest.fixture
def day(tmp_path):
    """A matching Mac day and VM shadow day; tests override pieces."""
    def make(vm_short=None, vm_opt=None, mac_short=None, mac_opt=None, rc=None, vm_picks=None, **kw):
        mac = write_root(tmp_path / "mac", mac_short or shorting_log("execute"), mac_opt or options_log("EXECUTE"),
                         picks_json=picks_file([p[0] for p in PICKS]), kind="mac")
        vm = write_root(tmp_path / "vm", vm_short or shorting_log("dry_run", fallback=True),
                        vm_opt or options_log("DRY RUN"), markers(VM_SHORT_JOBS, rc), markers(VM_OPT_JOBS, rc),
                        picks_json=picks_file(vm_picks or [p[0] for p in PICKS]), kind="vm", **kw)
        return mac, vm
    return make


def run(mac, vm, *extra):
    a = _args(*extra)
    m, v = sc.Side(mac, DAY, "mac"), sc.Side(vm, DAY, "vm")
    items = sc.compare(m, v, a)
    return items, sc.verdict(items, a.strict)


def _args(*extra):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-overlap", type=float, default=0.8)
    ap.add_argument("--min-universe-overlap", type=float, default=0.6)
    ap.add_argument("--qty-tol", type=float, default=0.10)
    ap.add_argument("--strike-tol", type=float, default=2.0)
    ap.add_argument("--strict", action="store_true")
    return ap.parse_args(list(extra))


def find(items, name):
    hits = [i for i in items if i["item"].startswith(name)]
    assert hits, f"no item {name!r} in {[i['item'] for i in items]}"
    return hits[0]


# ---------------------------------------------------------------------------------- tests
def test_matching_day_passes(day):
    mac, vm = day()
    items, ok = run(mac, vm)
    bad = [i for i in items if i["status"] in sc.FAILING]
    assert ok, bad
    assert find(items, "15:45 scan: picks for the next open")["status"] == sc.MATCH
    assert find(items, "09:31 planned shorts")["status"] == sc.MATCH
    assert find(items, "15:55 cover-all")["status"] == sc.MATCH
    assert find(items, "15:55 long sleeve buys")["status"] == sc.MATCH
    assert find(items, "options decision: SPY entry")["status"] == sc.MATCH
    assert find(items, "options decision: condor sleeve")["status"] == sc.MATCH
    assert find(items, "short-monitor")["status"] == sc.NA
    assert find(items, "short-close")["status"] == sc.MATCH
    assert find(items, "opt-intraday")["status"] == sc.MATCH


def test_vm_fallback_scan_does_not_replace_the_main_picks(day):
    """The VM's 15:50 dry fallback logs its own board/picks/long sleeve; the 15:40 job's ones count."""
    mac, vm = day()
    items, ok = run(mac, vm)
    p = find(items, "15:45 scan: picks for the next open")
    assert "ZZZ" not in p["vm"]
    assert "ZZZ" not in find(items, "15:55 long sleeve buys")["vm"]


def test_one_pick_different_is_tolerated(day):
    other = PICKS[:4] + [("KRRO", 12.05, 120, 15.0)]
    mac, vm = day(vm_short=shorting_log("dry_run", picks=other))
    items, ok = run(mac, vm)
    p = find(items, "15:45 scan: picks for the next open")
    assert p["status"] == sc.TOL and "4/5" in p["note"]
    assert ok


def test_picks_mostly_different_fail(day):
    other = PICKS[:2] + [("KRRO", 12.05, 120, 15.0), ("HPP", 10.0, 100, 15.0), ("VITL", 9.0, 100, 15.0)]
    mac, vm = day(vm_short=shorting_log("dry_run", picks=other))
    items, ok = run(mac, vm)
    assert find(items, "15:45 scan: picks for the next open")["status"] == sc.MISMATCH
    assert not ok
    # a looser threshold lets it through
    items, ok = run(mac, vm, "--min-overlap", "0.4")
    assert find(items, "15:45 scan: picks for the next open")["status"] == sc.TOL


def test_same_picks_other_order_is_tolerated(day):
    mac, vm = day(vm_short=shorting_log("dry_run", picks=list(reversed(PICKS))))
    items, ok = run(mac, vm)
    assert find(items, "15:45 scan: picks for the next open")["status"] == sc.TOL
    assert ok


def test_qty_outside_tolerance_fails(day):
    big = [(t, px, q * 2 if t == "AVX" else q, pct) for t, px, q, pct in PICKS]
    mac, vm = day(vm_short=shorting_log("dry_run", picks=big))
    items, ok = run(mac, vm)
    assert find(items, "15:45 scan: pick size")["status"] == sc.MISMATCH
    assert not ok


def test_vm_job_nonzero_exit_fails(day):
    mac, vm = day(rc={"short-close": 1})
    items, ok = run(mac, vm)
    j = find(items, "short-close")
    assert j["status"] == sc.MISMATCH and "rc 1" in j["vm"]
    assert not ok


def test_vm_missed_options_decision(day):
    mac, vm = day(vm_opt=[x for x in options_log("DRY RUN") if " 15:55:" not in x])
    items, ok = run(mac, vm)
    assert find(items, "options decision")["status"] == sc.MISSING
    assert not ok


def test_cover_race_vm_saw_less_is_tolerated(day):
    cover = {p[0]: -p[2] for p in PICKS if p[0] != "AVX"}
    mac, vm = day(vm_short=shorting_log("dry_run", fallback=True, cover=cover))
    items, ok = run(mac, vm)
    c = find(items, "15:55 cover-all")
    assert c["status"] == sc.TOL and "AVX" in c["note"]
    assert ok


def test_cover_vm_saw_more_fails(day):
    cover = {**{p[0]: -p[2] for p in PICKS}, "XYZ": -10}
    mac, vm = day(vm_short=shorting_log("dry_run", cover=cover))
    items, ok = run(mac, vm)
    assert find(items, "15:55 cover-all")["status"] == sc.MISMATCH
    assert not ok


def test_open_race_already_held_is_tolerated(day):
    plan = {p[0]: p[2] for p in PICKS if p[0] != "RCEL"}
    mac, vm = day(vm_short=shorting_log("dry_run", open_plan=plan, open_skips={"RCEL": "already held (-96)"}))
    items, ok = run(mac, vm)
    o = find(items, "09:31 planned shorts")
    assert o["status"] == sc.TOL and "RCEL" in o["note"]
    assert ok


def test_open_real_skip_difference_fails(day):
    plan = {p[0]: p[2] for p in PICKS if p[0] != "RCEL"}
    mac, vm = day(vm_short=shorting_log("dry_run", open_plan=plan,
                                        open_skips={"RCEL": "gapped up 30% over scan price $16.20"}))
    items, ok = run(mac, vm)
    assert find(items, "09:31 planned shorts")["status"] == sc.MISMATCH
    assert not ok


def test_vm_without_dry_picks_is_not_comparable(day):
    rows = [r for r in shorting_log("dry_run") if "open: " not in r and "plan SELL" not in r]
    rows.insert(3, L("09:31:02", "ALERT no_picks: 2026-10-27: no picks file at next_open_picks.json - nothing entered",
                     "WARNING"))
    mac, vm = day(vm_short=rows)
    items, ok = run(mac, vm)
    assert find(items, "09:31 picks read")["status"] == sc.NC
    assert ok
    items, ok = run(mac, vm, "--strict")
    assert not ok


def test_option_strike_one_step_off_is_tolerated(day):
    mac, vm = day(vm_opt=options_log("DRY RUN", legs=("SPY261106P00684000", "SPY261106P00669000")))
    items, ok = run(mac, vm)
    assert find(items, "options decision: SPY entry")["status"] == sc.TOL
    assert ok


def test_option_far_strikes_or_skip_fail(day):
    mac, vm = day(vm_opt=options_log("DRY RUN", legs=("SPY261106P00660000", "SPY261106P00645000")))
    items, ok = run(mac, vm)
    assert find(items, "options decision: SPY entry")["status"] == sc.MISMATCH
    mac2, vm2 = mac, write_root_replace(vm, options_log("DRY RUN", spy_action="skip: RSI2 12.0 >= 10"))
    items, ok = run(mac2, vm2)
    assert find(items, "options decision: SPY entry")["status"] == sc.MISMATCH
    assert not ok


def write_root_replace(root, opt_rows):
    p = os.path.join(root, "Options Bot", "live", "logs", f"{DAY}.log")
    with open(p, "w") as fh:
        fh.write("\n".join(opt_rows) + "\n")
    return root


def test_vm_missing_a_job_fails(day):
    mac, vm = day(events=False)
    items, ok = run(mac, vm)
    assert find(items, "events-sync")["status"] == sc.MISSING
    assert not ok


def test_no_markers_falls_back_to_log_lines(day, tmp_path):
    mac, vm = day()
    os.remove(os.path.join(vm, "Stock Shorting Bot", "logs", "cron.log"))
    os.remove(os.path.join(vm, "Options Bot", "live", "logs", "cron.log"))
    os.remove(os.path.join(vm, "events", "logs", "sync.log"))
    os.remove(os.path.join(vm, "health", "logs", "check.log"))
    items, ok = run(mac, vm)
    assert find(items, "short-close")["status"] == sc.MATCH
    assert "log lines" in find(items, "short-close")["vm"]


def test_market_holiday(tmp_path):
    rows = [L("09:31:01", f"=== daily open run {DAY} | execute | short_intraday ==="),
            L("09:31:02", "market closed today - nothing to enter")]
    mac = write_root(tmp_path / "mac", rows, [L("09:45:02", "market closed today (alpaca); nothing to do")])
    vm = write_root(tmp_path / "vm", [r.replace("execute", "dry_run") for r in rows],
                    [L("09:45:02", "market closed today (alpaca); nothing to do")], kind="vm")
    items, ok = run(mac, vm)
    assert ok and items[0]["status"] == sc.NA


def test_picks_json_compared_when_both_for_the_day(day):
    mac, vm = day(vm_picks=["AVX", "BNC", "NAKA", "ASST", "KRRO"])
    items, ok = run(mac, vm)
    assert find(items, "picks file for the next open")["status"] == sc.TOL


def test_cli_json_and_exit_codes(day, capsys):
    mac, vm = day()
    rc = sc.main(["--mac", mac, "--vm", vm, "--date", DAY, "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["result"] == "PASS" and out["items"]
    mac2, vm2 = mac, vm
    rc = sc.main(["--mac", mac2, "--vm", vm2, "--date", "2026-10-29"])
    assert rc == 2   # no lines for that day
    write_root_replace(vm, [x for x in options_log("DRY RUN") if " 15:55:" not in x])
    rc = sc.main(["--mac", mac, "--vm", vm, "--date", DAY])
    text = capsys.readouterr().out
    assert rc == 1 and "RESULT: FAIL" in text


def test_board_rows_both_formats():
    v2 = "%2d %-6s %7s %6s %5s %5s %4s %7s %-13s %5.1f  %s" % (1, "AVX", "10.96", "11.1", "1.4", "1.1", "0.3", "5.2M",
                                                               "no_news", 55.0, "SHORT 412 (19.0%)")
    v1 = " 9 ROLR     12.4   4.5   1.3  2.5 no_news        64.1  not shortable on Alpaca"
    m = sc.BOARD_ROW_RE.match(v2)
    assert m and m.group(2) == "AVX" and float(m.group(4)) == 55.0 and "SHORT 412" in m.group(5)
    m = sc.BOARD_ROW_RE.match(v1)
    assert m and m.group(2) == "ROLR" and m.group(5) == "not shortable on Alpaca"
    m = sc.BOARD_ROW_RE.match(" 6 AARD     13.8   1.1   3.2  3.9 unclear        42.9  ")
    assert m and m.group(2) == "AARD"


def test_parse_dict_and_occ():
    assert sc.parse_dict("{'ASST': -51, 'AVX': -412}") == {"ASST": -51.0, "AVX": -412.0}
    assert sc.parse_dict("{}") == {}
    assert sc.parse_dict("garbage") is None
    assert sc.occ("SPY261030P00683000") == ("SPY", "261030", "P", 683.0)
    assert sc.occ("nope") is None


def test_half_day_times(tmp_path):
    rows = [L("12:40:02", f"=== daily close run {DAY} | execute | short_intraday ==="),
            L("12:40:02", "half day: close 13:00 ET, scan 12:45, cover 12:55")]
    mac = write_root(tmp_path / "mac", rows, [])
    s = sc.Side(mac, DAY, "mac")
    t = sc.session_times(DAY, s)
    assert t["half"] and f"{t['cover']:%H:%M}" == "12:55" and f"{t['start']:%H:%M}" == "12:40"
    assert sc.close_header(s, t) is not None
