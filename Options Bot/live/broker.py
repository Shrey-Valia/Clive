"""Alpaca REST access for the live options bot.

alpaca-py 0.21 has no multi-leg option orders (OrderClass lists only
simple/oco/oto/bracket), so this module talks to the REST API directly:

  POST   /v2/orders            {"order_class": "mleg", "qty", "type": "limit",
                                "limit_price", "time_in_force": "day",
                                "legs": [{"symbol", "ratio_qty", "side", "position_intent"}]}
  PATCH  /v2/orders/{id}       {"limit_price"}   (replace; returns a NEW order id)
  DELETE /v2/orders/{id}
  GET    /v2/orders/{id}

mleg limit_price sign (Alpaca docs, Replace an Order): positive = debit
(you pay), negative = credit (you receive).

Safety
------
* Trading calls go only to https://paper-api.alpaca.markets with the
  OPT_APCA_* keys of the dedicated options paper account. The account number
  must start with "PA" (paper). Anything else raises.
* Market-data calls go only to https://data.alpaca.markets. They may use the
  Stock Shorting Bot keys (read-only), which are never sent to the trading host.
* Mutating calls (submit/replace/cancel) raise unless the broker was built
  with execute=True.
* Secrets are never logged; errors include status codes and response text only.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import config as C
from .strategy import OptionQuote, parse_occ

log = logging.getLogger("optionslive")
ET = ZoneInfo(C.MARKET_TZ)
_DATA_FORBIDDEN = ("/orders", "/positions", "/account", "/activities")


class BrokerError(RuntimeError):
    pass


class SafetyError(RuntimeError):
    pass


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class Broker:
    def __init__(self, execute: bool = False, env: dict | None = None, session: requests.Session | None = None):
        self.execute = execute
        env = C.options_env() if env is None else env
        self.session = session or requests.Session()
        self.data_creds = C.data_creds(env)
        self.trade_creds = None
        self.trading_error = None
        try:
            self.trade_creds = C.trading_creds(env)
        except C.ConfigError as e:
            self.trading_error = str(e)
            if execute:
                raise
        self.base = env.get("OPT_APCA_BASE_URL", C.PAPER_TRADING_URL).rstrip("/")
        self._account_checked = False

    # ------------------------------------------------------------ transport
    @property
    def has_trading(self) -> bool:
        return self.trade_creds is not None

    def _trade(self, method: str, path: str, *, params=None, json=None, mutate=False):
        if self.trade_creds is None:
            raise BrokerError(self.trading_error or "no trading credentials")
        if self.trade_creds.source != "options":
            raise SafetyError("trading calls require the OPT_* options account keys")
        C.assert_paper_url(self.base)
        if mutate and not self.execute:
            raise SafetyError(f"{method} {path} blocked: dry run (pass --execute to send orders)")
        if not self._account_checked and path != "/v2/account":
            self.verify_paper_account()
        url = self.base + path
        h = {"APCA-API-KEY-ID": self.trade_creds.key, "APCA-API-SECRET-KEY": self.trade_creds.secret}
        r = self.session.request(method, url, params=params, json=json, headers=h, timeout=C.REQUEST_TIMEOUT)
        if r.status_code >= 300:
            raise BrokerError(f"{method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}

    def _data(self, path: str, params: dict | None = None) -> dict:
        if any(x in path for x in _DATA_FORBIDDEN):
            raise SafetyError(f"data client refuses {path}")
        url = C.DATA_URL + path
        assert url.startswith("https://data.alpaca.markets/")
        h = {"APCA-API-KEY-ID": self.data_creds.key, "APCA-API-SECRET-KEY": self.data_creds.secret}
        for attempt in range(4):
            r = self.session.get(url, params=params, headers=h, timeout=C.REQUEST_TIMEOUT)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code >= 300:
                raise BrokerError(f"GET {path}: HTTP {r.status_code} {r.text[:200]}")
            return r.json()
        raise BrokerError(f"GET {path}: retries exhausted")

    # ------------------------------------------------------------ account (trading host)
    def verify_paper_account(self) -> dict:
        a = self._trade("GET", "/v2/account")
        num = str(a.get("account_number", ""))
        if not num.startswith("PA"):
            raise SafetyError(f"account number does not look like a paper account (prefix {num[:2]!r}); refusing")
        self._account_checked = True
        return a

    def account(self) -> dict:
        """Equity etc. In a dry run without an options account, a simulated
        account (C.DRY_RUN_EQUITY, flat) is returned and flagged."""
        if not self.has_trading:
            eq = C.DRY_RUN_EQUITY
            return {"equity": eq, "cash": eq, "last_equity": eq, "buying_power": eq,
                    "options_buying_power": eq, "options_trading_level": None,
                    "account_number": None, "status": "SIMULATED", "simulated": True}
        a = self.verify_paper_account()
        return {"equity": _f(a.get("equity")), "cash": _f(a.get("cash")),
                "last_equity": _f(a.get("last_equity")), "buying_power": _f(a.get("buying_power")),
                "options_buying_power": _f(a.get("options_buying_power"), None),
                "options_trading_level": a.get("options_trading_level"),
                "long_market_value": _f(a.get("long_market_value")),
                "short_market_value": _f(a.get("short_market_value")),
                "account_number_tail": str(a.get("account_number", ""))[-4:],
                "status": a.get("status"), "trading_blocked": bool(a.get("trading_blocked")),
                "simulated": False}

    def positions(self) -> list[dict]:
        if not self.has_trading:
            return []
        out = []
        for p in self._trade("GET", "/v2/positions"):
            out.append({"symbol": p["symbol"], "qty": _f(p.get("qty")), "side": p.get("side"),
                        "asset_class": p.get("asset_class"), "avg_entry_price": _f(p.get("avg_entry_price")),
                        "current_price": _f(p.get("current_price")), "market_value": _f(p.get("market_value")),
                        "unrealized_pl": _f(p.get("unrealized_pl")), "unrealized_plpc": _f(p.get("unrealized_plpc"))})
        return out

    def clock(self) -> dict | None:
        if not self.has_trading:
            return None
        return self._trade("GET", "/v2/clock")

    def calendar(self, start: dt.date, end: dt.date) -> list[dict] | None:
        if not self.has_trading:
            return None
        return self._trade("GET", "/v2/calendar", params={"start": start.isoformat(), "end": end.isoformat()})

    # ------------------------------------------------------------ orders (trading host, mutating)
    def submit_mleg(self, legs: list[dict], qty: int, limit_price: float, client_order_id: str) -> dict:
        body = {"order_class": "mleg", "qty": str(int(qty)), "type": "limit",
                "limit_price": f"{limit_price:.2f}", "time_in_force": "day",
                "client_order_id": client_order_id[:48],
                "legs": [{"symbol": l["symbol"], "ratio_qty": str(l.get("ratio_qty", 1)),
                          "side": l["side"], "position_intent": l["position_intent"]} for l in legs]}
        log.info("submit mleg qty=%s limit=%.2f %s", qty, limit_price,
                 " ".join(f"{l['position_intent']}:{l['symbol']}" for l in legs))
        return self._trade("POST", "/v2/orders", json=body, mutate=True)

    def submit_single(self, symbol: str, qty: int, side: str, intent: str, limit_price: float,
                      client_order_id: str) -> dict:
        """Single-leg limit order (emergency close of an unpaired leg only)."""
        body = {"symbol": symbol, "qty": str(int(qty)), "side": side, "type": "limit",
                "limit_price": f"{abs(limit_price):.2f}", "time_in_force": "day",
                "position_intent": intent, "client_order_id": client_order_id[:48]}
        log.info("submit single %s %s x%s limit=%.2f", intent, symbol, qty, limit_price)
        return self._trade("POST", "/v2/orders", json=body, mutate=True)

    def replace_order(self, order_id: str, limit_price: float) -> dict:
        return self._trade("PATCH", f"/v2/orders/{order_id}", json={"limit_price": f"{limit_price:.2f}"},
                           mutate=True)

    def cancel_order(self, order_id: str) -> None:
        self._trade("DELETE", f"/v2/orders/{order_id}", mutate=True)

    def cancel_all(self) -> None:
        self._trade("DELETE", "/v2/orders", mutate=True)

    def get_order(self, order_id: str) -> dict:
        return self._trade("GET", f"/v2/orders/{order_id}", params={"nested": "true"})

    def open_orders(self) -> list[dict]:
        if not self.has_trading:
            return []
        return self._trade("GET", "/v2/orders", params={"status": "open", "nested": "true", "limit": 500})

    def fills(self, after: str) -> list[dict]:
        if not self.has_trading:
            return []
        out, token = [], None
        while True:
            params = {"activity_types": "FILL", "after": after, "direction": "asc", "page_size": 100}
            if token:
                params["page_token"] = token
            page = self._trade("GET", "/v2/account/activities", params=params)
            if not page:
                break
            out += page
            if len(page) < 100:
                break
            token = page[-1]["id"]
        return out

    # ------------------------------------------------------------ market data (data host, read-only)
    def daily_bars(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """Split-adjusted daily OHLCV (SIP), dates [start, end]. Free plans allow
        SIP history older than 15 minutes, so ``end`` should be before today."""
        rows, token = [], None
        while True:
            params = {"symbols": symbol, "timeframe": "1Day", "start": start.isoformat(),
                      "end": end.isoformat(), "adjustment": "split", "feed": C.HIST_FEED, "limit": 10000}
            if token:
                params["page_token"] = token
            js = self._data("/v2/stocks/bars", params)
            rows += (js.get("bars") or {}).get(symbol, [])
            token = js.get("next_page_token")
            if not token:
                break
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        idx = pd.to_datetime(df["t"]).dt.tz_convert(ET).dt.normalize().dt.tz_localize(None)
        return pd.DataFrame({"open": df["o"].values, "high": df["h"].values, "low": df["l"].values,
                             "close": df["c"].values, "volume": df["v"].values}, index=pd.DatetimeIndex(idx))

    def stock_snapshot(self, symbol: str) -> dict:
        js = self._data("/v2/stocks/snapshots", {"symbols": symbol, "feed": C.STOCK_FEED}).get(symbol) or {}
        db, lt = js.get("dailyBar") or {}, js.get("latestTrade") or {}
        bar_date = pd.Timestamp(db["t"]).tz_convert(ET).date() if db.get("t") else None
        trade_ts = pd.Timestamp(lt["t"]).tz_convert(ET) if lt.get("t") else None
        return {"price": _f(lt.get("p"), None), "trade_time": trade_ts, "bar_date": bar_date,
                "open": _f(db.get("o"), None), "high": _f(db.get("h"), None), "low": _f(db.get("l"), None),
                "close": _f(db.get("c"), None), "feed": C.STOCK_FEED}

    def sip_intraday_range(self, symbol: str, day: dt.date) -> dict | None:
        """Open/high/low of today's SIP 1-minute bars up to 16 minutes ago
        (the free-plan delay). Used to widen the IEX day range for IBS."""
        start = dt.datetime.combine(day, dt.time(9, 30), ET)
        end = min(dt.datetime.now(ET) - dt.timedelta(minutes=16), dt.datetime.combine(day, dt.time(16, 0), ET))
        if end <= start:
            return None
        try:
            js = self._data("/v2/stocks/bars", {"symbols": symbol, "timeframe": "1Min", "start": start.isoformat(),
                                                "end": end.isoformat(), "feed": "sip", "limit": 10000,
                                                "adjustment": "split"})
        except BrokerError as e:
            log.warning("SIP minute bars unavailable for %s: %s", symbol, e)
            return None
        bars = (js.get("bars") or {}).get(symbol, [])
        if not bars:
            return None
        return {"open": bars[0]["o"], "high": max(b["h"] for b in bars), "low": min(b["l"] for b in bars),
                "last": bars[-1]["c"], "n": len(bars)}

    def option_chain(self, underlying: str, exp_gte: dt.date, exp_lte: dt.date, cp: str = "P") -> list[OptionQuote]:
        out, token = [], None
        while True:
            params = {"feed": C.OPTION_FEED, "type": "put" if cp == "P" else "call", "limit": 1000,
                      "expiration_date_gte": exp_gte.isoformat(), "expiration_date_lte": exp_lte.isoformat()}
            if token:
                params["page_token"] = token
            js = self._data(f"/v1beta1/options/snapshots/{underlying}", params)
            out += [q for q in (_snap_to_quote(s, v) for s, v in (js.get("snapshots") or {}).items()) if q]
            token = js.get("next_page_token")
            if not token:
                break
        return out

    def option_quotes(self, symbols: list[str]) -> dict[str, OptionQuote]:
        out = {}
        for i in range(0, len(symbols), 100):
            js = self._data("/v1beta1/options/snapshots",
                            {"symbols": ",".join(symbols[i:i + 100]), "feed": C.OPTION_FEED})
            for s, v in (js.get("snapshots") or {}).items():
                q = _snap_to_quote(s, v)
                if q:
                    out[s] = q
        return out


def _snap_to_quote(sym: str, snap: dict) -> OptionQuote | None:
    occ = parse_occ(sym)
    if occ is None:
        return None
    root, exp, cp, k = occ
    q = snap.get("latestQuote") or {}
    g = snap.get("greeks") or {}
    delta = g.get("delta")
    return OptionQuote(sym, root, exp, cp, k, _f(q.get("bp")), _f(q.get("ap")),
                       float(delta) if delta is not None else None, _f(snap.get("impliedVolatility"), None))
