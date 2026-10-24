"""Entry/exit decisions for the dip put credit spread, as pure functions.

Nothing here talks to the network or the clock: every input is passed in, so
tests can drive it with fake chains and features. run.py wires it to the broker.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

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
                  options_bp: float | None = None, risk_per_position: float | None = None) -> tuple[int, str]:
    """Contracts such that this spread's max loss <= 25% of equity (or
    ``risk_per_position``) and the total max loss across positions (both
    sleeves) <= 50% of equity. Also capped by options buying power (Alpaca holds
    the width) and MAX_CONTRACTS. For an iron condor, ``width`` is the wider wing."""
    per = (width - credit) * 100
    if per <= 0 or equity <= 0:
        return 0, "non-positive max loss or equity"
    room_total = C.RISK_TOTAL * equity - open_max_loss
    rpp = C.RISK_PER_POSITION if risk_per_position is None else risk_per_position
    budget = min(rpp * equity, room_total)
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


# ================================================================ iron condor sleeve (spreads finalist F1)
# SPY iron condor: short put and short call near 0.10 delta, long wings 3% of spot beyond them,
# 50-70 DTE (target 60), held to expiry. Nothing above this line is used differently by it, and
# nothing below changes the dip sleeve.
def condor_id(underlying: str, expiry: date, put_long_k: float, put_short_k: float, call_short_k: float,
              call_long_k: float) -> str:
    return f"{underlying}-{expiry:%Y%m%d}-IC{put_long_k:g}/{put_short_k:g}/{call_short_k:g}/{call_long_k:g}"


@dataclass
class CondorPick:
    """A priced iron condor candidate (per-share prices)."""
    underlying: str
    expiry: date
    put_short: OptionQuote
    put_long: OptionQuote
    call_short: OptionQuote
    call_long: OptionQuote
    dte: int
    spot: float

    @property
    def legs(self) -> tuple[OptionQuote, OptionQuote, OptionQuote, OptionQuote]:
        return self.put_short, self.put_long, self.call_short, self.call_long

    @property
    def symbols(self) -> set[str]:
        return {q.symbol for q in self.legs}

    @property
    def id(self) -> str:
        return condor_id(self.underlying, self.expiry, self.put_long.strike, self.put_short.strike,
                         self.call_short.strike, self.call_long.strike)

    @property
    def put_width(self) -> float:
        return round(self.put_short.strike - self.put_long.strike, 4)

    @property
    def call_width(self) -> float:
        return round(self.call_long.strike - self.call_short.strike, 4)

    @property
    def width(self) -> float:
        """Only one side can finish in the money, so the max loss is the wider wing less the credit."""
        return max(self.put_width, self.call_width)

    @property
    def mid_credit(self) -> float:
        return round(self.put_short.mid - self.put_long.mid + self.call_short.mid - self.call_long.mid, 4)

    @property
    def natural_credit(self) -> float:          # sell at bid, buy at ask
        return round(self.put_short.bid - self.put_long.ask + self.call_short.bid - self.call_long.ask, 4)

    @property
    def far_credit(self) -> float:              # sell at ask, buy at bid
        return round(self.put_short.ask - self.put_long.bid + self.call_short.ask - self.call_long.bid, 4)


@dataclass
class Condor:
    """An open iron condor as held at the broker (qty = number of condors)."""
    underlying: str
    expiry: date
    put_short_symbol: str
    put_long_symbol: str
    call_short_symbol: str
    call_long_symbol: str
    put_short_strike: float
    put_long_strike: float
    call_short_strike: float
    call_long_strike: float
    qty: int
    put_short_avg: float = 0.0
    put_long_avg: float = 0.0
    call_short_avg: float = 0.0
    call_long_avg: float = 0.0

    @property
    def id(self) -> str:
        return condor_id(self.underlying, self.expiry, self.put_long_strike, self.put_short_strike,
                         self.call_short_strike, self.call_long_strike)

    @property
    def width(self) -> float:
        return max(round(self.put_short_strike - self.put_long_strike, 4),
                   round(self.call_long_strike - self.call_short_strike, 4))

    @property
    def broker_credit(self) -> float:
        return round(self.put_short_avg - self.put_long_avg + self.call_short_avg - self.call_long_avg, 4)


def condor_width(r: dict) -> float:
    """Wider wing of a stored condor record."""
    return max(float(r["put_short_strike"]) - float(r["put_long_strike"]),
               float(r["call_long_strike"]) - float(r["call_short_strike"]))


def condor_leg_symbols(r: dict) -> list[str]:
    return [r["put_short_symbol"], r["put_long_symbol"], r["call_short_symbol"], r["call_long_symbol"]]


def _nearest_abs_delta(quotes, target: float, tol: float, otm_key):
    """Valid quote whose |delta| is nearest ``target`` (within ``tol``); ties go
    to the further out-of-the-money strike (``otm_key`` larger = further OTM)."""
    best = None
    for q in quotes:
        if q.delta is None or not math.isfinite(q.delta) or not q.valid:
            continue
        d = round(abs(abs(q.delta) - target), 6)
        if best is None or d < best[0] or (d == best[0] and otm_key(q) > otm_key(best[1])):
            best = (d, q)
    if best is None or best[0] > tol:
        return None
    return best[1]


def _nearest_strike(quotes, target_k: float, short_k: float):
    """Valid quote nearest ``target_k`` strictly beyond the short strike (below it
    for a put wing, above it for a call wing); ties go to the narrower wing."""
    below = target_k < short_k
    best = None
    for q in quotes:
        if not q.valid or (q.strike >= short_k if below else q.strike <= short_k):
            continue
        d = round(abs(q.strike - target_k), 6)
        if best is None or d < best[0] or (d == best[0] and abs(q.strike - short_k) < abs(best[1].strike - short_k)):
            best = (d, q)
    return None if best is None else best[1]


def pick_condor(chain: list[OptionQuote], underlying: str, today: date, spot: float | None
                ) -> tuple[CondorPick | None, str]:
    """Short put / short call near 0.10 delta, wings 3% of spot beyond them, on
    the best-ranked 50-70 DTE expiry (the same ranking as the dip sleeve and the
    backtest engine: distance to 60 DTE, monthlies preferred). Falls back to the
    next expiry when one has no usable strikes."""
    if spot is None or not math.isfinite(spot) or spot <= 0:
        return None, "no spot price"
    puts = [q for q in chain if q.cp == "P" and q.underlying == underlying]
    calls = [q for q in chain if q.cp == "C" and q.underlying == underlying]
    if not puts or not calls:
        return None, "empty put or call chain"
    wing = C.CONDOR_WING_PCT * spot
    why = f"no expiry in the {C.CONDOR_DTE_MIN}-{C.CONDOR_DTE_MAX} DTE window with both puts and calls"
    exps = {q.expiry for q in puts} & {q.expiry for q in calls}
    for exp in rank_expiries(exps, today, C.CONDOR_DTE_MIN, C.CONDOR_DTE_MAX, C.CONDOR_DTE_TARGET):
        ps_all = [q for q in puts if q.expiry == exp]
        cs_all = [q for q in calls if q.expiry == exp]
        ps = _nearest_abs_delta([q for q in ps_all if q.strike < spot], C.CONDOR_SHORT_DELTA,
                                C.CONDOR_DELTA_TOLERANCE, otm_key=lambda q: -q.strike)
        cs = _nearest_abs_delta([q for q in cs_all if q.strike > spot], C.CONDOR_SHORT_DELTA,
                                C.CONDOR_DELTA_TOLERANCE, otm_key=lambda q: q.strike)
        if ps is None or cs is None:
            why = f"{exp}: no quoted {'put' if ps is None else 'call'} near {C.CONDOR_SHORT_DELTA}Δ"
            continue
        pl = _nearest_strike(ps_all, ps.strike - wing, ps.strike)
        cl = _nearest_strike(cs_all, cs.strike + wing, cs.strike)
        if pl is None or cl is None:
            why = f"{exp}: no quoted {'put' if pl is None else 'call'} wing 3% of spot beyond the short"
            continue
        return CondorPick(underlying, exp, ps, pl, cs, cl, (exp - today).days, float(spot)), "ok"
    return None, why


def check_condor(p: CondorPick, now: datetime | None = None,
                 max_age: float = C.MAX_OPTION_QUOTE_AGE_SECONDS) -> str | None:
    """Quote sanity and the credit/width guards for a condor. Returns a skip
    reason or None. Same rules as ``check_pick`` (stale / untimestamped quotes
    fail closed with ``now``), with the condor's credit floor."""
    names = ("short put", "long put", "short call", "long call")
    if min(p.put_width, p.call_width) < C.MIN_WIDTH:
        return f"wing {min(p.put_width, p.call_width):g} < {C.MIN_WIDTH:g}"
    if now is not None:
        for leg, name in zip(p.legs, names):
            age = leg.age_seconds(now)
            if age is None:
                return f"{name} quote has no timestamp (fail closed)"
            if age > max_age:
                return f"{name} quote is {age / 60:.0f} min old (stale)"
    for leg, name in zip(p.legs, names):
        if not leg.valid:
            return f"{name} has no two-sided quote"
        if leg.spread_frac > C.MAX_LEG_SPREAD_FRAC:
            return f"{name} bid/ask too wide ({leg.bid:.2f}/{leg.ask:.2f})"
    combo = p.far_credit - p.natural_credit
    if combo > max(C.MAX_COMBO_SPREAD_ABS, C.MAX_COMBO_SPREAD_FRAC * max(p.mid_credit, 0)):
        return f"combo bid/ask too wide ({p.natural_credit:.2f}..{p.far_credit:.2f})"
    c = p.mid_credit
    if c > C.CONDOR_MAX_CREDIT_FRAC * p.width:
        return f"credit {c:.2f} > {C.CONDOR_MAX_CREDIT_FRAC:.0%} of width {p.width:g} (bad quote guard)"
    if c <= C.CONDOR_MIN_CREDIT_ABS:
        return f"credit {c:.2f} <= ${C.CONDOR_MIN_CREDIT_ABS:.2f}"
    return None


