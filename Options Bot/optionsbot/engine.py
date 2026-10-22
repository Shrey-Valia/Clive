"""Daily-bar options backtest engine.

One Engine instance simulates one strategy on one underlying with one starting
capital. All decisions and fills happen at the daily close.

Key mechanics
-------------
* Contract selection by target |delta| and DTE window. Delta is computed from
  an IV inferred by inverting Black-Scholes on that day's option bar close vs
  the underlying close. Only strikes that actually printed that day are
  eligible; if none did, fall back to the nearest strike by model delta
  (seeded with realized vol) and a model price -- flagged `theo_entry`.
* Marks: each day an option leg is marked at its daily bar close if it
  traded; otherwise it is re-priced with Black-Scholes using its last inferred
  IV at today's spot/time ("model", default) or the last print is carried
  forward ("carry"). Either way the day is flagged stale and counted.
* Fills: buy at mark + slip, sell at mark - slip, slip = max(slip_abs,
  slip_pct*price) per contract leg, plus fee_per_contract.
* Expiration: legs settle at intrinsic using the underlying raw close on
  expiry day. settle='physical' positions (wheel) deliver/receive shares.
* Capital: cash-secured. Each position reserves cash (CSP: strike*100*n,
  vertical: width*100*n, uncovered short call: not allowed). New positions
  must fit into free cash = cash - reserved.

Engine options added by tuning/engine_fixes (defaults keep published numbers)
-----------------------------------------------------------------------------
* signal_lag (int, default 0): 0 = fill at the close that produced the
  decision (legacy). 1 = every eng.open / eng.close a strategy issues on day d
  is queued and filled at the close of the next trading day, re-priced at that
  day's print (or the model mark when the contract did not print). Sizing is
  the one decided on day d; the cash check is repeated at the fill. Share
  trades (buy_shares/sell_shares) are NOT delayed.
* share_rows (bool, default False): trades_frame() also returns one row per
  share holding episode (kind "shares": from 0 shares back to 0), so trade
  stats of physical-settlement strategies include the share P&L. Assigned or
  called-away shares are booked at the underlying price at assignment, which
  is consistent with the option row booking intrinsic. Option rows + share
  rows + interest reconcile exactly with the equity curve. trades_frame(
  include_shares=True) gives the same rows regardless of the flag.
* Market.is_monthly: a Thursday counts as a monthly expiry only when the 3rd
  Friday after it is an exchange holiday (Good Friday, Juneteenth). The old
  rule (any Thursday on the 14th-20th) is kept as is_monthly(e, legacy=True)
  or globally with engine.LEGACY_MONTHLY = True. NOTE: this changes the
  expiry ranking (Thursday dailies were treated as monthlies) and so can move
  published numbers; see tuning/engine_fixes/impact.csv.
* Market.bars caches unexpired contracts under end = min(expiry,
  max(market end, DATA_END)), so the cache key no longer depends on the end
  date passed to runner.market() (for any end <= DATA_END).
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import pricing as px

DATA_START = pd.Timestamp("2024-01-01")
# last date the option-bar cache was collected through; unexpired contracts are cached under
# min(expiry, max(market end, DATA_END)) so the key does not depend on the Market end date.
DATA_END = os.environ.get("OPTIONSBOT_DATA_END", "2026-10-20")
LEGACY_MONTHLY = False     # True restores the pre-fix is_monthly rule (any Thursday 14th-20th)


def _easter(y: int) -> pd.Timestamp:
    """Gregorian Easter Sunday (anonymous algorithm)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return pd.Timestamp(y, month, day)


def is_exchange_holiday_friday(f: pd.Timestamp) -> bool:
    """NYSE holidays that can fall on a 3rd Friday: Good Friday and Juneteenth
    (observed; from 2022). Other fixed holidays cannot land on the 15th-21st."""
    f = pd.Timestamp(f).normalize()
    if f.weekday() != 4:
        return False
    if f == _easter(f.year) - pd.Timedelta(days=2):
        return True
    if f.year >= 2022 and f.month == 6 and f.day in (18, 19):
        # June 19 on a Friday, or June 18 (Friday) observed for a Saturday June 19
        return f.day == 19 or (f + pd.Timedelta(days=1)).day == 19
    return False


# ---------------------------------------------------------------------------
@dataclass
class Costs:
    slip_abs: float = 0.03        # $ per contract-share price unit, per leg
    slip_pct: float = 0.02        # fraction of option price, per leg
    slip_cap: float = 0.25        # max $ slip per leg (on price per share)
    fee_per_contract: float = 0.03  # regulatory (ORF/OCC/TAF) pass-throughs; Alpaca $0 commission
    stock_slip_bps: float = 1.0
    sec_fee_rate: float = 27.8e-6  # on stock sale notional

    def slip(self, price: float) -> float:
        return min(self.slip_cap, max(self.slip_abs, self.slip_pct * price))


