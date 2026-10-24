# Round 3: longer history, condor management, dip PCS, tail hedge, sizing

Written 2026-10-23. Code, CSVs and charts are in `tuning/round3/` (`out/`). Every number can be traced to a file in `out/`. Nothing in `live/` was changed. One research-engine flag was added (`optionsbot/engine.py`, off by default). The coordinating session saved this file from the agent's hand-back, because the agent could not write report files itself.

## Bottom line
1. **The 2.6-year sample flattered both sleeves.** On a proxy chain validated against the real chain and against Cboe indexes, the live book (condor 20% + dip SPY/IWM 25% each, 50% total) at $15k over 2016-10 → 2026-10 has CAGR −4.7%, Sharpe −0.10 and max drawdown −67%. The same rules over Mar-2024 → Oct-2026, the window they were tuned on, show Sharpe 1.12.
2. **The condor has no edge we can find.**
   - 736 configurations on SPX 1990-2026. The best is IS 0.62 → OOS 0.23. Every deflated Sharpe is 0.
   - F1 itself scores IS 0.29 / OOS −0.22, with 7 near-max losses in 213 trades.
   - The published 15/15 real-chain record was partly engine bug R3-1: in 6 of 16 trades the "0.10Δ" short call was really a 0.02-0.04Δ call. With the bug fixed: 2 losses in 14 trades and Sharpe 0.49 (published 1.07).
   - The live bot sells true 0.10Δ calls, a riskier trade than the one backtested.
3. **The dip PCS on SPY has a small, real-looking edge. IWM and the current size do not.**
   - SPY over 10 years: Sharpe 0.30-0.32, IS 0.19 / OOS 0.42.
   - SPX over 36 years (RSI2 only): Sharpe 0.53 across 320 trades.
   - IWM: Sharpe 0.01, IS −0.27. Adding IWM roughly doubles the drawdown, −25% → −51%.
   - No variant survives the deflated Sharpe.
4. **A paid tail hedge doesn't fix this book. Smaller size does.**
   - Losses come from −3% to −6% moves into short-dated spreads, which far-OTM puts barely pay for.
   - 1%/yr in SPY put spreads cut the COVID drawdown from 29% to 23% and did nothing in Feb-2018 or Aug-2024. A 1-lot standing hedge at $15k costs 4-6% a year.
   - Cutting the dip to SPY only at 10% risk takes the worst replay stress from −49% to −11% and the 10-year max drawdown from −67% to −9.5%. Sharpe 0.25 vs live −0.10.
5. **Recommended config:** condor off; dip on SPY only; 10% max loss per spread and 10-15% total; signal, structure and exits unchanged. This is a config change for the owner to make and was not applied. No new live code: nothing passed "clearly winning IS and OOS after deflation".

## Verdict table
| Question | Verdict | Key numbers |
|---|---|---|
| Q1. Longer window? | **Yes.** The proxy is validated (next section). | Dip on 2016-26 SIP OHLC. Condor on SPX/10 1990-2026, rescaled so strikes and costs match today's SPY. |
| Q1. Do the current rules hold OOS? | **No.** | Dip SPY: Sharpe 0.04 before Mar-2024, 1.04 after. IWM: −0.27 / 1.28. Condor F1 SPY: −0.08 / 0.04. Condor F1 SPX: CAGR −0.6%, max DD −64%. |
| Q2. Condor management | **Hold is the least bad. No variant is robust.** | Median IS/OOS Sharpe: hold 0.11/0.02, tp50 −0.14/−0.21, dte21 −0.39/−0.47, sl2x −0.10/−0.24, breach-close −0.44/−0.46, tp50+sl2x+dte21 −0.65/−0.69. |
| Q2. Wing and delta | 5% wings and 45-60 DTE are least bad. | Wing 5% median −0.12/−0.21 vs 3% −0.29/−0.34. Best IS: 20Δ put / 10Δ call, 5% wings, 45 DTE, sl2x, VIX/VIX3M<1 → IS 0.62, OOS 0.23. |
| Q2. VIX filter / FOMC-CPI skip | **No help.** | FOMC/CPI ±2 days: F1 IS 0.29→0.28, OOS unchanged. VIX 15-30 raised OOS to 0.35 but cut IS to 0.15. |
| Q3. Dip definition | **Keep it as is.** | SPY+IWM book IS/OOS: live −0.02/0.34; IBS<0.1 0.34/0.36; close<SMA5−2% 0.18/0.58; RSI2 only −0.67/0.58; unconditional control −1.69. |
| Q3. Delta, width, DTE, exits | **Keep the live structure and exits.** The 0.05Δ long wing lowers drawdown. | 25/05 vs 25/10 (with IBS<0.1): DD −16%/−23% vs −19%/−42%, OOS 0.59 vs 0.36. TP 75%, no TP and a 10-day max are within ±0.1. Stops hurt. |
| Q3. More names | **No.** 55-80% of their signals land within 2 days of a SPY/IWM signal. | Real chain 2024-26 Sharpe: DIA 0.40, XLK 0.37, XLF −1.05, XLE −0.11, XLI −0.19, XLV 0.11, GLD 0.15, TLT −0.64. Proxy book SPY+IWM+DIA: DD −54% vs −51%. |
| Q4. Tail hedge | **None at $15k. Cut size instead.** | 1%/yr 10/20% OTM put spreads: COVID DD −29% → −23%, nothing else. 1-lot: 4-6%/yr. VIX calls are not tradable on Alpaca. |
| Q5. Sizing | **Far too large for the edge.** | Fully-loaded replays: −40% to −49% in Feb-2018, Oct-2018, COVID, Sep-2023. Kelly is uninformative: SPY dip 0.67 [0, 1]. |

