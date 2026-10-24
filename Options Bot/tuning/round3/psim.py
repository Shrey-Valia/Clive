"""Proxy-chain option backtester (daily closes) for long histories.

Option prices come from the IV-surface proxy (surface.py) on top of the Cboe vol-index term
structure; underlying prices are real daily bars. Mechanics mirror optionsbot.engine:
fills at mark -/+ slip (slip = clip(2% x price, $0.03, $0.25) per leg, x cost_mult),
$0.03/contract fees, cash-settled at intrinsic on expiry-day close, spreads reserve width x 100,
sizing by max loss = (width - credit) x 100 <= risk_frac x equity. Decisions at the close.

A Book holds one cash account and several sleeves (dip PCS, iron condor, tail hedge); every
sleeve sees the shared open-risk total (risk_total).
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import r3lib as L
import surface as SF
from common import CACHE, HERE
from optionsbot import indicators as ind

warnings.filterwarnings("ignore", category=RuntimeWarning)
np.seterr(all="ignore")

EVENTS = pd.read_csv(HERE / "events.csv", parse_dates=["date"])


# ------------------------------------------------------------------ underlyings
def load_ohlc(sym: str) -> pd.DataFrame:
    """Daily OHLC. ETFs: raw Alpaca SIP bars (2016-) from round3/ohlc/ (fetch_ohlc.py), with
    split-adjusted s_* columns for the indicators; 'SPX10': Cboe SPX close / 10 (1990-),
    close-only (high = low = close, so no IBS)."""
    if sym == "SPX10":
        s = L.cboe("SPX")
        s = s[s.index >= "1989-01-01"] / 10.0
        df = pd.DataFrame({"open": s, "high": s, "low": s, "close": s})
        for c in ("open", "high", "low", "close"):
            df["s_" + c] = df[c]
        return df
    p = HERE / "ohlc" / f"{sym}.csv"
    df = pd.read_csv(p, parse_dates=["date"]).set_index("date").sort_index()
    return df[["open", "high", "low", "close", "s_open", "s_high", "s_low", "s_close"]].astype(float)


VOL_INDEX = {"SPY": None, "SPX10": None, "IWM": "RVX", "QQQ": "VXN", "DIA": "VXD"}
SURF = {"SPY": "SPY", "SPX10": "SPY", "IWM": "IWM", "QQQ": "QQQ"}


class U:
    """One underlying: bars, indicators, vol state, proxy surface."""

    def __init__(self, sym: str, surf_name: str | None = None, iv_ratio: pd.Series | None = None,
                 div: float | None = None):
        self.sym = sym
        b = load_ohlc(sym)
        self.bars = b
        self.idx = b.index
        c = b["s_close"]
        sb = b[["s_open", "s_high", "s_low", "s_close"]].set_axis(["open", "high", "low", "close"], axis=1)
        f = pd.DataFrame(index=b.index)
        f["close"] = c
        f["rsi2"] = ind.rsi(c, 2)
        f["ibs"] = ind.ibs(sb) if (sb["high"] != sb["low"]).any() else np.nan
        for n in (5, 10, 20, 50, 100, 200):
            f[f"sma{n}"] = ind.sma(c, n)
        f["streak"] = ind.streak(c)
        f["ret1"] = c.pct_change()
        f["rv20"] = np.log(c).diff().rolling(20).std() * math.sqrt(252)
        vp = L.vol_panel(b.index)
        self.vp = vp
        f = f.join(vp)
        self.f = f
        vi = VOL_INDEX.get(sym, "missing")
        if iv_ratio is not None:
            self.ratio = iv_ratio.reindex(b.index).ffill()
        elif vi is None:
            self.ratio = pd.Series(1.0, index=b.index)
        elif vi == "missing":
            # no Cboe vol index for this ETF: scale VIX by the 60-day realized-vol ratio to SPY
            # (crude; used only for the signal-overlap / correlation study, not for verdicts)
            spy = load_ohlc("SPY")["close"].reindex(b.index).ffill()
            rv = np.log(b["close"]).diff().rolling(60).std()
            rvs = np.log(spy).diff().rolling(60).std()
            self.ratio = (rv / rvs).clip(0.5, 4.0).bfill()
        else:
            self.ratio = (vp[vi] / vp["VIX"]).ffill()
        self.surf = SF.Surface.load(surf_name or SURF.get(sym, "SPY"))
        self.q = L.DIV.get(sym, 0.015) if div is None else div
        self.strike_step = 1.0
        # SPX/10 in 1990 is ~$33: rescale strike grid, slippage floors/caps, fees and credit floors
        # by spot/600 so the cost structure matches today's SPY at every date (returns unchanged)
        self.scaled = sym == "SPX10"
        # arrays for speed
        self._c = b["close"].to_dict()          # raw close: strikes and option prices
        self._vols = vp[["VIX9D", "VIX", "VIX3M", "VIX6M"]].to_numpy()
        self._pos = {d: i for i, d in enumerate(b.index)}
        self._vix = vp["VIX"].to_numpy()
        self._ts = vp["TS"].to_numpy()
        self._r = vp["r"].fillna(0.02).to_numpy()
        self._ratio = self.ratio.to_numpy()

    def spot(self, d):
        return self._c[d]

    def scale(self, d):
        return self._c[d] / 600.0 if self.scaled else 1.0

    def step(self, d):
        return self.strike_step * self.scale(d)

    def iv(self, d, K, T):
        i = self._pos[d]
        K = np.atleast_1d(np.asarray(K, float))
        T = np.broadcast_to(np.asarray(T, float), K.shape)
        base = L.term_vol(np.repeat(self._vols[i][None, :], len(K), 0), T) * self._ratio[i]
        S = self._c[d]
        F = S * np.exp((self._r[i] - self.q) * T)
        z = np.log(K / F) / (base * np.sqrt(np.maximum(T, 1e-6)))
        vix = np.full(len(K), self._vix[i])
        ts = np.full(len(K), self._ts[i])
        return self.surf.iv(base, z, T, vix, ts)

    def price(self, d, K, T, cp):
        K = np.atleast_1d(np.asarray(K, float))
        i = self._pos[d]
        sig = self.iv(d, K, T)
        cpn = np.where(np.asarray(cp) == "C", 1.0, -1.0) if not np.isscalar(cp) or isinstance(cp, str) else cp
        if isinstance(cp, str):
            cpn = 1.0 if cp == "C" else -1.0
        S = self._c[d]
        p = L.bs(S, K, T, self._r[i], self.q, sig, cpn)
        dl = L.delta(S, K, T, self._r[i], self.q, sig, cpn)
        return p, sig, dl


# ------------------------------------------------------------------ expiries
WEEKLY_HORIZON = 45     # Friday weeklies are listed ~6 weeks ahead (they only print that close in)


def expiry_calendar(idx: pd.DatetimeIndex, start="1989-01-01", end="2027-12-31"):
    """SPY-style expiries: Friday weeklies and 3rd-Friday monthlies (Thursday when the Friday is
    not a trading day) plus end-of-month expiries (last trading day). Returns a list of
    (expiry_trading_day, kind) with kind in {"M", "W", "EOM"}."""
    tset = set(idx)
    out = {}
    last = idx[-1]

    def to_trading(d):
        k = 0
        while d <= last and d not in tset and k < 4:
            d = d - pd.Timedelta(days=1)
            k += 1
        return d if (d > last or d in tset) else None

    for fri in pd.date_range(start, end, freq="W-FRI"):
        d = to_trading(fri)
        if d is not None:
            out[d] = "M" if 15 <= fri.day <= 21 else "W"
    for me in pd.date_range(start, end, freq="ME"):
        d = to_trading(me)
        if d is not None and out.get(d) != "M":
            out[d] = "EOM"
    return sorted(out.items())


def rank_expiries(cal, d, dte_min, dte_max, dte_target, monthly_only=False, weeklies=True, eom=True):
    """The engine's ranking (monthly 0, Friday +4, other +10), restricted to what is listed."""
    res = []
    for e, kind in cal:
        dte = (e - d).days
        if dte < dte_min or dte > dte_max:
            continue
        if monthly_only and kind != "M":
            continue
        if kind == "W" and (not weeklies or dte > WEEKLY_HORIZON):
            continue
        if kind == "EOM" and not eom:
            continue
        pen = 0.0 if kind == "M" else (4.0 if e.weekday() == 4 or kind == "W" else 10.0)
        if kind == "EOM" and dte <= WEEKLY_HORIZON and e.weekday() == 4:
            pen = 4.0
        res.append((abs(dte - dte_target) + pen, e))
    return [e for _, e in sorted(res)]


