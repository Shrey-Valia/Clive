# Event and short-dated options: results

None of the event or short-dated structures tested made money after costs on these names over 2024–2026. The one firm result is negative: selling 1DTE SPY/QQQ iron condors loses money, and the loss is statistically clear.

This file was saved by the coordinating session. The tuning agent was not allowed to write report files, so the text comes from its returned findings. `tables.md` holds the generated numeric tables.

## Protocol
- **Earnings dates.** 345 EDGAR 8-K Item 2.02 reports from 2016–2026, all released after the close. Excluded: TSLA delivery-only 2.02 filings, pre-market or intraday filings, and pre-announcements (a 2.02 filed less than 40 days before the next one).
  - `acceptanceDateTime` is not ET wall-clock time; true ET = to_ET(ts) + utcoffset. Reading it naively shifts AAPL reports by one day.
- **Macro dates.** FOMC dates come from federalreserve.gov. CPI dates come from FRED for 2025–26 and the BLS archive for 2024.
- **Coverage.** Option data exists for 88 reports, 11 per ticker. Every leg had to have volume above 0 on both entry and exit.
- **Costs.** Results use the engine's default slippage, with 0×, 0.5× and 2× shown alongside.
- **Gate and splits.** The gate (implied move / median of the last 8 realized moves ≥ 1.2) was fixed in advance. In-sample (IS) is before 2025-07-01; out-of-sample (OOS) is 2025-07 to 2026-10.
- **Multiple testing.** 57 decision variants were tested (228 counting cost levels). About 3 results with |t| > 2 would be expected by chance.

## E1: implied vs realized earnings move (88 reports)
Mean realized/implied is 0.93 (median 0.86), and realized exceeded implied 44% of the time. On average the implied move was only +0.5 pp of spot too high (IS +0.7, OOS +0.1).

| Ticker | Mean realized/implied | Realized > implied |
|---|---|---|
| AAPL | 0.62 | 18% |
| NVDA | 0.75 | 27% |
| GOOGL | 0.83 | 45% |
| AMZN | 0.87 | 45% |
| NFLX | 0.90 | 55% |
| MSFT | 1.09 | 55% |
| TSLA | 1.18 | 55% |
| META | 1.22 | 55% |

## Earnings structures
P&L is shown as a share of max risk per trade, at base costs.

| Test | n | Mean | t | IS → OOS | Verdict |
|---|---|---|---|---|---|
| E2 run-up straddle (D−3 → D) | 88 | −1.5% | −1.33 | −2.1% → −0.7% | Reject. +1.9% at zero slippage, all of it lost to costs |
| E3 iron condor, all events | 88 | −1.8% | −0.46 | +1.4% → −5.7% | Reject. 64% win rate, but 4 trades lost ≥90% of risk |
| E3 iron condor, gated | 37 | −7.6% | −1.07 | −0.1% → −11.2% | Reject |
| E4 iron fly | 88 | −3.8% | −0.54 | +2.0% → −10.8% | Reject |
| E5 straddle calendar | 83 | −7.0% | −1.29 | −4.3% → −9.9% | Reject. 8 leg fills cost about 13 pp |
| E5 call calendar | 83 | −5.3% | −1.07 | −1.4% → −9.8% | Reject |

- **Picking "cheap-vol" names in-sample did not hold up.** AAPL, AMZN, META and NVDA all looked cheap in-sample. Out of sample, E3 on them went from +9.9% to −5.7% and E4 from +20.9% to −8.4%.
- **The worst losses were real moves, not data errors:** TSLA 2024-10-23 (implied 8.1%, realized 21.9%) and META 2024-02-01 (6.1% vs 20.3%).

## Z1: 1DTE SPY + QQQ, close-to-settlement

| Structure | n | Mean | t | Win |
|---|---|---|---|---|
| Iron condor, ±1× implied, $5 wings | 1377 | −3.8% | −3.37 | 67% |
| Iron condor, wings at ±2× implied | 1377 | −3.8% | −3.44 | |
| Iron fly | 1375 | −7.2% | −2.98 | |
| Short straddle | 1377 | +0.2% | +0.28 | |

The $5 condor still loses at zero slippage (−0.9%). Risking 2% of a $100k account per trade, it compounds to −61%.

## FOMC and CPI eves
- The condor loses −3.6% on FOMC eves and −6.3% on CPI eves, in line with ordinary days.
- The FOMC-eve fly (+15.8%, n=20) and straddle did not pass the protocol. The fly swung from −8.7% IS to +45.2% OOS. Log them forward on paper only.

## Account sizing
- **$10k account:** at 2–5% risk per trade, one lot already risks $800–4,000, so every earnings trade is skipped.
- **$100k account:** every earnings stream finishes between −23% and +6%.

## Caveats
- Closes are last trades, not mids.
- Thursday reporters and the 1DTE trades settle at intrinsic with no exit cost.
- After-hours pin risk is ignored.
- The sample covers a single regime.
- With about 11 reports per ticker, each per-ticker estimate has a standard error of roughly ±10–25 pp.