def condor_entry_ladder(p: CondorPick, steps: int = C.ENTRY_STEPS,
                        max_conc: float = C.ENTRY_MAX_CONCESSION) -> list[float]:
    """Credits to ask for, best first: the net mid walking toward the natural
    credit by at most ``max_conc`` of the distance, never at or below the
    condor's credit floor. Rounded down to the tick."""
    floor = round(_ceil_tick(C.CONDOR_MIN_CREDIT_ABS + C.TICK), 2)
    mid, nat = p.mid_credit, p.natural_credit
    gap = max(mid - nat, 0) * max_conc
    out = []
    for k in range(steps + 1):
        c = max(round(_floor_tick(mid - gap * k / max(steps, 1)), 2), floor)
        if not out or c < out[-1]:
            out.append(c)
    return out


def condor_exit_prices(psq, plq, csq, clq) -> tuple[float | None, float | None, str | None]:
    """(mid_debit, natural_debit, note) to close a condor: each vertical priced
    with ``exit_prices`` (a long wing may bid 0 or be unquoted; each short leg
    needs an offer), then summed."""
    pm, pn, pnote = exit_prices(psq, plq)
    cm, cn, cnote = exit_prices(csq, clq)
    notes = "; ".join(f"{side}: {n}" for side, n in (("put", pnote), ("call", cnote)) if n) or None
    if pm is None or cm is None:
        return None, None, notes
    return round(pm + cm, 4), round(pn + cn, 4), notes


