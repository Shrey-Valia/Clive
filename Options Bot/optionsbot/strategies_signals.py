"""Signal-timed and directional option strategies (technical-indicator entries).

Research code for tuning/signals/. Built on the core Strategy / Engine API
(nothing in engine.py / strategies.py is modified). All decisions are taken
at the daily close of the signal day (proxy for a ~15:45 snapshot; the
`lag` parameter delays every signal by N bars for a stricter test).

Families
--------
* SignalSpread (generic): entry signal x structure x exit rules.
    signal: dip | always | overbought | volspike | random
    structure: pcs (bull put credit spread) | cds (bull call debit spread)
               | ccs (bear call credit spread)
* PMCCSignal: PMCC whose short calls are only sold after an overbought /
  up-day signal and never below the 50DMA.
* RS routing: any SignalSpread with `rs_months` = set of 'YYYY-MM' months in
  which the underlying is in the top-k by 12-1 momentum (computed outside
  the engine from prior-month-end closes; see tuning/signals/common.py).

Indicators come from optionsbot.indicators on split-adjusted OHLC.
Earnings guard (single names): no entry if an earnings report date (AMC,
SEC EDGAR 8-K item 2.02, from research/event_strategies.md) falls between
entry and the option expiry; any open position is closed at the close of the
report day (before the after-close release).
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from . import indicators as ind
from .engine import load_vix
from .strategies import PoorMansCoveredCall, PutCreditSpread, Strategy

# --------------------------------------------------------------------------
# Earnings report dates (all after the close). Source: SEC EDGAR 8-K item 2.02
# filings, extracted in research/event_strategies.md section 1. The 2026-10
# dates are estimates (EDGAR is historical); they only matter for positions
# open on the last days of the sample.
_E = {
    "AAPL": "2024-02-01 2024-05-02 2024-08-01 2024-10-31 2025-01-30 2025-05-01 2025-07-31 2025-10-30 2026-01-29 2026-04-30 2026-07-30 2026-10-29",
    "MSFT": "2024-01-30 2024-04-25 2024-07-30 2024-10-30 2025-01-29 2025-04-30 2025-07-30 2025-10-29 2026-01-28 2026-04-29 2026-07-29 2026-10-28",
    "NVDA": "2024-02-21 2024-05-22 2024-08-28 2024-11-20 2025-02-26 2025-05-28 2025-08-27 2025-11-19 2026-02-25 2026-05-20 2026-08-26 2026-11-18",
    "AMZN": "2024-02-01 2024-04-30 2024-08-01 2024-10-31 2025-02-06 2025-05-01 2025-07-31 2025-10-30 2026-02-05 2026-04-29 2026-07-30 2026-10-29",
    "GOOGL": "2024-01-30 2024-04-25 2024-07-23 2024-10-29 2025-02-04 2025-04-24 2025-07-23 2025-10-29 2026-02-04 2026-04-29 2026-07-22 2026-10-27",
    "META": "2024-02-01 2024-04-24 2024-07-31 2024-10-30 2025-01-29 2025-04-30 2025-07-30 2025-10-29 2026-01-28 2026-04-29 2026-07-29 2026-10-28",
}
EARNINGS = {k: [pd.Timestamp(x) for x in v.split()] for k, v in _E.items()}

_FEAT: dict = {}


def features(m) -> pd.DataFrame:
    """Per-market indicator frame (split-adjusted), memoized per Market object."""
    key = (m.symbol, id(m))
    if key in _FEAT:
        return _FEAT[key]
    s = m.stock
    k = s["split_close"] / s["close"]
    df = pd.DataFrame({"open": s["open"] * k, "high": s["high"] * k, "low": s["low"] * k,
                       "close": s["split_close"]}, index=s.index)
    c = df["close"]
    f = pd.DataFrame(index=s.index)
    f["close"] = c
    f["rsi2"] = ind.rsi(c, 2)
    f["ibs"] = ind.ibs(df)
    for n in (5, 10, 20, 50, 200):
        f[f"sma{n}"] = ind.sma(c, n)
    f["atr14"] = ind.atr(df, 14)
    f["adx14"] = ind.adx(df, 14)["adx"]
    f["rv20"] = ind.rv_close(c, 20)
    f["streak"] = ind.streak(c)
    f["mom12_1"] = c.shift(21) / c.shift(252) - 1
    f["ret1"] = c.pct_change()
    vix, vix3m = load_vix(m.client.cache_dir)
    if vix is not None:
        vr = ind.iv_rank(vix, 252)
        f["vix"] = vix.reindex(s.index).ffill(limit=5)
        f["vix_ivr"] = vr.reindex(s.index).ffill(limit=5)
        f["vix_ratio"] = ind.vix_term_ratio(vix, vix3m).reindex(s.index).ffill(limit=5)
    else:
        f["vix"] = f["vix_ivr"] = f["vix_ratio"] = np.nan
    f["regime"] = ind.regime(c, f["vix"], 200, vol_threshold=20.0)
    f["pos"] = np.arange(len(f))
    _FEAT[key] = f
    return f


# --------------------------------------------------------------------------
class SignalSpread(PutCreditSpread):
    """Signal-timed vertical spread on one underlying (one position at a time)."""
    id, name = "sig_spread", "Signal-timed vertical spread"
    defaults = dict(
        # --- entry signal
        signal="dip", lag=0,
        rsi_lo=10.0, ibs_lo=None,              # dip
        rsi_hi=90.0, atr_k=2.0,                # overbought: close > sma20 + atr_k*ATR
        vix_ratio_hi=1.0, vix_hi=30.0,         # volspike: ratio > x OR vix > y
        trend="sma200",                        # 'sma200' (bull entries need close>200DMA),
                                               # 'below200' (bear entries need close<200DMA), None
        ivr_max=None, ivr_min=None,            # VIX IV-rank (0-100, 252d) gate
        rand_p=0.1, rand_seed=0,               # placebo
        rs_months=None,                        # RS routing: set of 'YYYY-MM' active months
        # --- structure
        structure="pcs", short_delta=0.30, long_delta=0.15, width_pct=None, width_abs=None,
        dte_min=7, dte_max=21, dte_target=14,
        risk_frac=0.25, max_positions=1,
        # --- exits
        tp=0.5, sl_mult=None, close_dte=None,
        debit_tp=0.5, debit_sl=0.5,            # cds: +50% / -50% of debit
        max_days=5,                            # trading days held
        exit_sma5=True,                        # bull: close>sma5 ; bear: close<sma5
        earn_guard=True,
        cooldown=0,                            # bars to wait after an exit
        # --- quote sanity (asynchronous option closes can show credit ~ width)
        max_credit_frac=0.6, min_debit_frac=0.15,
    )

    # ------------------------------------------------------------- signal
    def _row(self, eng, d, lag=None):
        f = features(eng.m)
        i = int(f.at[d, "pos"]) - (self.p["lag"] if lag is None else lag)
        return f.iloc[i] if i >= 0 else None

    def bullish(self):
        return self.p["structure"] in ("pcs", "cds")

    def signal_ok(self, eng, d) -> bool:
        p = self.p
        r = self._row(eng, d)
        if r is None:
            return False
        sig = p["signal"]
        if p["rs_months"] is not None and d.strftime("%Y-%m") not in p["rs_months"]:
            return False
        if p["trend"] == "sma200":
            if not (np.isfinite(r["sma200"]) and r["close"] > r["sma200"]):
                return False
        elif p["trend"] == "below200":
            if not (np.isfinite(r["sma200"]) and r["close"] < r["sma200"]):
                return False
        elif p["trend"] == "sma50":
            if not (np.isfinite(r["sma50"]) and r["close"] > r["sma50"]):
                return False
        ivr = r["vix_ivr"]
        if p["ivr_max"] is not None and not (np.isfinite(ivr) and ivr <= p["ivr_max"]):
            return False
        if p["ivr_min"] is not None and not (np.isfinite(ivr) and ivr >= p["ivr_min"]):
            return False
        if sig == "always":
            return True
        if sig == "dip":
            ok = np.isfinite(r["rsi2"]) and r["rsi2"] < p["rsi_lo"]
            if p["ibs_lo"] is not None:
                ok = ok and np.isfinite(r["ibs"]) and r["ibs"] < p["ibs_lo"]
            return bool(ok)
        if sig == "ibs":
            return bool(np.isfinite(r["ibs"]) and r["ibs"] < p["ibs_lo"])
        if sig == "overbought":
            ok = np.isfinite(r["rsi2"]) and r["rsi2"] > p["rsi_hi"]
            if p["atr_k"] is not None:
                ok = ok and np.isfinite(r["atr14"]) and r["close"] > r["sma20"] + p["atr_k"] * r["atr14"]
            return bool(ok)
        if sig == "volspike":
            vr, v = r["vix_ratio"], r["vix"]
            return bool((np.isfinite(vr) and vr > p["vix_ratio_hi"]) or
                        (p["vix_hi"] is not None and np.isfinite(v) and v > p["vix_hi"]))
        if sig == "random":
            h = hashlib.md5(f"{eng.m.symbol}|{d.date()}|{p['rand_seed']}".encode()).hexdigest()
            return int(h[:8], 16) / 2 ** 32 < p["rand_p"]
        raise ValueError(sig)

    # ------------------------------------------------------------ earnings
    def earnings_between(self, sym, a, b) -> bool:
        return any(a <= e <= b for e in EARNINGS.get(sym, []))

    # -------------------------------------------------------------- exits
    def held_bars(self, eng, pos, d):
        f = features(eng.m)
        return int(f.at[d, "pos"] - f.at[pos.open_date, "pos"])

    def manage(self, eng, d):
        p = self.p
        f = features(eng.m)
        r = f.loc[d]
        for pos in list(eng.positions):
            reason = None
            if pos.kind == "cds":
                debit = pos.entry_mark_value          # > 0
                val = pos.value()
                if debit > 0:
                    if p["debit_tp"] is not None and val >= debit * (1 + p["debit_tp"]):
                        reason = "take_profit"
                    elif p["debit_sl"] is not None and val <= debit * (1 - p["debit_sl"]):
                        reason = "stop_loss"
                if reason is None and p["close_dte"] is not None and pos.min_dte(d) <= p["close_dte"]:
                    reason = "dte_exit"
            else:
                reason = self.credit_exit(eng, pos, d, p["tp"], p["sl_mult"], p["close_dte"])
            held = self.held_bars(eng, pos, d)
            if reason is None and p["max_days"] is not None and held >= p["max_days"]:
                reason = "time_exit"
            if reason is None and p["exit_sma5"] and held >= 1 and np.isfinite(r["sma5"]):
                if pos.kind in ("pcs", "cds") and r["close"] > r["sma5"]:
                    reason = "sma5_exit"
                elif pos.kind == "ccs" and r["close"] < r["sma5"]:
                    reason = "sma5_exit"
            if reason is None and p["earn_guard"] and d in EARNINGS.get(eng.m.symbol, []):
                reason = "earnings_exit"
            if reason is None and p["rs_months"] is not None and d.strftime("%Y-%m") not in p["rs_months"]:
                reason = "rs_exit"
            if reason:
                eng.close(pos, reason)
                self._last_exit = int(f.at[d, "pos"])

    # ------------------------------------------------------------ entries
    def on_day(self, eng, d):
        self.manage(eng, d)
        p = self.p
        if len(eng.positions) >= p["max_positions"]:
            return
        f = features(eng.m)
        le = self.__dict__.get("_last_exit")
        if p["cooldown"] and le is not None and f.at[d, "pos"] - le < p["cooldown"]:
            return
        if not self.signal_ok(eng, d):
            return
        st = p["structure"]
        cp = "P" if st == "pcs" else "C"
        if st == "cds":
            lq = eng.m.select(d, "C", delta=p["long_delta"], **self.dte_kw())
            if lq is None:
                return
            sq = eng.m.select(d, "C", delta=p["short_delta"], expiry=lq.expiry, iv_hint=lq.iv)
            if p.get("width_pct"):
                sq = eng.m.select(d, "C", strike=lq.strike + p["width_pct"] * eng.spot(),
                                  expiry=lq.expiry, iv_hint=lq.iv)
            if sq is None or sq.strike <= lq.strike:
                return
            expiry = lq.expiry
        else:
            sq = eng.m.select(d, cp, delta=p["short_delta"], **self.dte_kw())
            if sq is None:
                return
            lq = self.long_leg(eng, d, sq, cp)
            if lq is None:
                return
            if (cp == "P" and lq.strike >= sq.strike) or (cp == "C" and lq.strike <= sq.strike):
                return
            expiry = sq.expiry
        if p["earn_guard"] and self.earnings_between(eng.m.symbol, d, expiry):
            eng.note("skipped entry: earnings before expiry")
            return
        if st == "cds":
            c = eng.costs
            per = ((lq.price + c.slip(lq.price)) - max(sq.price - c.slip(sq.price), 0.0)) * 100 \
                + 2 * c.fee_per_contract
            if per <= 5 or (lq.price - sq.price) <= 0.05:
                return
            if (lq.price - sq.price) < p["min_debit_frac"] * (sq.strike - lq.strike):
                eng.note("skipped entry: debit implausibly small vs width (stale prints)")
                return
            budget = min(eng.equity() * p["risk_frac"], eng.free_cash())
            n = int(budget // per)
            if n == 0:
                eng.blocked("skipped entries: one debit spread exceeds risk budget")
                return
            eng.open([(lq, n), (sq, -n)], "cds", reserve=0.0)
        else:
            width = abs(sq.strike - lq.strike)
            credit = sq.price - lq.price
            if credit <= 0.05:
                return
            if credit > p["max_credit_frac"] * width:
                eng.note("skipped entry: credit implausibly large vs width (stale prints)")
                return
            n = self.size(eng, (width - credit) * 100)
            if n == 0:
                eng.blocked("skipped entries: one spread's max loss exceeds risk budget")
                return
            eng.open([(sq, -n), (lq, n)], st, reserve=width * 100 * n)


# --------------------------------------------------------------------------
class DipPCS(SignalSpread):
    id, name = "dip_pcs", "RSI2 dip in uptrend -> short-dated put credit spread"
    defaults = {**SignalSpread.defaults}


class DipCDS(SignalSpread):
    id, name = "dip_cds", "RSI2 dip in uptrend -> call debit spread"
    defaults = {**SignalSpread.defaults, "structure": "cds", "long_delta": 0.50,
                "short_delta": 0.25, "dte_min": 21, "dte_max": 35, "dte_target": 28,
                "max_days": 10, "ivr_max": 50.0, "risk_frac": 0.10}


class OverboughtCCS(SignalSpread):
    id, name = "ob_ccs", "Overbought (RSI2>90, >20DMA+2ATR) -> call credit spread"
    defaults = {**SignalSpread.defaults, "signal": "overbought", "structure": "ccs",
                "trend": None, "short_delta": 0.25, "long_delta": 0.10}


class VolSpikeBull(SignalSpread):
    id, name = "vol_spike", "VIX spike (VIX/VIX3M>1 or VIX>30) -> bull spread"
    defaults = {**SignalSpread.defaults, "signal": "volspike", "trend": None,
                "dte_min": 21, "dte_max": 45, "dte_target": 30, "max_days": 21,
                "exit_sma5": False, "short_delta": 0.25, "long_delta": 0.10}


# --------------------------------------------------------------------------
class PMCCSignal(PoorMansCoveredCall):
    """PMCC; the short call is only sold on an overbought/up-move signal and
    never while close < 50DMA. Optional take-profit on the short call."""
    id, name = "pmcc_signal", "PMCC, signal-timed short calls"
    defaults = {**PoorMansCoveredCall.defaults, "skip_short_below_sma50": True,
                "sell_signal": "rsi2", "sell_rsi": 90.0, "sell_streak": 3,
                "short_tp": None}

    def sell_ok(self, eng, d) -> bool:
        p = self.p
        r = features(eng.m).loc[d]
        if p["skip_short_below_sma50"] and not (np.isfinite(r["sma50"]) and r["close"] > r["sma50"]):
            return False
        s = p["sell_signal"]
        if s is None:
            return True
        if s == "rsi2":
            return bool(np.isfinite(r["rsi2"]) and r["rsi2"] > p["sell_rsi"])
        if s == "streak":
            return bool(r["streak"] >= p["sell_streak"])
        if s == "ibs_hi":
            return bool(r["rsi2"] > p["sell_rsi"] or r["streak"] >= p["sell_streak"])
        raise ValueError(s)

    def on_day(self, eng, d):
        _pmcc_on_day(self, eng, d)


def _pmcc_on_day(self, eng, d):
    """Copy of PoorMansCoveredCall.on_day with the signal gate (kept here so
    strategies.py stays untouched)."""
    p = self.p
    if p["short_tp"] is not None:
        for s in eng.open_positions("pmcc_short"):
            sh = s.legs[0]
            if sh.open and sh.mark <= sh.entry_mark * (1 - p["short_tp"]):
                eng.close(s, "take_profit")
    leaps = eng.open_positions("pmcc_leaps")
    for s in eng.open_positions("pmcc_short"):
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
    if not self.sell_ok(eng, d):
        return
    n = L.legs[0].qty
    q = eng.m.select(d, "C", delta=p["call_delta"], **self.dte_kw())
    if q is None or q.expiry >= L.legs[0].expiry:
        return
    reserve = max(0.0, L.legs[0].strike - q.strike) * 100 * n
    eng.open([(q, -n)], "pmcc_short", reserve=reserve)




SIGNAL_REGISTRY = {c.id: c for c in [SignalSpread, DipPCS, DipCDS, OverboughtCCS, VolSpikeBull,
                                      PMCCSignal]}
