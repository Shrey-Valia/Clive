"""Alpaca paper account: reads, borrow checks, and plain market orders."""
from __future__ import annotations

import datetime as dt
import functools
import logging
import os
import time
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
import requests
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetCalendarRequest, GetOrdersRequest, MarketOrderRequest, StopOrderRequest

from . import config

log = logging.getLogger("shortingbot")
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


def _with_timeout(c):
    """alpaca-py sends with no timeout: a hung socket would stall a job past its cutoff."""
    c._session.request = functools.partial(c._session.request, timeout=config.ALPACA_TIMEOUT_SECONDS)
    return c


def client() -> TradingClient:
    global _client
    if _client is None:
        key, secret = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
        if not key or not secret:
            raise BrokerError("APCA_API_KEY_ID / APCA_API_SECRET_KEY missing - copy .env.example to .env")
        if os.environ.get("APCA_PAPER", "true").lower() not in ("1", "true", "yes"):
            raise BrokerError("APCA_PAPER is off. This bot is paper-only.")
        _client = _with_timeout(TradingClient(key, secret, paper=True))
    return _client


def _num(v):
    return float(v) if v not in (None, "") else None


def account() -> dict:
    """Account numbers. The PDT fields are None when Alpaca doesn't report them
    (the paper account currently omits all three)."""
    a = client().get_account()
    return {
        "equity": float(a.equity), "cash": float(a.cash), "last_equity": float(a.last_equity),
        "buying_power": float(a.buying_power), "long_market_value": float(a.long_market_value),
        "short_market_value": float(a.short_market_value),
        "shorting_enabled": bool(a.shorting_enabled), "blocked": bool(a.trading_blocked),
        "pattern_day_trader": a.pattern_day_trader,
        "daytrading_buying_power": _num(a.daytrading_buying_power),
        "daytrade_count": int(a.daytrade_count) if a.daytrade_count is not None else None,
    }


def positions() -> list[dict]:
    return [{
        "ticker": p.symbol, "qty": float(p.qty), "side": str(p.side.value),
        "avg_entry_price": float(p.avg_entry_price), "current_price": float(p.current_price or 0),
        "market_value": float(p.market_value or 0), "unrealized_pl": float(p.unrealized_pl or 0),
        "unrealized_plpc": float(p.unrealized_plpc or 0),
    } for p in client().get_all_positions()]


def signed_holdings() -> dict[str, float]:
    """{ticker: qty}, negative for shorts. Whole shares as int. Alpaca shorts are whole shares;
    a fractional qty is kept as it is (logged) so a cover never rounds it away or buys past it."""
    out = {}
    for p in positions():
        q = p["qty"]
        if abs(q - round(q)) < 1e-6:
            out[p["ticker"]] = int(round(q))
        else:
            log.warning("fractional position %s %s", p["ticker"], q)
            out[p["ticker"]] = q
    return out


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


def _as_et(day: dt.date, v) -> dt.datetime:
    if isinstance(v, dt.datetime):
        return v.replace(tzinfo=ET) if v.tzinfo is None else v.astimezone(ET)
    return dt.datetime.combine(day, v, ET)


def sessions(start: dt.date, end: dt.date) -> list[tuple[dt.date, dt.datetime, dt.datetime]]:
    """[(date, open, close)] from Alpaca's exchange calendar. Half days carry their early close."""
    cal = client().get_calendar(GetCalendarRequest(start=start, end=end))
    return [(d.date, _as_et(d.date, d.open), _as_et(d.date, d.close)) for d in cal]


def session_today() -> tuple[dt.datetime, dt.datetime] | None:
    """(open, close) in ET for today, or None when the market is closed."""
    today = dt.datetime.now(ET).date()
    cal = sessions(today, today)
    if not cal or cal[0][0] != today:
        return None
    return cal[0][1], cal[0][2]


def next_session(after: dt.date) -> dt.date | None:
    """The first trading day after `after`: where today's picks get traded."""
    later = [d for d, _, _ in sessions(after + dt.timedelta(days=1), after + dt.timedelta(days=10)) if d > after]
    return later[0] if later else None


def prev_session(before: dt.date) -> dt.date | None:
    """The last trading day before `before`: when the picks being entered were scanned."""
    earlier = [d for d, _, _ in sessions(before - dt.timedelta(days=10), before - dt.timedelta(days=1)) if d < before]
    return earlier[-1] if earlier else None


def cancel_open_orders(symbols: list[str] | None = None) -> int:
    """Cancel open orders, all of them or only those on `symbols`. Returns how many were open.
    Each cancel is tried on its own: one that fails (a stop filling meanwhile) is logged and the
    rest still go. The caller waits and re-reads holdings either way."""
    open_orders = client().get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
    if symbols is not None:
        open_orders = [o for o in open_orders if o.symbol in set(symbols)]
    for o in open_orders:
        try:
            client().cancel_order_by_id(o.id)
        except Exception as e:
            log.warning("cancel %s order %s failed: %s", o.symbol, o.id, e)
    return len(open_orders)


def open_order_symbols(symbols: list[str]) -> list[str]:
    """Which of `symbols` still have an open order."""
    return sorted({o.symbol for o in client().get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
                   if o.symbol in set(symbols)})


def is_duplicate_id_error(e) -> bool:
    t = str(e).lower()
    return "client_order_id" in t and ("unique" in t or "duplicate" in t or "exists" in t)