def condor_intrinsic(r: dict, spot: float | None) -> float | None:
    if spot is None or not math.isfinite(spot):
        return None
    put = max(float(r["put_short_strike"]) - spot, 0.0) - max(float(r["put_long_strike"]) - spot, 0.0)
    call = max(spot - float(r["call_short_strike"]), 0.0) - max(spot - float(r["call_long_strike"]), 0.0)
    return round(put + call, 4)


def condor_blind_exit_prices(r: dict, spot: float | None) -> tuple[list[float], float]:
    """Forced exit (expiry) with no usable quotes, as ``blind_exit_prices``:
    intrinsic + $0.05 walking up a quarter of the wider wing, cutoff at intrinsic
    + half the wing, never above the wing."""
    width = condor_width(r)
    intr = condor_intrinsic(r, spot)
    lo = (intr + 0.05) if intr is not None else 0.05 * width
    hi = min(lo + 0.25 * width, width)
    lad = exit_ladder(min(lo, width), hi)
    cut = round(min(_ceil_tick(lo + 0.5 * width), width), 2)
    return lad, max(cut, lad[-1])


def condor_exit_reason(*, expiry: date, today: date, trading_days_to_expiry: int, mode: str,
                       force_all: bool = False) -> str | None:
    """Held to expiry: no take-profit, no stop. The only exits are
    * "expiry_exit": the daily run on the last trading day before expiry
      (trading days to expiry <= CONDOR_CLOSE_DTE_TRADING_DAYS);
    * "expiry_day_sweep": anything still open on expiry day (sweep / intraday /
      daily; run.py refuses to send it at or after CONDOR_EXPIRY_DAY_DEADLINE);
    * "emergency_close": --close-all."""
    if force_all:
        return "emergency_close"
    if expiry <= today:
        return "expiry_day_sweep"
    if mode == "daily" and trading_days_to_expiry <= C.CONDOR_CLOSE_DTE_TRADING_DAYS:
        return "expiry_exit"
    return None


