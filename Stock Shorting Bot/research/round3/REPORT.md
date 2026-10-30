# Round 3: minute-bar study of the v2 open-to-close short and the overnight long sleeve

Prepared 2026-10-23. Research only. Nothing here is switched on. The one piece of new live code sits behind `ROUND3_SHORT_ENTRY_ORDER`, which defaults to `"market"`, the current behaviour. The coordinating session saved this file from the agent's hand-back, because the agent could not write report files itself.

## Verdict in brief

1. **The live bot is not trading the strategy that was backtested.**
   - Round 1's P7 (IS Sharpe 1.33, OOS 1.21) assumed an opening-print entry, no stop, a close exit and the FTD-weight-10 score.
   - Live v2 uses a 09:31 market order, a 35% broker stop, a 15:55 market cover and the FTD-weight-0 score.
   - On minute bars with time-of-day spreads, v2 as it runs today scores **IS 1.14 / OOS 0.81**, and **0.93 / 0.48 with costs doubled**.
   - The live score passes about twice as many names as the P7 score (248 vs 153 IS trades, 350 vs 170 OOS) at about the same Sharpe, so the score change is not the problem.
   - Execution is. The 09:31 fill and the 35% stop cost about 0.2 to 0.4 Sharpe.
2. **The edge is an all-day fade. Every early exit tested hurts it:** hard stops from 5% to 25%, trailing stops, profit targets, 12:00 and 14:00 time exits, VWAP-reclaim exits and "cover if up X% at 10:00" checkpoints are all worse than v2 in both IS and OOS.
3. **The 35% broker stop also costs money.** Removing it gives IS 1.23 / OOS 1.01. The worst single short then goes from −37% to −71%, which is −3.5% of equity at 5% sizing.
4. **The one clear win is to use the auctions, with the stop off.**
   - A market-on-open short entry, no broker stop, and a close exit: **IS 1.44 / OOS 1.19**, deflated Sharpe 0.57; with costs doubled, 1.33 / 1.01.
   - With the 35% stop kept, an auction entry is *worse* OOS (0.62), because the opening print gets stopped out by spikes in the first minute.
5. **The overnight long sleeve should sell in the opening auction (OPG), not at 09:31.**
   - OPG exit: IS 0.80 / OOS 0.99. The 09:31 market exit used today: IS 0.33 / OOS 0.74.
   - The 09:31 VWAP is on average 0.35% below the opening print for these names, and the spread at 09:31 is about 2.7× the closing spread.
   - The sleeve is a lottery ticket: its median trade is negative in 9 of 11 years, and it lost money in every year 2016–2019 and in 2022.
6. **No new selection feature, score re-weighting, sizing rule or regime filter clearly wins in both periods.** Re-weighting the score (relative volume weight 0, or the DTC-only crowding block) is flat IS and better OOS: WATCH, not GO. No regime filter survives.

## Verdict table

Sharpe is annualised on all trading days. "Trades" is IS / OOS. DSR is the deflated Sharpe in round 1's headline convention (null variance 1/T) against **N = 1,240 trials** (1,037 earlier plus 203 in round 3). With the cross-trial-variance convention every configuration has DSR ≈ 0, including round-1 P7. All rows are the v2 selection (live score ≥ 70, top 5, 5% each) unless stated.

