# Defined-risk spreads tuning (PCS / IC / CCS overlay)

The coordinating session saved this file from the tuning agent's report. Data is in `results.csv`, `finalists.json`, `frozen.json` and `stage0_costs.txt`.

- Full period: 2024-03-01 → 2026-10-20.
- In-sample (IS): 2024-03-01 → 2025-06-30. Every parameter was chosen on IS data.
- Out-of-sample (OOS): 2025-07-01 → 2026-10-20. It was looked at only after the three finalists were frozen.
- All runs use default costs and the bug #7 guard: an entry is skipped when its credit is more than 60% of the spread width.
- **1,563 trials in total.**

## Verdicts

| # | Config | Verdict |
|---|---|---|
| F1 | SPY iron condor. 0.10Δ shorts on both sides, wings at 3% of spot, 50–70 DTE (target 60), held to expiry with no profit target or stop. Max loss 20% of equity, 1 position at a time. | **Robust.** OOS beat IS, it survives cost stress, and it fits a $10k account (1 contract). |
| F2 | F1 run on SPY, QQQ and IWM, equal thirds. | **Robust at $100k** (OOS Sharpe 1.02). Not viable at $10k. |
| F3 | PCS 0.20Δ / 3% wings / 60 DTE, held to expiry, plus an RSI2>90 call credit spread overlay. | **Overfit.** OOS Sharpe fell to 0.54 at $100k and −0.14 at $10k. |

## Finalist metrics (default costs, $100k unless noted)

| Config | Segment | CAGR | Sharpe | Max DD | Trades | Win |
|---|---|---|---|---|---|---|
| F1 SPY | IS | 8.6% | 0.82 | −6.8% | 8 | 100% |
| F1 SPY | OOS | 11.0% | **1.46** | −5.8% | 7 | 100% |
| F1 SPY | Full | 9.8% | 1.06 | −6.8% | 15 | 100% |
| F1 SPY, $10k | Full | 7.9% | 1.08 (OOS 1.59) | −6.3% | 15 | 100% |
| F2 3-ETF | OOS | 8.5% | 1.02 | −6.4% | 20 | 90% |
| F2 3-ETF | Full | 8.2% | 0.92 | −6.4% | 41 | 95% |
| F3 | IS → OOS | 14.6% → 5.0% | 0.98 → 0.54 | → −16.4% | | |

Benchmarks:
- SPY buy-and-hold: CAGR 17.9%, Sharpe 1.14, max DD −18.8%.
- The old managed `iron_condor` on SPY: CAGR 9.7%, Sharpe 0.76, max DD −12.7%.

F1's beta to SPY is about 0.3.

## Why the baseline put credit spread loses
- **Before costs it earns +5.7% a year; default costs turn that into −2.8%.** Slippage equals 9% of all credit collected.
- **Stops are the bigger leak.** The 8 stop-outs cost $58.7k, against $90.4k earned from 57 take-profits. Stops are checked only at the close, so gaps carry losses past the stop: the average stopped loss was 2.56x credit on a 2.0x rule.

**Cost calibration.** Alpaca indicative quote snapshots taken 2026-10-20 give these median half-spreads:

| Underlying | Median half-spread | Default model vs observed |
|---|---|---|
| SPY | 0.58% | about 3x too high |
| QQQ | 1.10% | about 1.8x too high |
| IWM | 1.23% | about 1.7x too high |

- Proposed cost model: slip = max($0.01, k × price), capped at $0.20, with k = 1.25 × the observed half-spread.
- Calibrated costs move the baseline PCS on SPY from −2.8% to +2.8% a year.
- The real fix is fewer round trips and no stops.

## Cost sensitivity (full-period Sharpe)

| Config | Zero | Calibrated | Default | Double |
|---|---|---|---|---|
| F1 | 1.19 | 1.15 | 1.06 | 0.93 |
| F2 | 1.07 | 1.01 | 0.92 | 0.76 |
| F3 | 0.98 | 0.94 | 0.79 | 0.60 |

## Shock windows (return / max DD inside the window)

| Window | F1 | SPY buy-and-hold |
|---|---|---|
| Aug-2024 | +4.5% / −3.6% | −0.2% / −8.4% |
| Jan-2025 | −0.3% / −1.4% | +2.0% / −2.0% |
| Apr-2025 | +1.4% / −5.9% | −1.9% / −13.7% |

## Robust vs noise
- **Robust:**
  - Hold to expiry rather than managing the trade.
  - Stops hurt.
  - Fewer, longer (~60 DTE), far-OTM condors with wide wings beat managed 0.16–0.25Δ spreads.
  - Sizing scales returns and drawdown linearly.
- **Noise or harmful:**
  - The RSI2 call overlay.
  - Trend and VIX filters.
  - Laddering positions.
  - The kill switch as built. Its loss trigger can't fire for spreads.

## Multiple testing
- Spearman correlation between IS and OOS Sharpe was only 0.29–0.31.
- Deflated Sharpe at N=1,563: F1 0.02, F2 0.03, F3 0.04. None is significant after selection.
- PSR(OOS Sharpe > 0): F1 0.95–0.97.

## Unproven tail
F1 never took a max loss in the sample. Its structural worst case is −20% of equity per cycle, and a 0.10Δ short gets touched roughly 10–20% of the time per side. The sample can't price that tail, so size for the structural maximum, not the backtest drawdown.

## Live notes
- Close or roll positions on expiry day before about 15:00 ET. That avoids pin risk and Alpaca's pre-expiry liquidation.
- At $10k, F1 is exactly one SPY condor, about $1.8–2k at risk.

## Bugs
See `BUGS.md`:
- The kill-switch loss trigger can't fire for spreads.
- A $0.03 per-leg slippage floor.
- Stops filled at last-trade marks overshoot.
- The bug #7 guard changed nothing on the finalists.
- Forked workers segfault on macOS. Fix: set `no_proxy=*`.