def group_book(positions: list[dict], condor_records: dict | None = None, dip_records: dict | None = None
               ) -> tuple[list[Spread], list[Condor], list[dict]]:
    """Split broker option legs into dip put spreads and iron condors.

    1. Every stored condor record (state store, sleeve "condor") claims its own
       four symbols, up to its quantity. Whole condors become ``Condor``s; any
       leftover of those legs is an orphan tagged ``sleeve: condor`` and is never
       handed to the dip grouping.
    2. Call legs only ever come from condors (the dip sleeve trades puts only).
       Untracked call verticals are paired with an untracked put vertical on the
       same expiry and adopted as a condor. Put legs a stored dip record accounts
       for are reserved and never used for this.
    3. Everything else goes through ``group_spreads`` unchanged.

    With no condor records and no call legs this is exactly ``group_spreads``."""
    condor_records = condor_records or {}
    def is_opt(p):
        return (parse_occ(p.get("symbol", "")) is not None
                and p.get("asset_class", "us_option") in ("us_option", "option"))
    if not condor_records and not any(is_opt(p) and parse_occ(p["symbol"])[2] == "C" for p in positions):
        spreads, orphans = group_spreads(positions)
        return spreads, [], orphans
    others, book = [], {}
    for p in positions:
        if not is_opt(p):
            others.append(p)
            continue
        q = int(round(float(p["qty"])))
        if p.get("side") == "short" and q > 0:
            q = -q
        root, exp, cp, k = parse_occ(p["symbol"])
        book[p["symbol"]] = {"qty": q, "avg": abs(float(p.get("avg_entry_price") or 0)), "raw": p,
                             "root": root, "exp": exp, "cp": cp, "k": k}

    def avail(sym, sign):
        b = book.get(sym)
        return max(b["qty"] * sign, 0) if b else 0

    condors, orphans = [], []

    def make(ps, pl, cs, cl, n):
        b = [book[s] for s in (ps, pl, cs, cl)]
        for s, sign in ((ps, -1), (pl, 1), (cs, -1), (cl, 1)):
            book[s]["qty"] -= sign * n
        return Condor(b[0]["root"], b[0]["exp"], ps, pl, cs, cl, b[0]["k"], b[1]["k"], b[2]["k"], b[3]["k"], n,
                      b[0]["avg"], b[1]["avg"], b[2]["avg"], b[3]["avg"])

    # 1. stored condors claim their legs
    for _cid, r in sorted(condor_records.items()):
        legs = list(zip(condor_leg_symbols(r), (-1, 1, -1, 1)))
        want = int(r.get("qty") or 0)
        have = [avail(s, sign) for s, sign in legs]
        n = min([want] + have)
        if n > 0:
            condors.append(make(*[s for s, _ in legs], n))
        for (s, sign), a in zip(legs, have):
            left = min(a, want) - max(n, 0)
            if left > 0:
                orphans.append({**book[s]["raw"], "unmatched_qty": sign * left, "sleeve": C.SLEEVE_CONDOR})
                book[s]["qty"] -= sign * left
    # 2. adopt untracked condors (call vertical + put vertical on one expiry)
    reserved: dict[str, int] = {}
    for r in (dip_records or {}).values():
        for s, sign in ((r.get("short_symbol"), -1), (r.get("long_symbol"), 1)):
            if s in book:
                reserved[s] = reserved.get(s, 0) + min(int(r.get("qty") or 0), avail(s, sign) - reserved.get(s, 0))
    for (root, exp) in sorted({(b["root"], b["exp"]) for b in book.values() if b["cp"] == "C" and b["qty"]}):
        same = [(s, b) for s, b in book.items() if b["root"] == root and b["exp"] == exp]
        free = lambda s, sign: avail(s, sign) - (reserved.get(s, 0) if book[s]["cp"] == "P" else 0)  # noqa: E731
        while True:
            sc = [(s, b) for s, b in same if b["cp"] == "C" and free(s, -1) > 0]
            lc = [(s, b) for s, b in same if b["cp"] == "C" and free(s, 1) > 0]
            sp = [(s, b) for s, b in same if b["cp"] == "P" and free(s, -1) > 0]
            lp = [(s, b) for s, b in same if b["cp"] == "P" and free(s, 1) > 0]
            pair_c = next(((s, l) for s, sb in sorted(sc, key=lambda x: x[1]["k"])
                           for l, lb in sorted(lc, key=lambda x: x[1]["k"]) if lb["k"] > sb["k"]), None)
            pair_p = next(((s, l) for s, sb in sorted(sp, key=lambda x: -x[1]["k"])
                           for l, lb in sorted(lp, key=lambda x: -x[1]["k"]) if lb["k"] < sb["k"]), None)
            if pair_c is None or pair_p is None:
                break
            n = min(free(pair_p[0], -1), free(pair_p[1], 1), free(pair_c[0], -1), free(pair_c[1], 1))
            condors.append(make(pair_p[0], pair_p[1], pair_c[0], pair_c[1], n))
    # 3. the rest: the dip sleeve's grouping, on the remaining quantities
    rest = list(others)
    for s, b in book.items():
        if b["qty"] == 0:
            continue
        rest.append({**b["raw"], "qty": abs(b["qty"]), "side": "short" if b["qty"] < 0 else "long"})
    spreads, orph = group_spreads(rest)
    for o in orph:
        if "unmatched_qty" not in o and parse_occ(o.get("symbol", "")):
            o = {**o, "unmatched_qty": -int(o["qty"]) if o.get("side") == "short" else int(o["qty"])}
        orphans.append(o)
    return spreads, condors, orphans


