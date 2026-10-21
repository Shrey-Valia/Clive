"""Parameterized option strategies (daily-close decisions).

Every strategy implements on_day(eng, d), called after marks & expirations.
Exits are checked before entries. All sizing is cash-secured: no naked calls,
no margin leverage (except the inherent leverage of long LEAPS in the PMCC).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


class Strategy:
    id = "base"
    name = "Base"
    defaults: dict = {}

    def __init__(self, **params):
        self.p = {**self.defaults, **params}

    def __repr__(self):
        return f"{self.id}({self.p})"

    def on_day(self, eng, d):
        raise NotImplementedError

    # ---- shared helpers
    def dte_kw(self, prefix=""):
        p = self.p
        return dict(dte_min=p[prefix + "dte_min"], dte_max=p[prefix + "dte_max"],
                    dte_target=p[prefix + "dte_target"],
                    monthly_only=bool(p.get(prefix + "monthly_only", False)))

    def entry_filter_ok(self, eng, d) -> bool:
        f = self.p.get("filter")
        if not f:
            return True
        row = eng.m.stock.loc[d]
        ok = True
        if "sma200" in f:
            ok &= bool(np.isfinite(row["sma200"]) and row["close"] > row["sma200"])
        if "rv" in f:
            ok &= bool(np.isfinite(row["rv20_pct80"]) and row["rv20"] < row["rv20_pct80"])
        if "vix" in f:
            # Cboe VIX close (falls back to SPY ~30-DTE ATM IV proxy * 100)
            v = row.get("vix", np.nan)
            if not np.isfinite(v):
                v = row.get("vix_proxy", np.nan) * 100
            ok &= bool(np.isfinite(v) and v < self.p.get("vix_max", 30.0))
        return ok

    # ---- intramonth kill switch (research/long_history_validation.md)
    def kill_switch(self, eng, d) -> bool:
        """Returns True when new short-premium entries are blocked today.

        Signal at close t: VIX/VIX3M > kill_ratio, VIX > kill_vix, or a position
        loss > kill_loss * notional (short strikes*100*n). Action: close all
        open positions at the close of t+1. Re-entry only after the next monthly
        expiry and once VIX/VIX3M <= kill_ratio and VIX <= kill_vix."""
        p = self.p
        if not p.get("kill_switch"):
            return False
        st = self.__dict__.setdefault("_ks", {"pending": False, "until": None, "n": 0})
        row = eng.m.stock.loc[d]
        ratio, vix = row.get("vix_ratio", np.nan), row.get("vix", np.nan)
        stress = bool((np.isfinite(ratio) and ratio > p.get("kill_ratio", 1.15)) or
                      (np.isfinite(vix) and vix > p.get("kill_vix", 40.0)))
        if st["pending"]:
            for pos in list(eng.positions):
                eng.close(pos, "kill_switch")
            st["pending"] = False
            nxt = [e for e in eng.m.expiries["P"] if e > d and eng.m.is_monthly(e)]
            st["until"] = nxt[0] if nxt else d
            st["n"] += 1
            eng.kill_events = st["n"]
            return True
        loss_hit = False
        for pos in eng.positions:
            notional = sum(-l.qty * 100 * l.strike for l in pos.legs if l.qty < 0 and l.open)
            pnl = pos.entry_cash + pos.exit_cash + pos.value()
            if notional > 0 and pnl < -p.get("kill_loss", 0.08) * notional:
                loss_hit = True
        if eng.positions and (stress or loss_hit):
            st["pending"] = True     # act at tomorrow's close
        if st["until"] is not None:
            if d < st["until"] or stress:
                return True
            st["until"] = None
        return False

    @staticmethod
    def credit_exit(eng, pos, d, tp, sl_mult, close_dte):
        """Standard credit-trade management. Returns reason or None."""
        credit = pos.credit()            # premium at open, at marks
        cost = -pos.value()              # current cost to close, at marks
        if credit > 0:
            if tp is not None and cost <= credit * (1 - tp):
                return "take_profit"
            if sl_mult is not None and cost >= credit * (1 + sl_mult):
                return "stop_loss"
        if close_dte is not None and pos.min_dte(d) <= close_dte:
            return "dte_exit"
        return None


# ---------------------------------------------------------------------------
class BuyHold(Strategy):
    id, name = "buy_hold", "Buy & hold (dividends reinvested)"
    defaults = {}

    def on_day(self, eng, d):
        if eng.shares == 0:
            n = eng.cash / (eng.spot() * 1.0002)
            eng.buy_shares(math.floor(n * 1e4) / 1e4, "initial")   # fractional shares
        elif eng.cash > 1.0:
            eng.buy_shares(math.floor(eng.cash / (eng.spot() * 1.0002) * 1e4) / 1e4, "reinvest")


# ---------------------------------------------------------------------------
class _StockPlusCalls(Strategy):
    """Shared machinery for covered call / collar: hold 100-share lots and
    keep a short call (and optionally a long put) on every lot."""
    use_put = False

    def lots_affordable(self, eng):
        return int(eng.equity() * self.p["alloc"] // (100 * eng.spot() * 1.001))

    def ensure_stock(self, eng):
        target = self.lots_affordable(eng)
        have = int(round(eng.shares / 100))
        if have == 0 and target == 0:
            eng.blocked(f"infeasible: 100 shares of {eng.m.symbol} (~${100 * eng.spot():,.0f}) "
                     f"exceed capital ${eng.capital:,.0f}")
            return 0
        # if a settlement drove cash negative, sell lots to restore cash >= 0
        while eng.cash < 0 and eng.shares >= 100:
            eng.sell_shares(100, "raise_cash")
        have = int(round(eng.shares / 100))
        if target > have and not eng.positions:
            # only add lots when no option is open (keeps legs matched to lots)
            need = target - have
            cost = need * 100 * eng.spot() * 1.001
            if eng.cash >= cost:
                eng.buy_shares(100 * need, "add_lots")
        return int(round(eng.shares / 100))

    def on_day(self, eng, d):
        p = self.p
        for pos in list(eng.positions):
            reason = None
            if p.get("roll_dte") is not None and pos.min_dte(d) <= p["roll_dte"]:
                reason = "roll"
            elif p.get("tp") is not None:
                short = [l for l in pos.legs if l.qty < 0 and l.open]
                if short:
                    sc, sm = short[0].entry_mark, short[0].mark
                    if sm <= sc * (1 - p["tp"]):
                        reason = "take_profit"
            if reason:
                eng.close(pos, reason)
        lots = self.ensure_stock(eng)
        if lots == 0 or eng.positions:
            return
        call = eng.m.select(d, "C", delta=p["call_delta"], **self.dte_kw())
        if call is None:
            return
        legs = [(call, -lots)]
        if self.use_put:
            put = eng.m.select(d, "P", delta=p["put_delta"], expiry=call.expiry)
            if put is None:
                return
            legs.append((put, lots))
        eng.open(legs, self.id, reserve=0.0)  # covered by shares


class CoveredCall(_StockPlusCalls):
    id, name = "covered_call", "Covered call"
    defaults = dict(call_delta=0.30, dte_min=30, dte_max=45, dte_target=38, roll_dte=21,
                    tp=None, alloc=1.0)


class Collar(_StockPlusCalls):
    id, name = "collar", "Collar"
    use_put = True
    defaults = dict(call_delta=0.30, put_delta=0.25, dte_min=30, dte_max=45, dte_target=38,
                    roll_dte=21, tp=None, alloc=1.0)


# ---------------------------------------------------------------------------
class CashSecuredPut(Strategy):
    id, name = "csp", "Cash-secured put"
    defaults = dict(put_delta=0.30, dte_min=30, dte_max=45, dte_target=38, tp=0.5,
                    close_dte=None, sl_mult=None, alloc=1.0, filter=None)

    def on_day(self, eng, d):
        p = self.p
        for pos in list(eng.positions):
            r = self.credit_exit(eng, pos, d, p["tp"], p["sl_mult"], p["close_dte"])
            if r:
                eng.close(pos, r)
        blocked = self.kill_switch(eng, d)
        if eng.positions or blocked or not self.entry_filter_ok(eng, d):
            return
        if eng.free_cash() * p["alloc"] < 0.5 * eng.spot() * 100:   # can't secure even a deep OTM put
            eng.blocked(f"infeasible: securing 1 put needs ~${eng.spot() * 100 * (1 - p['put_delta'] / 5):,.0f} "
                        f"cash > available ${eng.free_cash():,.0f}")
            return
        q = eng.m.select(d, "P", delta=p["put_delta"], **self.dte_kw())
        if q is None:
            return
        n = int(eng.free_cash() * p["alloc"] // (q.strike * 100))
        if n == 0:
            eng.blocked(f"infeasible: securing 1 put at strike {q.strike:.0f} needs "
                     f"${q.strike * 100:,.0f} cash > available")
            return
        eng.open([(q, -n)], self.id, reserve=q.strike * 100 * n)


# ---------------------------------------------------------------------------
class Wheel(Strategy):
    id, name = "wheel", "The wheel"
    defaults = dict(put_delta=0.30, call_delta=0.30, dte_min=30, dte_max=45, dte_target=38,
                    put_tp=0.5, call_tp=None, call_above_basis=True, alloc=1.0)

    def on_day(self, eng, d):
        p = self.p
        for pos in list(eng.positions):
            tp = p["put_tp"] if pos.kind == "wheel_put" else p["call_tp"]
            r = self.credit_exit(eng, pos, d, tp, None, None)
            if r:
                eng.close(pos, r)
        if eng.positions:
            return
        lots = int(round(eng.shares / 100))
        if lots == 0:
            if eng.free_cash() * p["alloc"] < 0.5 * eng.spot() * 100:
                eng.blocked(f"infeasible: securing 1 put needs ~${eng.spot() * 100 * (1 - p['put_delta'] / 5):,.0f} "
                            f"cash > available ${eng.free_cash():,.0f}")
                return
            q = eng.m.select(d, "P", delta=p["put_delta"], **self.dte_kw())
            if q is None:
                return
            n = int(eng.free_cash() * p["alloc"] // (q.strike * 100))
            if n == 0:
                eng.blocked(f"infeasible: securing 1 put at strike {q.strike:.0f} needs "
                         f"${q.strike * 100:,.0f} cash > available")
                return
            eng.open([(q, -n)], "wheel_put", reserve=q.strike * 100 * n, settle="physical")
        else:
            if eng.shares % 100:   # odd shares (shouldn't happen) are sold
                eng.sell_shares(eng.shares % 100, "odd_lot")
            q = eng.m.select(d, "C", delta=p["call_delta"], **self.dte_kw())
            if q is None:
                return
            if p["call_above_basis"] and q.strike < eng.share_basis:
                g = eng.m._by_exp[(q.expiry, "C")]
                ks = g[g.strike_price >= eng.share_basis].strike_price
                if len(ks):
                    q2 = eng.m.select(d, "C", strike=float(ks.iloc[0]), expiry=q.expiry, iv_hint=q.iv)
                    if q2 is not None and q2.price >= 0.05:
                        q = q2
            eng.open([(q, -lots)], "wheel_call", reserve=0.0, settle="physical")


# ---------------------------------------------------------------------------
class PutCreditSpread(Strategy):
    id, name = "put_credit_spread", "Put credit spread"
    defaults = dict(short_delta=0.25, long_delta=0.10, width_pct=None, width_abs=None,
                    dte_min=30, dte_max=45,
                    dte_target=38, tp=0.5, sl_mult=2.0, close_dte=21, risk_frac=0.25,
                    max_positions=1, filter=None)

    def long_leg(self, eng, d, short, cp, delta_key="long_delta"):
        p = self.p
        if p.get("width_abs"):
            k = short.strike - p["width_abs"] if cp == "P" else short.strike + p["width_abs"]
            return eng.m.select(d, cp, strike=k, expiry=short.expiry, iv_hint=short.iv)
        if p.get("width_pct"):
            off = p["width_pct"] * eng.spot()
            k = short.strike - off if cp == "P" else short.strike + off
            return eng.m.select(d, cp, strike=k, expiry=short.expiry, iv_hint=short.iv)
        return eng.m.select(d, cp, delta=p[delta_key], expiry=short.expiry, iv_hint=short.iv)

    def size(self, eng, max_loss_per):
        if max_loss_per <= 0:
            return 0
        budget = min(eng.equity() * self.p["risk_frac"], eng.free_cash())
        return int(budget // max_loss_per)

    def manage(self, eng, d):
        p = self.p
        for pos in list(eng.positions):
            r = self.credit_exit(eng, pos, d, p["tp"], p["sl_mult"], p["close_dte"])
            if r:
                eng.close(pos, r)

    def on_day(self, eng, d):
        self.manage(eng, d)
        p = self.p
        blocked = self.kill_switch(eng, d)
        if blocked or len(eng.positions) >= p["max_positions"] or not self.entry_filter_ok(eng, d):
            return
        if p["close_dte"] is not None and p["dte_min"] <= p["close_dte"]:
            return
        sp = eng.m.select(d, "P", delta=p["short_delta"], **self.dte_kw())
        if sp is None:
            return
        lp = self.long_leg(eng, d, sp, "P")
        if lp is None or lp.strike >= sp.strike:
            return
        width = sp.strike - lp.strike
        credit = sp.price - lp.price
        if credit <= 0.05:
            return
        n = self.size(eng, (width - credit) * 100)
        if n == 0:
            eng.blocked("skipped entries: one spread's max loss exceeds risk budget")
            return
        eng.open([(sp, -n), (lp, n)], self.id, reserve=width * 100 * n)


class TrendPutCreditSpread(PutCreditSpread):
    id, name = "trend_pcs", "Put credit spread, trend filter (close > SMA200)"
    defaults = {**PutCreditSpread.defaults, "filter": "sma200"}


class RegimePutCreditSpread(PutCreditSpread):
    id, name = "regime_pcs", "Put credit spread, SMA200 + realized-vol regime filter"
    defaults = {**PutCreditSpread.defaults, "filter": "sma200_rv"}


# ---------------------------------------------------------------------------
class IronCondor(PutCreditSpread):
    id, name = "iron_condor", "Iron condor"
    defaults = dict(short_delta=0.16, long_delta=0.05, width_pct=None, width_abs=None,
                    dte_min=38, dte_max=52,
                    dte_target=45, tp=0.5, sl_mult=None, close_dte=21, risk_frac=0.25,
                    max_positions=1, filter=None)

    def on_day(self, eng, d):
        self.manage(eng, d)
        p = self.p
        blocked = self.kill_switch(eng, d)
        if blocked or len(eng.positions) >= p["max_positions"] or not self.entry_filter_ok(eng, d):
            return
        sp = eng.m.select(d, "P", delta=p["short_delta"], **self.dte_kw())
        if sp is None:
            return
        sc = eng.m.select(d, "C", delta=p["short_delta"], expiry=sp.expiry, iv_hint=sp.iv)
        if sc is None:
            return
        lp = self.long_leg(eng, d, sp, "P")
        lc = self.long_leg(eng, d, sc, "C")
        if lp is None or lc is None or lp.strike >= sp.strike or lc.strike <= sc.strike:
            return
        wp, wc = sp.strike - lp.strike, lc.strike - sc.strike
        credit = sp.price + sc.price - lp.price - lc.price
        if credit <= 0.10:
            return
        width = max(wp, wc)
        n = self.size(eng, (width - credit) * 100)
        if n == 0:
            eng.blocked("skipped entries: one condor's max loss exceeds risk budget")
            return
        # only one side can finish ITM: reserve the wider wing
        eng.open([(sp, -n), (lp, n), (sc, -n), (lc, n)], self.id, reserve=width * 100 * n)


# ---------------------------------------------------------------------------
class PoorMansCoveredCall(Strategy):
    id, name = "pmcc", "Poor man's covered call"
    defaults = dict(leaps_delta=0.80, leaps_dte_min=180, leaps_dte_max=400, leaps_dte_target=270,
                    leaps_roll_dte=90, call_delta=0.30, dte_min=30, dte_max=45, dte_target=38,
                    roll_dte=21, alloc=0.35, skip_short_below_sma50=False)

    def on_day(self, eng, d):
        p = self.p
        leaps = eng.open_positions("pmcc_leaps")
        shorts = eng.open_positions("pmcc_short")
        for s in shorts:
            if s.min_dte(d) <= p["roll_dte"]:
                eng.close(s, "roll")
        if leaps and leaps[0].min_dte(d) <= p["leaps_roll_dte"]:
            for s in eng.open_positions("pmcc_short"):
                eng.close(s, "leaps_roll")
            eng.close(leaps[0], "roll")
        leaps = eng.open_positions("pmcc_leaps")
        if not leaps:
            q = eng.m.select(d, "C", delta=p["leaps_delta"], **self.dte_kw("leaps_"))
            if q is None:
                return
            cost1 = (q.price + eng.costs.slip(q.price)) * 100 + eng.costs.fee_per_contract
            n = int(eng.equity() * p["alloc"] // cost1)
            n = min(n, int(eng.free_cash() // cost1))
            if n == 0:
                eng.blocked(f"infeasible: one LEAPS call costs ${cost1:,.0f} > {p['alloc']:.0%} of equity")
                return
            eng.open([(q, n)], "pmcc_leaps", reserve=0.0)
            leaps = eng.open_positions("pmcc_leaps")
            if not leaps:
                return
        L = leaps[0]
        if eng.open_positions("pmcc_short"):
            return
        n = L.legs[0].qty
        if p["skip_short_below_sma50"]:
            row = eng.m.stock.loc[d]
            if np.isfinite(row["sma50"]) and row["close"] < row["sma50"]:
                return
        q = eng.m.select(d, "C", delta=p["call_delta"], **self.dte_kw())
        if q is None or q.expiry >= L.legs[0].expiry:
            return
        reserve = max(0.0, L.legs[0].strike - q.strike) * 100 * n
        eng.open([(q, -n)], "pmcc_short", reserve=reserve)


# ---------------------------------------------------------------------------
class VixFilteredPutCreditSpread(PutCreditSpread):
    id, name = "vix_pcs", "Put credit spread, SMA200 + VIX<30 entry filter"
    defaults = {**PutCreditSpread.defaults, "filter": "sma200_vix"}


class IronCondorHold(IronCondor):
    """CNDR-like: monthly, ~20d shorts / ~5d wings, held to expiration."""
    id, name = "iron_condor_hold", "Iron condor held to expiry (CNDR-like)"
    defaults = {**IronCondor.defaults, "short_delta": 0.20, "long_delta": 0.05, "tp": None,
                "sl_mult": None, "close_dte": None, "dte_min": 20, "dte_max": 40,
                "dte_target": 30, "monthly_only": True}


class PMCCTrend(PoorMansCoveredCall):
    id, name = "pmcc_sma50", "PMCC, no short call below 50DMA"
    defaults = {**PoorMansCoveredCall.defaults, "skip_short_below_sma50": True}


class CSPKill(CashSecuredPut):
    id, name = "csp_kill", "Cash-secured put + intramonth kill switch"
    defaults = {**CashSecuredPut.defaults, "kill_switch": True}


class PCSKill(PutCreditSpread):
    id, name = "pcs_kill", "Put credit spread + intramonth kill switch"
    defaults = {**PutCreditSpread.defaults, "kill_switch": True}


class IronCondorKill(IronCondor):
    id, name = "iron_condor_kill", "Iron condor + intramonth kill switch"
    defaults = {**IronCondor.defaults, "kill_switch": True}


class PUTLikeKill(CashSecuredPut):
    id, name = "put_write_kill", "Monthly ATM put-write + kill switch"
    defaults = {**CashSecuredPut.defaults, "put_delta": 0.50, "dte_min": 20, "dte_max": 40,
                "dte_target": 30, "tp": None, "monthly_only": True, "kill_switch": True}


# Cboe-index-like calibration clones (monthly, held to expiry)
class BXMLike(CoveredCall):
    id, name = "cal_bxm", "Calibration: ATM monthly buy-write (BXM-like)"
    defaults = {**CoveredCall.defaults, "call_delta": 0.50, "dte_min": 20, "dte_max": 40,
                "dte_target": 30, "roll_dte": None, "monthly_only": True}


class BXMDLike(BXMLike):
    id, name = "cal_bxmd", "Calibration: 30-delta monthly buy-write (BXMD-like)"
    defaults = {**BXMLike.defaults, "call_delta": 0.30}


class PUTLike(CashSecuredPut):
    id, name = "cal_put", "Calibration: ATM monthly cash-secured put-write (PUT-like)"
    defaults = {**CashSecuredPut.defaults, "put_delta": 0.50, "dte_min": 20, "dte_max": 40,
                "dte_target": 30, "tp": None, "monthly_only": True}


REGISTRY = {c.id: c for c in [BuyHold, CoveredCall, CashSecuredPut, Wheel, PutCreditSpread,
                               IronCondor, Collar, PoorMansCoveredCall, TrendPutCreditSpread,
                               RegimePutCreditSpread, VixFilteredPutCreditSpread, IronCondorHold,
                               PMCCTrend, CSPKill, PCSKill, IronCondorKill, PUTLikeKill,
                               BXMLike, BXMDLike, PUTLike]}