@dataclass
class Quote:
    symbol: str
    cp: str
    strike: float
    expiry: pd.Timestamp
    price: float
    iv: float
    delta: float
    theo: bool
    series: dict


@dataclass
class Leg:
    symbol: str
    cp: str
    strike: float
    expiry: pd.Timestamp
    qty: int               # signed contracts (+ long, - short)
    entry_price: float     # fill price per share
    entry_mark: float      # mark (pre-slippage) at entry
    mark: float
    iv: float
    series: dict
    delta: float = 0.0
    open: bool = True
    stale: bool = False


@dataclass
class Position:
    id: int
    kind: str
    legs: list
    open_date: pd.Timestamp
    entry_cash: float          # net cash at open incl. slippage & fees (+ = credit)
    entry_mark_value: float    # sum(qty*100*mark) at open (negative for credits)
    reserve: float
    settle: str = "cash"
    meta: dict = field(default_factory=dict)
    exit_cash: float = 0.0
    close_date: pd.Timestamp | None = None
    reason: str = ""
    stale_days: int = 0
    theo_entry: bool = False

    @property
    def is_open(self):
        return any(l.open for l in self.legs)

    def value(self) -> float:
        return sum(l.qty * 100 * l.mark for l in self.legs if l.open)

    def credit(self) -> float:
        """Premium collected at open, at marks (positive for credit trades)."""
        return -self.entry_mark_value

    def min_dte(self, d) -> int:
        return min((l.expiry - d).days for l in self.legs if l.open)

    def max_loss(self) -> float | None:
        """Structural max loss ($, >= 0) of a position with long protection:
        -(entry cash + worst expiry payoff over all underlying prices). None when
        the position has no long leg or the loss is unbounded (net short calls).
        Uses meta['max_loss'] when the strategy recorded it at entry. Legs with
        different expiries are treated as if they expired together."""
        if "max_loss" in self.meta:
            return float(self.meta["max_loss"])
        if not any(l.qty > 0 for l in self.legs):
            return None
        net_calls = sum(l.qty for l in self.legs if l.cp == "C")
        if net_calls < 0:
            return None
        pts = [0.0] + sorted({l.strike for l in self.legs})

        def payoff(S):
            return sum(l.qty * 100 * (max(0.0, S - l.strike) if l.cp == "C" else max(0.0, l.strike - S))
                       for l in self.legs)
        worst = min(payoff(x) for x in pts)
        return max(0.0, -(self.entry_cash + worst))


def load_vix(cache_dir):
    """Cboe VIX / VIX3M daily closes from the cached Cboe CSVs (.cache/charts/)."""
    out = []
    for name in ("VIX_History.csv", "VIX3M_History.csv"):
        f = cache_dir / "charts" / name
        if not f.exists():
            out.append(None)
            continue
        df = pd.read_csv(f)
        idx = pd.to_datetime(df["DATE"], format="%m/%d/%Y")
        out.append(pd.Series(df["CLOSE"].values, index=idx).sort_index())
    return out[0], out[1]