| # | Idea | IS SR | OOS SR | DSR | Trades | Verdict |
|---|---|---|---|---|---|---|
| 0 | **v2 as live today** (09:31 mkt, 35% stop, 15:55 mkt) | 1.14 | 0.81 | 0.30 | 248 / 350 | baseline |
| 0a | Round-1 P7 spec (open print, no stop, close), live score | 1.35 | 0.99 | — | 255 / 356 | reference |
| 0b | Live execution, round-1 P7 score (FTD weight 10) | 1.19 | 0.82 | — | 153 / 170 | reference |
| Q1 | Enter 09:35 / 09:45 / 10:00 | 1.37 / 1.27 / 1.23 | 0.59 / 0.50 / 0.01 | — | ~248 / ~348 | NO |
| Q1 | Opening-range breakdown entry, 5 / 15 / 30 min | 1.22 / 0.97 / 1.07 | 0.63 / 0.05 / 0.34 | — | 176–112 / 242–144 | NO |
| Q1 | Gap-up skip at 20% / 10% / any gap up (vs 40% today) | 0.92 / 0.65 / 0.49 | 0.64 / 0.53 / 0.85 | — | — | NO (keep 40%; no skip at all is 1.11 / 0.70) |
| Q1 | Skip a gap down of more than 5% / 10% / 20% | 1.17 / 1.21 / 1.27 | 0.33 / 0.50 / 0.68 | — | — | NO |
| Q1 | Market-on-open entry, 35% stop kept | 1.37 | 0.62 | 0.51 | 250 / 350 | NO |
| Q2 | No broker stop | 1.23 | 1.01 | 0.38 | 248 / 350 | GO if the owner accepts the tail (worst short −71%) |
| Q2 | Hard stop at 5 / 10 / 15 / 20 / 25 / 50 / 75% | 0.53 – 1.11 | −0.78 – 0.86 | — | — | NO (tighter is worse; 50% ≈ 35%) |
| Q2 | Trailing stop 5–30%, armed or not | −0.83 – 0.86 | −1.12 – 0.45 | — | — | NO |
| Q2 | Profit target 5–30% | −0.20 – 1.17 | 0.38 – 0.84 | — | — | NO |
| Q2 | Time exit 10:30 / 12:00 / 14:00 / 15:00 | 0.34 / 0.78 / 1.02 / 1.00 | 0.09 / 0.51 / 0.83 / 0.90 | — | — | NO |
| Q2 | VWAP-reclaim exit (after 15–120 min) | 0.03 – 0.95 | −0.17 – 0.56 | — | — | NO |
| Q2 | Checkpoint: cover if up 5–20% at 10:00 / 10:30 | 1.03 – 1.16 | 0.66 – 0.81 | — | — | NO |
| Q2 | MOC cover instead of the 15:55 market cover (stop kept) | 1.16 | 0.81 | — | 248 / 350 | WATCH (tiny) |
| **Q1+Q2** | **v2.1: MOO entry + no stop + MOC cover** | **1.44** | **1.19** | **0.57** | 250 / 350 | **GO (stop-off decision)** |
| | v2.1 as built: MOO + no stop + 15:55 cover | 1.39 | 1.15 | — | 250 / 350 | GO |
| Q3 | Score: relative volume sub-weight 0 (threshold 69) | 1.13 | 1.38 | 0.29 | 267 / 451 | WATCH |
| Q3 | Score: crowding = DTC band only (threshold 71) | 1.12 | 1.04 | 0.28 | 239 / 377 | WATCH |
| Q3 | Filter: DTC ≤ 5 | 1.28 | 0.90 | 0.42 | 145 / 256 | WATCH (−40% trades) |
| Q3 | New features as filters: premarket gap, VWAP distance at 15:45, close-in-range, wick, 5-day run-up, float rotation, same-industry peers, biotech, news class, time of the high | ≤ 1.27 | — | — | — | NO (none beats v2 in both) |
| Q3 | Rules filter (change ≥ 20–50%, close-in-range ≤ 70/85), no score | ≤ 1.00 | 0.54 – 1.18 | — | — | NO on IS (ranking wins IS) |
| Q4 | Inverse-ATR sizing / score-weighted sizing | 1.11 / 1.18 | 0.37 / 0.80 | — | — | NO |
| Q4 | Top 10 at 2.5% (score ≥ 70) | 1.14 | 0.79 | — | 250 / 351 | NO (same Sharpe, half the return) |
| Q4 | Score ≥ 65 / 60 / 75 / 80 at 5% | 0.80 / 0.37 / 1.00 / 0.75 | 1.23 / 0.90 / 0.51 / 0.42 | — | — | NO |
| Q5 | 31 regime filters (SPY/IWM vs SMA50/200, IWM 20d, SPY up on T, VIX level, VIX/VIX3M, VIX vs its 20d mean, weekday, month) | — | — | — | — | NO. Only "skip Nov" and "skip Aug" pass the both-periods test: 2 of 31, no mechanism |
| Q6 | Long sleeve, 09:31 market exit (live) | 0.33 | 0.74 | — | 204 / 268 | baseline |
| Q6 | **Long sleeve, OPG exit** | **0.80** | **0.99** | — | 209 / 271 | **GO** (already a config option) |
| Q6 | Sleeve on other subsets (score ≥ 50/60, close-in-range, change, news class, all strict names) | −1.41 – 0.82 | −1.41 – 0.71 | — | — | NO (keep v2 picks) |
| All | Both books: A = live today | 1.11 | 1.04 | — | | baseline |
| All | B = A with the sleeve on OPG | 1.32 | 1.23 | — | | GO |
| All | **C = v2.1 short + OPG sleeve** (sleeve names shorted at 09:31) | **1.45** | **1.32** | — | | **GO** |