## Q1: proxy and its error
**Model.**
- IV(K, T, t) = base_t(T) × exp(s(z, T, state)).
- base: the VIX9D/VIX/VIX3M/VIX6M term structure interpolated in total variance, × RVX/VIX for IWM and × VXN/VIX for QQQ.
- z = ln(K/F) / (base·√T).
- s: a cubic B-spline in z, with coefficients linear in ln T, (ln T)², ln(VIX/18), VIX/VIX3M and ln VIX·ln T.
- Fit on 1.12M SPY, 0.43M IWM and 0.94M QQQ OTM prints (2024-01..2026-10).
- Pre-2009 VIX3M and pre-2011 VIX9D are regression fills.
- Underlyings: raw SIP OHLC from 2016 (signals on the split-adjusted series), SPX/10 from 1990 with strike grid, slippage floors and fees scaled by spot/600.
- Mechanics follow `optionsbot.engine`, including end-of-month expiries and the ~6-week weekly listing horizon.

**Error vs prints** (surface fit on 2024-01..2025-06; vol points):

| SPY slice | IS bias / MAE | OOS bias / MAE | OOS median abs price error |
|---|---|---|---|
| dip short (0.25Δ put, 5-16 DTE) | −0.18 / 1.04 | −0.29 / 0.82 | 9% |
| dip long (0.10Δ) | −0.12 / 0.90 | −0.57 / 0.89 | 15% |
| condor short put (0.10Δ, 45-75 DTE) | +0.18 / 0.40 | −0.04 / 0.35 | 4% |
| condor short call | +0.13 / 0.73 | +0.18 / 0.71 | 18% |
| hedge puts (0.01-0.06Δ, 30-120 DTE) | +0.07 / 0.54 | −0.10 / 0.44 | 6% |

**Same legs the engine traded:** dip net credit bias −4% to −7% with MAE 11-14%; condor +8% to +12% with MAE 14-29%.

**Strategy level, 2024-03..2026-10:**

| | Real Sharpe | Proxy Sharpe | Real trades | Proxy trades | Trade P&L correlation |
|---|---|---|---|---|---|
| Dip SPY | 1.09 | 0.89 / 1.04 | 20 | 21 | 0.79 |
| Dip IWM | 1.50 | 1.25 / 1.29 | 16 | 16 | 0.94 |
| Dip QQQ | 0.37 | 0.28 / 0.23 | 19 | 20 | 0.97 |

All 55 real dip trades are matched. Read the proxy as about 0.1-0.25 Sharpe pessimistic on the dip.

**Cboe indexes, 2007-2026:**

| Index | Monthly correlation | CAGR proxy vs Cboe | Worst month proxy vs Cboe |
|---|---|---|---|
| PUT | 0.979 | 6.0% vs 7.2% | −29.3% vs −27.8% |
| PPUT | 0.989 | 8.6% vs 8.1% | — |

- Pre-2007 PUT data is stale, so it is not usable.
- The proxy is ~1%/yr conservative on ATM premium and ~0.5%/yr cheap on 5% OTM puts.

**VIX calls:** Black-76 on a forward-variance future, vol = 0.9 × VVIX, rebuilt as the VXTH overlay. IS 2006-15 correlation 0.92, OOS 2016-26 0.81, OOS overlay total −64% (proxy) vs −71% (actual).

