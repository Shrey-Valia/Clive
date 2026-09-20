"""Alpaca execution for the 24-hour hold.

Credentials load from `.env` in the project root, or from the environment if
already exported. `.env` is gitignored and chmod 600.

    APCA_API_KEY_ID=...
    APCA_API_SECRET_KEY=...
    APCA_PAPER=1

Paper trading is the default and `--live` is required to change that.

DRY RUN IS THE DEFAULT everywhere in this module. Nothing reaches the broker
unless the caller explicitly passes live=True, which `daily.py` only does
when given --execute.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from . import config

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


class BrokerError(RuntimeError):
    pass


@dataclass
class Order:
    ticker: str
    side: str          # "buy" | "sell" (sell = short)
    qty: int
    notional: float
    price: float
    conviction: str
    expectancy: float | None


def load_env(path: str = ".env") -> None:
    """Read .env into os.environ without clobbering anything already set."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _client(paper: bool = True):
    load_env()
    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise BrokerError(
            "APCA_API_KEY_ID / APCA_API_SECRET_KEY not found in .env or the "
            "environment."
        )
    from alpaca.trading.client import TradingClient

    return TradingClient(key, secret, paper=paper)


def account(paper: bool = True) -> dict:
    a = _client(paper).get_account()
    return {
        "equity": float(a.equity),
        "cash": float(a.cash),
        "buying_power": float(a.buying_power),
        "shorting_enabled": bool(a.shorting_enabled),
        "blocked": bool(a.trading_blocked or a.account_blocked),
        "status": str(a.status),
    }


def open_positions(paper: bool = True) -> list[dict]:
    return [
        {"ticker": p.symbol, "qty": int(float(p.qty)), "side": str(p.side),
         "unrealized_pl": float(p.unrealized_pl),
         "market_value": float(p.market_value)}
        for p in _client(paper).get_all_positions()
    ]


def cancel_open_orders(paper: bool = True, live: bool = False) -> list[str]:
    """Kill any unfilled orders before doing anything else.

    A market order submitted after the bell sits in NEW until the next open
    and fills there - wrong time, wrong price, and invisible unless you go
    looking. Always clear these first.
    """
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        client = _client(paper)
        stale = client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN,
                                                   limit=100))
    except Exception:
        return []
    ids = [f"{o.symbol} {o.side} {o.qty}" for o in stale]
    if live:
        for o in stale:
            try:
                client.cancel_order_by_id(o.id)
            except Exception:
                pass
    return ids


def close_all(paper: bool = True, live: bool = False) -> list[dict]:
    """Flatten everything. Called at 3:55pm before new entries go on."""
    held = open_positions(paper)
    if not live:
        return held
    _client(paper).close_all_positions(cancel_orders=True)
    return held


def shortable(tickers: list[str], paper: bool = True) -> dict[str, bool]:
    """Which of these can actually be sold short on this account.

    Alpaca rejects a short on a non-shortable asset at submit time, which is
    too late - the order is already gone and the rest of the batch has moved
    on. Checking first turns that into a clean skip.
    """
    try:
        client = _client(paper)
    except BrokerError:
        return {}
    out = {}
    for t in tickers:
        try:
            a = client.get_asset(t)
            out[t] = bool(a.shortable and a.easy_to_borrow is not False)
        except Exception:
            out[t] = False
    return out


def plan(rows: list[dict], equity: float,
         shortable_map: dict[str, bool] | None = None) -> tuple[list[Order], list[str]]:
    """Size the HIGH-conviction names. Returns (orders, skipped_reasons).

    Sizing is `RISK_PER_TRADE_PCT` of equity as NOTIONAL, not stop-based
    risk - this strategy has no stop. The exit is time-based at ~3:55pm the
    next day, so between entry and exit the downside is whatever the gap
    does. There is no price at which the position self-closes.
    """
    orders, skipped = [], []
    per_trade = equity * (config.RISK_PER_TRADE_PCT / 100.0)

    for r in rows:
        t = r["ticker"]
        if r.get("conviction") != config.EXECUTE_CONVICTION:
            continue
        if r.get("call") not in ("LONG", "SHORT"):
            skipped.append(f"{t}: call is {r.get('call')}")
            continue
        price = r.get("close")
        if not price or price <= 0:
            skipped.append(f"{t}: no price")
            continue
        if (r["call"] == "SHORT" and shortable_map is not None
                and not shortable_map.get(t, False)):
            skipped.append(f"{t}: not shortable on this account")
            continue
        qty = int(per_trade // price)
        if qty < 1:
            skipped.append(f"{t}: ${per_trade:,.0f} buys <1 share at ${price:,.2f}")
            continue
        orders.append(Order(
            ticker=t,
            side="buy" if r["call"] == "LONG" else "sell",
            qty=qty,
            notional=round(qty * price, 2),
            price=price,
            conviction=r["conviction"],
            expectancy=r.get("exp_avg_pct"),
        ))
    return orders, skipped


def current_signed(paper: bool = True) -> dict[str, int]:
    """{ticker: signed qty} - positive long, negative short."""
    out = {}
    for p in open_positions(paper):
        q = abs(p["qty"])
        out[p["ticker"]] = q if "LONG" in p["side"].upper() else -q
    return out


def reconcile(target: list[Order], paper: bool = True) -> list[Order]:
    """Net current holdings against today's target into a single order set.

    Closing everything and then re-entering produces two orders per repeated
    name, and Alpaca rejects the second as a wash trade. Netting instead
    means a name held two days running generates NO order at all, and a name
    being swapped generates exactly one.
    """
    current = current_signed(paper)
    want = {o.ticker: (o.qty if o.side == "buy" else -o.qty) for o in target}
    meta = {o.ticker: o for o in target}

    out: list[Order] = []
    for t in set(current) | set(want):
        delta = want.get(t, 0) - current.get(t, 0)
        if delta == 0:
            continue
        ref = meta.get(t)
        price = ref.price if ref else 0.0
        out.append(Order(
            ticker=t,
            side="buy" if delta > 0 else "sell",
            qty=abs(delta),
            notional=round(abs(delta) * price, 2),
            price=price,
            conviction=ref.conviction if ref else "EXIT",
            expectancy=ref.expectancy if ref else None,
        ))
    return out


def submit(orders: list[Order], paper: bool = True, live: bool = False) -> list[dict]:
    """Submit as market-on-close so fills land at the official close."""
    if not live:
        return [{"ticker": o.ticker, "status": "DRY_RUN", "side": o.side,
                 "qty": o.qty} for o in orders]

    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    client, results = _client(paper), []
    for o in orders:
        try:
            req = MarketOrderRequest(
                symbol=o.ticker, qty=o.qty,
                side=OrderSide.BUY if o.side == "buy" else OrderSide.SELL,
                time_in_force=TimeInForce(config.ORDER_TIF),
            )
            res = client.submit_order(req)
            results.append({"ticker": o.ticker, "status": str(res.status),
                            "id": str(res.id), "side": o.side, "qty": o.qty})
        except Exception as e:
            results.append({"ticker": o.ticker, "status": "FAILED",
                            "error": str(e)[:160], "side": o.side, "qty": o.qty})
    return results