# ------------------------------------------------------------------ book
@dataclass
class Costs:
    slip_abs: float = 0.03
    slip_pct: float = 0.02
    slip_cap: float = 0.25
    fee: float = 0.03
    mult: float = 1.0               # global multiplier
    stress_vix: float = 30.0        # above this VIX, multiply slip by stress_mult
    stress_mult: float = 1.0

    def slip(self, price, vix=0.0, k=1.0):
        s = min(self.slip_cap * k, max(self.slip_abs * k, self.slip_pct * price)) * self.mult
        if vix > self.stress_vix:
            s *= self.stress_mult
        return s


@dataclass
class Leg:
    K: float
    cp: str
    qty: int        # signed contracts
    mark: float = 0.0
    delta: float = 0.0


@dataclass
class Pos:
    sleeve: str
    sym: str
    kind: str
    expiry: pd.Timestamp
    legs: list
    open_date: pd.Timestamp
    entry_cash: float
    credit_mark: float          # per-share credit at marks (premium for debits: negative)
    max_loss: float             # $ total
    reserve: float
    n: int
    meta: dict = field(default_factory=dict)
    exit_cash: float = 0.0
    close_date: pd.Timestamp | None = None
    reason: str = ""

    def value(self):
        return sum(l.qty * 100 * l.mark for l in self.legs)


