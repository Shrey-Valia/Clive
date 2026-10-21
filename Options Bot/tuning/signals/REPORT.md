# Signal-timed and directional options: results

The coordinating session wrote this file from the tuning agent's findings; the harness does not let subagents write report files. Every number here can be traced to `results.csv` and `finalists.json`.

**Setup**
- Universe: SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, GOOGL, META.
- Account: $10,000.
- Engine: `optionsbot`, using Alpaca daily option bars and the default fill model.
- In-sample (IS), used for choosing parameters: 2024-03-01 → 2025-06-30.
- Out-of-sample (OOS), with parameters frozen: 2025-07-01 → 2026-10-20.
- 128 variants tested in total. Controls, placebos, cost checks and lag checks were extra runs and are not counted in that total.

## TL;DR

| # | Idea | IS-selected variant | IS Sharpe | OOS Sharpe | Unconditional control, OOS | Verdict |
|---|---|---|---|---|---|---|
| 1 | RSI2 dip in an uptrend → short put credit spread | RSI2<10, IBS<0.2, close>200DMA; 0.25Δ/0.10Δ, 7–14 DTE; exit at 50% TP, close>5DMA or 5 days | 0.85 | **1.01** | −0.68 | **FINALIST** |
| 2 | Same dip → call debit spread | 0.50Δ/0.25Δ, 21–35 DTE | 0.66 | 0.65 | −1.92 | Rejected: −0.74 OOS at 2× slippage |
| 3 | Overbought → call credit spread | RSI2>90, close>20DMA+2ATR | −0.34 | −1.65 | −1.72 | Rejected: all 24 variants negative OOS |
| 4 | VIX/VIX3M>1 → bull spread | call debit spread, 10 days | 0.68 | −0.36 | −0.94 | Rejected: about 10 distinct episodes |
| 5 | PMCC with signal-timed short calls | call sold only when RSI2>90 and close>50DMA | 0.76 | 1.67 | pmcc_sma50 1.77 | Rejected: buy-and-hold of the same names reached OOS 1.83 |
| 6 | 12-1 momentum top-2 mega caps → rule 1 | monthly | 2.48 | **1.40** | ungated 0.32 | **WATCHLIST**: neighbors fail OOS; 14 of 24 trades are NVDA |

Equal-weight buy-and-hold of all 9 names: IS Sharpe 1.01, OOS 1.47, CAGR 25.1%, max drawdown −23.8%.

## Finalist: RSI2/IBS dip → short-dated put credit spread

- **Entry:** at the close, when close>200DMA, RSI(2)<10, IBS<0.2, and no earnings fall before expiry.
- **Position:** short ≈0.25Δ put, long ≈0.10Δ put, 7–14 DTE.
- **Exit:** at 50% of the credit, at close>5DMA, or after 5 trading days, whichever comes first.
- **Size:** max loss ≤ 25% of the sleeve.

| | IS | OOS | Full |
|---|---|---|---|
| Basket-9 Sharpe | 0.85 | 1.01 | 0.93 |
| Basket-9 CAGR / max DD | 4.3% / −6.0% | 4.9% / −3.2% | 4.6% / −6.0% |
| Trades / win rate | 54 / 94% | 74 / 92% | 128 / 93% |
| Mean per-trade return on max loss | +4.1% (t 1.49) | +4.3% (t 2.04) | +4.2% (t 2.52) |
| 95% week-clustered bootstrap CI | [−5.8%, +9.5%] | [−1.3%, +8.2%] | [−1.2%, +8.2%] |
| Unconditional control: Sharpe / per-trade return | −1.66 / −1.9% | −0.68 / −0.9% | −1.26 / −1.5% |
| Welch t vs control (p) | 2.13 (0.037) | 2.41 (0.018) | 3.31 (0.001) |
| Share of 20 random-entry placebo seeds beaten | 100% | 85% | 100% |
| Sharpe at half slippage | 1.02 | 1.20 | 1.11 |
| Sharpe at double slippage | 0.53 | 0.55 | 0.54 |
| Sharpe when entering one day late | **−0.62** | 1.75 | 0.48 |
| Deflated Sharpe, family N=32 / all N=128 | 0.62 / 0.17 | | |

**Per name, IS / OOS Sharpe**

| SPY | QQQ | IWM | AAPL | MSFT | NVDA | AMZN | GOOGL | META |
|---|---|---|---|---|---|---|---|---|
| 0.87 / 1.60 | 0.16 / 1.02 | 1.73 / 0.95 | 0.74 / 0.46 | 1.06 / 0.59 | 2.15 / −0.24 | 0.66 / −0.42 | −1.31 / 0.76 | 0.43 / 0.37 |

- **SPY alone at $10k:** 20 trades, all 20 winners, CAGR 10.9%, max drawdown −7.4%. Buy-and-hold over the same period: 17.9% CAGR, −18.8% drawdown.
- **One pooled $10k account trading all 9 names:** CAGR 20.8%, Sharpe 1.01, max drawdown −23.4%. The worst day was −17% on 2025-02-27, when 6 names dipped together.
  - At half that scale: CAGR 11.2%, max drawdown −14.1%.
  - Cap total risk across names at roughly 50% of equity.
- **Neighbor stability:** all 32 grid variants were positive OOS, median 1.55.

**Caveats**
1. Entering one day late turned IS negative, so part of the IS edge depends on filling at the signal close.
2. The deflated Sharpe across all 128 trials is only 0.17.
3. Once clustering is accounted for, the confidence interval on mean trade return includes zero.
4. The sample is a mostly bull regime of about 2.6 years. The strategy sits in cash 73% of the time and has a 0.35 correlation with buy-and-hold.

## Watchlist: momentum routing → dip put credit spread

Each month, rank the six mega caps by 12-1 momentum. The top 2 each get a $5k sleeve running the finalist rule.

| | Sharpe | CAGR | Max DD |
|---|---|---|---|
| IS | 2.48 | 10.7% | −1.4% |
| OOS | 1.40 | 9.1% | −4.6% |

- 24 trades, all winners.
- It beat random top-2 routing in 17 of 20 seeds OOS.
- Neighbor variants collapse OOS (family median −0.02), and 14 of the 24 trades are NVDA.
- Paper-trade it before allocating any money.

## Rejected
- **Call debit spreads on dips:** paying two spreads on every round trip kills the edge.
- **Overbought call credit spreads:** negative across the board.
- **VIX-spike dip buying:** too few episodes to validate. Test it on `longhist/` instead.
- **Timed PMCC:** loses to buy-and-hold of the same names.

## Multiple testing
- The expected maximum IS Sharpe under the null hypothesis is 1.71. The S1 winner (0.85) is below that.
- IS→OOS Spearman correlation by family: S1 0.22, S2 0.04, S3 −0.30, S4 −0.32, S5 0.14, S6 0.11.

## Engine bug found (BUGS.md #7)
Spread sizing in the core `PutCreditSpread` and `IronCondor` uses leg closes printed at different times. On 2025-04-24, one SPY call credit spread showed a $1.81 credit on a $2-wide spread. The engine opened 159–185 contracts on it and booked a fictitious +$20–26k. This happened once in 26,454 trades. The signal strategies guard against it by skipping any spread whose credit exceeds 60% of its width. The core strategies should get the same guard.