**Engine bug R3-1.**
- What happens: `Market._select_on` stops widening the strike window when the window-edge strike has no print. The call search is seeded with the put leg's skewed IV, so the first window sits far out of the money.
- Result: 6 of 16 F1 short calls were 0.018-0.038Δ by their own prints. Puts were 0.10-0.19Δ.
- Fix: `STRICT_DELTA_SEARCH` (env `OPTIONSBOT_STRICT_DELTA=1`, off by default) with tests in `tests/test_round3_strict_delta.py`.
- Re-run with the fix: 14 trades, 12 wins, worst −$716 (Jun-2024, call side), Sharpe 0.49, IS 0.12 / OOS 0.80, DD −11.9%.
- Expiry-calendar luck: on monthlies only, the real chain takes a max loss in Feb→Apr 2025 (−$1,627). That run is incomplete in the offline cache, so treat it as indicative.

**Long history, frozen rules ($10k, default costs):**

| Run | Years | CAGR | Sharpe | Max DD | Trades | Win | Near-max losses | Sharpe before Mar-2024 |
|---|---|---|---|---|---|---|---|---|
| Dip SPY | 10.0 | 2.9% | 0.30 | −26% | 57 | 91% | 0 (worst −67% of risk) | 0.04 |
| Dip IWM | 10.0 | −0.6% | 0.01 | −37% | 48 | 85% | 1 | −0.27 |
| Dip SPX (RSI2 only, hypothetical weeklies) | 36.0 | 7.5% | 0.53 | −45% | 320 | 89% | 4 | 0.45 |
| Condor F1 SPY | 10.7 | −1.7% | −0.08 | −45% | 62 | 84% | 3 | −0.08 |
| Condor F1 SPX | 36.6 | −0.6% | 0.04 | −64% | 213 | 83% | 7 | 0.10 |

- Worst dip trades: Sep-2023 (−15% of equity), Oct-2023, Feb-2018, Jan-2022, Feb-2020.
- Worst condor trades are mostly call-side melt-ups: Jan-2018, 2003, 2004, 2010, 1997.
- The kill switch is about neutral for the dip and inconsistent for the condor (−0.27 on SPY, +0.11 on SPX).

## Q2: condor
- 736 trials on SPX 1990-2026, IS 1990-2009 / OOS 2010-2026, $100k, 20% risk. Chart: `out/condor_grid_is_oos.png`. Deflated Sharpe is 0 for all at N = 995.
- **Cost sensitivity, F1 Sharpe:** zero 0.26, calibrated (≈ mid) 0.21, default (≈ natural) 0.13, stress 0.05.
- **Entry pricing realism** (live 2026-10-23: 1x SPY 2026-12-18 IC 650/675/814/835, mid 2.29, natural 2.13, no fill at 2.28→2.20):
  - The default model charges about 0.17 over four legs, which is about natural. That is the realistic assumption for paper. The mid-based calibrated model is too optimistic.
  - The live ladder (`ENTRY_MAX_CONCESSION = 0.5`) stops halfway to natural, so expect frequent paper non-fills.
  - Don't loosen the ladder for the condor; turn it off.

## Q3: dip PCS
- 139 trials on the SPY+IWM book at the live sizing, IS 2016-10..2020-12 / OOS 2021..2026-10.
- The unconditional control gives −1.69, so the signal matters. Neighbours of the live rule sit between −0.3 and +0.4 IS and +0.2 and +0.8 OOS. None is significant after deflation.
- **Share of a name's signals within 2 days of a SPY/IWM signal:** QQQ 77%, DIA 77%, XLK 79%, IJR 83%, RSP 76%, XLF 55%, XLE 35%, XLU 30%, GLD 24%, TLT 10%.
- The uncorrelated names trade 3-7 times a year with fat 5-day tails (GLD −10%, XLU −9%).
- Several sector-ETF real-chain runs look unreliable (XLF 29% win, XLV 47%), likely the same strike-search issue on low-priced names.
- **SPY alone beats SPY+IWM in both periods:** IS 0.19 vs −0.02, OOS 0.42 vs 0.28, DD −25% vs −51%.

## Q4: tail hedge
Book A is the live book, 2016-26 at $15k. Book B is the SPX analogue, 1990-2026. Hedges are bought at the ask and sold at the bid. Drawdowns are max DD inside the event window.