# ---------------------------------------------------------------------------
class Market:
    """Per-underlying data: stock bars, contract chain, cached option bars."""

    def __init__(self, client, symbol: str, start: str, end: str, hist_start: str = "2023-01-01"):
        self.client = client
        self.symbol = symbol
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.stock = client.stock_bars(symbol, hist_start, end)
        if self.stock.empty:
            raise RuntimeError(f"no stock data for {symbol}")
        s = self.stock
        s["sma200"] = s["split_close"].rolling(200).mean() * s["close"] / s["split_close"]
        lr = np.log(s["adj_close"]).diff()
        s["rv20"] = lr.rolling(20).std() * math.sqrt(252)
        s["rv20_pct80"] = s["rv20"].rolling(252, min_periods=120).quantile(0.80)
        s["sma50"] = s["split_close"].rolling(50).mean() * s["close"] / s["split_close"]
        s["vix_proxy"] = np.nan
        vix, vix3m = load_vix(client.cache_dir)
        s["vix"] = vix.reindex(s.index).ffill(limit=5) if vix is not None else np.nan
        s["vix3m"] = vix3m.reindex(s.index).ffill(limit=5) if vix3m is not None else np.nan
        s["vix_ratio"] = s["vix"] / s["vix3m"]
        self.split_dates = list(s.index[s["split_factor"] != 1.0])
        self.q = px.div_yield(symbol)
        # contracts: fetch in quarterly windows (cache-friendly)
        frames = []
        lo = pd.Timestamp(start) - pd.Timedelta(days=5)
        hi = pd.Timestamp(end) + pd.Timedelta(days=400)
        cur = pd.Timestamp(lo.year, ((lo.month - 1) // 3) * 3 + 1, 1)
        while cur <= hi:
            nxt = cur + pd.DateOffset(months=3)
            frames.append(client.option_contracts(symbol, cur.strftime("%Y-%m-%d"),
                                                  (nxt - pd.Timedelta(days=1)).strftime("%Y-%m-%d")))
            cur = nxt
        frames = [f for f in frames if len(f)]
        ch = pd.concat(frames).drop_duplicates("symbol") if frames else pd.DataFrame()
        ch["cp"] = ch["type"].str[0].str.upper()
        self.chain = ch
        self._by_exp = {k: g.sort_values("strike_price") for k, g in ch.groupby(["expiration_date", "cp"])}
        self.expiries = {cp: sorted({e for (e, c) in self._by_exp if c == cp}) for cp in ("C", "P")}
        self.n_theo = 0

    def atm_iv_series(self, target_dte=30) -> pd.Series:
        """Daily ~30-DTE ATM implied vol (mean of the 2 nearest-strike calls and
        puts on the expiry closest to target_dte). Used as a VIX proxy when
        built on SPY. Cached to disk."""
        import json
        cp_ = self.client.cache_dir / f"atm_iv_{self.symbol}_{self.start.date()}_{self.end.date()}.json"
        if cp_.exists():
            js = json.loads(cp_.read_text())
            return pd.Series(js["v"], index=pd.to_datetime(js["d"]))
        dates = self.stock.index[(self.stock.index >= self.start) & (self.stock.index <= self.end)]
        plan = {}
        for d in dates:
            ex = self.rank_expiries(d, "C", target_dte - 12, target_dte + 15, target_dte)
            if not ex:
                continue
            e = ex[0]
            S = self.spot(d)
            picks = []
            for cp in ("C", "P"):
                g = self._by_exp.get((e, cp))
                if g is None:
                    continue
                gi = g.iloc[np.argsort(np.abs(g["strike_price"].values - S))[:2]]
                picks += [(sym, k, cp) for sym, k in zip(gi["symbol"], gi["strike_price"])]
            plan[d] = (e, picks)
        by_exp = {}
        for d, (e, picks) in plan.items():
            by_exp.setdefault(e, set()).update(sym for sym, _, _ in picks)
        bars = {}
        for e, syms in by_exp.items():
            bars.update(self.bars(sorted(syms), e))
        out = {}
        for d, (e, picks) in plan.items():
            ivs = []
            for sym, k, cp in picks:
                pr = bars.get(sym, {}).get(d)
                if pr:
                    iv = self.iv_from_price(pr, d, k, e, cp)
                    if iv:
                        ivs.append(iv)
            if ivs:
                out[d] = float(np.median(ivs))
        ser = pd.Series(out).sort_index()
        cp_.write_text(json.dumps({"d": [str(x.date()) for x in ser.index], "v": list(ser.values)}))
        return ser

    def attach_vix_proxy(self, ser: pd.Series):
        self.stock["vix_proxy"] = ser.reindex(self.stock.index).ffill(limit=10)

    # -- basics
    def spot(self, d) -> float:
        return float(self.stock.at[d, "close"])

    def T(self, d, exp) -> float:
        return max((exp - d).days, 0) / 365.0

    def r(self, d) -> float:
        return px.risk_free(d)

    def iv_from_price(self, price, d, K, exp, cp):
        T = self.T(d, exp)
        if T <= 0:
            return None
        iv = px.implied_vol(price, self.spot(d), K, T, self.r(d), self.q, cp)
        if iv is None or not (0.03 <= iv <= 3.0):
            return None
        return iv

    def model_price(self, d, K, exp, cp, iv):
        return px.bs_price(self.spot(d), K, self.T(d, exp), self.r(d), self.q, iv, cp)

    def model_delta(self, d, K, exp, cp, iv):
        return px.bs_delta(self.spot(d), K, self.T(d, exp), self.r(d), self.q, iv, cp)

    def seed_iv(self, d) -> float:
        rv = self.stock.at[d, "rv20"]
        if not np.isfinite(rv):
            rv = 0.25
        return float(min(max(rv * 1.15, 0.10), 1.5))

    def _bars_window(self, exp):
        start = max(DATA_START, exp - pd.Timedelta(days=450)).strftime("%Y-%m-%d")
        # key must not depend on the Market end (tuning/events/BUGS.md #3): expired contracts
        # use their expiry; unexpired ones the data horizon (or a later Market end)
        end = min(exp, max(self.end, pd.Timestamp(DATA_END))).strftime("%Y-%m-%d")
        return start, end

    def bars(self, symbols, exp) -> dict:
        start, end = self._bars_window(exp)
        got = self.client.option_bars(list(symbols), start, end)
        return {s: dict(zip(v.index, v.values)) for s, v in got.items()}

    def bar_frames(self, symbols, exp) -> dict:
        """{symbol: DataFrame[close, volume]} for the same window/cache as bars()."""
        start, end = self._bars_window(exp)
        return self.client.option_bar_frames(list(symbols), start, end)

    # -- expiry choice
    @staticmethod
    def is_monthly(e: pd.Timestamp, legacy: bool | None = None) -> bool:
        """3rd Friday, or the Thursday before it when that Friday is an exchange
        holiday. legacy=True (or engine.LEGACY_MONTHLY) restores the old rule,
        which also counted every ordinary Thursday on the 14th-20th (daily
        SPY/QQQ expiries) as a monthly."""
        if LEGACY_MONTHLY if legacy is None else legacy:
            return (e.weekday() == 4 and 15 <= e.day <= 21) or (e.weekday() == 3 and 14 <= e.day <= 20)
        if e.weekday() == 4:
            return 15 <= e.day <= 21 and not is_exchange_holiday_friday(e)
        if e.weekday() == 3:
            f = e + pd.Timedelta(days=1)
            return 15 <= f.day <= 21 and is_exchange_holiday_friday(f)
        return False

    def rank_expiries(self, d, cp, dte_min, dte_max, dte_target, monthly_only=False):
        """Expiries in the DTE window, best first: monthlies preferred, then
        Friday weeklies, then other weeklies (which are listed late)."""
        out = []
        for e in self.expiries[cp]:
            dte = (e - d).days
            if dte < dte_min or dte > dte_max:
                continue
            if any(d < sd <= e for sd in self.split_dates):
                continue  # never hold options across a split (OCC symbols get re-used)
            if monthly_only and not self.is_monthly(e):
                continue
            pen = 0.0 if self.is_monthly(e) else (4.0 if e.weekday() == 4 else 10.0)
            out.append((abs(dte - dte_target) + pen, e))
        return [e for _, e in sorted(out)]

    def select(self, d, cp, *, delta=None, strike=None, expiry=None, dte_min=30, dte_max=45,
               dte_target=38, iv_hint=None, window=6, max_expiries=4,
               monthly_only=False) -> Quote | None:
        """Pick a contract. Prefers a strike that printed today on the best
        ranked expiry; tries the next expiries if none printed; falls back to a
        model-priced contract (flagged theo) on an expiry that was listed."""
        exps = [expiry] if expiry is not None else \
            self.rank_expiries(d, cp, dte_min, dte_max, dte_target, monthly_only)[:max_expiries]
        fallback = None
        for e in exps:
            q, theo = self._select_on(d, cp, e, delta, strike, iv_hint, window)
            if q is not None:
                return q
            if fallback is None and theo is not None:
                fallback = theo
        if fallback is not None:
            self.n_theo += 1
        return fallback

    def _select_on(self, d, cp, expiry, delta, strike, iv_hint, window):
        g = self._by_exp.get((expiry, cp))
        if g is None or g.empty:
            return None, None
        strikes = g["strike_price"].values
        syms = g["symbol"].values
        sig0 = iv_hint or self.seed_iv(d)
        if strike is not None:
            center = int(np.argmin(np.abs(strikes - strike)))
            fetch_idx = [center]
        else:
            mdel = np.array([abs(self.model_delta(d, k, expiry, cp, sig0)) for k in strikes])
            center = int(np.argmin(np.abs(mdel - delta)))
            fetch_idx = list(range(max(0, center - window), min(len(strikes), center + window + 1)))
        cands = {}

        def dist(i, iv):
            return abs(abs(self.model_delta(d, strikes[i], expiry, cp, iv)) - delta)

        for _ in range(5):
            need = [syms[i] for i in fetch_idx if syms[i] not in cands]
            if need:
                b = self.bars(need, expiry)
                for i in fetch_idx:
                    s = syms[i]
                    if s in cands:
                        continue
                    ser = b.get(s, {})
                    pr = ser.get(d)
                    iv = self.iv_from_price(pr, d, strikes[i], expiry, cp) if pr else None
                    cands[s] = (i, ser, pr, iv)
            if strike is not None:
                break
            valid = [(dist(i, iv), i) for (i, ser, pr, iv) in cands.values() if iv is not None]
            if not valid:
                break
            _, bi = min(valid)
            lo_i, hi_i = min(fetch_idx), max(fetch_idx)
            if bi == lo_i and lo_i > 0:
                fetch_idx = list(range(max(0, lo_i - window), lo_i + 1))
            elif bi == hi_i and hi_i < len(strikes) - 1:
                fetch_idx = list(range(hi_i, min(len(strikes), hi_i + window + 1)))
            else:
                break
        valid = [v for v in cands.values() if v[3] is not None]
        if strike is None and valid:
            i, ser, pr, iv = min(valid, key=lambda v: dist(v[0], v[3]))
            return Quote(syms[i], cp, float(strikes[i]), expiry, float(pr), iv,
                         self.model_delta(d, strikes[i], expiry, cp, iv), False, ser), None
        if strike is not None:
            i, ser, pr, iv = cands[syms[center]]
            if iv is not None:
                return Quote(syms[i], cp, float(strikes[i]), expiry, float(pr), iv,
                             self.model_delta(d, strikes[i], expiry, cp, iv), False, ser), None
            use_iv = iv_hint or sig0
        else:
            ivs = [v[3] for v in cands.values() if v[3] is not None]
            use_iv = float(np.mean(ivs)) if ivs else sig0
        # model-priced fallback only if this expiry demonstrably existed (some
        # fetched strike printed on/before d)
        listed = any(any(t <= d for t in v[1]) for v in cands.values())
        if not listed:
            return None, None
        i = center
        ser = cands[syms[i]][1]
        pr = self.model_price(d, strikes[i], expiry, cp, use_iv)
        if pr < 0.01:
            return None, None
        return None, Quote(syms[i], cp, float(strikes[i]), expiry, float(pr), use_iv,
                           self.model_delta(d, strikes[i], expiry, cp, use_iv), True, ser)


# ---------------------------------------------------------------------------
class Engine:
    def __init__(self, market: Market, strategy, capital: float, start, end,
                 costs: Costs | None = None, mark_mode: str = "model",
                 cash_yield: bool = False, early_assign: bool = True,
                 signal_lag: int = 0, share_rows: bool = False):
        self.m = market
        self.signal_lag = int(signal_lag or 0)
        self.share_rows = bool(share_rows)
        self.pending: list[dict] = []      # orders queued by signal_lag
        self._executing = False
        self._lot: dict | None = None      # current share-holding episode (trade log)
        self.share_lots: list[dict] = []
        self.cash_yield = cash_yield
        self.early_assign = early_assign
        self.interest_earned = 0.0
        self.n_early_assign = 0
        self.strategy = strategy
        self.capital = float(capital)
        self.costs = costs or Costs()
        self.mark_mode = mark_mode
        idx = market.stock.index
        self.dates = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
        self.cash = float(capital)
        self.shares = 0.0
        self.share_basis = 0.0
        self.positions: list[Position] = []
        self.closed: list[Position] = []
        self.records = []
        self.stock_trades = []
        self._notes: dict = {}
        self.infeasible = False     # set at end of run: never able to open anything
        self.blocked_days = 0
        self.kill_events = 0
        self._next_id = 1
        self.d = None
        self._i = 0
        self.dividends_received = 0.0
        self.fees_paid = 0.0
        self.slippage_paid = 0.0

    # ------------------------------------------------------------ helpers
    def spot(self, d=None):
        return self.m.spot(d or self.d)

    def reserved(self) -> float:
        return sum(p.reserve for p in self.positions)

    def free_cash(self) -> float:
        return self.cash - self.reserved()

    def equity(self) -> float:
        return self.cash + self.shares * self.spot() + sum(p.value() for p in self.positions)

    def open_positions(self, kind=None):
        return [p for p in self.positions if kind is None or p.kind == kind]

    def blocked(self, msg):
        """Record that today's entry was impossible with the available capital."""
        self.blocked_days += 1
        self.note(msg)

    def note(self, msg):
        # de-duplicate notes that differ only in numbers; keep first text + count
        key = re.sub(r"[\d$,.~]+", "#", msg)
        if key in self._notes:
            self._notes[key][1] += 1
        else:
            self._notes[key] = [msg, 1]

    @property
    def notes(self) -> list[str]:
        return [m if n == 1 else f"{m} [x{n} days]" for m, n in self._notes.values()]

    # ------------------------------------------------------------- stock
    def buy_shares(self, n: float, reason="buy"):
        if n <= 0:
            return
        S = self.spot()
        fill = S * (1 + self.costs.stock_slip_bps / 1e4)
        self.slippage_paid += n * (fill - S)
        self.share_basis = (self.share_basis * self.shares + fill * n) / (self.shares + n)
        self.shares += n
        self.cash -= n * fill
        self._lot_in(n * fill)
        self.stock_trades.append({"date": self.d, "side": "buy", "qty": n, "price": fill, "reason": reason})

    def sell_shares(self, n: float, reason="sell"):
        n = min(n, self.shares)
        if n <= 0:
            return
        S = self.spot()
        fill = S * (1 - self.costs.stock_slip_bps / 1e4)
        fee = n * fill * self.costs.sec_fee_rate
        self.slippage_paid += n * (S - fill)
        self.fees_paid += fee
        self.shares -= n
        self.cash += n * fill - fee
        if self.shares <= 1e-9:
            self.shares, self.share_basis = 0.0, 0.0
        self._lot_out(n * fill - fee, reason)
        self.stock_trades.append({"date": self.d, "side": "sell", "qty": n, "price": fill, "reason": reason})

    # share-holding episodes for the trade log (share_rows). Amounts are $ at the reference
    # price: fills for buys/sells, the underlying close for assignments / call-aways.
    def _lot_in(self, amount: float):
        if self._lot is None:
            self._lot = {"open_date": self.d, "cost": 0.0, "proceeds": 0.0, "div": 0.0, "max": 0.0}
        self._lot["cost"] += amount
        self._lot["max"] = max(self._lot["max"], self.shares)

    def _lot_out(self, amount: float, reason: str):
        if self._lot is None:
            return
        self._lot["proceeds"] += amount
        if self.shares <= 1e-9:
            self._lot.update(close_date=self.d, reason=reason)
            self.share_lots.append(self._lot)
            self._lot = None

    # ----------------------------------------------------------- options
    def _fill(self, mark: float, qty: int) -> float:
        s = self.costs.slip(mark)
        return mark + s if qty > 0 else max(mark - s, 0.0)

    def open(self, legs: list[tuple[Quote, int]], kind: str, reserve: float = 0.0,
             settle: str = "cash", meta: dict | None = None, check_cash: bool = True):
        if any(q is None for q, _ in legs) or not legs:
            return None
        if self.signal_lag and not self._executing:
            order = {"op": "open", "i": self._i, "legs": list(legs), "kind": kind, "reserve": reserve,
                     "settle": settle, "meta": meta, "check_cash": check_cash}
            self.pending.append(order)
            return order          # truthy placeholder; the position exists after the fill
        cash_flow, mark_val, nct = 0.0, 0.0, 0
        built = []
        for q, qty in legs:
            if qty == 0:
                continue
            fill = self._fill(q.price, qty)
            cash_flow -= qty * 100 * fill
            mark_val += qty * 100 * q.price
            self.slippage_paid += abs(qty) * 100 * abs(fill - q.price)
            nct += abs(qty)
            built.append(Leg(q.symbol, q.cp, q.strike, q.expiry, qty, fill, q.price, q.price, q.iv,
                             q.series, q.delta))
        fees = nct * self.costs.fee_per_contract
        cash_flow -= fees
        if check_cash and (self.free_cash() + cash_flow - reserve) < -1e-6:
            return None
        self.fees_paid += fees
        self.cash += cash_flow
        meta = dict(meta or {})
        meta["reserve0"] = reserve
        p = Position(self._next_id, kind, built, self.d, cash_flow, mark_val, reserve, settle,
                     meta or {}, theo_entry=any(q.theo for q, _ in legs))
        self._next_id += 1
        self.positions.append(p)
        return p

    def close(self, p: Position, reason: str):
        if self.signal_lag and not self._executing:
            if not any(o["op"] == "close" and o["pos"] is p for o in self.pending):
                self.pending.append({"op": "close", "i": self._i, "pos": p, "reason": reason})
            return
        cf, nct = 0.0, 0
        for l in p.legs:
            if not l.open:
                continue
            fill = self._fill(l.mark, -l.qty)
            cf += l.qty * 100 * fill
            self.slippage_paid += abs(l.qty) * 100 * abs(fill - l.mark)
            nct += abs(l.qty)
            l.open = False
        fees = nct * self.costs.fee_per_contract
        self.fees_paid += fees
        cf -= fees
        self.cash += cf
        p.exit_cash += cf
        self._finish(p, reason)

    def _finish(self, p: Position, reason: str):
        p.close_date = self.d
        p.reason = reason
        p.reserve = 0.0
        self.positions.remove(p)
        self.closed.append(p)

    # ------------------------------------------------------- signal lag
    def _requote(self, q: Quote, d) -> Quote | None:
        """Price a queued leg at today's close: its print if it traded, else the
        model price at its last IV (flagged theo)."""
        if q.expiry <= d:
            return None
        pr = q.series.get(d)
        if pr is not None and pr > 0:
            iv = self.m.iv_from_price(pr, d, q.strike, q.expiry, q.cp) or q.iv
            return Quote(q.symbol, q.cp, q.strike, q.expiry, float(pr), iv,
                         self.m.model_delta(d, q.strike, q.expiry, q.cp, iv), False, q.series)
        pr = self.m.model_price(d, q.strike, q.expiry, q.cp, q.iv)
        if not pr or pr < 0.01:
            return None
        return Quote(q.symbol, q.cp, q.strike, q.expiry, float(pr), q.iv,
                     self.m.model_delta(d, q.strike, q.expiry, q.cp, q.iv), True, q.series)

    def _execute_pending(self, d):
        due = [o for o in self.pending if self._i - o["i"] >= self.signal_lag]
        if not due:
            return
        due_ids = {id(o) for o in due}
        self.pending = [o for o in self.pending if id(o) not in due_ids]
        self._executing = True
        try:
            for o in due:
                if o["op"] == "close":
                    if o["pos"] in self.positions:
                        self.close(o["pos"], o["reason"])
                    continue
                legs = [(self._requote(q, d), qty) for q, qty in o["legs"]]
                if any(q is None for q, _ in legs):
                    self.note("lagged entry dropped: a leg expired or could not be priced at the fill")
                    continue
                if self.open(legs, o["kind"], o["reserve"], o["settle"], o["meta"], o["check_cash"]) is None:
                    self.note("lagged entry rejected at the fill: not enough free cash")
        finally:
            self._executing = False

    # --------------------------------------------------------- daily steps
    def _mark(self, d):
        S = self.spot(d)
        for p in self.positions:
            any_stale = False
            for l in p.legs:
                if not l.open:
                    continue
                intrinsic = max(0.0, S - l.strike) if l.cp == "C" else max(0.0, l.strike - S)
                pr = l.series.get(d)
                if pr is not None and pr > 0:
                    l.mark = max(float(pr), intrinsic)
                    iv = self.m.iv_from_price(l.mark, d, l.strike, l.expiry, l.cp)
                    if iv is not None:
                        l.iv = iv
                    l.stale = False
                else:
                    any_stale = True
                    l.stale = True
                    if self.mark_mode == "model":
                        l.mark = max(self.m.model_price(d, l.strike, l.expiry, l.cp, l.iv), intrinsic)
                    else:
                        l.mark = max(l.mark, intrinsic)
                l.delta = self.m.model_delta(d, l.strike, l.expiry, l.cp, l.iv)
            if any_stale:
                p.stale_days += 1

    def _expire(self, d):
        S = self.spot(d)
        for p in list(self.positions):
            for l in p.legs:
                if not l.open or l.expiry > d:
                    continue
                # use the underlying close on the expiry date (or last close before it)
                Se = S if l.expiry == d else self.m.spot(self.m.stock.index[self.m.stock.index <= l.expiry][-1])
                intrinsic = max(0.0, Se - l.strike) if l.cp == "C" else max(0.0, l.strike - Se)
                n = abs(l.qty)
                if intrinsic > 0 and p.settle == "physical" and l.qty < 0:
                    if l.cp == "P":       # assigned: buy shares at strike
                        self.cash -= l.strike * 100 * n
                        self.share_basis = ((self.share_basis * self.shares + l.strike * 100 * n)
                                            / (self.shares + 100 * n))
                        self.shares += 100 * n
                        self._lot_in(Se * 100 * n)   # option row books the intrinsic
                        self.stock_trades.append({"date": d, "side": "assigned_buy", "qty": 100 * n,
                                                  "price": l.strike, "reason": "put assignment"})
                    else:                 # called away
                        deliver = min(100 * n, self.shares)
                        self.shares -= deliver
                        self.cash += l.strike * deliver
                        if deliver < 100 * n:   # uncovered remainder cash-settled
                            self.cash -= intrinsic * (100 * n - deliver)
                        if self.shares <= 1e-9:
                            self.shares, self.share_basis = 0.0, 0.0
                        self._lot_out(Se * deliver, "called_away")
                        self.stock_trades.append({"date": d, "side": "called_away", "qty": deliver,
                                                  "price": l.strike, "reason": "call assignment"})
                    p.exit_cash += l.qty * 100 * intrinsic   # cash-equivalent P&L for the trade log
                    p.meta["assigned"] = True
                else:
                    cf = l.qty * 100 * intrinsic
                    self.cash += cf
                    p.exit_cash += cf
                    if intrinsic > 0 and l.qty < 0:
                        p.meta["assigned"] = True
                l.mark = intrinsic
                l.open = False
            if not p.is_open:
                self._finish(p, "expired_itm" if p.meta.get("assigned") else "expired")

    def _accrue(self, prev, d):
        """Optional T-bill-like interest on positive cash (collateral included)."""
        if self.cash_yield and self.cash > 0:
            amt = self.cash * self.m.r(prev) * (d - prev).days / 365.0
            self.cash += amt
            self.interest_earned += amt

    def _early_assign_calls(self, d, div):
        """Heuristic early exercise: an ITM short call whose remaining time value
        is below the next-day dividend is assumed exercised at today's close."""
        S = self.spot(d)
        for p in list(self.positions):
            shorts = [l for l in p.legs if l.open and l.cp == "C" and l.qty < 0]
            for l in shorts:
                intrinsic = S - l.strike
                if intrinsic <= 0 or l.mark - intrinsic >= div:
                    continue
                n = abs(l.qty)
                if p.settle == "physical":
                    deliver = min(100 * n, self.shares)
                    self.shares -= deliver
                    self.cash += l.strike * deliver - intrinsic * (100 * n - deliver)
                    if self.shares <= 1e-9:
                        self.shares, self.share_basis = 0.0, 0.0
                    self._lot_out(S * deliver, "called_away_early")
                    self.stock_trades.append({"date": d, "side": "called_away", "qty": deliver,
                                              "price": l.strike, "reason": "early assignment (ex-div)"})
                else:
                    self.cash -= intrinsic * 100 * n
                p.exit_cash -= intrinsic * 100 * n
                l.mark, l.open = intrinsic, False
                p.meta["assigned"] = True
                p.meta["early_assigned"] = True
                self.n_early_assign += 1
            if not p.is_open:
                self._finish(p, "early_assigned")
            elif p.meta.get("early_assigned"):
                # remaining legs of a spread are closed out at the same time
                self.close(p, "early_assigned")

    def _corporate_actions(self, d):
        row = self.m.stock.loc[d]
        f = row["split_factor"]
        if f != 1.0 and self.shares:
            self.shares *= f
            self.share_basis /= f
        if self.shares and row["dividend"] > 0:
            amt = self.shares * row["dividend"]
            self.cash += amt
            self.dividends_received += amt
            if self._lot is not None:
                self._lot["div"] += amt

    # ------------------------------------------------------------------ run
    def run(self):
        st = self.strategy
        idx = self.m.stock.index
        for i, d in enumerate(self.dates):
            self.d = d
            self._i = i
            if i > 0:
                self._accrue(self.dates[i - 1], d)
                self._corporate_actions(d)
            self._mark(d)
            self._expire(d)
            if self.early_assign:
                nxt = idx[idx > d]
                if len(nxt) and self.m.stock.at[nxt[0], "dividend"] > 0:
                    self._early_assign_calls(d, float(self.m.stock.at[nxt[0], "dividend"]))
            if self.pending:
                self._execute_pending(d)
            st.on_day(self, d)
            self._record(d)
        self.n_theo = self.m.n_theo
        self.infeasible = not (self.closed or self.positions or self.stock_trades)
        return self

    def _record(self, d):
        S = self.spot(d)
        eq = self.equity()
        opt_val = sum(p.value() for p in self.positions)
        delta_dollars = self.shares * S + sum(l.qty * 100 * l.delta * S for p in self.positions
                                              for l in p.legs if l.open)
        long_opt = sum(l.qty * 100 * l.mark for p in self.positions for l in p.legs if l.open and l.qty > 0)
        at_risk = self.shares * S + self.reserved() + long_opt
        self.records.append({
            "date": d, "equity": eq, "cash": self.cash, "shares": self.shares, "spot": S,
            "option_value": opt_val, "reserved": self.reserved(), "delta_dollars": delta_dollars,
            "capital_at_risk": at_risk, "n_positions": len(self.positions),
            "stale_legs": sum(1 for p in self.positions for l in p.legs if l.open and l.stale),
        })

    # -------------------------------------------------------------- output
    def equity_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.records).set_index("date")

    def share_rows_frame(self) -> pd.DataFrame:
        """One row per share-holding episode (see module docstring, share_rows)."""
        rows = []
        lots = self.share_lots + ([self._lot] if self._lot is not None else [])
        for k, lot in enumerate(lots):
            open_ = lot is self._lot
            mv = self.shares * self.spot() if open_ else 0.0
            pnl = lot["proceeds"] + lot["div"] + mv - lot["cost"]
            close = None if open_ else lot["close_date"]
            rows.append({
                "id": 1_000_000 + k, "kind": "shares", "open_date": lot["open_date"].date(),
                "close_date": close.date() if close is not None else None,
                "legs": f"{lot['max']:g} {self.m.symbol} shares", "contracts": 0,
                "entry_cash": round(-lot["cost"], 2), "exit_cash": round(lot["proceeds"] + lot["div"], 2),
                "pnl": round(pnl, 2), "reserve_at_open": 0.0,
                "reason": "open_at_end" if open_ else lot["reason"],
                "stale_mark_days": 0, "theo_entry": False,
                "days_held": ((close or self.d) - lot["open_date"]).days, "assigned": False,
            })
        return pd.DataFrame(rows)

    def trades_frame(self, include_shares: bool | None = None) -> pd.DataFrame:
        """Option trade log. include_shares (default: the engine's share_rows
        flag) appends the share-episode rows (kind 'shares')."""
        if include_shares is None:
            include_shares = self.share_rows
        df = self._option_trades_frame()
        if include_shares:
            sh = self.share_rows_frame()
            if len(sh):
                df = pd.concat([df, sh], ignore_index=True) if len(df) else sh
        return df

    def _option_trades_frame(self) -> pd.DataFrame:
        rows = []
        for p in self.closed + self.positions:
            legs = ";".join(f"{'+' if l.qty > 0 else ''}{l.qty} {l.symbol}@{l.entry_price:.2f}" for l in p.legs)
            open_ = p.is_open
            pnl = p.entry_cash + p.exit_cash + (p.value() if open_ else 0.0)
            rows.append({
                "id": p.id, "kind": p.kind, "open_date": p.open_date.date(),
                "close_date": (p.close_date.date() if p.close_date is not None else None),
                "legs": legs, "contracts": max(abs(l.qty) for l in p.legs),
                "entry_cash": round(p.entry_cash, 2), "exit_cash": round(p.exit_cash, 2),
                "pnl": round(pnl, 2), "reserve_at_open": round(p.meta.get("reserve0", 0.0), 2),
                "reason": p.reason if not open_ else "open_at_end",
                "stale_mark_days": p.stale_days, "theo_entry": p.theo_entry,
                "days_held": ((p.close_date or self.d) - p.open_date).days,
                "assigned": bool(p.meta.get("assigned", False)),
            })
        return pd.DataFrame(rows)
