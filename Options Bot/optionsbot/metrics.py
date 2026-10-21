"""Performance metrics from a daily equity curve and a trade log."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TD = 252


def drawdown(eq: pd.Series) -> pd.Series:
    return eq / eq.cummax() - 1.0


def max_dd_duration(eq: pd.Series) -> int:
    """Longest stretch (trading days) spent below a prior equity high."""
    peak = eq.cummax()
    under = eq < peak - 1e-9
    longest = cur = 0
    for u in under.values:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    return int(longest)


def equity_metrics(eq: pd.Series, rf: float = 0.0) -> dict:
    eq = eq.dropna()
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return {}
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years > 0 and eq.iloc[-1] > 0 else float("nan")
    vol = rets.std() * math.sqrt(TD)
    ex = rets - rf / TD
    sharpe = ex.mean() / rets.std() * math.sqrt(TD) if rets.std() > 0 else float("nan")
    downside = np.sqrt((np.minimum(ex, 0) ** 2).mean())
    sortino = ex.mean() / downside * math.sqrt(TD) if downside > 0 else float("nan")
    dd = drawdown(eq)
    return {
        "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
        "start_equity": round(float(eq.iloc[0]), 2), "end_equity": round(float(eq.iloc[-1]), 2),
        "total_return": float(total), "cagr": float(cagr), "ann_vol": float(vol),
        "sharpe": float(sharpe), "sortino": float(sortino),
        "max_drawdown": float(dd.min()), "max_dd_duration_days": max_dd_duration(eq),
        "calmar": float(cagr / -dd.min()) if dd.min() < 0 else float("nan"),
        "best_day": float(rets.max()), "worst_day": float(rets.min()),
    }


def trade_metrics(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"n_trades": 0}
    t = trades[trades["reason"] != "open_at_end"]
    if t.empty:
        return {"n_trades": 0, "n_open_at_end": int(len(trades))}
    pnl = t["pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    return {
        "n_trades": int(len(t)),
        "win_rate": float(len(wins) / len(t)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(pf),
        "worst_trade": float(pnl.min()),
        "best_trade": float(pnl.max()),
        "total_trade_pnl": float(pnl.sum()),
        "avg_days_held": float(t["days_held"].mean()),
        "pct_theo_entries": float(t["theo_entry"].mean()),
        "pct_trades_with_stale_marks": float((t["stale_mark_days"] > 0).mean()),
        "n_assigned": int(t["assigned"].sum()),
        "exit_reasons": t["reason"].value_counts().to_dict(),
    }


def exposure_metrics(ef: pd.DataFrame) -> dict:
    eq = ef["equity"]
    in_mkt = (ef["n_positions"] > 0) | (ef["shares"].abs() > 0)
    return {
        "pct_time_in_market": float(in_mkt.mean()),
        "avg_capital_at_risk_pct": float((ef["capital_at_risk"] / eq).mean()),
        "avg_net_delta_pct": float((ef["delta_dollars"] / eq).mean()),
        "pct_days_with_stale_legs": float((ef["stale_legs"] > 0).mean()),
    }


def monthly_returns(eq: pd.Series) -> pd.Series:
    m = eq.resample("ME").last()
    first = eq.iloc[0]
    r = m.pct_change()
    r.iloc[0] = m.iloc[0] / first - 1
    return r


def compare(eq: pd.Series, bench: pd.Series) -> dict:
    j = pd.concat([eq.pct_change(), bench.pct_change()], axis=1).dropna()
    if len(j) < 10 or j.iloc[:, 0].std() == 0 or j.iloc[:, 1].std() == 0:
        return {}
    a, b = j.iloc[:, 0], j.iloc[:, 1]
    beta = a.cov(b) / b.var() if b.var() > 0 else float("nan")
    alpha = (a.mean() - beta * b.mean()) * TD
    return {"corr_vs_bh": float(a.corr(b)), "beta_vs_bh": float(beta),
            "alpha_ann_vs_bh": float(alpha),
            "excess_total_return_vs_bh": float((eq.iloc[-1] / eq.iloc[0]) - (bench.iloc[-1] / bench.iloc[0]))}