# ================================================================ round 3: ex-dividend early-exercise guard, slippage
def third_friday(year: int, month: int) -> date:
    d = date(year, month, 15)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def exdiv_dates(symbol: str, start: date, end: date, sessions: list[date] | None = None,
                known: list[date] | None = None) -> list[date]:
    """Expected ex-dividend dates of ``symbol`` in [start, end].

    SPY (EXDIV_QUARTER_MONTHS): the third Friday of Mar/Jun/Sep/Dec, moved to the previous
    session when that Friday is not a trading day (2026-06-18 for the Juneteenth Friday). Alpaca's
    corporate-actions history confirms the rule (2024-12-20 ... 2026-09-18). ``known`` (announced
    dates from Alpaca) replace the rule date in the same month."""
    months = C.EXDIV_QUARTER_MONTHS.get(symbol)
    out = set()
    known = sorted(known or [])
    if months:
        sset = set(sessions or [])
        lo, hi = (min(sset), max(sset)) if sset else (None, None)
        y, m = start.year, start.month
        while date(y, m, 1) <= end:
            if m in months:
                d = third_friday(y, m)
                if sset:
                    while lo <= d <= hi and d not in sset:
                        d -= timedelta(days=1)
                if not any(k.year == d.year and k.month == d.month for k in known):
                    out.add(d)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    out |= set(known)
    return sorted(d for d in out if start <= d <= end)


