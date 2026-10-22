"""Programmatic API for running backtests (used by backtest.py and tuning agents).

    from optionsbot.runner import run
    res = run("put_credit_spread", {"short_delta": 0.20}, ["SPY", "QQQ"],
              "2024-03-01", "2026-10-20", 100_000)
    res["SPY"]["metrics"]["sharpe"]; res["SPY"]["equity"]  # DataFrame

Deterministic: same inputs + same cache -> identical outputs.

Engine options pass through **engine_kw, e.g. run_one(..., signal_lag=1) fills
every strategy decision at the next close, share_rows=True adds share-holding
rows to the trade log (see optionsbot/engine.py docstring).
"""
from __future__ import annotations

import pandas as pd

from . import metrics as M
from .data import AlpacaData
from .engine import Costs, Engine, Market
from .strategies import REGISTRY, BuyHold

MARKET_START = "2024-01-19"      # first full day of Alpaca option bars is 2024-01-18
DEFAULT_END = "2026-10-20"
IS_END = "2025-06-30"            # in-sample / out-of-sample split
OOS_START = "2025-07-01"

_client: AlpacaData | None = None
_markets: dict = {}
_vix: pd.Series | None = None


def client(offline: bool = False) -> AlpacaData:
    global _client
    if _client is None:
        _client = AlpacaData(offline=offline)
    return _client


def market(symbol: str, end: str = DEFAULT_END) -> Market:
    global _vix
    key = (symbol, end)
    if key not in _markets:
        m = Market(client(), symbol, MARKET_START, end)
        if _vix is None:
            spy = m if symbol == "SPY" else market("SPY", end)
            _vix = spy.atm_iv_series()
        m.attach_vix_proxy(_vix)
        _markets[key] = m
    return _markets[key]


def run_one(strategy, params: dict | None, symbol: str, start: str, end: str, capital: float,
            costs: Costs | None = None, **engine_kw) -> dict:
    """Run one strategy on one underlying. `strategy` is a registry id or a
    Strategy class. Returns dict(equity, trades, metrics, notes, infeasible, engine)."""
    cls = REGISTRY[strategy] if isinstance(strategy, str) else strategy
    m = market(symbol, max(end, DEFAULT_END) if end else DEFAULT_END)
    m.n_theo = 0
    eng = Engine(m, cls(**(params or {})), capital, start, end, costs=costs, **engine_kw).run()
    ef = eng.equity_frame()
    tr = eng.trades_frame()
    if len(tr):
        tr.insert(0, "underlying", symbol)
        tr.insert(1, "capital", capital)
    met = summarize(ef, tr, eng)
    return {"equity": ef, "trades": tr, "metrics": met, "notes": eng.notes,
            "infeasible": eng.infeasible, "engine": eng, "strategy": cls.id,
            "params": eng.strategy.p}


def summarize(ef: pd.DataFrame, tr: pd.DataFrame, eng: Engine | None = None) -> dict:
    eq = ef["equity"]
    met = {**M.equity_metrics(eq), **M.trade_metrics(tr), **M.exposure_metrics(ef)}
    met["in_sample"] = M.equity_metrics(eq[:IS_END])
    met["out_of_sample"] = M.equity_metrics(eq[OOS_START:])
    if eng is not None:
        met.update({"dividends": round(eng.dividends_received, 2), "fees": round(eng.fees_paid, 2),
                    "slippage": round(eng.slippage_paid, 2), "interest": round(eng.interest_earned, 2),
                    "n_theo_selections": eng.m.n_theo, "n_early_assign": eng.n_early_assign,
                    "infeasible": eng.infeasible, "blocked_days": eng.blocked_days,
                    "kill_events": eng.kill_events})
        if eng.share_lots or eng._lot is not None:
            # trade stats incl. share-holding episodes (tuning/wheel/BUGS.md #2); the flat
            # n_trades / win_rate / profit_factor above stay option-leg-only unless the
            # engine ran with share_rows=True
            met["trade_stats_with_shares"] = M.trade_metrics(eng.trades_frame(include_shares=True))
    return met


def run(strategy, params: dict | None, universe: list[str], start: str = "2024-03-01",
        end: str = DEFAULT_END, capital: float = 100_000, with_benchmark: bool = True,
        costs: Costs | None = None, **engine_kw) -> dict:
    """Run a strategy over a universe. Returns {symbol: result-dict}; each
    result gets metrics['vs_buy_hold'] when with_benchmark."""
    out = {}
    for sym in universe:
        r = run_one(strategy, params, sym, start, end, capital, costs=costs, **engine_kw)
        if with_benchmark:
            b = run_one(BuyHold, None, sym, start, end, capital, costs=costs, **engine_kw)
            r["metrics"]["vs_buy_hold"] = M.compare(r["equity"]["equity"], b["equity"]["equity"])
            r["benchmark_metrics"] = {k: v for k, v in b["metrics"].items()
                                      if not isinstance(v, dict)}
        out[sym] = r
    return out
