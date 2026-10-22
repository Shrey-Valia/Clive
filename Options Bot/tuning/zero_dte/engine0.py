"""Minute-resolution 0DTE SPY pricing / fill / mark-to-market engine (research only).

Price model for contract X at minute t:
  * if X printed in minute t: that bar's VWAP (mode='vw') or close (mode='c');
  * otherwise BS(S_t, K, tau_t, iv_K) where iv_K is the most recent implied vol
    for strike K, taken from the OTM side's print when available (put-call parity
    makes the strike's vol the same for C and P), else from the contract's own print.
  Prices are floored at intrinsic. tau = minutes to 16:00 / (390*252) (trading time).
Fills: sells at price - h, buys at price + h, h = slip_mult * tier(price)
  tier: $0.01 (<$0.50), $0.02 ($0.50-$2), $0.03 (>$2) ~ half the typical SPY 0DTE spread.
Fees: $0.03 per contract per leg per transaction.
Bounds: each short vertical is valued in [0, width] and the whole fly/condor in [0, max width],
  because stale trade prints on one leg can otherwise imply arbitrage values. Entries where a
  vertical's mid is >= 95% of its width are rejected (credit_near_width).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import zdata as z  # noqa: E402
from optionsbot.pricing import risk_free  # noqa: E402

YEAR_MIN = 390 * 252
FEE = 0.03 / 100           # per share-equivalent, per leg per transaction
SQ_PI2 = math.sqrt(math.pi / 2)


def bs_vec(S, K, T, r, sig, is_call):
    S, K, T, sig = map(np.asarray, (S, K, T, sig))
    v = sig * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + (r + 0.5 * sig * sig) * T) / v
    d2 = d1 - v
    df = np.exp(-r * T)
    c = S * ndtr(d1) - K * df * ndtr(d2)
    p = K * df * ndtr(-d2) - S * ndtr(-d1)
    return np.where(is_call, c, p)


def iv_vec(price, S, K, T, r, is_call, lo=0.01, hi=6.0, it=60):
    price = np.asarray(price, float)
    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    plo = bs_vec(S, K, T, r, a, is_call)
    phi = bs_vec(S, K, T, r, b, is_call)
    ok = (price > plo + 1e-6) & (price < phi) & np.isfinite(price)
    for _ in range(it):
        m = 0.5 * (a + b)
        pm = bs_vec(S, K, T, r, m, is_call)
        up = pm > price
        b = np.where(up, m, b)
        a = np.where(up, a, m)
    return np.where(ok, 0.5 * (a + b), np.nan)


def tier(p):
    p = np.asarray(p, float)
    return np.where(p < 0.5, 0.01, np.where(p <= 2.0, 0.02, 0.03))


class Day:
    """One session: SPY minute path + option price surfaces on a 09:30..15:59 grid."""

    def __init__(self, day: str, mode: str = "vw"):
        self.day = day
        self.D = pd.Timestamp(day)
        self.mode = mode
        spy = z.spy_all()
        spy = spy.loc[day]
        self.grid = pd.date_range(f"{day} 09:30", f"{day} 15:59", freq="1min", tz="America/New_York")
        rth = spy.reindex(self.grid)
        col = "vw" if mode == "vw" else "c"
        self.S = rth[col].ffill().bfill().to_numpy()
        self.Sc = rth["c"].ffill().bfill().to_numpy()
        self.spy_open = float(spy.between_time("09:30", "09:30")["o"].iloc[0]) if len(rth) else np.nan
        self.close = float(self.Sc[-1])
        mins_left = 390 - np.arange(390)            # minute i starts at 09:30+i; tau to 16:00
        self.tau = (mins_left - 0.5) / YEAR_MIN
        self.r = risk_free(self.D)
        lo, hi = int(np.ceil(self.spy_open * 0.95)), int(np.floor(self.spy_open * 1.05))
        self.strikes = np.arange(lo, hi + 1)
        syms = [z.occ(self.D, cp, k) for k in self.strikes for cp in "CP"]
        bars = z.option_minutes(day, syms)
        n, m = len(self.strikes), 390
        self.raw = {"C": np.full((n, m), np.nan), "P": np.full((n, m), np.nan)}
        self.vol = {"C": np.zeros((n, m)), "P": np.zeros((n, m))}
        for j, k in enumerate(self.strikes):
            for cp in "CP":
                b = bars[z.occ(self.D, cp, k)]
                if len(b):
                    b = b.reindex(self.grid)
                    self.raw[cp][j] = b[col].to_numpy(float)
                    self.vol[cp][j] = b["v"].fillna(0).to_numpy(float)
        self._surface()

    def _surface(self):
        K = self.strikes[:, None].astype(float)
        S = self.S[None, :]
        T = self.tau[None, :]
        ivc = iv_vec(self.raw["C"], S, K, T, self.r, True)
        ivp = iv_vec(self.raw["P"], S, K, T, self.r, False)
        otm_c = K >= S
        iv = np.where(otm_c, np.where(np.isfinite(ivc), ivc, ivp), np.where(np.isfinite(ivp), ivp, ivc))
        ivdf = pd.DataFrame(iv.T)
        self.iv = ivdf.ffill().to_numpy().T                 # carried per-strike vol
        last = pd.DataFrame(np.where(np.isfinite(iv), np.arange(390)[None, :], np.nan).T).ffill().to_numpy().T
        self.age = np.arange(390)[None, :] - last            # minutes since strike's last usable print
        self.px = {}
        for cp, isc in (("C", True), ("P", False)):
            model = bs_vec(S, K, T, self.r, self.iv, isc)
            intr = np.maximum(S - K, 0) if isc else np.maximum(K - S, 0)
            p = np.where(np.isfinite(self.raw[cp]), self.raw[cp], model)
            self.px[cp] = np.maximum(p, intr)

    # ------------------------------------------------------------------ helpers
    def idx(self, hhmm: str) -> int:
        h, m = map(int, hhmm.split(":"))
        return (h - 9) * 60 + m - 30

    def j(self, k: float) -> int | None:
        jj = int(round(k)) - int(self.strikes[0])
        return jj if 0 <= jj < len(self.strikes) else None

    def price(self, cp: str, k: float, i: int) -> float:
        jj = self.j(k)
        return np.nan if jj is None else float(self.px[cp][jj, i])

    def leg_age(self, k, i):
        jj = self.j(k)
        return np.nan if jj is None else float(self.age[jj, i])

    def straddle(self, i: int):
        k = float(round(self.S[i]))
        c, p = self.price("C", k, i), self.price("P", k, i)
        return k, c, p


# ---------------------------------------------------------------------- structures
def build_legs(d: Day, i: int, structure: str, wing: str):
    """Return list of (cp, K, qty) with qty -1 short / +1 long, plus info dict.
    structure: 'fly' (ATM short straddle + wings) or 'ic' (shorts at +-1 sigma-move).
    wing: '1.0x' | '1.5x' (multiples of the straddle-implied 1-sigma remaining move)
          or '$3' | '$5' | '$10' (fixed distance from the short strikes)."""
    K0, c, p = d.straddle(i)
    S = d.S[i]
    if not (np.isfinite(c) and np.isfinite(p)):
        return None, {"fail": "no_atm"}
    sig_move = (c + p) * SQ_PI2
    if structure == "fly":
        kc = kp = K0
    else:
        kc = max(round(S + sig_move), K0 + 1)
        kp = min(round(S - sig_move), K0 - 1)
    if wing.endswith("x"):
        mult = float(wing[:-1])               # fly: wings at +-m sigma; ic: longs at +-m sigma
        wc = max(round(S + mult * sig_move), kc + 1)
        wp = min(round(S - mult * sig_move), kp - 1)
    else:
        w = float(wing[1:])
        wc, wp = kc + w, kp - w
    legs = [("P", kp, -1), ("P", wp, +1), ("C", kc, -1), ("C", wc, +1)]
    if any(d.j(k) is None for _, k, _ in legs):
        return None, {"fail": "strike_out_of_range"}
    width = max(kp - wp, wc - kc)
    return legs, {"K0": K0, "sig_move": sig_move, "straddle": c + p, "width": width,
                  "strikes": f"{wp:g}/{kp:g}/{kc:g}/{wc:g}"}


def _sides(legs):
    """Split [P short, P long, C short, C long] into two short verticals with widths."""
    (pc, kp, _), (_, wp, _), (cc, kc, _), (_, wc, _) = legs
    return [("P", kp, wp, kp - wp), ("C", kc, wc, wc - kc)]


def side_mid(d: Day, cp, ks, kl, w, i):
    """Mid value of a short vertical (what it costs to buy back), bounded to [0, width]:
    stale trade prints on one leg can otherwise imply arbitrage values."""
    v = d.price(cp, ks, i) - d.price(cp, kl, i)
    return float(min(max(v, 0.0), w))


def value(d: Day, legs, i: int) -> float:
    """Mark value of the position (per share, negative = liability) at minute i."""
    sd = _sides(legs)
    # an iron fly/condor pays at most max(width) at expiry, so its value is bounded too
    return -min(sum(side_mid(d, cp, ks, kl, w, i) for cp, ks, kl, w in sd), max(x[3] for x in sd))


def fill(d: Day, legs, i: int, slip: float, opening: bool, skip_cheap=False):
    """Cash flow (per share) of opening (credit, >0) or closing (<0) the position at minute i.
    Each vertical trades at its bounded mid, less slippage on both legs, less fees."""
    cash = 0.0
    S = d.S[i]
    sd = _sides(legs)
    excess = max(0.0, sum(side_mid(d, cp, ks, kl, w, i) for cp, ks, kl, w in sd) - max(x[3] for x in sd))
    cash += excess if not opening else -excess   # remove any cross-side value above the bound
    for cp, ks, kl, w in sd:
        mid = side_mid(d, cp, ks, kl, w, i)
        ps, pl = d.price(cp, ks, i), d.price(cp, kl, i)
        if skip_cheap and not opening:
            otm = (ks > S * 1.003) if cp == "C" else (ks < S * 0.997)
            if ps <= 0.02 and otm:
                # left to expire: settles at the 16:00 intrinsic, no fill cost
                intr_s = max(d.close - ks, 0) if cp == "C" else max(ks - d.close, 0)
                intr_l = max(d.close - kl, 0) if cp == "C" else max(kl - d.close, 0)
                cash -= min(max(intr_s - intr_l, 0.0), w)
                continue
        cost = slip * (float(tier(ps)) + float(tier(pl))) + 2 * FEE
        cash += (mid - cost) if opening else -(mid + cost)
    return cash


def run_trade(d: Day, structure: str, wing: str, entry: str, exit_: str = "15:25",
              pt: float | None = None, slip: float = 1.0, keep_path: bool = False):
    i0, i1 = d.idx(entry), d.idx(exit_)
    legs, info = build_legs(d, i0, structure, wing)
    if legs is None:
        return {"ok": False, **info}
    ages = [d.leg_age(k, i0) for _, k, _ in legs]
    if not all(np.isfinite(d.price(cp, k, i0)) for cp, k, _ in legs):
        return {"ok": False, "fail": "no_entry_price", **info}
    if any(side_mid(d, cp, ks, kl, w, i0) >= 0.95 * w for cp, ks, kl, w in _sides(legs)):
        return {"ok": False, "fail": "credit_near_width", **info}
    credit = fill(d, legs, i0, slip, opening=True)
    if not np.isfinite(credit) or credit <= 0:
        return {"ok": False, "fail": "bad_credit", **info}
    max_risk = info["width"] - credit
    if max_risk <= 0:
        return {"ok": False, "fail": "bad_risk", **info}
    path = []
    ix = i1
    hit = False
    for i in range(i0 + 1, i1 + 1):
        path.append(credit + value(d, legs, i))   # P&L if marked at mid (no exit cost)
        if pt is not None and i < i1:
            if credit + fill(d, legs, i, slip, opening=False) >= pt * credit:
                ix = min(i + 1, i1)               # signal on bar i, fill on bar i+1
                hit = True
                break
    pnl = credit + fill(d, legs, ix, slip, opening=False, skip_cheap=True)
    path = np.array(path) if path else np.array([0.0])
    out = {"ok": True, **info, "S": d.S[i0], "credit": credit, "max_risk": max_risk, "pnl": pnl,
           "pnl_usd": pnl * 100, "ret": pnl / max_risk, "pnl_bp": pnl / d.S[i0] * 1e4,
           "mae": float(min(0.0, np.nanmin(path))), "mae_ret": float(min(0.0, np.nanmin(path))) / max_risk,
           "exit_t": str(d.grid[ix].strftime("%H:%M")), "pt_hit": hit,
           "max_leg_age_entry": float(np.nanmax(ages)),
           "move_entry_exit": d.S[ix] / d.S[i0] - 1}
    if keep_path:
        out["path"] = path
        out["path_t"] = [t.strftime("%H:%M") for t in d.grid[i0 + 1:i0 + 1 + len(path)]]
    return out
