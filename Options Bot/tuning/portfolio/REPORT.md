# Options portfolios for $10k and $100k accounts

Saved by the coordinating session from the portfolio agent's findings. Data is in `results.csv` and `portfolios.json`. Charts are in `out/`.

## Setup
- Every finalist sleeve was rebuilt from code and matches its `finalists.json` to 4 decimals.
- Bug guards are applied: #7 (credit > 60% of width), no spread kill switch, and metrics taken from the equity curve.
- In-sample (IS) runs 2024-03 → 2025-06 and out-of-sample (OOS) 2025-07 → 2026-10. All choices were made on IS data only.
- Trials: 18 for the $10k book and 13 for $100k. Counting the upstream studies, the global totals are 1,902 and 1,897.

## Correlation
- **Wheel and covered call are close to the same bet.** They correlate 0.95 with each other and about 0.92 with SPY.
- **The dip put spreads are the real diversifier**, at about 0.2–0.3 correlation to SPY. The exception is QQQ dips, whose drawdowns overlapped SPY's 74% of the time.
- **Collar and iron condor:** the collar correlates 0.72 with SPY but has tiny drawdowns. The iron condor sits at 0.56.

## $10k (one shared-cash account)

| Candidate | CAGR | Sharpe | Max DD | Beta | IS Sharpe | OOS Sharpe / CAGR / DD |
|---|---|---|---|---|---|---|
| **B: 80% SPY core + dip-PCS overlay (picked)** | 30.1% | 1.47 | −13.5% | 0.99 | 1.37 | 1.58 / 31.9% / −8.2% |
| A: dip-PCS only, 25% total cap, idle cash | 16.6% | 1.08 | −10.4% | 0.24 | 1.01 | 1.20 / 16.1% / −6.1% |
| C: iron condor + dip, 50% cap | 28.1% | 1.27 | −16.6% | 0.54 | 1.11 | 1.48 |
| Buy and hold SPY | 17.9% | 1.14 | −18.8% | 1.00 | 0.93 | 1.52 / 19.3% / −8.9% |

- **Caps of 50% and 75% fail in-sample** (drawdown −28% to −35%) because SPY, QQQ and IWM dip together.
- **B:** deflated Sharpe 0.88 against its 18 trials, and 0.77 against all 1,902. The bootstrap Sharpe CI is [0.55, 2.44], and the probability it beats SPY is 0.88.
- **A:** deflated Sharpe 0.80 against 18 trials, 0.64 against 1,902. The probability it beats SPY is 0.50.

## $100k

| Candidate | CAGR | Sharpe | Max DD | Beta | OOS Sharpe / CAGR | Deflated Sharpe (13 / 1,897) |
|---|---|---|---|---|---|---|
| **L collar60 (picked)** | 11.5% | 1.72 | −4.0% | 0.35 | 1.67 / 10.9% | 0.93 / 0.82 |
| L cc60 (alternative) | 14.9% | 1.37 | −11.7% | 0.63 | 1.55 / 14.8% | 0.82 / 0.63 |
| Covered call QQQ alone | 18.6% | 1.22 | −18.2% | | 1.39 | |
| SPY | 17.9% | 1.14 | −18.8% | 1.00 | 1.52 / 19.3% | |

- **L collar60** splits into four sub-accounts: $60k SPY collar, $12.7k dip-PCS, $17.9k SPY iron condor and $9.5k SPY shares.
- **L cc60** is the same split with the QQQ covered call in place of the collar.
- **Textbook blends can't be built at $100k.** Equal weight, inverse volatility, risk parity and minimum drawdown all need one 100-share lot ($42–70k) per stock sleeve, and their CAGR roughly halves when run with whole lots.
- **L collar60 beats SPY on Sharpe but not on return.** It trails SPY by about 8 points a year out of sample, and the collar on its own failed OOS in the wheel study.

## Stress (max drawdown in window)

| | Aug-2024 | Apr-2025 | GFC (synthetic) | COVID (synthetic) | 2022 (synthetic) |
|---|---|---|---|---|---|
| B core80 | −5.4% | −11.0% | −50 to −68% | −42 to −51% | −36 to −44% |
| A cap25 cash | −2.4% | 0% | −25 to −38% | −25 to −32% | −25 to −30% |
| L collar60 | −2.7% | −2.5% | −23 to −30% | −18% | −16 to −19% |
| SPY | −8.4% | −13.7% | −47 to −57% | −34 to −35% | −25 to −26% |

The synthetic windows are labeled synthetic and were not backtested. They use two methods: SPX and PUT-index paths scaled by each portfolio's fitted betas, and Cboe index proxies (CLL, BXMD). Both methods add one full max loss on the spread cap. **The $10k pick's crash tail is worse than SPY's.**

## Rules for the $10k pick (B core80)
- **SPY core:** on the first trading day of each month, set SPY to 80% of equity using free cash.
- **Entry signal:** each day, on SPY, then QQQ, then IWM, whenever that name has no open spread. Require close > 200DMA, RSI(2) < 10 and IBS < 0.2.
- **Trade:** sell a 0.25Δ put and buy a 0.10Δ put, 7–14 DTE.
- **Skip the trade** if the credit is ≤ $0.05 or > 60% of the width.
- **Contracts:** floor(min(25% equity, 25% equity − open max loss, free cash) / ((width − credit) × 100)).
- **Exit** at 50% of the credit, at the first close above the 5DMA, or after 5 days, whichever comes first.

## What to paper-trade first
**The dip-PCS leg**, because it is the most fragile piece. With a one-day signal lag, IS Sharpe falls from 1.01 to 0.24. Log the signal time against the close, and each multi-leg fill against the mid. Once it holds up live, add the iron condor, then the stock sleeve.