| Hedge (budget %/yr) | A CAGR (none −4.7%) | A COVID DD | A Feb-2018 DD | B CAGR (none 8.0%) | B GFC DD | B Aug-2011 DD |
|---|---|---|---|---|---|---|
| none | −4.7% | −29.0% | −16.9% | 8.0% | −21.6% | −13.3% |
| 10/20% OTM put spread, 75 DTE, 1% | −4.7% | −23.3% | −16.3% | 7.6% | −21.2% | −12.7% |
| same, 2% | −6.9% | −22.3% | −15.8% | 7.3% | −20.3% | −12.1% |
| 0.10Δ/0.05Δ spread, 60 DTE, 1% | −6.7% | −26.9% | −16.5% | 7.4% | −21.1% | −12.9% |
| 3%/8% OTM monthly spread, 1% | −6.7% | −26.6% | −16.3% | 7.7% | −21.5% | −12.8% |
| 15% OTM put, 90 DTE, 1% | −4.8% | −25.9% | −16.5% | 7.4% | −19.5% | −12.6% |
| VIX 30Δ 1-month calls, 2% (model; not on Alpaca) | −4.7% | −23.2% | −12.9% | 2.8%* | −18.3% | −10.9% |
| 1-lot 10/20% spread at $15k | −18.7% | −17.5% | −14.8% | — | — | — |

\* Book B VIX rows start in 2006.

- **Shorting bot** (P7 backtest at 5% per name with the +35% stop):
  - Daily correlation with SPY is −0.13, or −0.31 on |SPY|>2% days. Mean +0.29% on days SPY falls more than 2%.
  - It had no position on Feb-5-2018, Mar-2020, Aug-5-2024 or Apr-3/4/7/9-2025.
  - Its tail is idiosyncratic squeezes (worst P7 day −7.4% at 10% weight).
- **Recommendation:** no standing hedge. If one is wanted anyway, 1%/yr of 10/20% OTM SPY put spreads, and only once the account is ≥ ~$100k.

## Q5: sizing and stress
**Replays with the book fully loaded the day before** (worst close, % of the $15k options account):

| Event | Live | SPY dip 10%, no condor | SPY+IWM 5%/10%, no condor |
|---|---|---|---|
| Feb-2018 | **−39.9%** | −10.9% | −9.3% |
| COVID (entry Feb-21-2020) | **−48.9%** | −8.9% | −6.5% |
| Oct-2018 | −42.7% | −10.4% | −10.3% |
| Sep-2023 | −37.3% | −9.8% | −6.8% |
| Aug-2024 | −23.7% | −5.6% | −2.7% |
| Apr-2025 (below 200DMA, only the condor on) | −2.8% | 0% | 0% |
| Aug-2015 (SPX) | −34.9% | −8.5% | −4.2% |
| Aug-2011 (SPX) | −33.6% | −9.6% | −4.8% |
| Oct-1997 (SPX) | −30.6% | −8.8% | −4.4% |
| Oct-2008 (SPX, below 200DMA) | −16.4% | 0% | 0% |

**Synthetic overnight gaps, live book** (whole book = $15k options + $10k shorting, with the shorts squeezed +30%):

| Start | Gap | VIX after | Options account | Whole book (% of $25k) |
|---|---|---|---|---|
| Calm (VIX 13) | −3% | 25 | −23% | −17% |
| Calm (VIX 13) | −10% | 45 | −42% | −28% |
| Calm (VIX 13) | −20% | 80 | −49% | −32% |
| Typical | −10% | 45 | −20% | −15% |
| Elevated (VIX 27) | −10% | 45 | −26% | −18% |

The institutional review's limit is ≤ 15% NAV on this scenario; the live book breaks it.

**Kelly** (f* = share of equity at max loss per trade):

| Source | f* | 90% CI |
|---|---|---|
| Dip SPY | 0.67 | [0, 1] |
| Dip SPX | 0.62 | [0.35, 0.85] |
| Dip IWM | 0 | — |
| Condor SPY | 0 | [0, 0.14] |
| Condor SPX | 0.19 | [0, 0.53] |

Per-trade Kelly ignores simultaneous losses, model error and selection. Use stress instead.

**Cap grid** ($15k, integer contracts, 2016-2026):

| Config | CAGR | Sharpe | Max DD | IS / OOS | Trades |
|---|---|---|---|---|---|
| Live | −4.7% | −0.10 | −67% | −0.13 / −0.07 | 145 |
| SPY+IWM 25%/50%, no condor | 0.7% | 0.15 | −51% | −0.02 / 0.28 | 103 |
| SPY only 25% | 3.4% | 0.32 | −25% | 0.19 / 0.42 | 56 |
| **SPY only 10% (recommended)** | 0.9% | 0.25 | **−9.5%** | 0.13 / 0.34 | 51 |
| SPY+IWM 10%/20%, 25/05 wing | 0.8% | 0.20 | −9.7% | 0.06 / 0.41 | 66 |