def last_cum_div_session(ex_date: date, sessions: list[date]) -> date:
    """The last trading session before ``ex_date`` (the day a call holder exercises early)."""
    prior = [s for s in sessions if s < ex_date]
    if prior:
        return max(prior)
    d = ex_date - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def early_exercise_risk(call_strike: float, call: OptionQuote | None, spot: float | None,
                        dividend: float) -> tuple[bool, str]:
    """(at risk, why) for a SHORT call on the last cum-dividend session: the standard
    rational-exercise test, in the money and extrinsic value (call mid - intrinsic) below the
    dividend. Without a usable quote an ITM call counts as at risk (fail safe)."""
    if spot is None or not math.isfinite(spot):
        return False, "no spot"
    intrinsic = spot - call_strike
    if intrinsic <= C.EXDIV_ITM_BUFFER:
        return False, f"short call {call_strike:g} out of the money (spot {spot:.2f})"
    if call is None or not call.quoted:
        return True, f"short call {call_strike:g} ITM by {intrinsic:.2f}, no quote (fail safe)"
    extrinsic = call.mid - intrinsic
    if extrinsic < dividend:
        return True, (f"short call {call_strike:g} ITM by {intrinsic:.2f}, extrinsic {extrinsic:.2f} "
                      f"< dividend {dividend:.2f}")
    return False, f"short call {call_strike:g} ITM, extrinsic {extrinsic:.2f} >= dividend {dividend:.2f}"


def slippage(credit: bool, mid: float | None, fill: float | None) -> float | None:
    """Per-share cost of the fill vs the planned mid: positive = worse than mid
    (a credit received below the mid, or a debit paid above it)."""
    if mid is None or fill is None:
        return None
    return round((mid - fill) if credit else (fill - mid), 4)


def bt_slip(price: float) -> float:
    """The backtest's per-leg slippage (optionsbot.engine.Costs defaults)."""
    return min(C.BT_SLIP_CAP, max(C.BT_SLIP_ABS, C.BT_SLIP_PCT * price))


def backtest_credit(sells, buys) -> float:
    """Credit the backtest would have booked: each leg at its mark (here the live mid) -/+ slip."""
    return round(sum(q.mid - bt_slip(q.mid) for q in sells) - sum(q.mid + bt_slip(q.mid) for q in buys), 4)


def natural_ladder(mid: float, natural: float, bt_credit: float, steps: int = C.ENTRY_STEPS,
                   min_credit: float = 0.0) -> list[float]:
    """Credits best first: from the mid to target = max(natural, backtest credit, min_credit), evenly.
    The last price is the target rounded UP to the tick, so the ladder never asks for less than the
    natural (which a paper fill needs and a real fill never needs to beat) nor less than the
    research assumed. Prices strictly decrease; never above the mid."""
    target = max(natural, bt_credit, min_credit)
    top = round(_floor_tick(mid), 2)
    last = round(_ceil_tick(target), 2)
    if last >= top:
        return [top] if top >= last else [last]
    out = []
    for k in range(steps + 1):
        c = round(_floor_tick(mid - (mid - target) * k / max(steps, 1)), 2)
        c = max(c, last)
        if not out or c < out[-1]:
            out.append(c)
    if out[-1] != last:
        out.append(last)
    return out


def dip_entry_prices(p: Pick) -> list[float]:
    """DIP_ENTRY_LADDER: "halfway" (entry_ladder) or "natural" (natural_ladder with the credit floor)."""
    if C.DIP_ENTRY_LADDER != "natural":
        return entry_ladder(p)
    floor = max(C.MIN_CREDIT_FRAC * p.width, C.MIN_CREDIT_ABS)
    return natural_ladder(p.mid_credit, p.natural_credit, backtest_credit([p.short], [p.long]),
                          min_credit=floor)


def condor_entry_prices(p: CondorPick) -> list[float]:
    """CONDOR_ENTRY_LADDER: "halfway" (condor_entry_ladder) or "natural"."""
    if C.CONDOR_ENTRY_LADDER != "natural":
        return condor_entry_ladder(p)
    return natural_ladder(p.mid_credit, p.natural_credit,
                          backtest_credit([p.put_short, p.call_short], [p.put_long, p.call_long]),
                          min_credit=C.CONDOR_MIN_CREDIT_ABS + C.TICK)


def take_profit_debit(entry_mid: float) -> float:
    """Resting take-profit price: the research rule, cost to close <= entry mid * (1 - TAKE_PROFIT),
    rounded down to the tick (never above the rule's price), at least one tick."""
    return round(max(_floor_tick(entry_mid * (1 - C.TAKE_PROFIT)), C.TICK), 2)
