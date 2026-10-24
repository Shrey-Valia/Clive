"""Offline fixtures: a fake Alpaca broker, a fake Supabase, and temp log files.

Nothing in the test suite talks to Alpaca, Supabase, TradingView or ntfy. The broker
module's functions are replaced wholesale, and requests is blocked."""
from __future__ import annotations

import datetime as dt
import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import daily  # noqa: E402
from shortingbot import alerts, broker, config, db, earnings, news, shortdata, tradingview  # noqa: E402

ET = broker.ET


class FakeBroker:
    """Positions, account and orders in memory. Market orders fill at once at `prices`."""

    def __init__(self):
        self.equity = 10_000.0
        self.last_equity = 10_000.0
        self.acct_extra = {"pattern_day_trader": None, "daytrading_buying_power": None, "daytrade_count": None}
        self.pos: dict[str, dict] = {}            # ticker -> {"qty", "entry", "price"}
        self.prices: dict[str, float] = {}
        self.borrow_map: dict[str, dict] = {}
        self.adv: dict[str, float] = {}
        self.orders: list[dict] = []
        self.events: list = []                    # shared timeline, tests read the order of things
        self.reject: dict[str, str] = {}          # ticker -> rejection message
        now = dt.datetime.now(ET)
        self.session = (now - dt.timedelta(hours=6), now + dt.timedelta(minutes=10))
        self.next_day = (now + dt.timedelta(days=1)).date()
        self.cancelled = 0
        self.open_orders: list[dict] = []         # resting stops
        self.day_trades: int | None = 0           # what broker.day_trade_count() answers
        self.client_ids: list[str | None] = []    # client_order_id of every submit / submit_stop / submit_long
        self.long_mode: dict[str, object] = {}    # ticker -> "rest" (no fill yet) or ("partial", n), next submit_long only
        self.quote_map: dict[str, dict] = {}      # overrides quotes(); default ask = last = prices[t]
        self.sold_today_error: str | None = None  # sold_today() raises this
        self.opg_mode: dict[str, str] = {}        # ticker -> "rest" / "expire" for the next submit_opg

    # --- the broker API daily.py / monitor.py use
    def account(self):
        return {"equity": self.equity, "cash": self.equity, "last_equity": self.last_equity,
                "buying_power": 2 * self.equity, "long_market_value": 0.0,
                "short_market_value": -sum(abs(p["qty"]) * p["price"] for p in self.pos.values()),
                "shorting_enabled": True, "blocked": False, **self.acct_extra}

    def positions(self):
        out = []
        for t, p in sorted(self.pos.items()):
            side = "short" if p["qty"] < 0 else "long"
            pl = (p["price"] - p["entry"]) * p["qty"]
            out.append({"ticker": t, "qty": float(p["qty"]), "side": side, "avg_entry_price": p["entry"],
                        "current_price": p["price"], "market_value": p["qty"] * p["price"],
                        "unrealized_pl": pl, "unrealized_plpc": pl / abs(p["qty"] * p["entry"])})
        return out

    def signed_holdings(self):
        return {t: int(p["qty"]) for t, p in self.pos.items()}

    def borrow(self, tickers):
        return {t: self.borrow_map.get(t, {"shortable": True, "easy_to_borrow": True}) for t in tickers}

    def adv20(self, tickers):
        return {t: {"adv20_usd": self.adv[t]} for t in tickers if t in self.adv}

    def last_prices(self, tickers, today_only=True):
        return {t: self.prices.get(t) for t in tickers}

    def session_today(self):
        return self.session

    def next_session(self, after):
        return self.next_day

    def next_sessions(self, after, n=2):
        return [self.next_day + dt.timedelta(days=i) for i in range(n)]

    def sold_today(self):
        """Symbols with a filled sell today (every fake order is today's)."""
        if self.sold_today_error:
            raise RuntimeError(self.sold_today_error)
        return {o["ticker"] for o in self.orders if o["side"] == "sell" and o.get("filled_qty")}

    def cancel_open_orders(self, symbols=None):
        self.events.append(("cancel", tuple(symbols or ())))
        hit = [o for o in self.open_orders if symbols is None or o["ticker"] in symbols]
        for o in hit:
            o["status"] = "canceled"
        self.open_orders = [o for o in self.open_orders if o not in hit]
        return len(hit)

    def wait_no_open_orders(self, symbols, seconds=10):
        return True

    def open_order_symbols(self, symbols):
        return sorted({o["ticker"] for o in self.open_orders if o["ticker"] in symbols})

    def day_trade_count(self, sessions_back=5):
        return self.day_trades

    def submit_stop(self, ticker, qty, stop_price, client_order_id=None):
        self.events.append(("stop", ticker, qty, stop_price))
        self.client_ids.append(client_order_id)
        if ticker in self.reject:
            raise RuntimeError(self.reject[ticker])
        o = {"id": f"s{len(self.orders) + 1}", "ticker": ticker, "side": "buy", "qty": float(qty),
             "status": "new", "submitted_at": None, "filled_qty": 0.0, "filled_avg_price": None,
             "filled_at": None, "stop_price": stop_price}
        self.orders.append(o)
        self.open_orders.append(o)
        return dict(o)

    def trigger_stop(self, ticker):
        """The stop fills: the short is bought back at the stop price."""
        o = next(o for o in self.open_orders if o["ticker"] == ticker)
        self.open_orders.remove(o)
        o.update(status="filled", filled_qty=o["qty"], filled_avg_price=o["stop_price"])
        p = self.pos[ticker]
        p["qty"] += int(o["qty"])
        if p["qty"] == 0:
            del self.pos[ticker]

    def submit(self, ticker, qty, side, client_order_id=None):
        self.events.append(("submit", side, ticker, qty))
        self.client_ids.append(client_order_id)
        if ticker in self.reject:
            raise RuntimeError(self.reject[ticker])
        px = self.prices.get(ticker) or self.pos.get(ticker, {}).get("price") or 10.0
        signed = qty if side == "buy" else -qty
        p = self.pos.setdefault(ticker, {"qty": 0, "entry": px, "price": px})
        p["qty"] += signed
        if p["qty"] == 0:
            del self.pos[ticker]
        o = {"id": f"o{len(self.orders) + 1}", "ticker": ticker, "side": side, "qty": float(qty),
             "status": "filled", "submitted_at": None, "filled_qty": float(qty),
             "filled_avg_price": px, "filled_at": None}
        self.orders.append(o)
        return dict(o)

    def submit_long(self, ticker, qty, side, kind, limit_price=None, client_order_id=None):
        """The long sleeve's orders. Fills at once at `prices` unless long_mode says otherwise or a
        limit is under the price; what doesn't fill rests in open_orders."""
        self.events.append(("long", side, kind, ticker, qty))
        self.client_ids.append(client_order_id)
        if ticker in self.reject:
            raise RuntimeError(self.reject[ticker])
        px = self.prices.get(ticker) or self.pos.get(ticker, {}).get("price") or 10.0
        mode = self.long_mode.pop(ticker, None)
        fill = qty
        if mode == "rest" or (kind == "limit" and limit_price is not None and px > limit_price):
            fill = 0
        elif isinstance(mode, tuple):
            fill = mode[1]
        if fill:
            self._apply(ticker, fill if side == "buy" else -fill, px)
        status = "filled" if fill == qty else ("partially_filled" if fill else "new")
        o = {"id": f"o{len(self.orders) + 1}", "ticker": ticker, "side": side, "qty": float(qty), "status": status,
             "submitted_at": None, "filled_qty": float(fill), "filled_avg_price": px if fill else None,
             "filled_at": None, "kind": kind, "limit_price": limit_price}
        self.orders.append(o)
        if status != "filled":
            self.open_orders.append(o)
        return dict(o)

    def submit_opg(self, ticker, qty, side, client_order_id=None):
        """Market-on-open order (round 3 auction short). Fills in full at `prices`, unless
        `opg_mode[ticker]` is "rest" (accepted, not filled yet) or "expire" (died unfilled)."""
        self.events.append(("opg", side, ticker, qty))
        self.client_ids.append(client_order_id)
        if ticker in self.reject:
            raise RuntimeError(self.reject[ticker])
        px = self.prices.get(ticker) or 10.0
        mode = self.opg_mode.pop(ticker, None)
        fill = 0 if mode in ("rest", "expire") else qty
        if fill:
            self._apply(ticker, fill if side == "buy" else -fill, px)
        status = {"rest": "accepted", "expire": "expired"}.get(mode, "filled")
        o = {"id": f"o{len(self.orders) + 1}", "ticker": ticker, "side": side, "qty": float(qty), "status": status,
             "submitted_at": None, "filled_qty": float(fill), "filled_avg_price": px if fill else None,
             "filled_at": None, "kind": "opg"}
        self.orders.append(o)
        return dict(o)

    def _apply(self, ticker, signed, px):
        p = self.pos.setdefault(ticker, {"qty": 0, "entry": px, "price": px})
        p["qty"] += signed
        if p["qty"] == 0:
            del self.pos[ticker]

    def fill_resting(self, ticker):
        """A resting long-sleeve order fills in full (a late fill)."""
        o = next(o for o in self.open_orders if o["ticker"] == ticker)
        self.open_orders.remove(o)
        rest = o["qty"] - o["filled_qty"]
        px = self.prices.get(ticker) or 10.0
        self._apply(ticker, rest if o["side"] == "buy" else -rest, px)
        o.update(status="filled", filled_qty=o["qty"], filled_avg_price=px)

    def quotes(self, tickers):
        out = {}
        for t in tickers:
            px = self.prices.get(t)
            out[t] = self.quote_map.get(t) or {"bid": px and px - 0.02, "ask": px, "mid": px and px - 0.01, "last": px}
        return out

    def open_prints(self, tickers):
        return {t: self.prices.get(t) for t in tickers}

    def refresh(self, ids):
        return [dict(o) for o in self.orders if o["id"] in ids]

    def wait_filled(self, ids, seconds):
        return self.refresh(ids)

    # --- helpers
    def short(self, ticker, qty, entry, price=None):
        self.pos[ticker] = {"qty": -qty, "entry": entry, "price": price or entry}

    def sent(self, side=None):
        return [o for o in self.orders if (side is None or o["side"] == side) and o["id"].startswith("o")]