- Every candidate has a deflated Sharpe of 0 at N = 995. PSR(SR > 0) for SPY 10% is 0.78.
- At $15k an SPY spread risks about $600-700, so 10% gives 2 contracts. The condor (about $2.0-2.3k per lot) cannot be held under a ~13% cap.

## Recommended config changes (not applied)
| Setting | Current | Recommended | Why |
|---|---|---|---|
| `CONDOR_ENABLED` | True | **False** | No edge in 36 years. The live trade is not the backtested one (R3-1). Paper fills need natural. |
| `UNIVERSE` | SPY, IWM | **SPY** | IWM Sharpe 0.01 (IS −0.27). It doubles the drawdown. |
| `RISK_PER_POSITION` | 0.25 | **0.10** | Stress −49% → −9%; DD −67% → −9.5%. |
| `RISK_TOTAL` | 0.50 | **0.10-0.15** | Institutional ≤ 10% NAV aggregate cap. |
| Signal, structure, exits | — | unchanged | No neighbour is significantly better. |
| Tail hedge | none | none | Sizing is cheaper. VIX options are not on Alpaca. |

**Caveats.**
- No intraday gaps or bid/ask blowouts beyond the stress multiplier; no 15:55 timing.
- The pre-2016 dip uses SPX closes without IBS and assumes weeklies existed. Treat it as context.
- The OOS 2021-26 dip window overlaps the window the live rule was chosen on.

## Files
All in `tuning/round3/`.
- Core: `common.py`, `r3lib.py`, `surface.py`, `calib_iv.py`, `extract_chain.py`, `surface_eval.py`, `psim.py`, `vixopt.py`, `book.py`, `rlib.py`.
- Validation: `repro.py`, `validate_proxy.py`, `validate_legs.py`, `validate_condor.py`, `validate_cboe.py`, `validate_vxth.py`, `check_deltas.py`, `realchain_f1_strict.py`.
- Studies: `q1_long.py`, `q2_condor.py`, `q3_dip.py`, `q3_names.py`, `realchain_extra.py`, `q4_hedge.py`, `q5_stress.py`, `q5_sizing.py`, `q_costs.py`, `final.py`.
- Data: `fetch_ohlc.py` (the `ohlc/` folder is gitignored), `build_events.py` → `events.csv`.
- Charts: `out/book_equity_2016_2026.png`, `out/condor_grid_is_oos.png`, `out/stress_replay.png`.
- Trials: 995 (`out/trials.json`).

## References
- Bailey, D. H., & López de Prado, M. (2014). The Deflated Sharpe Ratio. *Journal of Portfolio Management*, 40(5), 94-107. [Access options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Bailey&issn=0095-4918&title=The%20Journal%20of%20Portfolio%20Management&atitle=The%20Deflated%20Sharpe%20Ratio%3A%20Correcting%20for%20Selection%20Bias%2C%20Backtest%20Overfitting%2C%20and%20Non-Normality&volume=40&issue=5&spage=94&epage=107&date=2014-09-30&rft_id=info:doi/10.3905%2Fjpm.2014.40.5.094&rft_id=info:pmid/&sid=LibKey)
- Moreira, A., & Muir, T. (2017). Volatility-Managed Portfolios. *Journal of Finance*, 72(4), 1611-1644. [Download PDF](https://libkey.io/libraries/545/articles/89148290/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Carr, P., & Wu, L. (2009). Variance Risk Premiums. *Review of Financial Studies*, 22(3), 1311-1341. [Download PDF](https://libkey.io/libraries/545/articles/27131017/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Coval, J. D., & Shumway, T. (2001). Expected Option Returns. *Journal of Finance*, 56(3), 983-1009. [Download PDF](https://libkey.io/libraries/545/articles/29954678/full-text-file?utm_source=api_4212&allow_speedbump=true)
- Bondarenko, O. (2014). Why Are Put Options So Expensive? *Quarterly Journal of Finance*, 4(3), 1450015. [Access options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Bondarenko&issn=2010-1392&title=Quarterly%20Journal%20of%20Finance&atitle=Why%20Are%20Put%20Options%20So%20Expensive%3F&volume=4&issue=3&spage=1450015&epage=&date=2014-09-01&rft_id=info:doi/10.1142%2FS2010139214500153&rft_id=info:pmid/&sid=LibKey)
- Israelov, R. (2019). Pathetic Protection: The Elusive Benefits of Protective Puts. *Journal of Alternative Investments*. Not LibKey-resolved.
- Cboe index methodologies (PUT, PPUT, VXTH, CNDR): cboe.com. White papers, not LibKey-resolved.
