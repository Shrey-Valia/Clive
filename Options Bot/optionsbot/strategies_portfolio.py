"""Portfolio-level strategy subclasses (tuning/portfolio/).

Nothing in the core engine / other strategy files is modified.

PooledDipPCS
    The frozen signals finalist (RSI2<10 & IBS<0.2 & close>200DMA -> 0.25/0.10 delta
    7-14 DTE put credit spread; exits 50% TP / close>5DMA / 5 days) whose contract
    count is decided by a portfolio controller (shared risk cap across underlyings)
    instead of the sleeve's own equity. Keeps the bug-#7 guard (skip spreads whose
    credit > 60% of width) inherited from SignalSpread.

    The controller must expose  risk_budget(eng) -> dollars of max loss allowed for
    the next trade on that sleeve. Without a controller it behaves exactly like
    DipPCS (risk_frac of the sleeve's own equity).
"""
from __future__ import annotations

from .strategies_signals import DipPCS
from .strategies_spreads import SpreadBook

# Frozen finalist parameters (tuning/signals/finalists.json, sig_dip_pcs_*).
DIP_PCS_FINALIST = dict(signal="dip", rsi_lo=10, ibs_lo=0.2, trend="sma200", structure="pcs",
                        short_delta=0.25, long_delta=0.10, dte_min=7, dte_max=14, dte_target=10,
                        tp=0.5, max_days=5, exit_sma5=True, risk_frac=0.25, max_positions=1,
                        max_credit_frac=0.6)


def position_max_loss(pos) -> float:
    """Structural max loss of an open vertical credit spread: collateral - net credit."""
    if "max_loss" in pos.meta:          # SpreadBook positions record it at entry (mark credit)
        return float(pos.meta["max_loss"])
    return max(pos.meta.get("reserve0", pos.reserve) - pos.entry_cash, 0.0)


class PooledDipPCS(DipPCS):
    id, name = "pooled_dip_pcs", "Dip PCS sized by a shared portfolio risk cap"
    defaults = {**DipPCS.defaults, **DIP_PCS_FINALIST}

    controller = None   # set by the portfolio runner (class attr default -> standalone)

    def size(self, eng, max_loss_per):
        if max_loss_per <= 0:
            return 0
        ctl = self.controller
        if ctl is None:
            return super().size(eng, max_loss_per)
        budget = min(ctl.risk_budget(eng), eng.free_cash())
        return max(int(budget // max_loss_per), 0)


# Frozen spreads finalist F1 (tuning/spreads/frozen.json): SPY iron condor, 0.10 delta shorts,
# wings 3% of spot, 50-70 DTE (target 60), held to expiry, max loss <= 20% of equity.
IC_FINALIST = dict(structure="ic", put_delta=0.10, call_delta=0.10, wing="pct", width_pct=0.03,
                   dte_min=50, dte_max=70, dte_target=60, tp=None, sl_mult=None, breach=False,
                   close_dte=None, risk_frac=0.20, max_positions=1, max_credit_frac=0.60)


class PooledIronCondor(SpreadBook):
    """Spreads finalist F1 with the contract count set by the portfolio controller."""
    id, name = "pooled_ic", "SPY 10-delta 60 DTE iron condor sized by a shared risk cap"
    defaults = {**SpreadBook.defaults, **IC_FINALIST}

    controller = None

    def _size(self, eng, max_loss_per):
        if max_loss_per <= 0:
            return 0
        ctl = self.controller
        if ctl is None:
            return super()._size(eng, max_loss_per)
        budget = min(ctl.risk_budget(eng), eng.free_cash())
        return max(int(budget // max_loss_per), 0)


PORTFOLIO_REGISTRY = {c.id: c for c in [PooledDipPCS, PooledIronCondor]}