class FakeDB:
    """Captures db.insert / db.update. Inserts return rows with ids, like PostgREST."""

    def __init__(self):
        self.inserts: list[tuple[str, dict]] = []
        self.updates: list[tuple[str, dict, dict]] = []
        self.heartbeats: list[tuple] = []
        self.next_id = 100

    def insert(self, table, rows, upsert_on=None):
        rows = rows if isinstance(rows, list) else [rows]
        out = []
        for r in rows:
            self.next_id += 1
            r = {"id": self.next_id, **r}
            self.inserts.append((table, r))
            out.append(r)
        return out

    def update(self, table, match, values):
        self.updates.append((table, match, values))

    def heartbeat(self, status, message, source="run"):
        self.heartbeats.append((source, status, message))

    def rows(self, table):
        return [r for t, r in self.inserts if t == table]

    def updated(self, table, rid):
        out = {}
        for t, m, v in self.updates:
            if t == table and m.get("id") == rid:
                out.update(v)
        return out


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """Every test: temp logs, Supabase off, no network, no sleeping, no ntfy."""
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "NTFY_TOPIC"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(broker, "load_env", lambda path=None: None)
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(config, "PICKS_FILE", str(tmp_path / "next_open_picks.json"))
    monkeypatch.setattr(daily, "GUARD", str(tmp_path / "last_execute.json"))
    monkeypatch.setattr(daily, "OPEN_GUARD", str(tmp_path / "last_open.json"))
    monkeypatch.setattr(daily, "DRY_PICKS_FILE", str(tmp_path / "next_open_picks.dry_run.json"))
    monkeypatch.setattr(alerts, "STATE", str(tmp_path / "alerts_state.json"))
    monkeypatch.setattr(daily.time, "sleep", lambda s: None)
    monkeypatch.setattr(daily, "code_version", lambda: "test")
    monkeypatch.setattr(daily, "ORDER_ID_SUFFIX", "")
    monkeypatch.setattr(config, "OVERNIGHT_LONG_ENABLED", False)   # tests turn the sleeve on themselves
    monkeypatch.setattr(config, "CACHE_DIR", str(tmp_path / ".cache"))
    # earnings gate sources: answered, no earnings, unless a test says otherwise
    monkeypatch.setattr(earnings, "from_market_events", lambda tickers, start, end: ({}, True))
    monkeypatch.setattr(earnings, "from_nasdaq", lambda tickers, days: ({}, True))

    def no_network(*a, **k):
        raise AssertionError("network call in an offline test")
    monkeypatch.setattr("requests.request", no_network)
    monkeypatch.setattr("requests.post", no_network)
    monkeypatch.setattr("requests.get", no_network)
    db.events.buffer.clear()
    real = _real_logs()
    yield
    assert _real_logs() == real, "a test wrote into the real logs/ folder"


