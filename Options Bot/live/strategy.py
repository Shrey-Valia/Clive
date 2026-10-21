"""Entry/exit decisions for the dip put credit spread, as pure functions.

Nothing here talks to the network or the clock: every input is passed in, so
tests can drive it with fake chains and features. run.py wires it to the broker.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import config as C

_OCC = re.compile(r"^([A-Z.]+?)(\d{6})([CP])(\d{8})$")


# ---------------------------------------------------------------- data types
@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    underlying: str
    expiry: date
    cp: str                     # "P" | "C"
    strike: float
    bid: float
    ask: float
    delta: float | None         # signed (puts negative)
    iv: float | None = None
    ts: datetime | None = None  # quote time (tz-aware), when the feed gives one

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def valid(self) -> bool:
        """Two-sided market (needed to open)."""
        return self.bid > 0 and self.ask > 0 and self.ask >= self.bid

    @property
    def quoted(self) -> bool:
        """Has an offer; the bid may be 0 (a far OTM leg we are selling back)."""
        return self.ask > 0 and self.bid >= 0 and self.ask >= self.bid

    def age_seconds(self, now: datetime) -> float | None:
        if self.ts is None:
            return None
        return (now - self.ts).total_seconds()

    @property
    def spread_frac(self) -> float:
        return (self.ask - self.bid) / self.mid if self.mid > 0 else math.inf


@dataclass
class Pick:
    """A priced put credit spread candidate (per-share prices)."""
    underlying: str
    expiry: date
    short: OptionQuote
    long: OptionQuote
    dte: int

    @property
    def width(self) -> float:
        return round(self.short.strike - self.long.strike, 4)

    @property
    def mid_credit(self) -> float:
        return round(self.short.mid - self.long.mid, 4)

    @property
    def natural_credit(self) -> float:          # sell at bid, buy at ask
        return round(self.short.bid - self.long.ask, 4)

    @property
    def far_credit(self) -> float:              # sell at ask, buy at bid
        return round(self.short.ask - self.long.bid, 4)


@dataclass
class Signal:
    symbol: str
    close: float
    sma200: float
    sma5: float
    rsi2: float
    ibs: float
    asof: date
    ok: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class Spread:
    """An open put credit spread as held at the broker (qty = number of spreads)."""
    underlying: str
    expiry: date
    short_symbol: str
    long_symbol: str
    short_strike: float
    long_strike: float
    qty: int
    short_avg: float = 0.0       # per-share avg entry of the short leg (credit received)
    long_avg: float = 0.0

    @property
    def id(self) -> str:
        return spread_id(self.underlying, self.expiry, self.short_strike, self.long_strike)

    @property
    def width(self) -> float:
        return round(self.short_strike - self.long_strike, 4)

    @property
    def broker_credit(self) -> float:
        return round(self.short_avg - self.long_avg, 4)


def spread_id(underlying: str, expiry: date, short_k: float, long_k: float) -> str:
    return f"{underlying}-{expiry:%Y%m%d}-P{short_k:g}/{long_k:g}"


def parse_occ(sym: str):
    """'SPY261030P00650000' -> ('SPY', date(2026,10,30), 'P', 650.0) or None."""
    m = _OCC.match(sym)
    if not m:
        return None
    root, ymd, cp, k = m.groups()
    return root, date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:])), cp, int(k) / 1000


def occ_symbol(root: str, expiry: date, cp: str, strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{cp}{int(round(strike * 1000)):08d}"


# ---------------------------------------------------------------- signal
def entry_signal(sig: Signal) -> Signal:
    """close > 200DMA, RSI(2) < 10, IBS < 0.2. Mutates and returns ``sig``."""
    r = []
    vals = (sig.close, sig.sma200, sig.rsi2, sig.ibs)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals):
        r.append("indicators not available")
    else:
        if not sig.close > sig.sma200:
            r.append(f"close {sig.close:.2f} <= 200DMA {sig.sma200:.2f}")
        if not sig.rsi2 < C.RSI_MAX:
            r.append(f"RSI2 {sig.rsi2:.1f} >= {C.RSI_MAX:g}")
        if not sig.ibs < C.IBS_MAX:
            r.append(f"IBS {sig.ibs:.2f} >= {C.IBS_MAX:g}")
    sig.reasons = r
    sig.ok = not r
    return sig


def kill_switch(vix: float | None, vix3m: float | None, fresh: bool = True,
                stale_reason: str = "") -> tuple[bool, str]:
    """(blocked, reason). Fails closed on missing data when configured, and
    always on stale / mixed-source quotes (``fresh=False``)."""
    if not fresh and vix is not None and vix3m is not None:
        return True, f"VIX/VIX3M not fresh: {stale_reason or 'stale or mixed sources'} (fail closed)"
    if vix is None or not math.isfinite(vix) or vix3m is None or not math.isfinite(vix3m) or vix3m <= 0:
        if C.KILL_ON_MISSING_VIX:
            return True, "VIX/VIX3M unavailable (fail closed)"
        return False, "VIX/VIX3M unavailable (ignored)"
    ratio = vix / vix3m
    if vix > C.VIX_MAX:
        return True, f"VIX {vix:.2f} > {C.VIX_MAX:g}"
    if ratio > C.VIX_RATIO_MAX:
        return True, f"VIX/VIX3M {ratio:.3f} > {C.VIX_RATIO_MAX:g}"
    return False, f"VIX {vix:.2f}, VIX/VIX3M {ratio:.3f}"


# ---------------------------------------------------------------- strikes
def is_monthly(d: date) -> bool:
    """Third Friday of the month, or the Thursday before it (when that Friday is
    a holiday, e.g. Good Friday or Juneteenth). Same rule as the backtest engine
    (optionsbot.engine.Market.is_monthly)."""
    return (d.weekday() == 4 and 15 <= d.day <= 21) or (d.weekday() == 3 and 14 <= d.day <= 20)


def rank_expiries(expiries, today: date, dte_min=C.DTE_MIN, dte_max=C.DTE_MAX,
                  dte_target=C.DTE_TARGET) -> list[date]:
    """Expiries in the DTE window, best first. Same ranking as the backtest
    engine (optionsbot.engine.Market.rank_expiries): distance to the target DTE,
    plus a penalty of 4 for Friday weeklies and 10 for other weeklies."""
    out = []
    for e in set(expiries):
        dte = (e - today).days
        if dte < dte_min or dte > dte_max:
            continue
        pen = 0.0 if is_monthly(e) else (4.0 if e.weekday() == 4 else 10.0)
        out.append((abs(dte - dte_target) + pen, dte, e))
    return [e for _, _, e in sorted(out)]


def _nearest_delta(quotes, target, max_strike=None):
    best = None
    for q in quotes:
        if q.delta is None or not math.isfinite(q.delta) or not q.valid:
            continue
        if max_strike is not None and q.strike >= max_strike:
            continue
        d = abs(abs(q.delta) - target)
        if best is None or d < best[0] or (d == best[0] and q.strike > best[1].strike):
            best = (d, q)
    if best is None or best[0] > C.DELTA_TOLERANCE:
        return None
    return best[1]


def pick_spread(chain: list[OptionQuote], underlying: str, today: date) -> tuple[Pick | None, str]:
    """Short ~0.25 delta put and long ~0.10 delta put on the best 7-14 DTE expiry.
    Tries the next-ranked expiry if one has no usable strikes."""
    puts = [q for q in chain if q.cp == "P" and q.underlying == underlying]
    if not puts:
        return None, "empty put chain"
    why = "no expiry in the 7-14 DTE window"
    for exp in rank_expiries({q.expiry for q in puts}, today):
        qs = [q for q in puts if q.expiry == exp]
        short = _nearest_delta(qs, C.SHORT_DELTA)
        if short is None:
            why = f"{exp}: no quoted put near {C.SHORT_DELTA}Δ"
            continue
        long = _nearest_delta(qs, C.LONG_DELTA, max_strike=short.strike)
        if long is None:
            why = f"{exp}: no quoted put near {C.LONG_DELTA}Δ below {short.strike:g}"
            continue
        return Pick(underlying, exp, short, long, (exp - today).days), "ok"
    return None, why


def check_pick(p: Pick, now: datetime | None = None,
               max_age: float = C.MAX_OPTION_QUOTE_AGE_SECONDS) -> str | None:
    """Quote sanity and the credit/width guard. Returns a skip reason or None.
    With ``now``, a leg quote older than ``max_age`` seconds (or without a
    timestamp) is rejected: fail closed."""
    if p.width < C.MIN_WIDTH:
        return f"width {p.width:g} < {C.MIN_WIDTH:g}"
    if now is not None:
        for leg, name in ((p.short, "short"), (p.long, "long")):
            age = leg.age_seconds(now)
            if age is None:
                return f"{name} leg quote has no timestamp (fail closed)"
            if age > max_age:
                return f"{name} leg quote is {age / 60:.0f} min old (stale)"
    for leg, name in ((p.short, "short"), (p.long, "long")):
        if not leg.valid:
            return f"{name} leg has no two-sided quote"
        if leg.spread_frac > C.MAX_LEG_SPREAD_FRAC:
            return f"{name} leg bid/ask too wide ({leg.bid:.2f}/{leg.ask:.2f})"
    combo = p.far_credit - p.natural_credit
    if combo > max(C.MAX_COMBO_SPREAD_ABS, C.MAX_COMBO_SPREAD_FRAC * max(p.mid_credit, 0)):
        return f"combo bid/ask too wide ({p.natural_credit:.2f}..{p.far_credit:.2f})"
    c = p.mid_credit
    if c > C.MAX_CREDIT_FRAC * p.width:
        return f"credit {c:.2f} > {C.MAX_CREDIT_FRAC:.0%} of width {p.width:g} (bad quote guard)"
    if c < C.MIN_CREDIT_FRAC * p.width or c < C.MIN_CREDIT_ABS:
        return f"credit {c:.2f} < {C.MIN_CREDIT_FRAC:.1%} of width {p.width:g}"
    return None


# ---------------------------------------------------------------- sizing
def size_position(equity: float, width: float, credit: float, open_max_loss: float,
                  options_bp: float | None = None) -> tuple[int, str]:
    """Contracts such that this spread's max loss <= 25% of equity and the total
    max loss across positions <= 50% of equity. Also capped by options buying
    power (Alpaca holds the width) and MAX_CONTRACTS."""
    per = (width - credit) * 100
    if per <= 0 or equity <= 0:
        return 0, "non-positive max loss or equity"
    room_total = C.RISK_TOTAL * equity - open_max_loss
    budget = min(C.RISK_PER_POSITION * equity, room_total)
    if budget <= 0:
        return 0, f"total risk cap reached (open max loss ${open_max_loss:,.0f})"
    n = int(budget // per)
    why = f"max loss ${per:,.0f}/spread, budget ${budget:,.0f}"
    if options_bp is not None:
        nbp = int(max(options_bp, 0) // (width * 100))
        if nbp < n:
            n, why = nbp, why + f", options BP caps at {nbp}"
    if n > C.MAX_CONTRACTS:
        n, why = C.MAX_CONTRACTS, why + f", MAX_CONTRACTS {C.MAX_CONTRACTS}"
    if n == 0:
        return 0, f"one spread's max loss ${per:,.0f} exceeds budget ${budget:,.0f}"
    return n, why


def max_loss(width: float, credit: float, qty: int) -> float:
    return max(width - credit, 0) * 100 * qty


# ---------------------------------------------------------------- price ladders
def _floor_tick(x: float) -> float:
    return math.floor(round(x / C.TICK, 6)) * C.TICK


def _ceil_tick(x: float) -> float:
    return math.ceil(round(x / C.TICK, 6)) * C.TICK


def entry_ladder(p: Pick, steps: int = C.ENTRY_STEPS, max_conc: float = C.ENTRY_MAX_CONCESSION) -> list[float]:
    """Credits to ask for, best first: mid, then walking toward the natural
    credit by at most ``max_conc`` of the distance. Never below the credit floor
    (MIN_CREDIT_FRAC of the width, and MIN_CREDIT_ABS). Rounded down to the tick."""
    floor = max(C.MIN_CREDIT_FRAC * p.width, C.MIN_CREDIT_ABS)
    mid, nat = p.mid_credit, p.natural_credit
    gap = max(mid - nat, 0) * max_conc
    out = []
    for k in range(steps + 1):
        c = round(_floor_tick(mid - gap * k / max(steps, 1)), 2)
        c = max(c, round(_ceil_tick(floor), 2))
        if not out or c < out[-1]:
            out.append(c)
    return out


def exit_ladder(mid_debit: float, natural_debit: float, steps: int = C.EXIT_STEPS,
                max_conc: float = C.EXIT_MAX_CONCESSION) -> list[float]:
    """Debits to pay, cheapest first: mid walking up to the natural debit."""
    gap = max(natural_debit - mid_debit, 0) * max_conc
    out = []
    for k in range(steps + 1):
        d = round(max(_ceil_tick(mid_debit + gap * k / max(steps, 1)), C.TICK), 2)
        if not out or d > out[-1]:
            out.append(d)
    return out


def exit_cutoff_price(natural_debit: float, width: float) -> float:
    """Aggressive fallback at the cutoff: natural + extra, never above the width."""
    return round(min(_ceil_tick(natural_debit + C.EXIT_CUTOFF_EXTRA), width), 2)


# ---------------------------------------------------------------- exits
def exit_prices(sq: OptionQuote | None, lq: OptionQuote | None) -> tuple[float | None, float | None, str | None]:
    """(mid_debit, natural_debit, note) to close a put credit spread: buy back the
    short leg, sell the long leg. The leg being sold (long) may bid 0, and a
    missing long quote counts as worth 0. The short leg needs an offer."""
    if sq is None or not sq.quoted:
        return None, None, "short leg has no offer"
    note = None
    if lq is None or not lq.quoted:
        lmid, lbid, note = 0.0, 0.0, "long leg unquoted (valued at 0)"
    else:
        lmid, lbid = lq.mid, lq.bid
        if lq.bid <= 0:
            note = "long leg bids 0"
    if sq.bid <= 0:
        note = (note + "; " if note else "") + "short leg bids 0"
    mid = round(max(sq.mid - lmid, 0.0), 4)
    natural = round(max(sq.ask - lbid, 0.0), 4)
    return mid, natural, note


def intrinsic_debit(short_k: float, long_k: float, spot: float | None) -> float | None:
    if spot is None or not math.isfinite(spot):
        return None
    return round(max(short_k - spot, 0.0) - max(long_k - spot, 0.0), 4)


def blind_exit_prices(short_k: float, long_k: float, spot: float | None) -> tuple[list[float], float]:
    """Ladder and cutoff price for a forced exit (expiry) with no usable option
    quotes: start at intrinsic value + $0.05, walk up a quarter of the width, and
    at the cutoff pay up to half the width over intrinsic, never above the width.
    Without a spot price the ladder walks from 5% to 50% of the width."""
    width = round(short_k - long_k, 4)
    intr = intrinsic_debit(short_k, long_k, spot)
    lo = (intr if intr is not None else 0.0) + 0.05
    if intr is None:
        lo = 0.05 * width
    hi = min(lo + 0.25 * width, width)
    lad = exit_ladder(min(lo, width), hi)
    cut = round(min(_ceil_tick(lo + 0.5 * width), width), 2)
    return lad, max(cut, lad[-1])


def exit_reason(*, entry_credit: float, cost_to_close: float | None, close: float, sma5: float,
                held_days: int, trading_days_to_expiry: int, end_of_day: bool,
                stop_mult: float | None = C.STOP_MULT) -> str | None:
    """Why to close now, or None. Order matches the backtest (credit_exit first,
    then time, then 5DMA). Intraday (``end_of_day=False``) only the price-based
    rules (take profit, stop) and the expiry guard fire; time and 5DMA rules
    use the close and are evaluated at the decision time (C.DECIDE_MINUTES_BEFORE_CLOSE).
    ``entry_credit`` should be the mid credit at entry (research: marks), not the fill."""
    if trading_days_to_expiry <= C.CLOSE_DTE_TRADING_DAYS:
        return "expiry_exit"
    if cost_to_close is not None and entry_credit > 0:
        if cost_to_close <= entry_credit * (1 - C.TAKE_PROFIT):
            return "take_profit"
        if stop_mult is not None and cost_to_close >= entry_credit * (1 + stop_mult):
            return "stop_loss"                  # research sl_mult semantics
    if not end_of_day:
        return None
    if held_days >= C.MAX_HOLD_DAYS:
        return "time_exit"
    if held_days >= 1 and math.isfinite(sma5) and close > sma5:
        return "sma5_exit"
    return None


# ---------------------------------------------------------------- broker positions
def group_spreads(positions: list[dict]) -> tuple[list[Spread], list[dict]]:
    """Pair option legs into put credit spreads: per (underlying, expiry), each
    short put is matched with the nearest lower-strike long put. Returns
    (spreads, orphans). Positions are Alpaca position dicts (symbol, qty, side,
    avg_entry_price, asset_class)."""
    legs: dict[tuple, list] = {}
    orphans = []
    for p in positions:
        occ = parse_occ(p.get("symbol", ""))
        if occ is None or p.get("asset_class", "us_option") not in ("us_option", "option"):
            orphans.append(p)
            continue
        root, exp, cp, k = occ
        if cp != "P":
            orphans.append(p)
            continue
        q = int(round(float(p["qty"])))
        if p.get("side") == "short" and q > 0:
            q = -q
        legs.setdefault((root, exp), []).append(
            {"symbol": p["symbol"], "strike": k, "qty": q, "avg": abs(float(p.get("avg_entry_price") or 0)), "raw": p})
    spreads = []
    for (root, exp), ls in sorted(legs.items()):
        shorts = sorted([l for l in ls if l["qty"] < 0], key=lambda l: -l["strike"])
        longs = sorted([l for l in ls if l["qty"] > 0], key=lambda l: -l["strike"])
        for s in shorts:
            for lg in longs:
                if lg["qty"] <= 0 or lg["strike"] >= s["strike"] or s["qty"] >= 0:
                    continue
                n = min(-s["qty"], lg["qty"])
                spreads.append(Spread(root, exp, s["symbol"], lg["symbol"], s["strike"], lg["strike"], n,
                                      s["avg"], lg["avg"]))
                s["qty"] += n
                lg["qty"] -= n
        for l in ls:
            if l["qty"] != 0:
                orphans.append({**l["raw"], "unmatched_qty": l["qty"]})
    return spreads, orphans


def trading_days_until(expiry: date, today: date, sessions: list[date]) -> int:
    """Trading sessions after ``today`` up to and including ``expiry``.
    ``sessions`` is a sorted list of known trading days covering the range."""
    return sum(1 for s in sessions if today < s <= expiry)


def held_trading_days(entry: date, today: date, sessions: list[date]) -> int:
    """Sessions after the entry day up to and including today (entry day = 0)."""
    return sum(1 for s in sessions if entry < s <= today)