Combined-book CAGR / max drawdown: A IS 5.9% / −6.9%, OOS 7.5% / −11.3%; C IS 9.7% / −4.9%, OOS 11.7% / −10.5%. With costs doubled: A 0.81 / 0.67, C 1.27 / 1.10.

Per-year return, v2 live vs v2.1 (MOO, no stop, MOC):

| Year | v2 live | v2.1 |
|---|---|---|
| 2016 | 0.3% | 0.4% |
| 2017 | 1.3% | 1.8% |
| 2018 | 4.9% | 5.3% |
| 2019 | −0.8% | 0.0% |
| 2020 | 4.2% | 5.2% |
| 2021 | 19.0% | 29.5% |
| 2022 | −3.5% | −3.1% |
| 2023 | −0.7% | 0.3% |
| 2024 | −2.1% | −0.1% |
| 2025 | 16.1% | 19.0% |
| 2026 | 9.5% | 12.1% |

Trades per year are the same for both (5, 12, 26, 29, 63, 113, 54, 53, 78, 90, 75).

Worst trades: v2 live, all 35% stop-outs at about −36.8% (MI 2021-08-25, MLEC 2026-02-18, TULP, ISPC, IVVD, ASTC). v2.1: BTBT 2020-12-30 −50.7%, ISPC −44.5%, FLNA −40.6%, IVVD −38.9%, TULP −37.1%, ASTC −32.5%.

## Recommended v2.1 vs current v2 (exact diffs)

```diff
# shortingbot/config.py
-BROKER_STOP_PCT = 35
+BROKER_STOP_PCT = None                  # tail-risk decision for the owner, see "The stop" below
-ROUND3_SHORT_ENTRY_ORDER = "market"
+ROUND3_SHORT_ENTRY_ORDER = "opg"        # new flag (this branch): short in the opening auction
-OVERNIGHT_LONG_EXIT_ORDER = "market"
+OVERNIGHT_LONG_EXIT_ORDER = "opg"       # existing option
# crontab (owner): move the `daily.py --open --execute` line from 09:31 to 09:20 ET.
# The job then sends OPG orders before 09:28 and still runs the 09:31 path for whatever is left.
```

Everything else stays: score ≥ 70, the price/ADV gates, 5% per name, the 40% gap-up skip (checked against the last premarket print for the auction), the industry cap and the earnings gate.

**The stop.** The auction entry helps only with the stop off; with BROKER_STOP_PCT = 35 left on, `"opg"` is worse OOS than today. If the owner wants to keep a hard stop, the better choice is to change nothing on the short and only move the sleeve to OPG (book B: 1.32 / 1.23). The stop is insurance, not alpha: at 5% per name an uncapped +100% squeeze costs 5% of equity, which `MAX_SQUEEZE_LOSS_PCT` already assumes. The squeeze ledger shows a stop "saves" fewer return points than it "costs" at every level from 5% to 50%, in both periods, on both the v2 picks and the broad set.

**Not built: an MOC cover.** It adds about 0.02 to 0.05 Sharpe, needs the DAY stop cancelled before 15:50, and the 15:55 cover-all must never double-buy a name whose MOC order is still resting. v2.1 as built keeps the 15:55 cover: IS 1.39 / OOS 1.15.

## What a squeeze day looks like

