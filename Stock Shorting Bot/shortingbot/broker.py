"""Alpaca paper account: reads, borrow checks, and plain market orders."""
from __future__ import annotations

import datetime as dt
import os
import time
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest, MarketOrderRequest

from . import config

ET = ZoneInfo(config.MARKET_TZ)


class BrokerError(RuntimeError):
    pass


def load_env(path: str | None = None) -> None:
    """Read KEY=VALUE lines from .env without overriding the real environment."""
    path = path or os.path.join(config.ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_client: TradingClient | None = None


def client() -> TradingClient:
    global _client
    if _client is None:
        key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise BrokerError("APCA_API_KEY_ID / APCA_API_SECRET_KEY missing - copy .env.example to .env")
        if os.environ.get("APCA_PAPER", "true").lower() not in ("1", "true", "yes"):
            raise BrokerError("APCA_PAPER is off. This bot is paper-only.")
        _client = TradingClient(key, secret, paper=True)
    return _client


def account() -> dict:
    a = client().get_account()
    return {
        "equity": float(a.equity), "cash": float(a.cash), "last_equity": float(a.last_equity),
        "buying_power": float(a.buying_power), "long_market_value": float(a.long_market_value),
        "short_market_value": float(a.short_market_value),
        "shorting_enabled": bool(a.shorting_enabled), "blocked": bool(a.trading_blocked),
    }


def positions() -> list[dict]:
    return [{
        "ticker": p.symbol, "qty": float(p.qty), "side": str(p.side.value),
        "avg_entry_price": float(p.avg_entry_price), "current_price": float(p.current_price or 0),
        "market_value": float(p.market_value or 0), "unrealized_pl": float(p.unrealized_pl or 0),
        "unrealized_plpc": float(p.unrealized_plpc or 0),
    } for p in client().get_all_positions()]


def signed_holdings() -> dict[str, int]:
    """{ticker: qty}, negative for shorts."""
    return {p["ticker"]: int(round(p["qty"])) for p in positions()}


def borrow(tickers: list[str]) -> dict[str, dict]:
    out = {}
    for t in tickers:
        try:
            a = client().get_asset(t)
            out[t] = {"shortable": bool(a.shortable and a.tradable),
                      "easy_to_borrow": bool(a.easy_to_borrow)}
        except Exception:
            out[t] = {"shortable": False, "easy_to_borrow": False}
    return out


def session_today() -> tuple[dt.datetime, dt.datetime] | None:
    """(open, close) in ET for today, or None when the market is closed."""
    today = dt.datetime.now(ET).date()
    cal = client().get_calendar(GetCalendarRequest(start=today, end=today))
    if not cal or cal[0].date != today:
        return None
    day = cal[0]

    def as_et(v):
        if isinstance(v, dt.datetime):
            return v.replace(tzinfo=ET) if v.tzinfo is None else v.astimezone(ET)
        return dt.datetime.combine(today, v, ET)

    return as_et(day.open), as_et(day.close)


def cancel_open_orders() -> int:
    open_orders = client().get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    for o in open_orders:
        client().cancel_order_by_id(o.id)
    return len(open_orders)


def submit(ticker: str, qty: int, side: str) -> dict:
    o = client().submit_order(MarketOrderRequest(
        symbol=ticker, qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    ))
    return order_dict(o)


def order_dict(o) -> dict:
    return {
        "id": str(o.id), "ticker": o.symbol, "side": o.side.value, "qty": float(o.qty or 0),
        "status": o.status.value, "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
        "filled_qty": float(o.filled_qty or 0),
        "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
        "filled_at": o.filled_at.isoformat() if o.filled_at else None,
    }


def refresh(order_ids: list[str]) -> list[dict]:
    return [order_dict(client().get_order_by_id(i)) for i in order_ids]


def wait_filled(order_ids: list[str], seconds: int) -> list[dict]:
    """Poll until every order is terminal or time runs out."""
    deadline = time.time() + seconds
    while True:
        states = refresh(order_ids)
        if all(s["status"] in ("filled", "canceled", "expired", "rejected") for s in states):
            return states
        if time.time() > deadline:
            return states
        time.sleep(2)


def fills(after: str) -> list[dict]:
    """Every fill since `after` (YYYY-MM-DD), oldest first."""
    out, token = [], None
    while True:
        params = {"after": f"{after}T00:00:00Z", "direction": "asc", "page_size": 100}
        if token:
            params["page_token"] = token
        page = client().get("/account/activities/FILL", params) or []
        out.extend(page)
        if len(page) < 100:
            return out
        token = page[-1]["id"]
