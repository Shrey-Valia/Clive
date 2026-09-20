"""Track record. Without this, running for weeks produces nothing to learn from.

Two files in output/:

  open_trades.json  what went on today, with the full scan context that
                    justified it - scores, conviction, archetype, and the
                    base rate the scanner predicted at entry
  journal.csv       one row per COMPLETED round trip, pairing that entry
                    context against what actually happened

The point of the pairing is `exp_avg_pct` vs `pnl_pct`: predicted base rate
against realised result, trade by trade. That is the number that tells you
whether any of this works.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os

OUT = "output"
OPEN_FILE = os.path.join(OUT, "open_trades.json")
JOURNAL = os.path.join(OUT, "journal.csv")

FIELDS = [
    "entry_date", "exit_date", "ticker", "side", "qty",
    "entry_price", "exit_price", "pnl", "pnl_pct",
    "conviction", "call", "archetype", "long_score", "short_score",
    "exp_avg_pct", "exp_win_pct", "exp_n",
    "short_pct_float", "days_to_cover", "change_pct_at_entry",
    "close_in_range_at_entry", "overnight_risk", "flags",
]


def record_entries(orders, scored: list[dict]) -> None:
    """Snapshot what we entered and why, for pairing at exit."""
    by_ticker = {r["ticker"]: r for r in scored}
    payload = {"entry_date": dt.date.today().isoformat(), "trades": []}

    for o in orders:
        r = by_ticker.get(o.ticker, {})
        close, sma = r.get("close"), r.get("SMA20")
        hi, lo = r.get("high"), r.get("low")
        payload["trades"].append({
            "ticker": o.ticker, "side": o.side, "qty": o.qty,
            "entry_price": o.price,
            "conviction": r.get("conviction"), "call": r.get("call"),
            "archetype": r.get("archetype"),
            "long_score": r.get("long_score"), "short_score": r.get("short_score"),
            "exp_avg_pct": r.get("exp_avg_pct"), "exp_win_pct": r.get("exp_win_pct"),
            "exp_n": r.get("exp_n"),
            "short_pct_float": r.get("short_pct_float"),
            "days_to_cover": r.get("days_to_cover"),
            "change_pct_at_entry": round(r["change"], 2) if r.get("change") else None,
            "close_in_range_at_entry": (
                round(100 * (close - lo) / (hi - lo), 1)
                if (hi and lo and close and hi > lo) else None),
            "overnight_risk": r.get("overnight_risk"),
            "flags": r.get("flags"),
        })

    os.makedirs(OUT, exist_ok=True)
    with open(OPEN_FILE, "w") as fh:
        json.dump(payload, fh, indent=2)


def record_exits(closed: list[dict]) -> int:
    """Pair today's closes against the saved entry context. Returns rows written."""
    if not os.path.exists(OPEN_FILE):
        return 0
    try:
        prior = json.load(open(OPEN_FILE))
    except Exception:
        return 0

    ctx = {t["ticker"]: t for t in prior.get("trades", [])}
    today = dt.date.today().isoformat()
    rows = []

    for pos in closed:
        t = pos["ticker"]
        c = ctx.get(t, {})
        entry = c.get("entry_price")
        mv, qty = pos.get("market_value"), abs(pos.get("qty") or 0)
        exit_px = round(abs(mv) / qty, 4) if (mv and qty) else None
        pnl = pos.get("unrealized_pl")

        pnl_pct = None
        if entry and exit_px:
            raw = 100 * (exit_px / entry - 1)
            # A short profits when the price falls.
            pnl_pct = round(raw if c.get("side") != "sell" else -raw, 2)

        rows.append({
            "entry_date": prior.get("entry_date"), "exit_date": today,
            "ticker": t, "side": c.get("side", pos.get("side")),
            "qty": qty, "entry_price": entry, "exit_price": exit_px,
            "pnl": round(pnl, 2) if pnl is not None else None,
            "pnl_pct": pnl_pct,
            **{k: c.get(k) for k in FIELDS
               if k not in ("entry_date", "exit_date", "ticker", "side", "qty",
                            "entry_price", "exit_price", "pnl", "pnl_pct")},
        })

    if not rows:
        return 0
    os.makedirs(OUT, exist_ok=True)
    new = not os.path.exists(JOURNAL)
    with open(JOURNAL, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)
    return len(rows)


# --------------------------------------------------------------- broker sync
def sync_from_broker(paper: bool = True) -> int:
    """Rebuild completed round trips from Alpaca's ACTUAL fills.

    Necessary because market-on-close orders fill at 16:00 while the script
    that submitted them exited at 15:45 - it never sees a fill price. The
    earlier approach read `unrealized_pl` off the position at close time and
    got it wrong: it reported -$0.16 on a trade that actually lost $2.42
    (bought 4.03, sold 4.01).

    Filled orders are ground truth. This pairs them per ticker in time order
    and writes one row per completed round trip, replacing any rows it
    already covers so repeated runs converge instead of duplicating.
    """
    from . import broker

    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        client = broker._client(paper)
        orders = client.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, limit=500))
    except Exception as e:
        print(f"  broker sync failed: {str(e)[:120]}")
        return 0

    fills = sorted(
        ({"ticker": o.symbol,
          "side": "buy" if "BUY" in str(o.side).upper() else "sell",
          "qty": int(float(o.filled_qty or 0)),
          "price": float(o.filled_avg_price),
          "at": o.filled_at}
         for o in orders
         if o.filled_at and o.filled_avg_price and float(o.filled_qty or 0) > 0),
        key=lambda f: f["at"],
    )

    # Walk each ticker's fills, pairing opens against the closes that follow.
    ctx = {}
    if os.path.exists(OPEN_FILE):
        try:
            ctx = {t["ticker"]: t
                   for t in json.load(open(OPEN_FILE)).get("trades", [])}
        except Exception:
            ctx = {}

    books: dict[str, list] = {}
    rows = []
    for f in fills:
        book = books.setdefault(f["ticker"], [])
        if book and book[0]["side"] != f["side"]:
            # Closing fill - pair against the oldest open lot.
            opened = book.pop(0)
            qty = min(opened["qty"], f["qty"])
            entry, exit_px = opened["price"], f["price"]
            raw = 100 * (exit_px / entry - 1)
            pnl_pct = raw if opened["side"] == "buy" else -raw
            c = ctx.get(f["ticker"], {})
            rows.append({
                "entry_date": opened["at"].date().isoformat(),
                "exit_date": f["at"].date().isoformat(),
                "ticker": f["ticker"],
                "side": opened["side"], "qty": qty,
                "entry_price": round(entry, 4), "exit_price": round(exit_px, 4),
                "pnl": round(qty * (exit_px - entry) *
                             (1 if opened["side"] == "buy" else -1), 2),
                "pnl_pct": round(pnl_pct, 2),
                **{k: c.get(k) for k in FIELDS
                   if k not in ("entry_date", "exit_date", "ticker", "side",
                                "qty", "entry_price", "exit_price", "pnl",
                                "pnl_pct")},
            })
        else:
            book.append(f)

    if not rows:
        return 0
    os.makedirs(OUT, exist_ok=True)
    with open(JOURNAL, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)
