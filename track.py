#!/usr/bin/env python3
"""Manual paper tracking - log what the scan WOULD have entered, then settle it.

    python3 track.py                    # log today's entries from latest.csv
    python3 track.py --settle           # fill exit prices for matured rows
    python3 track.py --show             # current log

Timing. Two separate runs, and they are not interchangeable:

  ~15:50 ET, BEFORE the close   log tomorrow's names (you must pick before
                                the bell; prices are provisional)
  after 16:00 ET                --settle: re-mark those entries to the
                                official close, and close out matured rows

Entry and exit are both marked at the official close. That matches the
backtest exactly (close to close) and is within pennies of a 15:55 market
fill. Marking entry at 15:50 and exit at 16:00 would bake a ten-minute
asymmetry into every trade, so --settle normalises the entry once the
official close exists.

Two books are logged from every scan:

  REAL    the HIGH-conviction names your rule actually trades
  SHADOW  every other name, same 5% sizing, never traded

The shadow book exists because one HIGH name a day needs ~6 weeks to reach
30 trades, and without the other tiers you cannot tell whether HIGH is
actually better than LOW - only that HIGH made or lost money. Tracking all
ten gets you there in days and answers the harder question.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import warnings

import pandas as pd

# yfinance logs a "possibly delisted" error whenever a window has no bars,
# which is the normal case here - we routinely ask for a session that has
# not printed yet.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

from shortbot import config

LOG = os.path.join("output", "paper_log.csv")
SCAN = os.path.join("output", "latest.csv")

FIELDS = [
    "entry_date", "ticker", "book", "call", "conviction", "qty",
    "entry_price", "notional", "exit_date", "exit_price", "pnl", "pnl_pct",
    "status", "entry_mark", "exp_avg_pct", "exp_win_pct", "long_score", "short_score",
    "score_margin", "archetype", "overnight_risk",
    "change_pct_at_entry", "close_in_range_at_entry",
    "short_pct_float", "days_to_cover", "flags", "scraped_at",
]


def _session_finished(date: dt.date) -> bool:
    """True once `date`'s regular session has printed a final close.

    yfinance returns the LIVE price as the Close of an unfinished daily bar,
    so settling before the bell marks the exit at whatever moment the command
    happened to run - the result would change every time you re-ran it.
    """
    from zoneinfo import ZoneInfo

    now = dt.datetime.now(ZoneInfo("America/New_York"))
    if date < now.date():
        return True
    if date > now.date():
        return False
    # Today: wait for the 16:00 bell plus a few minutes for the print.
    return (now.hour, now.minute) >= (16, 5)


def _close_on_or_after(ticker: str, date: dt.date) -> tuple[str, float] | None:
    """First FINAL official close on or after `date`. Skips weekends/holidays."""
    import yfinance as yf

    try:
        h = yf.Ticker(ticker).history(
            start=date.isoformat(),
            end=(date + dt.timedelta(days=8)).isoformat(),
        )
        h = h.dropna(subset=["Close"])
        if h.empty:
            return None
        got = h.index[0].date()
        if not _session_finished(got):
            return None
        return got.isoformat(), round(float(h["Close"].iloc[0]), 4)
    except Exception:
        return None


def log_entries(equity: float) -> int:
    if not os.path.exists(SCAN):
        print(f"No scan at {SCAN}. Run `python3 run.py` first.")
        return 0

    scan = pd.read_csv(SCAN)
    existing = pd.read_csv(LOG) if os.path.exists(LOG) else pd.DataFrame(columns=FIELDS)

    per_trade = equity * config.RISK_PER_TRADE_PCT / 100.0
    rows = []

    for _, r in scan.iterrows():
        if r.get("call") not in ("LONG", "SHORT"):
            continue
        entry_date = str(r["scraped_at"])[:10]

        # Idempotent: re-running the same scan does not duplicate rows.
        if not existing.empty and (
            (existing.entry_date == entry_date) & (existing.ticker == r["ticker"])
        ).any():
            continue

        price = r.get("price")
        if not price or price <= 0:
            continue
        qty = int(per_trade // price)
        if qty < 1:
            continue

        rows.append({
            "entry_date": entry_date, "ticker": r["ticker"],
            "book": "REAL" if r.get("conviction") == config.EXECUTE_CONVICTION else "SHADOW",
            "call": r["call"], "conviction": r.get("conviction"),
            "qty": qty, "entry_price": price, "notional": round(qty * price, 2),
            "exit_date": None, "exit_price": None, "pnl": None, "pnl_pct": None,
            "status": "OPEN", "entry_mark": "PROVISIONAL",
            **{k: r.get(k) for k in (
                "exp_avg_pct", "exp_win_pct", "long_score", "short_score",
                "score_margin", "archetype", "overnight_risk",
                "short_pct_float", "days_to_cover", "flags", "scraped_at")},
            "change_pct_at_entry": r.get("change_pct"),
            "close_in_range_at_entry": r.get("close_in_range_pct"),
        })

    if not rows:
        print("Nothing new to log (already recorded, or no valid calls).")
        return 0

    fresh = pd.DataFrame(rows)
    out = fresh if existing.empty else pd.concat([existing, fresh], ignore_index=True)
    os.makedirs("output", exist_ok=True)
    out.to_csv(LOG, index=False, columns=FIELDS)

    real = sum(1 for r in rows if r["book"] == "REAL")
    print(f"Logged {len(rows)} entries ({real} REAL, {len(rows)-real} SHADOW) "
          f"-> {LOG}")
    return len(rows)


def _official_close(ticker: str, date: dt.date) -> float | None:
    """That exact session's official close, or None if it has not printed."""
    got = _close_on_or_after(ticker, date)
    if got is None:
        return None
    on, px = got
    return px if on == date.isoformat() else None


