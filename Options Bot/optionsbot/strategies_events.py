"""Event and short-dated option structures (earnings, 1DTE index, FOMC/CPI eves).

These are event studies, not daily-loop strategies: each structure is opened at
one close and closed at a later close (or settled at intrinsic on expiry), so
they are evaluated per event and then compounded into an account curve by
tuning/events/ (fixed fraction of equity at risk per event).

Data comes from the shared Market (chain + cached option bars, same cache keys
as Market.bars) with one addition: the bar *volume* that data.py already stores
in the cache, so every leg can be required to have printed (volume > 0) on both
the entry and the exit day.

Conventions
-----------
* D = last close before the news (EDGAR 8-K 2.02, all AMC -> the report day).
* IM (implied move) = (ATM call + ATM put) / S on the first expiry strictly
  after D, ATM = printed strike nearest to the underlying close.
* Fills: buy at close + slip, sell at close - slip, slip = engine Costs.slip
  (max($0.03, 2%) capped at $0.25 per leg) scaled by `slip_mult`; fees
  Costs.fee_per_contract per leg per side. Legs that reach expiry settle at
  intrinsic on the underlying close (no cost).
* P&L is per 1-lot, per share ($), and as a fraction of max risk.

REGISTRY maps ids to classes; each class has `defaults` and
`trade(ed, ev, costs, slip_mult=1.0, strict=True) -> dict` (a ledger row; key
`ok` False plus `fail` reason when the trade could not be priced honestly).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from .engine import Costs

DATA_START = pd.Timestamp("2024-01-01")


# ---------------------------------------------------------------------------
class EventData:
    """Option access with volume, on top of a runner.market() Market."""

    def __init__(self, market):
        self.m = market
        self.c = market.client
        self._bars: dict = {}
        self.tdays = market.stock.index

    # -- calendar
    def tday(self, d, k: int = 0):
        """Trading day k sessions from d (d must be a trading day for k != 0)."""
        d = pd.Timestamp(d)
        i = self.tdays.searchsorted(d)
        j = i + k
        if j < 0 or j >= len(self.tdays):
            return None
        return self.tdays[j]

    def spot(self, d) -> float:
        return float(self.m.stock.at[pd.Timestamp(d), "close"])

    def spot_on_or_before(self, d) -> float:
        idx = self.tdays[self.tdays <= pd.Timestamp(d)]
        return float(self.m.stock.at[idx[-1], "close"])

    def spans_split(self, d0, e) -> bool:
        return any(pd.Timestamp(d0) < sd <= pd.Timestamp(e) for sd in self.m.split_dates)

    # -- chain
    def expiries_after(self, d, cp="C"):
        d = pd.Timestamp(d)
        return [e for e in self.m.expiries[cp] if e > d]

    def first_expiry_after(self, d):
        ex = [e for e in self.expiries_after(d) if e in self.m.expiries["P"]]
        return ex[0] if ex else None

    def back_monthly(self, front, min_gap_days=21):
        for e in self.m.expiries["C"]:
            if (e - front).days >= min_gap_days and self.m.is_monthly(e) and e in self.m.expiries["P"]:
                return e
        return None

    def chain(self, exp, cp):
        g = self.m._by_exp.get((pd.Timestamp(exp), cp))
        if g is None:
            return np.array([]), np.array([])
        return g["strike_price"].values.astype(float), g["symbol"].values

    def nearest(self, exp, cp, target, n=1):
        ks, syms = self.chain(exp, cp)
        if not len(ks):
            return []
        order = np.argsort(np.abs(ks - target), kind="stable")[:n]
        return [(float(ks[i]), syms[i]) for i in order]

    def strikes_between(self, exp, cp, lo, hi):
        ks, syms = self.chain(exp, cp)
        sel = (ks >= lo) & (ks <= hi)
        return [(float(k), s) for k, s in zip(ks[sel], syms[sel])]

    # -- bars (close, volume) with the same cache key as Market.bars
    def _key(self, exp):
        exp = pd.Timestamp(exp)
        start = max(DATA_START, exp - pd.Timedelta(days=450)).strftime("%Y-%m-%d")
        end = min(exp, self.m.end).strftime("%Y-%m-%d")
        return start, end

    def load(self, syms, exp):
        syms = [s for s in dict.fromkeys(syms) if s not in self._bars]
        if not syms:
            return
        start, end = self._key(exp)
        self.c.option_bars(list(syms), start, end)          # fetches + caches what is missing
        for s in syms:
            p = self.c._cache_path("optbars", f"{s}|{start}|{end}")
            rows = json.loads(p.read_text()) if p.exists() else []
            self._bars[s] = {pd.Timestamp(r["t"][:10]): (float(r["c"]), float(r.get("v", 0) or 0))
                             for r in rows}

    def quote(self, sym, d):
        """(close, volume) on day d, or None if the contract did not print."""
        q = self._bars.get(sym, {}).get(pd.Timestamp(d))
        if q is None or not (q[0] > 0) or q[1] <= 0:
            return None
        return q

    def printed(self, exp, cp, target, d, n=6, max_dist=None):
        """Nearest strike to `target` that printed (vol>0) on d. Loads bars for the
        n nearest strikes. Returns (K, sym, price) or None."""
        cand = self.nearest(exp, cp, target, n)
        self.load([s for _, s in cand], exp)
        for k, s in cand:                        # already ordered by distance
            if max_dist is not None and abs(k - target) > max_dist:
                break
            q = self.quote(s, d)
            if q:
                return k, s, q[0]
        return None

    # -- model mark for the relaxed (non-strict) exit
    def model_mark(self, exp, cp, K, d):
        """BS price at d using the IV of the nearest printed same-expiry same-type
        strike (2 nearest averaged). None when nothing printed."""
        S = self.spot(d)
        cand = self.nearest(exp, cp, K, 8)
        self.load([s for _, s in cand], exp)
        ivs = []
        for k, s in cand:
            q = self.quote(s, d)
            if q:
                iv = self.m.iv_from_price(q[0], pd.Timestamp(d), k, pd.Timestamp(exp), cp)
                if iv:
                    ivs.append(iv)
            if len(ivs) >= 2:
                break
        if not ivs:
            return None
        intrinsic = max(0.0, S - K) if cp == "C" else max(0.0, K - S)
        return max(self.m.model_price(pd.Timestamp(d), K, pd.Timestamp(exp), cp, float(np.mean(ivs))),
                   intrinsic)

    # -- implied move
    def straddle(self, exp, d, S=None, n=4):
        """ATM straddle on `exp` at d: the strike nearest S where both the call and
        the put printed. Returns (K, call, put, csym, psym) or None."""
        S = self.spot(d) if S is None else S
        cc = self.nearest(exp, "C", S, n)
        pp = self.nearest(exp, "P", S, n)
        self.load([s for _, s in cc + pp], exp)
        pmap = {k: s for k, s in pp}
        for k, cs in cc:
            ps = pmap.get(k)
            if ps is None:
                continue
            qc, qp = self.quote(cs, d), self.quote(ps, d)
            if qc and qp:
                return k, qc[0], qp[0], cs, ps
        return None


# ---------------------------------------------------------------------------
def price_legs(ed: EventData, legs, d0, d1, costs: Costs, slip_mult=1.0, strict=True):
    """legs: list of (sym, cp, K, exp, qty) with qty>0 long. Opens at close d0,
    closes at close d1 (or settles at intrinsic if d1 >= expiry).
    Returns dict(open_cost, close_value, pnl, entry_mark, exit_mark, n_model) per
    share for a 1-lot, or {'fail': reason}."""
    fee = costs.fee_per_contract / 100.0
    open_cost = close_val = em = xm = fees = 0.0
    n_model = 0
    S_exit = None
    for sym, cp, K, exp, qty in legs:
        q0 = ed.quote(sym, d0)
        if q0 is None:
            return {"fail": "no_entry_print"}
        p0 = q0[0]
        s0 = costs.slip(p0) * slip_mult
        open_cost += qty * (p0 + (s0 if qty > 0 else -s0))
        em += qty * p0
        fees += abs(qty) * fee
        if pd.Timestamp(d1) >= pd.Timestamp(exp):
            S_exit = ed.spot_on_or_before(exp)
            intr = max(0.0, S_exit - K) if cp == "C" else max(0.0, K - S_exit)
            close_val += qty * intr
            xm += qty * intr
            continue
        q1 = ed.quote(sym, d1)
        if q1 is None:
            if strict:
                return {"fail": "no_exit_print"}
            p1 = ed.model_mark(exp, cp, K, d1)
            if p1 is None:
                return {"fail": "no_exit_print_nomodel"}
            n_model += 1
        else:
            S1 = ed.spot(d1)
            p1 = max(q1[0], max(0.0, S1 - K) if cp == "C" else max(0.0, K - S1))
        s1 = costs.slip(p1) * slip_mult
        close_val += qty * (p1 - (s1 if qty > 0 else -s1))
        xm += qty * p1
        fees += abs(qty) * fee
    pnl = close_val - open_cost - fees
    return {"open_cost": open_cost, "close_value": close_val, "pnl": pnl, "entry_mark": em,
            "exit_mark": xm, "fees": fees, "n_model": n_model, "pnl_mid": xm - em}


class EventStrategy:
    id = "base"
    name = "base"
    defaults: dict = {}

    def __init__(self, **p):
        self.p = {**self.defaults, **p}

    def trade(self, ed, ev, costs, slip_mult=1.0, strict=True) -> dict:
        raise NotImplementedError


def _implied(ed, ev):
    D = pd.Timestamp(ev["D"])
    front = ed.first_expiry_after(D)
    if front is None:
        return None, {"fail": "no_expiry"}
    st = ed.straddle(front, D)
    if st is None:
        return None, {"fail": "no_atm_print"}
    K, c, p, cs, ps = st
    S = ed.spot(D)
    return dict(front=front, K=K, call=c, put=p, csym=cs, psym=ps, S=S, im=(c + p) / S), None


# ---------------------------------------------------------------------------
class ImpliedMove(EventStrategy):
    """E1: measurement only."""
    id, name = "e1_implied_move", "E1 implied-move ledger"

    def trade(self, ed, ev, costs=None, slip_mult=1.0, strict=True):
        im, err = _implied(ed, ev)
        if err:
            return {"ok": False, **err}
        D = pd.Timestamp(ev["D"])
        D1 = ed.tday(D, 1)
        rm_signed = ed.m.stock.at[D1, "adj_close"] / ed.m.stock.at[D, "adj_close"] - 1
        return {"ok": True, "front": str(im["front"].date()), "dte": (im["front"] - D).days,
                "S": im["S"], "K_atm": im["K"], "atm_call": im["call"], "atm_put": im["put"],
                "im": im["im"], "rm": abs(rm_signed), "rm_signed": rm_signed,
                "ratio": abs(rm_signed) / im["im"]}


class EarningsCondor(EventStrategy):
    """E3 iron condor (shorts ±short_mult*IM, wings ±wing_mult*IM) and, with
    fly=True, E4 iron fly (short ATM straddle, wings ±wing_mult*IM).
    Enter close D, exit close D+hold (settle if expiry <= exit)."""
    id, name = "e3_earn_ic", "E3 earnings iron condor"
    defaults = {"short_mult": 1.0, "wing_mult": 2.0, "fly": False, "hold": 1}

    def legs(self, ed, im, D):
        p = self.p
        S, front, IM = im["S"], im["front"], im["im"]
        tol = 0.35 * IM * S
        if p["fly"]:
            K = im["K"]
            sc = (K, im["csym"], im["call"])
            sp = (K, im["psym"], im["put"])
        else:
            sc = ed.printed(front, "C", S * (1 + p["short_mult"] * IM), D, max_dist=tol)
            sp = ed.printed(front, "P", S * (1 - p["short_mult"] * IM), D, max_dist=tol)
        if sc is None or sp is None:
            return None
        wc = ed.printed(front, "C", max(S * (1 + p["wing_mult"] * IM), sc[0] + 0.01), D, max_dist=tol)
        wp = ed.printed(front, "P", min(S * (1 - p["wing_mult"] * IM), sp[0] - 0.01), D, max_dist=tol)
        if wc is None or wp is None or wc[0] <= sc[0] or wp[0] >= sp[0]:
            return None
        legs = [(sp[1], "P", sp[0], front, -1), (wp[1], "P", wp[0], front, +1),
                (sc[1], "C", sc[0], front, -1), (wc[1], "C", wc[0], front, +1)]
        width = max(sp[0] - wp[0], wc[0] - sc[0])
        return legs, width

    def trade(self, ed, ev, costs, slip_mult=1.0, strict=True):
        im, err = _implied(ed, ev)
        if err:
            return {"ok": False, **err}
        D = pd.Timestamp(ev["D"])
        D1 = ed.tday(D, self.p["hold"])
        built = self.legs(ed, im, D)
        if built is None:
            return {"ok": False, "fail": "no_strikes", "im": im["im"]}
        legs, width = built
        r = price_legs(ed, legs, D, D1, costs, slip_mult, strict)
        if "fail" in r:
            return {"ok": False, "im": im["im"], **r}
        credit = -r["open_cost"]
        max_risk = width - credit
        if credit <= 0 or max_risk <= 0:
            return {"ok": False, "fail": "bad_credit", "im": im["im"]}
        return {"ok": True, "im": im["im"], "S": im["S"], "front": str(im["front"].date()),
                "strikes": "/".join(f"{l[2]:g}" for l in legs), "credit": credit, "width": width,
                "max_risk": max_risk, "pnl": r["pnl"], "pnl_mid": r["pnl_mid"],
                "ret": r["pnl"] / max_risk, "n_model": r["n_model"],
                "settled": bool(D1 >= im["front"])}


class EarningsFly(EarningsCondor):
    id, name = "e4_earn_fly", "E4 earnings iron fly"
    defaults = {"short_mult": 0.0, "wing_mult": 1.5, "fly": True, "hold": 1}


class EarningsCalendar(EventStrategy):
    """E5: short front ATM (straddle or call) / long same strike back monthly
    (>= gap days after the front). Debit trade; max risk = debit."""
    id, name = "e5_earn_calendar", "E5 earnings long calendar"
    defaults = {"sides": "CP", "gap": 21, "hold": 1}

    def trade(self, ed, ev, costs, slip_mult=1.0, strict=True):
        im, err = _implied(ed, ev)
        if err:
            return {"ok": False, **err}
        D = pd.Timestamp(ev["D"])
        D1 = ed.tday(D, self.p["hold"])
        front = im["front"]
        back = ed.back_monthly(front, self.p["gap"])
        if back is None or ed.spans_split(D, back):
            return {"ok": False, "fail": "no_back_or_split", "im": im["im"]}
        # strike: nearest to spot that is listed AND printed on D in both expiries for every
        # side traded (half-dollar strikes are often missing or unprinted in the monthly)
        S = im["S"]
        legs = None
        for K, _ in ed.nearest(front, "C", S, 6):
            cand = []
            for cp in self.p["sides"]:
                f = [x for x in ed.nearest(front, cp, K, 1) if abs(x[0] - K) < 1e-6]
                b = [x for x in ed.nearest(back, cp, K, 1) if abs(x[0] - K) < 1e-6]
                if not f or not b:
                    break
                ed.load([f[0][1]], front)
                ed.load([b[0][1]], back)
                if not (ed.quote(f[0][1], D) and ed.quote(b[0][1], D)):
                    break
                cand += [(f[0][1], cp, K, front, -1), (b[0][1], cp, K, back, +1)]
            else:
                legs = cand
                break
        if legs is None:
            return {"ok": False, "fail": "no_common_printed_strike", "im": im["im"]}
        r = price_legs(ed, legs, D, D1, costs, slip_mult, strict)
        if "fail" in r:
            return {"ok": False, "im": im["im"], **r}
        debit = r["open_cost"]
        if debit <= 0:
            return {"ok": False, "fail": "bad_debit", "im": im["im"]}
        return {"ok": True, "im": im["im"], "S": im["S"], "front": str(front.date()),
                "back": str(back.date()), "strikes": f"{legs[0][2]:g}", "debit": debit, "max_risk": debit,
                "pnl": r["pnl"], "pnl_mid": r["pnl_mid"], "ret": r["pnl"] / debit,
                "n_model": r["n_model"], "settled": bool(D1 >= front)}


class RunupStraddle(EventStrategy):
    """E2: long ATM straddle (first expiry after D) bought at close D-k, sold at
    close D (before the AMC report)."""
    id, name = "e2_runup_straddle", "E2 pre-earnings run-up straddle"
    defaults = {"k": 3}

    def trade(self, ed, ev, costs, slip_mult=1.0, strict=True):
        D = pd.Timestamp(ev["D"])
        d0 = ed.tday(D, -self.p["k"])
        front = ed.first_expiry_after(D)
        if front is None or d0 is None:
            return {"ok": False, "fail": "no_expiry"}
        st = ed.straddle(front, d0)
        if st is None:
            return {"ok": False, "fail": "no_atm_print"}
        K, c, p, cs, ps = st
        legs = [(cs, "C", K, front, +1), (ps, "P", K, front, +1)]
        r = price_legs(ed, legs, d0, D, costs, slip_mult, strict)
        if "fail" in r:
            return {"ok": False, **r}
        debit = r["open_cost"]
        S0 = ed.spot(d0)
        return {"ok": True, "S": S0, "front": str(front.date()), "strikes": f"{K:g}",
                "cost_pct": (c + p) / S0, "debit": debit, "max_risk": debit, "pnl": r["pnl"],
                "pnl_mid": r["pnl_mid"], "ret": r["pnl"] / debit, "n_model": r["n_model"]}


class OneDayCondor(EventStrategy):
    """Z1 / F1 / F2: index option expiring the next session, sold at close T,
    settled at intrinsic on the T+1 close. structure: 'ic' (shorts ±short_mult*IM1,
    wings +wing_abs $ or, if wing_mult, at ±wing_mult*IM1), 'straddle' (short ATM
    straddle, undefined risk: max_risk reported as 3*IM1*S notional proxy) or
    'fly' (short ATM straddle with wings ±wing_mult*IM1)."""
    id, name = "z1_1dte_ic", "Z1 1DTE iron condor close-to-settle"
    defaults = {"structure": "ic", "short_mult": 1.0, "wing_abs": 5.0, "wing_mult": None}

    def trade(self, ed, ev, costs, slip_mult=1.0, strict=True):
        T = pd.Timestamp(ev["D"])
        T1 = ed.tday(T, 1)
        exp = T1
        if exp not in ed.m.expiries["C"] or exp not in ed.m.expiries["P"]:
            return {"ok": False, "fail": "no_1dte_expiry"}
        S = ed.spot(T)
        st = ed.straddle(exp, T)
        if st is None:
            return {"ok": False, "fail": "no_atm_print"}
        K0, c, p, cs, ps = st
        IM = (c + p) / S
        P = self.p
        if P["structure"] == "straddle":
            legs = [(cs, "C", K0, exp, -1), (ps, "P", K0, exp, -1)]
            width = None
        else:
            if P["structure"] == "fly":
                sc, sp = (K0, cs, c), (K0, ps, p)
            else:
                sc = ed.printed(exp, "C", S * (1 + P["short_mult"] * IM), T, n=8)
                sp = ed.printed(exp, "P", S * (1 - P["short_mult"] * IM), T, n=8)
            if sc is None or sp is None:
                return {"ok": False, "fail": "no_strikes", "im": IM}
            if P["wing_mult"]:
                tc = S * (1 + P["wing_mult"] * IM)
                tp = S * (1 - P["wing_mult"] * IM)
            else:
                tc, tp = sc[0] + P["wing_abs"], sp[0] - P["wing_abs"]
            wc = ed.printed(exp, "C", max(tc, sc[0] + 0.5), T, n=8)
            wp = ed.printed(exp, "P", min(tp, sp[0] - 0.5), T, n=8)
            if wc is None or wp is None or wc[0] <= sc[0] or wp[0] >= sp[0]:
                return {"ok": False, "fail": "no_wings", "im": IM}
            legs = [(sp[1], "P", sp[0], exp, -1), (wp[1], "P", wp[0], exp, +1),
                    (sc[1], "C", sc[0], exp, -1), (wc[1], "C", wc[0], exp, +1)]
            width = max(sp[0] - wp[0], wc[0] - sc[0])
        r = price_legs(ed, legs, T, T1, costs, slip_mult, strict)
        if "fail" in r:
            return {"ok": False, "im": IM, **r}
        credit = -r["open_cost"]
        if credit <= 0:
            return {"ok": False, "fail": "bad_credit", "im": IM}
        if width is None:
            max_risk = 3.0 * IM * S          # proxy for an undefined-risk straddle
        else:
            max_risk = width - credit
            if max_risk <= 0:
                return {"ok": False, "fail": "bad_credit", "im": IM}
        S1 = ed.spot(T1)
        return {"ok": True, "im": IM, "S": S, "rm": abs(S1 / S - 1), "front": str(exp.date()),
                "strikes": "/".join(f"{l[2]:g}" for l in legs), "credit": credit,
                "width": width, "max_risk": max_risk, "pnl": r["pnl"], "pnl_mid": r["pnl_mid"],
                "ret": r["pnl"] / max_risk, "n_model": 0, "settled": True}


REGISTRY = {c.id: c for c in [ImpliedMove, RunupStraddle, EarningsCondor, EarningsFly,
                              EarningsCalendar, OneDayCondor]}