class Book:
    def __init__(self, capital, start, end, sleeves, costs=None, risk_total=0.5,
                 cal_index=None):
        self.capital = float(capital)
        self.cash = float(capital)
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.sleeves = sleeves
        self.costs = costs or Costs()
        self.risk_total = risk_total
        self.positions: list[Pos] = []
        self.closed: list[Pos] = []
        self.records = []
        us = {}
        for s in sleeves:
            for u in s.unders():
                us[u.sym] = u
        self.us = us
        ref = cal_index if cal_index is not None else next(iter(us.values())).idx
        self.dates = ref[(ref >= self.start) & (ref <= self.end)]
        self.cal = expiry_calendar(ref)
        for s in sleeves:
            s.book = self

    # -- helpers
    def equity(self):
        return self.cash + sum(p.value() for p in self.positions)

    def reserved(self):
        return sum(p.reserve for p in self.positions)

    def free_cash(self):
        return self.cash - self.reserved()

    def open_risk(self):
        return sum(p.max_loss for p in self.positions if p.max_loss > 0)

    def vix(self, d):
        u = next(iter(self.us.values()))
        i = u._pos.get(d)
        return float(u._vix[i]) if i is not None else 0.0

    def open(self, sleeve, u, d, legs, kind, expiry, max_loss_per, reserve_per, n, meta=None):
        if n <= 0:
            return None
        vix = self.vix(d)
        k = u.scale(d)
        cf = 0.0
        nct = 0
        cm = 0.0
        for l in legs:
            s = self.costs.slip(l.mark, vix, k)
            fill = l.mark + s if l.qty > 0 else max(l.mark - s, 0.0)
            cf -= l.qty * n * 100 * fill
            cm -= l.qty * l.mark
            nct += abs(l.qty) * n
        cf -= nct * self.costs.fee * k
        built = [Leg(l.K, l.cp, l.qty * n, l.mark, l.delta) for l in legs]
        p = Pos(sleeve.name, u.sym, kind, expiry, built, d, cf, cm, max_loss_per * n, reserve_per * n, n,
                dict(meta or {}))
        if self.free_cash() + cf - p.reserve < -1e-6:
            return None
        self.cash += cf
        self.positions.append(p)
        return p

    def close(self, p, d, reason, legs=None):
        """Close all (or the given) legs at marks -/+ slip."""
        vix = self.vix(d)
        u = self.us[p.sym]
        k = u.scale(d) if d in u._pos else 1.0
        cf, nct = 0.0, 0
        todo = legs if legs is not None else list(p.legs)
        for l in todo:
            s = self.costs.slip(l.mark, vix, k)
            fill = l.mark - s if l.qty > 0 else l.mark + s
            fill = max(fill, 0.0)
            cf += l.qty * 100 * fill
            nct += abs(l.qty)
            p.legs.remove(l)
        cf -= nct * self.costs.fee * k
        self.cash += cf
        p.exit_cash += cf
        if not p.legs:
            p.close_date, p.reason = d, reason
            p.reserve = 0.0
            p.max_loss = 0.0
            self.positions.remove(p)
            self.closed.append(p)

    def mark(self, d):
        for p in self.positions:
            u = self.us[p.sym]
            T = max((p.expiry - d).days, 0) / 365.0
            if T <= 0:
                S = u.spot(d)
                for l in p.legs:
                    l.mark = max(0.0, S - l.K) if l.cp == "C" else max(0.0, l.K - S)
                continue
            Ks = np.array([l.K for l in p.legs])
            for cp in ("C", "P"):
                ls = [l for l in p.legs if l.cp == cp]
                if not ls:
                    continue
                pr, iv, dl = u.price(d, np.array([l.K for l in ls]), T, cp)
                for l, a, b in zip(ls, pr, dl):
                    l.mark, l.delta = float(a), float(b)

    def expire(self, d):
        for p in list(self.positions):
            if p.expiry <= d:
                u = self.us[p.sym]
                S = u.spot(d) if d in u._pos else u.spot(u.idx[u.idx <= p.expiry][-1])
                cf = 0.0
                for l in p.legs:
                    intr = max(0.0, S - l.K) if l.cp == "C" else max(0.0, l.K - S)
                    cf += l.qty * 100 * intr
                self.cash += cf
                p.exit_cash += cf
                p.legs = []
                p.close_date, p.reason = d, "expired"
                p.reserve = p.max_loss = 0.0
                self.positions.remove(p)
                self.closed.append(p)

    def run(self):
        for d in self.dates:
            self.d = d
            self.mark(d)
            self.expire(d)
            for s in self.sleeves:
                s.manage(d)
            for s in self.sleeves:
                s.enter(d)
            self.records.append((d, self.equity(), self.open_risk(), len(self.positions)))
        return self

    # -- output
    def equity_series(self):
        return pd.Series([r[1] for r in self.records], index=[r[0] for r in self.records])

    def trades(self):
        rows = []
        for p in self.closed + self.positions:
            pnl = p.entry_cash + p.exit_cash + (p.value() if p.close_date is None else 0.0)
            rows.append({"sleeve": p.sleeve, "sym": p.sym, "kind": p.kind, "open_date": p.open_date,
                         "close_date": p.close_date, "expiry": p.expiry, "n": p.n,
                         "credit": p.credit_mark, "entry_cash": p.entry_cash, "pnl": pnl,
                         "max_loss0": p.meta.get("max_loss0", np.nan), "reason": p.reason or "open",
                         **{k: v for k, v in p.meta.items() if k in ("width", "Ks", "Kl", "spot0", "vix0")}})
        return pd.DataFrame(rows)


