#!/usr/bin/env python3
"""Short-squeeze scanner. Run it, get a CSV.

    python3 run.py                    # top 10, full pipeline
    python3 run.py --top 25
    python3 run.py --min-price 5 --min-mcap 50000000
    python3 run.py --fast             # skip short volume + FTD
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from zoneinfo import ZoneInfo

from shortbot import (config, expectancy, ftd, report, scoring, shortdata,
                      shortvolume, tradingview)


def parse_args():
    p = argparse.ArgumentParser(description="Rank short-squeeze / exhaustion candidates.")
    p.add_argument("--top", type=int, default=config.TOP_N,
                   help=f"how many gainers to analyse (default {config.TOP_N})")
    p.add_argument("--min-price", type=float, default=config.MIN_PRICE,
                   help=f"minimum share price (default {config.MIN_PRICE})")
    p.add_argument("--min-mcap", type=float, default=config.MIN_MARKET_CAP,
                   help=f"minimum market cap (default {config.MIN_MARKET_CAP:,.0f})")
    p.add_argument("--max-price", type=float, default=None,
                   help="maximum share price - use this to actually bound a "
                        "penny-stock screen (default: no ceiling)")
    p.add_argument("--max-mcap", type=float, default=None,
                   help="maximum market cap (default: no ceiling)")
    p.add_argument("--outdir", default="output", help="CSV destination (default output/)")
    p.add_argument("--no-short-data", action="store_true",
                   help="skip the short-interest lookup")
    p.add_argument("--fast", action="store_true",
                   help="skip short volume and fails-to-deliver (blocks 3 and 4)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    config.MIN_PRICE, config.MIN_MARKET_CAP = args.min_price, args.min_mcap
    config.MAX_PRICE, config.MAX_MARKET_CAP = args.max_price, args.max_mcap
    ceiling = ""
    if args.max_price or args.max_mcap:
        bits = []
        if args.max_price:
            bits.append(f"price < ${args.max_price:,.2f}")
        if args.max_mcap:
            bits.append(f"mcap < ${args.max_mcap:,.0f}")
        ceiling = ", " + ", ".join(bits)

    scraped_at = dt.datetime.now(ZoneInfo(config.MARKET_TZ))
    print(f"[1/6] Gainers          TradingView | price > ${args.min_price:,.2f}, "
          f"mcap > ${args.min_mcap:,.0f}{ceiling}, top {args.top}")
    try:
        rows = tradingview.fetch_gainers(args.top)
    except Exception as e:
        print(f"      FAILED: {e}", file=sys.stderr)
        return 1
    if not rows:
        print("      Nothing passed the screen. Market closed, or filters too tight.")
        return 0
    print(f"      {len(rows)} passed: {', '.join(r['ticker'] for r in rows)}")

    tickers = [r["ticker"] for r in rows]

    def merge(step: str, label: str, fn, skip: bool, check: str | None = None):
        if skip:
            print(f"[{step}] {label:<16} SKIPPED")
            return
        print(f"[{step}] {label:<16} fetching...")
        try:
            data = fn()
            for r in rows:
                r.update(data.get(r["ticker"], {}))
            if check:
                got = sum(1 for r in rows if r.get(check) is not None)
                print(f"      {got}/{len(rows)} resolved")
        except Exception as e:
            print(f"      WARNING: unavailable ({e}); continuing", file=sys.stderr)

    merge("2/6", "Short interest", lambda: shortdata.enrich(tickers),
          args.no_short_data, "short_pct_float")
    merge("3/6", "Short volume", lambda: shortvolume.fetch(tickers),
          args.fast, "short_vol_ratio_avg")
    merge("4/6", "Fails-to-deliver", lambda: ftd.fetch(tickers),
          args.fast, "ftd_latest_date")

    print("[5/6] Scoring          squeeze + exhaustion + archetype")
    scored = scoring.score_all(rows)

    expectancy.enrich(scored)

    print("[6/6] Writing CSV")
    path = report.write_csv(scored, args.outdir, scraped_at)
    report.print_summary(scored)
    print(f"\nCSV: {path}")
    print(f"     {args.outdir}/latest.csv  (always the most recent run)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