Chart: `out/squeeze_worst_paths.png`.
- The worst v2 shorts are mostly **all-day grinds that print their high in the last hour**: BTBT 2020-12-30 (high at 15:51, −70% at 15:55); FLNA, BLNK, IOVA, VERU, NXH, ARVN (highs after 15:20). A few are morning spikes: TULP +105% by 10:22, ISPC +101% by 10:52.
- Of v2 trades that touched +20% against the short, the high came before 10:00 only 30% of the time, and 20% of those trades still finished in profit.
- The idealised stop ledger (`results/squeeze_stop_ledger.csv`, `out/squeeze_stop_ledger.png`) nets return points saved by stopping trades that finished worse than −X% against points lost on trades that touched +X% and recovered. It is negative at every X: −1.5% per trade at a 5% stop, −0.2% per trade at 35%, IS and OOS. Real fills are worse than the ledger (gaps through the level, LULD halts, 0.5% slippage).
- **A squeeze cannot be cut earlier without cutting the edge.** The money is made by holding through intraday noise.

## Entry timing and the open

`out/q12_entry_timing.png`: later entries (09:35 to 10:00) look better IS but are worse OOS; out of sample the fade starts right at the open. The opening print is the best entry price and carries no spread. On the broad set (every eligible strict-gate name, ~13.8k trades) the mean net trade is +0.15% IS / +0.08% OOS at the opening print vs −0.09% / −0.09% at the 09:31 market.

Spreads by time of day (Abdi–Ranaldo on the T+1 minute bars, `results/tod_spread.csv`):

| Window | Full spread |
|---|---|
| 09:30 | 44 bps |
| 09:31–34 | 40 bps |
| 09:35–44 | 32 bps |
| 09:45–59 | 28 bps |
| 10:00–29 | 22 bps |
| 10:30–15:30 | 16 bps |
| 15:30–15:55 | 15 bps |

## Selection (Q3)

Feature rank IC against the T+1 net short return under live execution, on all eligible strict-gate names (`results/q3_feature_ic.csv`, `out/q3_feature_ic.png`):
- **Only ATR14 is significant with the same sign in both periods** (IC +0.053 IS, t = 3.6; +0.035 OOS, t = 2.0): higher volatility makes a better short.
- **Raw DTC** is the most consistent crowding penalty (IC −0.074 IS, t = −4.1; −0.029 OOS, t = −1.7).
- **Relative volume** predicts the wrong way IS (t = −2.4) and is about zero OOS.
- Premarket gap, premarket dollar volume, VWAP distance at 15:45 and the move after the open on T are significant IS and nothing OOS.
- Close-in-range, wick, 5-day run-up, float rotation, same-industry peers (SEC SIC, 89% coverage), biotech and news class have no power in either period.
- Ranking beats a no-score rules filter IS (1.14 vs ≤ 1.00); the rules filters did better OOS (1.08 to 1.18).

## Sizing and capacity (Q4)

Equal 5% is as good as anything tested. Names passing per year at score ≥ 70: about 42 IS, 74 OOS; a day with more than 5 passing names is under 0.1% of days, so top-10 at 2.5% is just v2 at half size.

Liquidity at 5% per name (`results/q4_liquidity.csv`):

| Equity | Median share of 09:31–09:35 dollar volume | p90 |
|---|---|---|
| $100k | 0.05% | 0.5% |
| $1M | 0.5% | 5% |
| $10M | 4.9% | 52% |

A 09:31 market order stops being "small" somewhere around $1–2M of equity.

## Regime (Q5)

None of the 31 filters holds in both periods. Several look good OOS only (VIX < 20: 1.04; SPY above its 200-day: 0.94). "Skip November" (IS 1.46 / OOS 0.95) and "skip August" (1.18 / 0.84) pass the both-periods screen, but with 12 month filters about 3 would pass by chance, and there is no mechanism. Not recommended.

## The overnight long sleeve (Q6)

Exit at the opening print, not 09:31: OPG 0.80 / 0.99; 09:31 0.33 / 0.74; 09:35 0.32 / 0.41; doubled costs at 09:31 0.13 / 0.51.

By year at the 09:31 exit:

| Year | Return | Trades | Median trade |
|---|---|---|---|
| 2016 | 0.0% | 5 | −1.0% |
| 2017 | −1.5% | 11 | −0.7% |
| 2018 | −0.7% | 21 | −0.4% |
| 2019 | −0.3% | 17 | −0.5% |
| 2020 | +4.8% | 51 | +0.1% |
| 2021 | +4.7% | 99 | −1.3% |
| 2022 | −3.7% | 32 | −0.7% |
| 2023 | +0.1% | 39 | −0.4% |
| 2024 | +3.2% | 63 | −0.7% |
| 2025 | +13.9% | 76 | −0.5% |
| 2026 | +4.4% | 58 | +0.1% |

The worst overnight gap was −71% (IS). The sleeve earns its keep inside the combined book because its correlation with the short is about 0.1; standalone it is weak. No other subset beats the v2 picks in both periods (buying every strict-gate gainer: −0.06 / −0.90). Under v2.1, 78% of v2 picks are also sleeve names; those cannot be shorted in the auction they are sold in, so most shorts still go out at 09:31, with no stop, and still improve on today's book (C vs B).

## Method and assumptions

- **Data.** Round-1 scan events (read-only; 53,900 top-20 rows 2016-02 to 2026-10, including 4,226 rows of now-inactive symbols). New: full-session minute bars (04:00–16:00) for T+1 and 09:30–15:45 bars for T, for all 15,503 strict-gate events (15:45 price ≥ $10, ADV20 ≥ $10M); Alpaca SIP, `adjustment=all`; 99.8% coverage, delisted symbols included. HCAI (1:20 split after round 1's cache) rescaled to the round-1 basis. Also SEC SIC codes and CBOE VIX3M.
- **No look-ahead.** Scan features stop at 15:45 on T; premarket features at 09:29; the auction entry's gap-up skip uses the last premarket print, not the open; VIX and term structure use T's close; the score is the live `shortingbot.scoring` run on the historical rows.
- **Fills.** Opening print = the daily open (auction). "09:3K" = VWAP of that minute bar. Stops fill at the level, or the bar open on a gap or halt, plus 0.5%. The 15:55 cover = the open of the 15:55 bar. MOC = the official close. Half days shift to 12:55.
- **Costs, one way.** Round-1 base: 12 bps ($10–20), 8 bps ($20–50), 5 bps ($50+); +10 bps if up >50% on T; plus √-impact 1%·√(position / day's dollar volume). Multiplied by the time-of-day factor: 2.95× (09:30), 2.66× (09:31–34), 2.15×, 1.88×, 1.48×, then 1.08×; auction prints 1.0×. SEC Section 31 + FINRA TAF ≈ 0.3 bps on sells. Borrow 15%/yr, or 50%/yr for crowded names (SI > 15% or DTC > 5), charged for 0.3 days; availability is not modelled. The doubled-cost stress doubles all of the above.
- **Selection discipline.** Each family's winner was chosen on IS Sharpe by code; OOS reported once. The execution 2×2×2 (entry × stop × exit) was declared from the institutional review (§6) before the round-3 data was seen. Round 3 logged 203 trials: 167 configurations with a Sharpe, 33 feature screens and 3 final combinations.
- **Deflated Sharpe.** Round 1's "0.49 over 1,037 trials" used null variance 1/T (`extras.py`); the same convention is used here with N = 1,240. With the cross-trial variance of IS Sharpes, SR₀ ≈ 3.3 annualised and every configuration has DSR ≈ 0, including P7; round 1's `selection.json` shows the same. Read the DSR column as a relative ranking, not as proof.

## Limitations

- About 250 IS and 350 OOS trades; IS trades are 70% from 2020–21. Per-year results swing more than the Sharpe gaps between the GO and NO rows.
- The earnings gate and the max-2-per-industry cap could not be replayed (no historical earnings dates; SIC peers are a proxy).
- No historical borrow availability; squeeze names were likely hard to borrow.
- **OPG short orders on Alpaca have not been exercised live.** Paper-test `ROUND3_SHORT_ENTRY_ORDER="opg"` first: acceptance of OPG short sells, fills against the official open, partial auction fills, and the sparse IEX premarket prints that the gap guard and sizing rely on (a name with no premarket print falls back to 09:31).
- Auction depth is unknown, so MOO capacity is unmeasured.

## What was built (branch only, off)

- `shortingbot/config.py`: `ROUND3_SHORT_ENTRY_ORDER = "market"` (default; `"opg"` turns it on), with the backtest evidence in the comment.
- `daily.py`: `open_auction_shorts()` and `auction_followup()`, wired into `open_intraday`. Before the 09:28 OPG cutoff it re-checks borrow, price and gap against the premarket print and sells each pick short with an OPG order; it skips names the sleeve still holds, any held name, and the PDT-cap and halt cases, which fall through to the unchanged 09:31 path. At 09:31 it refreshes the auction orders; a dead unfilled order is retried at 09:31; a resting one is never entered twice. Broker stops (if configured) go on the auction fills. The leftover alert ignores auction shorts. Dry runs only log.
- `shortingbot/broker.py`: `submit_opg()` (an OPG market order).
- `tests/test_round3.py`: 13 tests (flag off, auction entry, cutoff, sleeve ordering, expired / resting / rejected orders, premarket gap skip, stops, PDT and halts, dry run). `tests/conftest.py` FakeBroker gained `submit_opg`.
- **Test suite: 223 passed** (210 existing + 13 new).

## Files

- Code: `research/round3/*.py`. Run order: `fetch_intraday.py`, `fetch_sic.py`, `data.py`, `costs.py`, `study_q12.py`, `study_q3.py`, `study_q4.py`, `study_q5.py`, `study_q6.py`, `squeeze.py`, `combined.py`, `final_r3.py`. New caches in `round3/.cache` (gitignored).
- Results: `results/*.csv`; full trial log `trials_r3.csv`; `selection_r3.json` lists the IS-chosen candidates.
- Charts: `out/*.png`: `final_equity`, `q12_*`, `q3_feature_ic`, `q5_regime`, `q6_sleeve`, `squeeze_*`.

## References

- Abdi, F., & Ranaldo, A. (2017). A simple estimation of bid-ask spreads from daily close, high, and low prices. *Review of Financial Studies*, 30(12), 4437–4480. https://doi.org/10.1093/rfs/hhx084 — [Download PDF](https://libkey.io/libraries/545/articles/145411663/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Bailey, D. H., & López de Prado, M. (2014). The deflated Sharpe ratio. *Journal of Portfolio Management*, 40(5), 94–107. https://doi.org/10.3905/jpm.2014.40.5.094 — [Access options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Bailey&issn=0095-4918&title=The%20Journal%20of%20Portfolio%20Management&atitle=The%20Deflated%20Sharpe%20Ratio%3A%20Correcting%20for%20Selection%20Bias%2C%20Backtest%20Overfitting%2C%20and%20Non-Normality&volume=40&issue=5&spage=94&epage=107&date=2014-09-30&rft_id=info:doi/10.3905%2Fjpm.2014.40.5.094&rft_id=info:pmid/&sid=LibKey)
- Harvey, C. R., Liu, Y., & Zhu, H. (2016). … and the cross-section of expected returns. *Review of Financial Studies*, 29(1), 5–68. https://doi.org/10.1093/rfs/hhv059 — [Download PDF](https://libkey.io/libraries/545/articles/57275733/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Heston, S. L., Korajczyk, R. A., & Sadka, R. (2010). Intraday patterns in the cross-section of stock returns. *Journal of Finance*, 65(4), 1369–1407. https://doi.org/10.1111/j.1540-6261.2010.01573.x — [Download PDF](https://libkey.io/libraries/545/articles/29955648/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Kaminski, K. M., & Lo, A. W. (2014). When do stop-loss rules stop losses? *Journal of Financial Markets*, 18, 234–254. https://doi.org/10.1016/j.finmar.2013.07.001 — [Download PDF](https://libkey.io/libraries/545/articles/46814011/full-text-file?utm_source=api_4212&allow_speedbump=true). Their result that stops add value only when returns are serially correlated fits what is found here: the fade is mean-reverting intraday, so stops cut it.
- Lou, D., Polk, C., & Skouras, S. (2019). A tug of war: Overnight versus intraday expected returns. *Journal of Financial Economics*, 134(1), 192–213. https://doi.org/10.1016/j.jfineco.2019.03.011 — [Download PDF](https://libkey.io/libraries/545/articles/302597070/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Institutional context: `Research/institutional_review.md` §1 and §6 (main checkout). Section 6 recommended auction orders for open and close executions before this round's data was examined.
