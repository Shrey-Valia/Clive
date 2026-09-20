"""CSV output + a readable console summary."""
from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

import pandas as pd

from . import config

# Decision-relevant fields first, then each analysis block, diagnostics last.
COLUMNS = [
    # verdict
    "rank", "ticker", "call", "conviction", "overnight_risk", "company", "archetype",
    "long_score", "short_score", "score_margin",
    # measured historical base rate for this setup + direction
    "exp_n", "exp_avg_pct", "exp_win_pct", "exp_worst1_pct", "exp_basis",
    # block 5 - price action
    "price", "change_pct", "close_in_range_pct", "close_vs_vwap_pct",
    "day_high", "day_low", "market_cap", "volume", "rel_volume", "rsi",
    "pct_above_sma20", "pct_off_52w_high",
    # block 1 - short interest
    "short_pct_float", "si_tier", "days_to_cover", "dtc_tier",
    "shares_short", "float_shares",
    # block 2 - borrow
    "borrow_fee_pct", "borrow_tier", "shares_available",
    # block 3 - short volume
    "short_vol_ratio_latest", "short_vol_ratio_avg", "short_vol_tier",
    "short_vol_days_above_50", "short_vol_sessions",
    # block 4 - fails to deliver
    "ftd_days", "ftd_max_shares", "ftd_total_shares", "ftd_max_pct_float",
    "ftd_latest_date", "ftd_periods",
    # provenance
    "short_interest_date", "short_data_age_days", "move_since_si_pct",
    "short_data_source", "squeeze_data_coverage_pct", "exhaustion_data_coverage_pct", "flags",
    "sector", "exchange",
    # normalised components (0-1) behind every score
    "c_close_in_range", "c_rel_volume", "c_float_tightness", "c_short_pct_float",
    "c_close_vs_vwap", "c_spike", "c_days_to_cover", "c_borrow_fee",
    "c_close_weak_in_range", "c_spike_extension", "c_sma_extension",
    "c_low_squeeze_risk", "c_rvol_blowoff", "c_rsi_hot", "c_close_below_vwap",
    "scraped_at",
]

PASSTHROUGH = [
    "rank", "ticker", "call", "conviction", "overnight_risk", "company", "archetype",
    "long_score", "short_score", "score_margin",
    "exp_n", "exp_avg_pct", "exp_win_pct", "exp_worst1_pct", "exp_basis",
    "move_since_si_pct",
    "short_pct_float", "si_tier", "days_to_cover",
    "dtc_tier", "shares_short", "float_shares", "borrow_fee_pct",
    "borrow_tier", "shares_available", "short_vol_ratio_latest",
    "short_vol_ratio_avg", "short_vol_tier", "short_vol_days_above_50",
    "short_vol_sessions", "ftd_days", "ftd_max_shares", "ftd_total_shares",
    "ftd_latest_date", "ftd_periods", "short_interest_date",
    "short_data_age_days", "short_data_source", "long_data_coverage_pct",
    "short_data_coverage_pct", "flags", "sector", "exchange",
]


def _shape(row: dict, stamp: str) -> dict:
    close, sma20 = row.get("close"), row.get("SMA20")
    hi52 = row.get("price_52_week_high")
    float_sh = row.get("float_shares") or row.get("float_shares_outstanding")
    ftd_max = row.get("ftd_max_shares")

    rec = {k: row.get(k) for k in PASSTHROUGH}
    rec.update({
        "price": round(close, 2) if close else None,
        "change_pct": round(row["change"], 2) if row.get("change") else None,
        "market_cap": round(row["market_cap_basic"]) if row.get("market_cap_basic") else None,
        "volume": round(row["volume"]) if row.get("volume") else None,
        "rel_volume": round(row["relative_volume_10d_calc"], 2) if row.get("relative_volume_10d_calc") else None,
        "rsi": round(row["RSI"], 1) if row.get("RSI") else None,
        "pct_above_sma20": round((close / sma20 - 1) * 100, 2) if close and sma20 else None,
        "pct_off_52w_high": round((close / hi52 - 1) * 100, 2) if close and hi52 else None,
        "day_high": round(row["high"], 2) if row.get("high") else None,
        "day_low": round(row["low"], 2) if row.get("low") else None,
        "close_in_range_pct": round(100 * (close - row["low"]) / (row["high"] - row["low"]), 1)
            if (row.get("high") and row.get("low") and close and row["high"] > row["low"]) else None,
        "close_vs_vwap_pct": round(100 * (close / row["VWAP"] - 1), 2)
            if (close and row.get("VWAP")) else None,
        "float_shares": float_sh,
        "ftd_max_pct_float": round(100 * ftd_max / float_sh, 3) if (ftd_max and float_sh) else None,
        "scraped_at": stamp,
    })
    rec.update({k: row.get(k) for k in COLUMNS if k.startswith("c_")})
    return rec