def _real_logs() -> dict:
    d = os.path.join(ROOT, "logs")
    out = {}
    for name in ("last_execute.json", "last_open.json", "next_open_picks.json", "next_open_picks.dry_run.json",
                 "alerts_state.json", "equity_peak.json", "halt.json", "overnight_longs.json",
                 "overnight_longs.dry_run.json", "etb_history.csv", "etb_history.dry_run.csv"):
        p = os.path.join(d, name)
        out[name] = open(p).read() if os.path.exists(p) else None
    return out


LEGACY_V2 = {"SIZE_PCT_PER_NAME": 10.0, "SIZE_PCT_MAX": 10.0, "SIZE_PCT_MIN": 10.0, "MAX_SQUEEZE_LOSS_PCT": None,
             "BROKER_STOP_PCT": None, "COVER_LEFTOVERS_AT_OPEN": False, "MAX_PER_INDUSTRY": None,
             "EARNINGS_GATE": False}


@pytest.fixture
def legacy_v2_config(monkeypatch):
    """The v2 settings before the 2026-10-23 upgrade (10% a name, no stop, no squeeze cap, no
    leftover cover at the open, no industry cap, no earnings gate). The suites written against
    them pin them; tests/test_upgrade.py tests the new defaults."""
    for k, v in LEGACY_V2.items():
        monkeypatch.setattr(config, k, v)