# ------------------------------------------------------------------ strike selection
def pick_by_delta(u, d, expiry, cp, target, step=None):
    """Strike whose proxy |delta| is closest to target (strikes on a `step` grid)."""
    S = u.spot(d)
    T = (expiry - d).days / 365.0
    step = step or u.step(d)
    sig0 = max(u.iv(d, np.array([S]), T)[0], 0.05)
    span = max(6 * sig0 * math.sqrt(T) * S, 3 * step)
    lo = math.floor((S - span) / step) * step if cp == "P" else math.floor(S / step) * step
    hi = math.ceil(S / step) * step if cp == "P" else math.ceil((S + span) / step) * step
    Ks = np.arange(max(lo, step), hi + step / 2, step)
    pr, iv, dl = u.price(d, Ks, T, cp)
    j = int(np.argmin(np.abs(np.abs(dl) - target)))
    return float(Ks[j]), float(pr[j]), float(dl[j])


def price_strike(u, d, expiry, cp, K):
    T = (expiry - d).days / 365.0
    pr, iv, dl = u.price(d, np.array([K]), T, cp)
    return float(pr[0]), float(dl[0])


def snap(K, step):
    return round(K / step) * step


# ------------------------------------------------------------------ sleeves
class Sleeve:
    name = "sleeve"

    def unders(self):
        return []

    def manage(self, d):
        pass

    def enter(self, d):
        pass

    def mine(self, sym=None):
        return [p for p in self.book.positions if p.sleeve == self.name and (sym is None or p.sym == sym)]

    def budget(self, risk_frac):
        b = self.book
        eq = b.equity()
        return max(0.0, min(eq * risk_frac, eq * b.risk_total - b.open_risk(), b.free_cash()))