def settle() -> int:
    if not os.path.exists(LOG):
        print("No log yet.")
        return 0
    d = pd.read_csv(LOG)
    if "entry_mark" not in d.columns:
        d["entry_mark"] = "PROVISIONAL"

    today = dt.date.today()

    # Step 1 - re-mark provisional entries to that session's official close,
    # so entry and exit are measured the same way.
    remarked = 0
    for i, r in d[d.entry_mark == "PROVISIONAL"].iterrows():
        entry = dt.date.fromisoformat(str(r.entry_date))
        px = _official_close(r.ticker, entry)
        if px is None:
            continue
        if abs(px - float(r.entry_price)) > 0.0001:
            d.at[i, "entry_price"] = px
            d.at[i, "notional"] = round(r.qty * px, 2)
            remarked += 1
        d.at[i, "entry_mark"] = "OFFICIAL_CLOSE"
    if remarked:
        print(f"Re-marked {remarked} entry price(s) to the official close.")

    # Step 2 - close out anything whose next session has printed.
    n = 0
    for i, r in d[d.status == "OPEN"].iterrows():
        entry = dt.date.fromisoformat(str(r.entry_date))
        got = _close_on_or_after(r.ticker, entry + dt.timedelta(days=1))
        if got is None:
            continue
        exit_date, exit_px = got
        if exit_date == str(r.entry_date):
            continue
        entry_px = float(d.at[i, "entry_price"])
        raw = 100 * (exit_px / entry_px - 1)
        pnl_pct = raw if r.call == "LONG" else -raw
        d.at[i, "exit_date"] = exit_date
        d.at[i, "exit_price"] = exit_px
        d.at[i, "pnl_pct"] = round(pnl_pct, 2)
        d.at[i, "pnl"] = round(float(d.at[i, "notional"]) * pnl_pct / 100, 2)
        d.at[i, "status"] = "CLOSED"
        n += 1

    d.to_csv(LOG, index=False, columns=FIELDS)
    print(f"Settled {n} row(s)." if n
          else "Nothing matured yet - exits need the next session's close.")
    return n


def show() -> None:
    if not os.path.exists(LOG):
        print("No log yet. Run `python3 track.py`.")
        return
    d = pd.read_csv(LOG)
    cols = ["entry_date", "ticker", "book", "call", "conviction", "qty",
            "entry_price", "exit_price", "pnl", "pnl_pct", "exp_avg_pct",
            "entry_mark", "status"]
    print(d[cols].to_string(index=False))

    done = d[d.status == "CLOSED"]
    if done.empty:
        print(f"\n{len(d)} open, none settled yet.")
        return
    print()
    for book in ("REAL", "SHADOW"):
        g = done[done.book == book]
        if g.empty:
            continue
        print(f"{book:<7} n={len(g):<4} total ${g.pnl.sum():+,.2f}  "
              f"avg {g.pnl_pct.mean():+.2f}%  win {100*(g.pnl>0).mean():.1f}%")
    if len(done) >= 5:
        print(f"\npredicted {done.exp_avg_pct.mean():+.2f}%  vs  "
              f"realised {done.pnl_pct.mean():+.2f}%")


def main() -> int:
    p = argparse.ArgumentParser(description="Manual paper tracking.")
    p.add_argument("--settle", action="store_true", help="fill matured exits")
    p.add_argument("--show", action="store_true", help="print the log")
    p.add_argument("--equity", type=float, default=10000.0,
                   help="account size for sizing (default 10000)")
    a = p.parse_args()

    if a.show:
        show()
    elif a.settle:
        settle()
        show()
    else:
        log_entries(a.equity)
        show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
