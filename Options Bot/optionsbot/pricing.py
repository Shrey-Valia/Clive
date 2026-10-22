"""Black-Scholes-Merton pricing, greeks and implied-vol inversion.

European formulas with a continuous dividend yield. US equity options are
American; for the short-dated OTM options traded here the early-exercise
premium is small, and it is ignored (documented in README).
"""
from __future__ import annotations

import math
from datetime import datetime

SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1d2(S, K, T, r, q, sigma):
    v = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / v
    return d1, d1 - v


def bs_price(S: float, K: float, T: float, r: float, q: float, sigma: float, cp: str) -> float:
    """cp: 'C' or 'P'. T in years."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if cp == "C" else max(0.0, K - S)
    d1, d2 = _d1d2(S, K, T, r, q, sigma)
    dfq, dfr = math.exp(-q * T), math.exp(-r * T)
    if cp == "C":
        return S * dfq * norm_cdf(d1) - K * dfr * norm_cdf(d2)
    return K * dfr * norm_cdf(-d2) - S * dfq * norm_cdf(-d1)


def bs_delta(S, K, T, r, q, sigma, cp) -> float:
    if T <= 0 or sigma <= 0:
        if cp == "C":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1, _ = _d1d2(S, K, T, r, q, sigma)
    if cp == "C":
        return math.exp(-q * T) * norm_cdf(d1)
    return -math.exp(-q * T) * norm_cdf(-d1)


def bs_vega(S, K, T, r, q, sigma) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1, _ = _d1d2(S, K, T, r, q, sigma)
    return S * math.exp(-q * T) * norm_pdf(d1) * math.sqrt(T)


def implied_vol(price: float, S: float, K: float, T: float, r: float, q: float, cp: str,
                lo: float = 0.01, hi: float = 5.0, tol: float = 1e-6) -> float | None:
    """Bisection-safeguarded Newton. Returns None when the price is outside the
    no-arbitrage band (below discounted intrinsic or above the upper bound)."""
    if price is None or not (price > 0) or T <= 0:
        return None
    lower = bs_price(S, K, T, r, q, lo, cp)
    upper = bs_price(S, K, T, r, q, hi, cp)
    if price < lower - 1e-9 or price > upper + 1e-9:
        return None
    if price <= lower + 1e-9:
        return lo
    a, b = lo, hi
    sigma = 0.3
    for _ in range(100):
        p = bs_price(S, K, T, r, q, sigma, cp)
        diff = p - price
        if abs(diff) < tol:
            return sigma
        if diff > 0:
            b = sigma
        else:
            a = sigma
        v = bs_vega(S, K, T, r, q, sigma)
        nxt = sigma - diff / v if v > 1e-8 else None
        sigma = nxt if (nxt is not None and a < nxt < b) else 0.5 * (a + b)
        if b - a < 1e-8:
            return sigma
    return sigma


# --------------------------------------------------------------------------
# Rates / dividend yields (approximate, documented assumptions)
# --------------------------------------------------------------------------
# Piecewise-constant approximation of the effective fed funds / 1-3m T-bill
# rate over the backtest window. Option values are insensitive to small errors
# here for 30-45 DTE contracts; LEAPS are a bit more sensitive.
_RATE_STEPS = [
    ("2000-01-01", 0.053),
    ("2024-09-19", 0.0485),
    ("2024-11-08", 0.046),
    ("2024-12-19", 0.0435),
    ("2025-09-18", 0.041),
    ("2025-10-30", 0.0385),
    ("2025-12-11", 0.036),
]


def risk_free(d) -> float:
    s = d.strftime("%Y-%m-%d") if isinstance(d, datetime) or hasattr(d, "strftime") else str(d)
    r = _RATE_STEPS[0][1]
    for start, val in _RATE_STEPS:
        if s >= start:
            r = val
    return r


DIV_YIELD = {
    "SPY": 0.013, "QQQ": 0.006, "IWM": 0.012, "AAPL": 0.005, "MSFT": 0.008,
    "NVDA": 0.0003, "AMZN": 0.0, "GOOGL": 0.004, "META": 0.0035,
    "TSLA": 0.0, "NFLX": 0.0,     # no cash dividends (tuning/events/BUGS.md #1)
}


def div_yield(sym: str) -> float:
    return DIV_YIELD.get(sym, 0.01)
