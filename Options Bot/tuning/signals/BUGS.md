# Bugs and caveats found in the core backtester (not fixed here, by rule)

Found while building the signal-timed strategies. Core files were not edited.

1. **Same-close signal and fill (design look-ahead, minor).** `Engine.run` calls `strategy.on_day(eng, d)` after marking at the close of `d`, and fills at that close. A signal computed from the close of `d` (RSI2, IBS, close vs 5DMA) is therefore traded at the price that produced it. For daily-close strategies this approximates trading on a 15:45 snapshot. It is optimistic for 1-5 day mean-reversion signals. Mitigation in this study: `SignalSpread(lag=1)` delays every signal by one bar. The REPORT includes the `lag1` result for every selected variant.

2. **Exit rules also fire on the same close.** `close > 5DMA` and `take_profit` are evaluated and filled at the same close. The effect is symmetric with item 1. `lag` does not delay exits.

3. **`PoorMansCoveredCall.on_day` has no hook for gating the short call.** The gate is an inline `skip_short_below_sma50` check. To time the short call, `PMCCSignal` reimplements `on_day` (a copy with a `sell_ok` gate) in `optionsbot/strategies_signals.py`. If `strategies.py` changes, keep that copy in sync.

4. **Expiry ranking with short DTE windows.** `Market.rank_expiries` adds a +4-day penalty to Friday weeklies and +10 to other weeklies. In a 7-14 DTE window this sometimes picks a non-Friday weekly or the edge of the window. Behaviour is consistent across variants, so the comparison is still fair. It is documented, not a bug.

5. **`metrics.trade_metrics` returns `profit_factor = inf` when a run has no losing trades.** JSON consumers see `Infinity`. `finalists.json` writes `null` in that case.

6. **PMCC infeasibility at $10k.** With the default `alloc=0.35`, one 0.80Δ LEAPS on SPY, QQQ, MSFT or META costs more than the allowance, and the run is flagged infeasible. At `alloc=0.6`, SPY and QQQ are blocked on most days, and MSFT and META stay infeasible. The S5 study therefore uses IWM, AAPL, NVDA, AMZN and GOOGL at `alloc=0.6`. This is the sizing-honesty rule working as intended.

7. **Spread sizing trusts asynchronous option closes (real bug, affects `strategies.PutCreditSpread.size` / `IronCondor` too).** Contracts are sized as `risk_budget / ((width - credit) * 100)`. Each leg is priced at its own last daily print, and the two prints can be minutes or hours apart. When the short leg's close is stale, credit can approach the width, max loss looks close to $0, and the position becomes huge. Observed case: S3 call credit spread on SPY opened 2025-04-24, 573/575C, "credit" $1.81 on a $2 width. It was sized at 159-185 contracts on a ~$6.6k sleeve and booked a fictitious +$20-26k the next day. Of 26,454 spread trades in this study, it was the only one with credit > 60% of width. It affected 4 cached S3 runs. Fix used here (strategies_signals.py only): skip a credit spread when credit > 60% of width (`max_credit_frac`), and skip a debit spread when debit < 15% of width (`min_debit_frac`). Suggested core fix: the same guard in `PutCreditSpread.on_day`/`IronCondor.on_day`, or size on `max(width - credit, 0.5 * width)`.