def write_csv(rows: list[dict], outdir: str = "output",
              scraped_at: dt.datetime | None = None) -> str:
    """Write the scan, filed under the date and labelled with the SCRAPE time.

    `scraped_at` is when the data was pulled, not when the file was written.
    Those differ by however long enrichment took, and the scrape time is the
    one that describes the market state the scores refer to.
    """
    scraped_at = scraped_at or dt.datetime.now(ZoneInfo(config.MARKET_TZ))
    if scraped_at.tzinfo is None:
        scraped_at = scraped_at.replace(tzinfo=ZoneInfo(config.MARKET_TZ))

    stamp = scraped_at.strftime("%Y-%m-%d %H:%M:%S %Z")
    df = pd.DataFrame([_shape(r, stamp) for r in rows], columns=COLUMNS)

    day_dir = os.path.join(outdir, "scans", scraped_at.strftime("%Y-%m-%d"))
    os.makedirs(day_dir, exist_ok=True)

    path = os.path.join(day_dir, f"scan_{scraped_at:%H%M}ET.csv")
    df.to_csv(path, index=False)
    df.to_csv(os.path.join(outdir, "latest.csv"), index=False)
    return path


def print_summary(rows: list[dict]) -> None:
    def num(v, w, p=1):
        return f"{v:>{w}.{p}f}" if isinstance(v, (int, float)) else f"{'-':>{w}}"

    print()
    print(f"{'#':<3}{'TICKER':<7}{'CALL':<6}{'CONV':<8}{'O/NRISK':<10}{'PRICE':>8}{'CHG%':>7}{'CLOSE@':>8}"
          f"{'SI%':>6}{'DTC':>6}{'BORROW':>8}{'SVOL%':>7}{'FTD/FLT':>8}"
          f"{'SINCE_SI':>9}  {'LONG':>6}{'SHORT':>7}  {'EXPECTANCY (measured)':<26}ARCHETYPE")
    print("-" * 175)
    for r in rows:
        hi, lo, cl = r.get("high"), r.get("low"), r.get("close")
        cir = (100 * (cl - lo) / (hi - lo)) if (hi and lo and cl and hi > lo) else None
        if r.get("exp_n"):
            exp = (f"{r['exp_avg_pct']:+.2f}%/trade {r['exp_win_pct']:.0f}%win "
                   f"n={r['exp_n']}")
        else:
            exp = "-"
        ftd_pct = None
        fm = r.get("ftd_max_shares")
        fl = r.get("float_shares") or r.get("float_shares_outstanding")
        if fm and fl:
            ftd_pct = 100 * fm / fl
        print(
            f"{r['rank']:<3}{r['ticker']:<7}{r.get('call', ''):<6}"
            f"{r.get('conviction', ''):<8}{r.get('overnight_risk') or '-':<10}"
            f"{num(r.get('close'), 8, 2)}{num(r.get('change'), 7, 1)}{num(cir, 8, 0)}"
            f"{num(r.get('short_pct_float'), 6, 1)}{num(r.get('days_to_cover'), 6, 1)}"
            f"{num(r.get('borrow_fee_pct'), 8, 1)}"
            f"{num(r.get('short_vol_ratio_avg'), 7, 1)}{num(ftd_pct, 8, 2)}{num(r.get('move_since_si_pct'), 9, 1)}  "
            f"{r['long_score']:>6}{r['short_score']:>7}  {exp:<26}{r.get('archetype', '')}"
        )
    print("-" * 175)

    # Lead with the actionable split, then the interaction read.
    for call in ("LONG", "SHORT", "PASS"):
        picks = [r for r in rows if r.get("call") == call]
        if not picks:
            continue
        for level in ("HIGH", "MEDIUM", "LOW", "NONE"):
            names = [r["ticker"] for r in picks if r.get("conviction") == level]
            if names:
                print(f"  {call:<6} {level:<7} {', '.join(names)}")

    # A board dominated by one sector is usually one catalyst hitting a whole
    # comp set, not N independent squeeze setups. Short interest did not
    # cause that move and is largely incidental to it.
    sectors: dict[str, list[str]] = {}
    for r in rows:
        if r.get("sector"):
            sectors.setdefault(r["sector"], []).append(r["ticker"])
    if sectors:
        top_sector, members = max(sectors.items(), key=lambda kv: len(kv[1]))
        if len(members) / len(rows) >= config.SECTOR_CONCENTRATION_WARN:
            print()
            print(f"  !! {len(members)}/{len(rows)} of this board is {top_sector}: "
                  f"{', '.join(members)}")
            print("     Likely one sector catalyst, not independent setups. "
                  "Short interest did not drive this move.")

    print()
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(r.get("archetype", "?"), []).append(r["ticker"])
    for arch, tickers in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {arch:<20} {', '.join(tickers)}")
