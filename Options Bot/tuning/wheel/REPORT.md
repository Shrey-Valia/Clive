# Wheel, CSP, covered-call and collar tuning: results

Saved by the coordinating session from the tuning agent's returned findings, because the harness doesn't let subagents write report files. The data lives in `results.csv` and `finalists.json`.

All results use a $100k account, default costs and no interest on cash.

- **In-sample (IS):** 2024-03 → 2025-06. Every parameter was chosen here.
- **Out-of-sample (OOS):** 2025-07 → 2026-10, frozen evaluation only.
- **How parameters were picked:** on an equal-weight basket of 9 names (SPY QQQ IWM AAPL AMZN GOOGL META NVDA MSFT), taking the best in-sample point after averaging it with its grid neighbors.

## Bottom line
Two changes held up out of sample against both the old defaults and buy-and-hold:
- **Wheel:** sell 0.15Δ calls instead of 0.30Δ, and roll in-the-money calls near expiry for a credit.
- **Covered call:** hold the call to expiry instead of rolling at 21 DTE, and accept call-away.

Two results did not hold up:
- The collar cut drawdown to about −2%, but its in-sample Sharpe edge over buy-and-hold turned into a shortfall out of sample.
- The multi-name portfolio picked in-sample fell behind buy-and-hold out of sample.

## Finalists

| | Config | Full CAGR / Sharpe / DD | IS S | OOS S | Baseline |
|---|---|---|---|---|---|
| F1 wheel QQQ | 0.30Δ put, TP 50%; 0.15Δ call; 30–45 DTE; roll ITM calls at ≤7 DTE for a net credit, max 3 rolls | 17.5% / 1.11 / −19.8% | 0.93 | 1.57 | built-in 14.9% / 1.08 / −18.2% |
| F2 covered call QQQ | 0.40Δ, 30–45 DTE, held to expiry, call-away then re-buy | 18.6% / 1.22 / −18.2% | 1.14 | 1.39 | built-in 13.6% / 0.92 / −18.7% |
| F3 collar SPY | long 0.25Δ put at 75–120 DTE (rolled at 30) + short 0.30Δ call at 30–45 DTE | 6.0% / 1.46 / −2.4% | 1.71 | 1.21 | built-in 5% / 1.21 / −5.1% |
| P1 8-name portfolio | F1 core, ranked by 12-1 momentum, 50% name cap | 20.0% / 1.01 / −19.0% | 1.08 | 0.95 | equal-weight buy-and-hold 24.4% / 1.08 / −25.4% |

Buy-and-hold for reference: QQQ 20.5% / 0.99 / −22.8%; SPY 17.9% / 1.14 / −18.8%.

Each frozen config beats buy-and-hold Sharpe on 8/9 names (wheel), 6/9 (covered call) and 5/9 (collar). On most names all three still trail buy-and-hold on raw CAGR.

## Selection statistics
Out of sample, the market itself got smoother: buy-and-hold's Sharpe rose from 1.01 to 1.47. So the useful test is each strategy's Sharpe excess over buy-and-hold:

| | Excess over buy-and-hold, IS → OOS |
|---|---|
| F1 | +0.21 → +0.40 (holds) |
| F2 | +0.23 → +0.38 (holds) |
| F3 | +0.68 → −0.40 (fails) |
| P1 | +0.14 → −0.43 (fails) |

- 193 configs were tried, about 1,413 single-name runs.
- Deflated Sharpe is 0.66–0.86 against all trials and 0.82–0.94 within each family. **None clears 0.95.**
- The rank correlation between IS and OOS Sharpe across all variants is −0.41. Both shocks fell in the IS window, so defensive variants won IS and aggressive variants won OOS.

## Effect of each lever
- **Call delta is the dominant lever.** Basket IS Sharpe was 1.18 at 0.15Δ, 0.98 at 0.30Δ and 0.92 at 0.40Δ.
- **Small help:** rolling tested calls for a credit, and the RSI2>90 / up-day call gates, which were neutral to slightly positive.
- **Hurt:**
  - The kill switch: wheel IS Sharpe fell from 1.18 to 0.65, and to 0.29 with share liquidation. Both shocks were V-shaped.
  - Rolling tested puts.
  - Selling calls below cost basis.
- **No effect:** put gates (IV/RV, VIX percentile, RSI2, 200DMA) and ex-dividend rolls.
- **CSP:** every variant trailed the wheel and buy-and-hold. Best was 0.40Δ, TP 50%: IS Sharpe 0.83, 11.4% CAGR.

## Shocks

| | Aug-2024 return / DD | Apr-2025 return / DD |
|---|---|---|
| F1 wheel QQQ | −1.4% / −9.3% | +5.4% / −13.5% |
| QQQ buy-and-hold | −5.3% / −13.6% | +5.8% / −15.7% |
| F3 collar SPY | −0.6% / −2.1% | +4.5% / −1.7% |
| SPY buy-and-hold | +0.4% / −8.4% | +2.9% / −13.7% |

The wheel and covered call trim shock drawdowns by only 2–4 points. Only the collar hedges.

## Cash yield (T-bill sweep; Alpaca paper pays none)

| | CAGR | Sharpe |
|---|---|---|
| F1 | 17.5% → 19.3% | 1.11 → 1.24 |
| F2 | 18.6% → 19.2% | 1.22 → 1.26 |
| F3 | 6.0% → 7.9% | 1.46 → 1.94 |
| P1 | 20.0% → 22.6% | 1.01 → 1.10 |

## Account size
- **$50k, run on IWM:**

  | | CAGR / Sharpe / DD |
  |---|---|
  | F2 covered call | 13.6% / 1.06 / −17.5% |
  | F1 wheel | 11.5% / 0.88 / −18.3% |
  | IWM buy-and-hold | 13.0% / 0.70 / −27.5% |

- **$10k:** only post-split NVDA can be traded. That makes it a single-stock bet, not a strategy, so stock-backed premium selling isn't practical at this size.

## Engine issues (see BUGS.md)
1. The built-in wheel's above-basis call rule silently falls back to a below-basis strike.
2. The trade log books assignment intrinsic as an option loss and never logs share P&L. Win rate and profit factor therefore count option legs only; trust the equity-curve metrics instead.
3. The built-in covered call rolls ITM calls at 21 DTE. This is the main reason it lags.
4. The base kill switch closes the call but keeps the shares.
5. There is no shared-cash multi-name mode, so `portfolio.py` steps the engines in lockstep.

Rebuild every output from the cache with `python3 tuning/wheel/finalize.py`.