def kill_blocked(u, d, ratio=1.15, vmax=40.0):
    i = u._pos[d]
    return bool(u._ts[i] > ratio or u._vix[i] > vmax)


class DipPCS(Sleeve):
    """RSI2/IBS dip in an uptrend -> short-dated put credit spread (tuning/signals finalist)."""
    defaults = dict(rsi_lo=10.0, ibs_lo=0.2, trend="sma200", signal="rsi_ibs",
                    pct_below=None, pct_sma=10, down_days=None,
                    short_delta=0.25, long_delta=0.10, width_pct=None,
                    dte_min=7, dte_max=14, dte_target=10, tp=0.5, max_days=5, exit_sma5=True,
                    sl_mult=None, risk_frac=0.25, kill=True, vix_max=None, vix_min=None, ts_max=None,
                    min_credit=0.05, max_credit_frac=0.60, size_mode="risk", vol_target=None)

    def __init__(self, unders, name="dip", **kw):
        self.p = {**self.defaults, **kw}
        self._u = unders
        self.name = name

    def unders(self):
        return self._u

    def signal(self, u, d):
        p = self.p
        r = u.f.loc[d]
        if p["trend"] == "sma200" and not (r["close"] > r["sma200"]):
            return False
        if p["trend"] == "sma100" and not (r["close"] > r["sma100"]):
            return False
        sg = p["signal"]
        if sg == "rsi_ibs":
            ok = r["rsi2"] < p["rsi_lo"]
            if p["ibs_lo"] is not None:
                ok = ok and r["ibs"] < p["ibs_lo"]
            return bool(ok)
        if sg == "rsi":
            return bool(r["rsi2"] < p["rsi_lo"])
        if sg == "ibs":
            return bool(r["ibs"] < p["ibs_lo"])
        if sg == "pct_below":
            return bool(r["close"] < r[f"sma{p['pct_sma']}"] * (1 - p["pct_below"]))
        if sg == "down_days":
            return bool(r["streak"] <= -p["down_days"])
        if sg == "always":
            return True
        raise ValueError(sg)

    def manage(self, d):
        p = self.p
        b = self.book
        for pos in list(self.mine()):
            u = b.us[pos.sym]
            if d not in u._pos:
                continue
            r = u.f.loc[d]
            cost = -pos.value() / (100 * pos.n)
            cr = pos.credit_mark
            reason = None
            if p["tp"] is not None and cost <= cr * (1 - p["tp"]):
                reason = "take_profit"
            elif p["sl_mult"] is not None and cost >= cr * (1 + p["sl_mult"]):
                reason = "stop_loss"
            held = u._pos[d] - u._pos[pos.open_date]
            if reason is None and p["max_days"] is not None and held >= p["max_days"]:
                reason = "time_exit"
            if reason is None and p["exit_sma5"] and held >= 1 and r["close"] > r["sma5"]:
                reason = "sma5_exit"
            if reason is None and (pos.expiry - d).days <= 0:
                reason = "expiry"
            if reason:
                b.close(pos, d, reason)

    def enter(self, d):
        p = self.p
        b = self.book
        od = p.get("only_dates")
        if od is not None and d not in od:
            return
        for u in self._u:
            if d not in u._pos or self.mine(u.sym):
                continue
            if p["kill"] and kill_blocked(u, d):
                continue
            i = u._pos[d]
            if p["vix_max"] is not None and u._vix[i] > p["vix_max"]:
                continue
            if p["vix_min"] is not None and u._vix[i] < p["vix_min"]:
                continue
            if p["ts_max"] is not None and u._ts[i] > p["ts_max"]:
                continue
            if not self.signal(u, d):
                continue
            ex = rank_expiries(b.cal, d, p["dte_min"], p["dte_max"], p["dte_target"])
            if not ex:
                continue
            e = ex[0]
            Ks, ps, ds = pick_by_delta(u, d, e, "P", p["short_delta"])
            if p["width_pct"]:
                Kl = snap(Ks - p["width_pct"] * u.spot(d), u.step(d))
                pl, dll = price_strike(u, d, e, "P", Kl)
            else:
                Kl, pl, dll = pick_by_delta(u, d, e, "P", p["long_delta"])
            if Kl >= Ks:
                continue
            width = Ks - Kl
            credit = ps - pl
            if credit <= p["min_credit"] * u.scale(d) or credit > p["max_credit_frac"] * width:
                continue
            ml = (width - credit) * 100
            budget = self.budget(p["risk_frac"])
            if p["vol_target"]:
                budget *= min(1.0, p["vol_target"] / max(u._vix[i], 1.0))
            n = int(budget // ml)
            if n <= 0:
                continue
            legs = [Leg(Ks, "P", -1, ps, ds), Leg(Kl, "P", 1, pl, dll)]
            b.open(self, u, d, legs, "pcs", e, ml, width * 100, n,
                   meta={"width": width, "Ks": Ks, "Kl": Kl, "spot0": u.spot(d), "vix0": u._vix[i],
                         "max_loss0": ml * n})


class Condor(Sleeve):
    """Iron condor (spreads finalist F1 by default) with management options."""
    defaults = dict(put_delta=0.10, call_delta=0.10, width_pct=0.03, dte_min=50, dte_max=70,
                    dte_target=60, monthly_only=False, tp=None, sl_mult=None, close_dte=None,
                    breach_delta=None, breach_action="close", risk_frac=0.20, kill=True,
                    vix_max=None, vix_min=None, ts_max=None, event_skip=None, events=("FOMC", "CPI"),
                    min_credit=0.10, max_credit_frac=0.60, close_before_expiry=True, puts_only=False,
                    eom=True)

    def __init__(self, u, name="condor", **kw):
        self.p = {**self.defaults, **kw}
        self.u = u
        self.name = name
        ev = EVENTS[EVENTS.event.isin(self.p["events"])].date
        self._ev = np.array(sorted(ev.values), dtype="datetime64[ns]")

    def unders(self):
        return [self.u]

    def _event_soon(self, d):
        k = self.p["event_skip"]
        if not k:
            return False
        u = self.u
        i = u._pos[d]
        hi = u.idx[min(i + k, len(u.idx) - 1)]
        j = np.searchsorted(self._ev, np.datetime64(d), side="right")
        return j < len(self._ev) and self._ev[j] <= np.datetime64(hi)

    def manage(self, d):
        p = self.p
        b = self.book
        u = self.u
        for pos in list(self.mine()):
            dte = (pos.expiry - d).days
            cost = -pos.value() / (100 * pos.n)
            cr = pos.credit_mark
            reason = None
            if p["tp"] is not None and cost <= cr * (1 - p["tp"]):
                reason = "take_profit"
            elif p["sl_mult"] is not None and cost >= cr * (1 + p["sl_mult"]):
                reason = "stop_loss"
            elif p["close_dte"] is not None and dte <= p["close_dte"]:
                reason = "dte_exit"
            elif p["close_before_expiry"] and d in u._pos:
                i = u._pos[d]
                if i + 1 < len(u.idx) and u.idx[i + 1] >= pos.expiry:
                    reason = "day_before_expiry"
            if reason:
                b.close(pos, d, reason)
                continue
            if p["breach_delta"] is not None:
                for cp in ("P", "C"):
                    sh = [l for l in pos.legs if l.cp == cp and l.qty < 0]
                    if sh and abs(sh[0].delta) >= p["breach_delta"]:
                        if p["breach_action"] == "close":
                            b.close(pos, d, f"breach_{cp}")
                        else:      # close the tested vertical only
                            side = [l for l in pos.legs if l.cp == cp]
                            b.close(pos, d, f"breach_{cp}", legs=side)
                            if pos.legs:
                                rest = [l for l in pos.legs]
                                w = abs(rest[0].K - rest[1].K) if len(rest) == 2 else 0
                                pos.max_loss = max(0.0, (w * 100 * pos.n) - (pos.entry_cash + pos.exit_cash))
                                pos.reserve = w * 100 * pos.n
                        break

    def enter(self, d):
        p = self.p
        b = self.book
        u = self.u
        if d not in u._pos or self.mine():
            return
        od = p.get("only_dates")
        if od is not None and d not in od:
            return
        i = u._pos[d]
        if p["kill"] and kill_blocked(u, d):
            return
        if p["vix_max"] is not None and u._vix[i] > p["vix_max"]:
            return
        if p["vix_min"] is not None and u._vix[i] < p["vix_min"]:
            return
        if p["ts_max"] is not None and u._ts[i] > p["ts_max"]:
            return
        if self._event_soon(d):
            return
        ex = rank_expiries(b.cal, d, p["dte_min"], p["dte_max"], p["dte_target"], p["monthly_only"],
                           eom=p["eom"])
        if not ex:
            return
        e = ex[0]
        S = u.spot(d)
        Kps, pps, dps = pick_by_delta(u, d, e, "P", p["put_delta"])
        Kpl = snap(Kps - p["width_pct"] * S, u.step(d))
        ppl, dpl = price_strike(u, d, e, "P", Kpl)
        legs = [Leg(Kps, "P", -1, pps, dps), Leg(Kpl, "P", 1, ppl, dpl)]
        credit = pps - ppl
        width = Kps - Kpl
        if not p["puts_only"]:
            Kcs, pcs, dcs = pick_by_delta(u, d, e, "C", p["call_delta"])
            Kcl = snap(Kcs + p["width_pct"] * S, u.step(d))
            pcl, dcl = price_strike(u, d, e, "C", Kcl)
            legs += [Leg(Kcs, "C", -1, pcs, dcs), Leg(Kcl, "C", 1, pcl, dcl)]
            credit += pcs - pcl
            width = max(width, Kcl - Kcs)
        if credit <= p["min_credit"] * u.scale(d) or credit > p["max_credit_frac"] * width:
            return
        ml = (width - credit) * 100
        n = int(self.budget(p["risk_frac"]) // ml)
        if n <= 0:
            return
        b.open(self, u, d, legs, "ic" if not p["puts_only"] else "pcs", e, ml, width * 100, n,
               meta={"width": width, "Ks": Kps, "spot0": S, "vix0": u._vix[i], "max_loss0": ml * n})


class PutHedge(Sleeve):
    """Standing tail hedge: long put spread (or single put) on SPY, rolled on a schedule.

    Budget: spend `budget_pa` x equity per year, spread evenly over rolls (premium at the ask).
    Strikes by delta (long_delta / short_delta) or by % OTM (long_otm / short_otm)."""
    defaults = dict(long_delta=None, short_delta=None, long_otm=0.10, short_otm=0.20,
                    dte_min=50, dte_max=100, dte_target=75, roll_dte=30, budget_pa=0.005,
                    monetize_mult=None, kind="put_spread", n_fixed=None, frac=False)

    def __init__(self, u, name="hedge", **kw):
        self.p = {**self.defaults, **kw}
        self.u = u
        self.name = name

    def unders(self):
        return [self.u]

    def manage(self, d):
        b = self.book
        for pos in list(self.mine()):
            dte = (pos.expiry - d).days
            val = pos.value()
            if self.p["monetize_mult"] and val >= -pos.entry_cash * self.p["monetize_mult"]:
                b.close(pos, d, "monetize")
            elif dte <= self.p["roll_dte"]:
                b.close(pos, d, "roll")

    def enter(self, d):
        p = self.p
        b = self.book
        u = self.u
        if d not in u._pos or self.mine():
            return
        ex = rank_expiries(b.cal, d, p["dte_min"], p["dte_max"], p["dte_target"], monthly_only=True)
        if not ex:
            return
        e = ex[0]
        S = u.spot(d)
        if p["long_delta"]:
            Kl, pl, dl = pick_by_delta(u, d, e, "P", p["long_delta"])
        else:
            Kl = snap(S * (1 - p["long_otm"]), u.step(d))
            pl, dl = price_strike(u, d, e, "P", Kl)
        legs = [Leg(Kl, "P", 1, pl, dl)]
        debit = pl
        if p["kind"] == "put_spread":
            if p["short_delta"]:
                Ks, ps, ds = pick_by_delta(u, d, e, "P", p["short_delta"])
            else:
                Ks = snap(S * (1 - p["short_otm"]), u.step(d))
                ps, ds = price_strike(u, d, e, "P", Ks)
            if Ks >= Kl:
                return
            legs.append(Leg(Ks, "P", -1, ps, ds))
            debit = pl - ps
        hold_days = max((e - d).days - p["roll_dte"], 7)
        spend = b.equity() * p["budget_pa"] * hold_days / 365.0
        c = b.costs
        k = u.scale(d)
        per = (debit + c.slip(pl, b.vix(d), k) + (c.slip(legs[1].mark, b.vix(d), k) if len(legs) > 1 else 0)) \
            * 100 + len(legs) * c.fee * k
        if p.get("n_fixed"):
            n = p["n_fixed"]
        elif p.get("frac"):
            n = spend / max(per, 0.01)
        else:
            n = int(spend // max(per, 0.01))
        if n <= 0 and not p.get("frac"):
            # fractional budget accrues: buy one when the accrued budget allows
            self.__dict__.setdefault("_acc", 0.0)
            self._acc += spend
            if self._acc >= per:
                n = 1
                self._acc -= per
            else:
                return
        b.open(self, u, d, legs, "hedge", e, 0.0, 0.0, n, meta={"spot0": S, "vix0": b.vix(d)})


# ------------------------------------------------------------------ metrics
def metrics(eq: pd.Series, rf=None) -> dict:
    eq = eq.dropna()
    if len(eq) < 30:
        return {}
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 and eq.iloc[-1] > 0 else -1.0
    sd = r.std()
    sh = r.mean() / sd * math.sqrt(252) if sd > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    return {"cagr": cagr, "sharpe": sh, "max_dd": dd, "vol": sd * math.sqrt(252),
            "worst_day": r.min(), "skew": float(r.skew())}