def _send(req):
    """submit_order with a client_order_id. alpaca-py re-POSTs on a 429/504; if the first POST
    went through, the retry is rejected as a duplicate id. That, or a timeout, is looked up by
    the id: an order found there was already sent and is returned instead of sending again."""
    try:
        return client().submit_order(req)
    except Exception as e:
        coid = getattr(req, "client_order_id", None)
        if not coid or not (is_duplicate_id_error(e) or isinstance(e, requests.exceptions.RequestException)):
            raise
        try:
            o = client().get_order_by_client_id(coid)
        except Exception:
            raise e
        log.warning("order %s already at Alpaca (%s) - not sent again", coid, str(e)[:200])
        return o


def submit(ticker: str, qty: float, side: str, client_order_id: str | None = None) -> dict:
    o = _send(MarketOrderRequest(
        symbol=ticker, qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY, client_order_id=client_order_id,
    ))
    return order_dict(o)


def submit_stop(ticker: str, qty: int, stop_price: float, client_order_id: str | None = None) -> dict:
    """Protective DAY buy-stop for a short. DAY so it dies at the close even if never cancelled."""
    o = _send(StopOrderRequest(
        symbol=ticker, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY, stop_price=stop_price,
        client_order_id=client_order_id))
    return order_dict(o)


def wait_no_open_orders(symbols: list[str], seconds: int = 10) -> bool:
    """After a cancel: poll until no order on `symbols` is still open (a stop may fill instead)."""
    deadline = time.time() + seconds
    while True:
        open_orders = [o for o in client().get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
                       if o.symbol in set(symbols)]
        if not open_orders:
            return True
        if time.time() > deadline:
            return False
        time.sleep(1)


def day_trade_count(sessions_back: int = 5) -> int | None:
    """Day trades in the last `sessions_back` sessions, today included: Alpaca's daytrade_count
    when reported, else rebuilt from fills (a symbol bought and sold on the same day = 1).
    None when neither is available."""
    try:
        reported = account()["daytrade_count"]
        if reported is not None:
            return reported
        today = dt.datetime.now(ET).date()
        days = [d for d, _, _ in sessions(today - dt.timedelta(days=14), today) if d <= today][-sessions_back:]
        if not days:
            return None
        sides: dict[tuple, set] = {}
        for f in fills(days[0].isoformat()):
            day = dt.datetime.fromisoformat(f["transaction_time"].replace("Z", "+00:00")).astimezone(ET).date()
            sides.setdefault((day, f["symbol"]), set()).add("buy" if f["side"] == "buy" else "sell")
        return sum(1 for (d, _), s in sides.items() if d in days and len(s) == 2)
    except Exception as e:
        log.warning("day trade count failed: %s", e)
        return None


def order_dict(o) -> dict:
    return {
        "id": str(o.id), "ticker": o.symbol, "side": o.side.value, "qty": float(o.qty or 0),
        "status": o.status.value, "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
        "filled_qty": float(o.filled_qty or 0),
        "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
        "filled_at": o.filled_at.isoformat() if o.filled_at else None,
        "stop_price": float(o.stop_price) if getattr(o, "stop_price", None) else None,
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


# ---------------------------------------------------------------- market data (read-only)
_data: StockHistoricalDataClient | None = None


def data_client() -> StockHistoricalDataClient:
    global _data
    if _data is None:
        client()                                  # same key checks
        _data = _with_timeout(StockHistoricalDataClient(os.environ["APCA_API_KEY_ID"],
                                                        os.environ["APCA_API_SECRET_KEY"]))
    return _data


def adv20(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {"adv20_usd": mean(close * volume)}} over the last ADV20_DAYS daily bars before
    today, unadjusted SIP bars as in the backtest. SIP on the free plan only serves data older
    than 15 minutes, which is all this needs. Missing on any error, so the gate excludes."""
    if not tickers:
        return {}
    today = dt.datetime.now(ET).date()
    start = dt.datetime.combine(today - dt.timedelta(days=45), dt.time(), ET)
    end = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=16)
    try:
        bars = data_client().get_stock_bars(StockBarsRequest(
            symbol_or_symbols=tickers, timeframe=TimeFrame.Day, start=start, end=end,
            feed=DataFeed.SIP, adjustment=Adjustment.RAW)).data
    except Exception as e:
        log.warning("adv20: Alpaca daily bars failed: %s", e)
        return {}
    out = {}
    for t, rows in bars.items():
        dv = [b.close * b.volume for b in rows if b.timestamp.astimezone(ET).date() < today][-config.ADV20_DAYS:]
        if len(dv) >= config.ADV20_MIN_DAYS:
            out[t] = {"adv20_usd": round(sum(dv) / len(dv), 0)}
    return out


def last_prices(tickers: list[str], today_only: bool = True) -> dict[str, float | None]:
    """Latest IEX trade per ticker, today's only, falling back to the quote midpoint.
    IEX because the free plan has no real-time SIP. None when neither exists.
    today_only=False lets a dry run outside market hours see yesterday's last print."""
    out = {t: None for t in tickers}
    if not tickers:
        return out
    today = dt.datetime.now(ET).date()
    try:
        trades = data_client().get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=tickers, feed=DataFeed.IEX))
        for t, tr in trades.items():
            if tr and tr.price and (not today_only or tr.timestamp.astimezone(ET).date() == today):
                out[t] = float(tr.price)
    except Exception as e:
        log.warning("latest trades failed: %s", e)
    missing = [t for t, v in out.items() if v is None]
    if missing:
        try:
            quotes = data_client().get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=missing, feed=DataFeed.IEX))
            for t, q in quotes.items():
                if q and q.bid_price and q.ask_price and (not today_only or q.timestamp.astimezone(ET).date() == today):
                    out[t] = round((float(q.bid_price) + float(q.ask_price)) / 2, 4)
        except Exception as e:
            log.warning("latest quotes failed: %s", e)
    return out
