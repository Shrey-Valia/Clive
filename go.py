#!/usr/bin/env python3
"""One command for the manual daily routine.

    python3 go.py           # settle what matured, scan, log today's entries
    python3 go.py --status  # just show where things stand

Order matters: settle first so yesterday's exits are booked before today's
entries go on. Safe to run more than once a day - settling is idempotent and
track.py will not double-log the same scan.

Run it once between 15:45 and 16:00 ET to capture entries at close-ish
prices, then again after 16:05 to settle. Running only after the close still
works; you just log entries at the official close rather than near it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def run(args: list[str], label: str) -> None:
    print(f"\n{'='*70}\n  {label}\n{'='*70}")
    subprocess.run([sys.executable, *args], check=False)


def main() -> int:
    p = argparse.ArgumentParser(description="Manual daily routine.")
    p.add_argument("--status", action="store_true", help="show state only")
    a = p.parse_args()

    now = dt.datetime.now(ET)
    print(f"ET now {now:%Y-%m-%d %H:%M:%S %Z}")

    if a.status:
        run(["track.py", "--show"], "CURRENT LOG")
        return 0

    # 1. Rebuild the real track record from Alpaca's actual fills. MOC
    #    orders fill at 16:00, after daily.py has exited, so fill prices can
    #    only be learned afterwards from the broker.
    print(f"\n{'='*70}\n  SYNC journal from broker fills\n{'='*70}")
    try:
        from shortbot.journal import sync_from_broker

        n = sync_from_broker(paper=True)
        print(f"  {n} completed round trip(s) rebuilt from actual fills"
              if n else "  no completed round trips at the broker yet")
    except Exception as e:
        print(f"  skipped: {str(e)[:100]}")

    # 2. Book any paper-log exits whose session has finished.
    run(["track.py", "--settle"], "SETTLE matured exits")

    # 3. Scan.
    run(["run.py"], "SCAN")

    # 4. Log today's would-be entries.
    run(["track.py"], "LOG today's entries")

    after_close = (now.hour, now.minute) >= (16, 5)
    if not after_close:
        print(f"\nNote: it is {now:%H:%M} ET. Entries are marked PROVISIONAL "
              "until the close prints.")
        print("Run `python3 go.py` again after 16:05 ET to re-mark them at the "
              "official close.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