@pytest.fixture
def fake(monkeypatch):
    fb = FakeBroker()
    for name in ("account", "positions", "signed_holdings", "borrow", "adv20", "last_prices",
                 "session_today", "next_session", "cancel_open_orders", "submit", "refresh", "wait_filled",
                 "wait_no_open_orders", "open_order_symbols", "day_trade_count", "submit_stop",
                 "submit_long", "quotes", "open_prints", "next_sessions", "sold_today", "submit_opg"):
        monkeypatch.setattr(broker, name, getattr(fb, name))
    return fb


@pytest.fixture
def fdb(monkeypatch):
    f = FakeDB()
    monkeypatch.setattr(db, "insert", f.insert)
    monkeypatch.setattr(db, "update", f.update)
    monkeypatch.setattr(db, "heartbeat", f.heartbeat)
    return f


def gainer(ticker, close=20.0, change=40.0, **kw):
    """A TradingView row that scores well above 70 with the default checklist."""
    row = {"ticker": ticker, "company": ticker, "sector": "Tech", "industry": f"Industry {ticker}", "open": close * 0.8, "high": close * 1.25,
           "low": close * 0.75, "close": close, "VWAP": close, "change": change, "market_cap": 5e8,
           "volume": 5e6, "rvol": 8.0, "avg_volume": 1e6, "float_shares_outstanding": 5e7,
           "prev_close": close / (1 + change / 100)}
    row.update(kw)
    return row


@pytest.fixture
def scan_inputs(monkeypatch, fake):
    """Feed the real scan() with canned sources. Returns the list of TradingView rows to fill."""
    rows: list[dict] = []
    monkeypatch.setattr(tradingview, "fetch_gainers", lambda: [dict(r) for r in rows])
    monkeypatch.setattr(shortdata, "enrich", lambda t: {x: {"si_pct_float": 10.0, "days_to_cover": 2.0,
                                                           "ftd_days": 0} for x in t})
    monkeypatch.setattr(news, "enrich", lambda t: {x: {"catalyst": "hype", "catalyst_score": 1.0} for x in t})
    return rows


def run_daily(*args) -> int:
    return daily.main(list(args))


def qty_for(equity, pct, price):
    return math.floor(equity * pct / 100 / price)
