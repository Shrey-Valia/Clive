# Engine / strategy issues found during the wheel study

None of these files were edited. Each issue lists the workaround used in `optionsbot/strategies_wheel.py` or in `tuning/wheel/`.

## 1. `Wheel.call_above_basis` is not strict (strategies.py, `Wheel.on_day`)
If the first strike at or above the cost basis prices below $0.05, the code silently falls back to the original target-delta strike. That strike can sit **below** basis. After a deep drawdown the "never sell below basis" rule therefore turns into "sell below basis whenever the above-basis call is worthless", which is exactly when it matters most.
*Workaround:* `WheelX(call_basis="strict")` skips the call and holds the shares uncovered for that day. `call_basis="engine"` reproduces the built-in behaviour bit-for-bit (verified: QQQ/NVDA wheel CAGR, Sharpe, DD and trade count identical).

## 2. Wheel trade log mixes option P&L and assignment marks (engine.py, `_expire`)
On physical assignment the engine adds `qty*100*intrinsic` to `exit_cash` "for the trade log". This books a put assignment as a loss equal to its intrinsic value. The later recovery of the assigned shares never appears in any trade row, because share P&L is not logged. `win_rate`, `profit_factor` and `worst_trade` for any physical-settlement strategy are therefore **option-leg statistics only**, and they overstate losses on assigned puts while ignoring the share P&L.
*Workaround:* every headline number in REPORT.md is computed from the equity curve. Trade stats are labelled "option legs". The worst-episode table uses equity drawdowns.

## 3. The built-in covered call rolls ITM calls at 21 DTE (strategies.py, `_StockPlusCalls`)
`roll_dte=21` closes the short call whatever its moneyness, so an ITM call is bought back at a loss and re-sold at 0.30Δ. Shares are never called away. In a strong uptrend this realizes the loss on every ITM call, and it is the main reason the built-in `covered_call` lags. This is a design choice rather than a bug, but the README does not say so.
*Workaround:* `WheelX(entry="stock")` holds the call to expiry, takes physical call-away, and re-buys lots at the next close. Optionally it closes OTM calls only at N DTE (`call_exit_dte`).

## 4. Base `Strategy.kill_switch` closes *all* positions
When the base kill switch is reused for a stock-backed strategy, it closes the covered call but keeps the shares, which leaves the account fully long and unhedged during the stress event. That is the opposite of the intent.
*Workaround:* `WheelX` has its own kill switch. It closes only short puts by default. With `kill_shares=True` it also closes the call and sells the shares, and re-entry waits for the next monthly expiry.

## 5. `Engine` has no multi-underlying / shared-cash mode
There is one engine per symbol, so a multi-name book cannot share collateral.
*Workaround:* `tuning/wheel/portfolio.py` steps one Engine per name in lock-step and calls the Engine's own daily methods in `Engine.run()` order. Cash sits in a shared pool and is lent to flat sleeves. The only engine internals touched are the `_`-prefixed step methods, which are called, not modified.

## 6. Minor
- `share_basis` after put assignment is the strike, not strike minus premium. WheelX's `call_basis="net"` tracks premium since the last call-away to compute a net basis.
- `metrics.trade_metrics` returns `profit_factor = inf` when there are no losing option legs. Those values are written as blank in results.csv.
- NVDA's 10:1 split on 2024-06-10: `rank_expiries` blocks any expiry that spans a split, so from late April 2024 the NVDA wheel can only sell sub-45-DTE options and pauses near the split. This is correct, but it depresses the NVDA IS numbers for long-DTE variants.
