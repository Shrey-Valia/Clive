# Systematic Options Strategies for Large-Cap Stocks and Index ETFs

**Scope:** retail account of $10k to $100k on Alpaca (paper), options level 3. Universe: SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA and similar.
**Purpose:** decide which strategies to backtest on Alpaca daily option bars (Feb 2024 onward) and which to automate.
**Date:** 2026-10-21. Cboe index figures come from factsheets dated 2026-09-30 unless noted otherwise.

---

## 0. Executive summary

1. **There is a real but modest edge, and it is the volatility risk premium (VRP).** Index option implied volatility has, on average, been above the volatility that follows, so option sellers get paid for taking crash risk. Delta-hedged index options lose money for their buyers (Bakshi & Kapadia 2003). Zero-beta ATM straddles lost about 3% per week (Coval & Shumway 2001). Over 1986 to 2015 the Cboe PUT index matched the S&P 500's return (10.13% vs 9.85% per year) at two-thirds of its volatility (10.16% vs 15.26%) (Bondarenko 2016).
2. **The premium has been weaker recently and it is not free money.** Since 2007 PUT returned 7.2% per year against 11.0% for the S&P 500 total return index, with volatility of 10.7% vs 15.4%, a max drawdown of -32.7% vs -50.9%, and a Sharpe of 0.52 vs 0.61. In strong bull years premium selling lags badly: PUT gained 2.1% in 2020 when the S&P gained 18.4%, and BXM lost 2.8%. Its value is a smoother path, not a higher return.
3. **The tails are what matter.** Short volatility loses in sudden spikes: Feb 2018 (XIV fell more than 90% in a day and LJM's fund lost about 80%), March 2020, Aug 5 2024, and April 2025. Defined-risk structures (spreads and condors) survive these events. Undefined-risk ones (naked strangles, leveraged short puts) are what blow up accounts.
4. **Alpaca's constraints narrow the menu.** Alpaca does not support any uncovered short option strategy, and each leg of a multi-leg order must be covered within that same order. That rules out short strangles and naked puts on margin. What is available: covered calls, cash-secured puts, long options, vertical spreads, iron condors and butterflies, calendars and diagonals.
5. **Account size matters a lot.** Cash-secured puts or covered calls on SPY need about $60k or more per contract. On $10k the only realistic tools are defined-risk spreads, typically 1 to 3 contracts at $5 wide. The wheel only becomes practical from about $50k, and then mainly on lower-priced names in the universe.
6. **Management rules carry most of the result.** Vendor research (tastytrade/tastylive, Option Alpha) consistently finds that entering around 45 DTE and exiting at 50% of max profit or at 21 DTE gives better P/L per day and lower variance than holding to expiry. This is not peer-reviewed, but it is consistent across many of their studies and is cheap to verify in our own backtest.
7. **Trend and VIX filters are plausible risk reducers, not proven alpha.** A 200-day moving average filter on short puts mainly avoids grinding bear markets like 2008 and 2022. Our 2.7-year sample has almost no such period, so the backtest will not be able to show much benefit from it.

### Top 5 strategies to pursue (in priority order)

| # | Strategy | Why |
|---|---|---|
| 1 | **SPY/QQQ put credit spread, 45 DTE, 25-delta short, ~$5 wide, 50% TP / 21 DTE exit, 200DMA + VIX filter** | Purest defined-risk VRP harvest. Fits $10k. Very liquid. Easy to automate as an mleg order. |
| 2 | **SPY/QQQ/IWM iron condor, 45 DTE, 16-delta shorts, 5-delta or fixed-width wings, 50% TP / 21 DTE** | Cboe's CNDR (a similar design) had the lowest volatility of the Cboe option-selling indexes (7.23%), and its worst drawdown was about 19% vs the S&P's 51%. It collects premium on both sides at the same buying power. |
| 3 | **Cash-secured put / wheel (30-delta, 30 to 45 DTE) on the universe for accounts of $50k or more**, with BXMD-style 30-delta covered calls after assignment | Closest to the PUT and BXMD indexes, which have the longest public record. Captures the equity premium plus the VRP. |
| 4 | **Post-earnings IV-crush short iron condor on mega-caps** (enter at the close before the report, short strikes outside the implied move, exit the next day) | Different return driver: event-vol overpricing. About 120 events in our window gives decent sample size, and the risk is defined. |
| 5 | **Poor man's covered call (LEAPS diagonal) on QQQ/SPY** | The only way to get equity-premium-plus-VRP exposure on a $10k account. Uses about 1/4 to 1/6 of the capital of a covered call. |

Worth testing but lower priority: pre-earnings long straddles (positive academic evidence, but mostly in small caps), collars as a hedge overlay, and calendars. **0DTE should be deferred** because daily bars cannot test it properly (see section 14).

---

## 1. Evidence base: where the edge comes from

### 1.1 The volatility risk premium (VRP)
- **Definition:** the market's implied volatility (or the variance swap rate) exceeds the volatility that is subsequently realized, on average. Option sellers are compensated for bearing crash and volatility risk.
- **Bakshi & Kapadia (2003), RFS 16(2):** delta-hedged S&P 500 option portfolios underperform zero, which implies a negative market volatility risk premium. The underperformance is larger when volatility is higher and smaller for options away from the money.
- **Carr & Wu (2009), RFS, "Variance Risk Premia":** synthetic variance swap rates exceed realized variance for the S&P 500 and most stocks. The index VRP is large and negative for buyers. Single-stock VRPs are smaller and more heterogeneous.
- **Coval & Shumway (2001), JF:** zero-beta ATM S&P straddles lost about 3% per week on average, which is direct evidence that long volatility pays the premium.
- **Magnitude (approximate):** VIX has averaged around 19 to 20 since 1990, while realized S&P volatility has averaged around 15 to 16. That gap of about 3 to 5 volatility points is the practitioner rule of thumb. The spread is not stable: it turns sharply negative in crashes, when realized volatility exceeds what was implied.
- **Caveat on recent decay:** some later work, summarized secondhand, reports that the realized S&P VRP over 2006 to 2020 was not statistically different from zero once crash months are included. Treat the VRP as positive in expectation but noisy, with heavy left tails.
- **Single stocks vs index:** index options carry a larger VRP than single-stock options. This is the "index puts are expensive" correlation-risk-premium story. For single names, the most consistent overpricing is around scheduled events (earnings), and even that is debated (see section 13).

### 1.2 Cboe benchmark indexes (the longest public "backtests")

All of these are SPX-based, hold collateral in T-bills, are monthly unless noted, and have no management rules (hold to expiry).

| Index | Construction | Ann. return | Ann. vol | Max DD | Sharpe | Period | Source |
|---|---|---|---|---|---|---|---|
| **PUT** | Sell 1-mo ATM SPX put, T-bill collateral | **7.2%** | 10.7% | **-32.7%** | 0.52 | Jan 2007 to Sep 2026 | Cboe PUT factsheet |
| S&P 500 TR (same window) | n/a | 11.0% | 15.4% | -50.9% | 0.61 | Jan 2007 to Sep 2026 | same |
| **BXM** | Long SPX, sell 1-mo ATM call | **8.6%** | 10.6% | **-35.8%** | 0.56 | Jun 1986 to Sep 2026 | Cboe BXM factsheet |
| **BXMD** | Long SPX, sell 1-mo **30-delta** call | **10.6%** | 12.9% | **-42.7%** | 0.61 | Jun 1986 to Sep 2026 | Cboe BXMD factsheet |
| S&P 500 TR (same window) | n/a | 11.2% | 15.2% | -50.9% | 0.56 | Jun 1986 to Sep 2026 | same |
| **BXMW** | Multi-week (weekly-staggered) buy-write | 7.6% | 8.3% | -19.1% | 0.71 | Jun 2012 to Sep 2026 (S&P: 14.9% / 13.9% / -23.9% / 0.95) | Cboe BXMW factsheet |
| **PUT (1986 to 2015)** | n/a | 10.13% | 10.16% | n/a | n/a | S&P: 9.85% / 15.26% | Bondarenko 2016 |
| **WPUT** (weekly ATM put) | n/a | n/a | lower than PUT | -24.2% (vs PUT -32.7%, S&P -50.9%) | n/a | 2006 to 2015 | Bondarenko 2016 |
| **CNDR** (iron condor) | Sell ~0.20-delta put and call (some docs say 0.15), buy ~0.05-delta wings, monthly | n/a | **7.23%** (lowest of 6 indexes, 1986 to 2015) | worst peak-to-trough ~**19%** | n/a | 1986 onward | Cboe/Black & Szado 2016; Cboe insights |
| **BFLY** (iron butterfly) | Sell ATM straddle, buy 5% OTM wings | n/a | n/a | -47.1% | n/a | 1986 onward | Cboe insights |
| **CLL** (95-110 collar) | Long SPX, buy quarterly 95% put, sell monthly 110% call | n/a | beta < 0.75 | n/a | n/a | 1986 onward | Cboe insights |
| **PPUT** (5% OTM protective put) | n/a | n/a | beta < 0.75 | 18 months of -6% or worse vs 35 for S&P | n/a | 1986 to 2021 | Cboe insights |

Average gross premium collected (Bondarenko): **PUT 24.1% per year, WPUT 39.3% per year** (2006 to 2015). BXM averaged about 1.7% gross premium per month (Cboe).

**Calendar-year returns (Cboe factsheets, total return):**

| Year | S&P 500 TR | PUT | BXM | BXMD | BXMW |
|---|---|---|---|---|---|
| 2018 | -4.4% | -5.9% | -4.8% | -5.4% | -6.6% |
| 2019 | 31.5% | 13.5% | 15.7% | 23.0% | 13.0% |
| **2020** | 18.4% | **2.1%** | **-2.8%** | 6.2% | -0.7% |
| 2021 | 28.7% | 21.8% | 20.5% | 22.2% | 18.1% |
| **2022** | **-18.1%** | **-7.7%** | -11.4% | -16.1% | -12.0% |
| 2023 | 26.3% | 14.3% | 11.8% | 19.8% | 14.9% |
| **2024** | 25.0% | 17.8% | 20.1% | 23.8% | 14.0% |
| **2025** | 17.9% | 9.2% | 8.9% | 12.1% | 10.4% |

**Takeaways:**
- In V-shaped crashes (2020) premium selling gets the downside without the rebound. The short option caps the recovery, and the strategy re-strikes at lower levels after the crash.
- In grinding bears (2022) it loses less than the index because the premium cushions the decline.
- In strong bull years (2019, 2023, 2024, 2025) it lags by 5 to 18 points.
- Our Feb 2024 to Oct 2026 window is mostly bull years. **Expect buy-and-hold to beat almost every premium-selling strategy on raw return in the backtest.** Compare risk-adjusted return and drawdown instead.

### 1.3 Covered calls are mostly equity beta
Israelov & Nielsen, "Covered Calls Uncovered" (FAJ 2015, AQR), decompose a covered call into three parts:
- **Equity exposure:** most of the risk and return.
- **Short volatility:** Sharpe near 1.0, but only about 10% of risk.
- **"Equity reversal" (a dynamic short-gamma exposure):** about 25% of risk with little return.

Implication: the premium-selling "edge" is a small slice of the P/L of covered calls and CSPs. The rest is just being long stocks, minus upside capture.

### 1.4 Management studies (tastytrade/tastylive). Vendor research, not peer-reviewed.
- **16-delta SPY strangles:** entering at 45 DTE and exiting at 50% profit or 21 DTE gave the largest daily P/L. A 21-DTE exit beat both managing at 50% only and holding to expiry. Newer SPY strangle studies found a 21-DTE exit gave the highest return with the lowest volatility.
- **Earlier studies:** managing winners at 50% beat holding to expiry on P/L per day. One SPY study reported 76% more profit.
- **Pattern:** the largest average gains of a 45-day short-premium trade come in its middle weeks, and gamma risk dominates the last 2 to 3 weeks.
- **IV rank:** SPY 45-DTE iron condors (30-delta short strangle, 16-delta wings) roughly doubled average P/L in high IV rank, and return on risk improved from about 5% to about 9.5%. Scaling deltas up in high IVR helps. In low IVR the delta choice barely matters.
- **Caveats:** these studies are selection-biased, cherry-pick periods, often ignore slippage, and mostly use naked strangles (not available to us). Treat them as hypotheses to verify.

### 1.5 0DTE evidence
- **Beckmeyer, Branger & Gayda (2023):** retail traders lost on average **$358k per day** in SPX 0DTE after daily expirations began (May 2022), up from $184k over the full sample. About 75% of retail S&P option trades were 0DTE. About 60% of losses came from transaction costs. **Short positions were profitable even after fees**, which is consistent with the VRP extending to intraday horizons.
- **Vilkov, "0DTE Trading Rules" (SPX, 2016 to Jan 2024):** static rules (fixed entry time, hold to expiry) were mostly not statistically significant. Return distributions were wide and skewed, and P/L was largely explained by realized skewness. Conditional rules might work, but this is unproven.
- **Option Alpha:** a 0.3% OTM SPX iron condor entered at 1:00 pm ET reached max profit 66.1% of the time over a 180-day window. Option Alpha itself warns that Black-Scholes probabilities are unreliable at 0DTE.

### 1.6 Known blow-ups
- **Feb 5 2018 ("Volmageddon"):** short-volatility ETPs lost more than 90% in one day (XIV, about $1.9B, was terminated). The LJM Preservation & Growth fund (naked short SPX puts) lost about 80% in roughly two days.
- **Feb to Mar 2020:** VIX hit about 82. PUT and BXM gave up most of their 2019 gains, then lagged the rebound.
- **2022:** a slow bear market. Short puts were assigned repeatedly and covered calls capped the bear-market rallies. PUT -7.7%, BXMD -16.1%.
- **Inside our backtest window:**
  - **Aug 5 2024:** yen carry unwind, VIX spiked intraday into the 60s.
  - **Jan 27 2025:** DeepSeek shock, NVDA -17% in a day.
  - **April 2025:** tariff shock. SPX fell about 19% from its Feb high to its Apr 8 low, VIX closed above 50, then the market made a V-shaped recovery.

  These give us at least three genuine stress tests.

---

## 2. Alpaca constraints that shape the design

| Constraint | Implication |
|---|---|
| Level 3 = level 1 (covered calls, CSPs) + level 2 (long calls and puts) + call and put spreads; multi-leg (`order_class: "mleg"`) supports condors, butterflies, calendars and straddles | Fine for everything except naked shorts. |
| **"We do not support any uncovered short options strategies."** Each mleg leg must be covered within the same order. | **No short strangles or straddles, no naked puts on margin.** Use iron condors and iron butterflies instead. Rolling a short leg must be done as a complete mleg (close and open together) or as close-then-open. |
| Margin = theoretical max loss across strikes (the "universal spread rule"), computed per expiry | A $5-wide credit spread ties up about $500 minus the credit. Capital efficient. |
| Order types: market, limit, stop, stop_limit; stop types are **single-leg only**; TIF only `day` or `gtc`; **no extended hours** | Stop-losses on spreads must be implemented by the bot (poll the mark, then send an mleg limit to close). |
| ITM by $0.01 or more at expiry is auto-exercised; if buying power is insufficient Alpaca **liquidates within about 1 hour before expiry**; DNE instructions need support | Close all short-option positions before expiry day (or by about 15:00 ET on expiry day) to avoid pin risk and forced liquidation. |
| Assignments are not pushed over websockets; on **paper, assignment and exercise activities appear the next day** | The bot must reconcile positions every morning. Paper assignment simulation may not match the real OCC process (early assignment is especially unrealistic in paper). |
| ETF and equity options are **American-style and physically settled** (SPY, QQQ, etc.). SPX/XSP index options are not the focus on Alpaca. | Early-assignment risk on short ITM calls before ex-dividend dates (SPY, QQQ, IWM and AAPL/MSFT pay quarterly). No Section 1256 tax treatment. |
| Historical option data only from **Feb 2024**; the free "indicative" feed is delayed; OPRA requires a subscription | Backtest window of about 2.7 years. See section 18.4. |
| Commission-free options, but regulatory fees (ORF, OCC, FINRA TAF) of a few cents per contract | Slippage, not commission, dominates costs. Model at least $0.01 to $0.02 per leg for SPY/QQQ and $0.03 to $0.10 per leg for single names (more for TSLA/NVDA wings). |

**Capital reality check (approximate prices; check live):** SPY ~$650 to 700, QQQ ~$550 to 600, IWM ~$220 to 250, AAPL ~$230 to 260, MSFT ~$450 to 520, NVDA ~$170 to 200, AMZN ~$210 to 240, GOOGL ~$200 to 260, META ~$650 to 750, TSLA ~$300 to 450.

| Instrument | Capital per contract | Fits $10k? | Fits $100k? |
|---|---|---|---|
| CSP or covered call on SPY | ~$65k | No | 1 contract only (concentration) |
| CSP or covered call on IWM / NVDA / AMZN | ~$18k to 25k | No | Yes, 2 to 4 |
| $5-wide put credit spread (any) | ~$350 to 450 risk | Yes (1 to 5) | Yes |
| $10-wide iron condor on SPY | ~$700 to 850 risk (one side) | Yes (1 to 2) | Yes |
| LEAPS 80-delta call on QQQ (6 to 12 months out) | ~$10k to 15k | Marginal; use IWM/AMZN/NVDA | Yes |

**Sizing rule to bake in:** max loss per trade at most 2 to 5% of equity, total short-premium max loss at most 30 to 50% of equity, and at most about 1/3 of risk in any one underlying. Highly correlated underlyings (SPY/QQQ/mega-caps) count as one cluster.

---

## 3. Covered calls (buy-write)

- **Mechanics:** own 100 shares and sell 1 OTM call against them. Roll or let it expire.
- **Typical parameters:** 30 to 45 DTE, 0.20 to 0.30 delta (BXMD uses 30 delta, which historically beat ATM BXM). Close at 50% profit or roll at 21 DTE. If tested (ITM), roll up and out for a credit or accept assignment.
- **Edge harvested:** mostly equity beta. A small VRP slice (Israelov & Nielsen) is earned by selling upside that is usually slightly overpriced on the index. On single stocks, upside call skew is often cheap, so the edge there is weaker.
- **Evidence:** BXMD returned 10.6% per year at 12.9% volatility (Sharpe 0.61) against 11.2% / 15.2% / 0.56 for the S&P since 1986. BXM returned 8.6% per year. Lag in bull years (2024: BXM 20.1% vs 25.0%), and the drawdown is similar to stock minus the premium (2022 BXMD -16.1%).
- **Risks and tails:** full downside of the stock with capped upside. Worst case is a V-shaped crash (2020: BXM -2.8% vs S&P +18.4%). Early assignment before ex-dividend if the call is ITM with extrinsic value below the dividend. Tax drag from assignments in taxable accounts.
- **$10k account:** only feasible on lower-priced names (none of the core universe is under $100 except possibly IWM-like ETFs). Not suitable at $10k. From $25k to 100k it works on NVDA, AMZN, GOOGL, AAPL and IWM.
- **Alpaca automation:** good. Level 1, single-leg orders, simple state machine. Note that equity-plus-option combo orders are not supported, so buy the shares and sell the call in separate orders.
- **Backtest parameters:** underlyings SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN; sell the 30-delta call at the expiry nearest 35 DTE. Exit at 50% profit or at 21 DTE, then re-sell. If the short call reaches 0.70 delta, roll up and out to the next ~35 DTE 30-delta strike for a net credit; else let shares be called away and re-buy at the next open. Benchmark against buy-and-hold of the same underlying.

## 4. Cash-secured puts (CSP)

- **Mechanics:** sell an OTM put, holding cash equal to strike × 100. If assigned, you own shares.
- **Typical parameters:** 30 to 45 DTE, 0.20 to 0.30 delta. 50% take-profit, 21 DTE exit or roll. Roll down and out for a credit if tested, or accept assignment (moves into the wheel).
- **Edge harvested:** equity risk premium on the cash collateral plus the index VRP (put skew is reliably rich on indexes). PUT is the purest benchmark.
- **Evidence:** PUT returned 10.13% vs 9.85% for the S&P at 2/3 of the volatility (1986 to 2015). Since 2007: 7.2% vs 11.0%, max DD -32.7% vs -50.9%. WPUT (weekly) had a lower max DD (-24.2%) over 2006 to 2015 and collected 39% per year in gross premium. **OTM puts (25 to 30 delta) give up premium but lower assignment frequency;** most practitioner studies show better risk-adjusted results than ATM.
- **Risks and tails:** economically the same downside as a covered call (put-call parity). Gap risk. In 2008 or 2022, repeated assignment at falling strikes.
- **$10k account:** infeasible on the listed universe. One IWM put needs about $22k. At $100k, 1 SPY or 3 to 4 single-name puts.
- **Alpaca automation:** good (level 1, single leg, buying power is checked automatically).
- **Backtest parameters:** sell the 30-delta put closest to 35 DTE; 50% TP, 21 DTE exit; optional roll; trend filter variant (section 15). Count interest on idle collateral as 0% (Alpaca paper does not pay it), but note that the Cboe indexes include T-bill yield, worth about 4 to 5% per year in 2024 to 2025.

## 5. The wheel

- **Mechanics:** sell CSPs until assigned, then sell covered calls until called away, then repeat.
- **Typical parameters:** 30-delta puts and 30-delta calls at 30 to 45 DTE. Only sell calls at or above cost basis (a common rule, but it creates stuck positions; the better rule is to sell 30-delta calls regardless, or on deep drawdowns sell calls at lower delta).
- **Edge harvested:** same as a CSP plus a covered call, so mostly long equity with a short-vol overlay.
- **Evidence:** no rigorous academic studies, but it is economically equivalent to a combination of PUT and BXMD-type exposures. Practitioner backtests on SPY (Lambda Finance, 2010 to 2025, 30-delta monthly) report about 9 to 11% per year with about 18% max DD vs 34% for buy-and-hold in 2020. Other amateur backtests show much worse results (about 1% CAGR), so results are fragile to rules. The wheel consistently trails buy-and-hold in strong bull markets.
- **Risks and tails:** the "sell calls above cost basis" rule leaves you holding a falling stock with no premium. Single-name idiosyncratic gaps (META -26% Feb 2022, NVDA -17% Jan 2025). Concentration is the big risk on small accounts.
- **$10k account:** not feasible on the universe. Sensible from about $50k, with at most 1 contract per name across 3 to 5 names.
- **Alpaca automation:** good. A state machine of CASH, SHORT_PUT, SHARES, SHARES_SHORT_CALL. Must reconcile assignment events the next morning on paper.
- **Backtest parameters:** universe IWM, AAPL, AMZN, NVDA, GOOGL (lowest prices); 30-delta, ~35 DTE, 50% TP / 21 DTE; calls at 30 delta irrespective of basis (variant A) vs at or above cost basis (variant B). Benchmark against equal-weight buy-and-hold.

## 6. Put credit spreads (bull put spreads)

- **Mechanics:** sell an OTM put and buy a further-OTM put at the same expiry. Max loss = width minus credit.
- **Typical parameters:** 30 to 45 DTE; short put at 0.20 to 0.30 delta; width $5 on SPY/QQQ (about 0.7 to 1% of spot), $2 to 5 on IWM, $5 to 10 on single names. Aim for a credit of about 1/4 to 1/3 of the width at 25 to 30 delta. TP 50%; exit at 21 DTE; stop at 2× credit (i.e., when the loss equals the credit received) or when the short strike is breached.
- **Edge harvested:** put skew overpricing on indexes. This is the most consistently overpriced part of the surface, and it is the core of the VRP.
- **Evidence:** not directly benchmarked by Cboe. PUT and WPUT give the evidence for the short leg. The long wing gives back part of the VRP (you buy an even more overpriced far-OTM put), so returns per unit of risk are lower than naked short puts on paper, but tail risk is capped. Option Alpha tested 45 DTE, 0.30-delta put spreads on SPY, GLD and TLT with and without a 200DMA filter, with a 50% profit target. Its numeric results were not retrievable (see section 15).
- **Risks and tails:** a gap through both strikes gives the full loss. Win rate is high (~70 to 85%), but a few max losses erase many wins, so expectancy is sensitive to slippage and stop rules. Early assignment on the short put when deep ITM near expiry (low risk for spreads; manage by closing).
- **$10k account:** **ideal.** About $350 to 450 risk per $5 spread, so 2 to 5 concurrent spreads.
- **Alpaca automation:** very good. A single mleg limit order to open and close; stops are polled by the bot.
- **Backtest parameter set:**
  - Underlyings SPY, QQQ, IWM.
  - Entry every trading day (or weekly on Mondays) when no position exists in that expiry, at the expiry nearest 45 DTE (35 to 50 allowed).
  - Short put nearest 0.25 delta (grid 0.16 / 0.20 / 0.25 / 0.30); width $5 (grid $2 / $5 / $10, or 1% of spot).
  - Exits: 50% TP (grid 25 / 50 / 75 / hold); time exit at 21 DTE (grid 14 / 21 / none); stop at loss = 1× / 2× credit / none.
  - Filters: SPY close > 200DMA (on/off); VIX < 30 (on/off); minimum credit at least 20% of width.

## 7. Call credit spreads (bear call spreads)

- **Mechanics:** sell an OTM call and buy a further-OTM call.
- **Typical parameters:** 30 to 45 DTE, short 0.16 to 0.25 delta, same management as put spreads.
- **Edge harvested:** small. Index call skew is flat or inverted, so OTM calls carry less VRP than puts, and the equity drift works against you. Historically the weakest leg of an index condor.
- **Evidence:** indirect. CNDR's call side adds income but loses in strong rallies. Tastytrade finds call-side-only strategies weaker than put-side on indexes.
- **Risks and tails:** melt-ups (Nov 2020, Nov 2024 post-election, the May 2025 rebound). Squeezes on single names (TSLA, NVDA).
- **$10k account:** fits like put spreads.
- **Alpaca automation:** very good.
- **Backtest parameters:** mainly as a conditional hedge or for condor construction. Standalone test: short 0.16-delta call, $5 wide, 45 DTE, 50% TP / 21 DTE, **only when price < 50DMA** (trend-following), to see whether a bearish filter rescues it. Expect it to be negative unfiltered in 2024 to 2026.

## 8. Iron condors

- **Mechanics:** put credit spread plus call credit spread at the same expiry. Max loss = larger width minus total credit (only one side can lose at expiry).
- **Typical parameters:**
  - Tastytrade-style: 45 DTE, short strikes at 16 delta (about 1 SD), wings 5 to 10 delta or fixed $5 to 10, TP 50%, exit 21 DTE.
  - CNDR-style: monthly, shorts at about 0.20 delta (older Cboe docs say 0.15), longs at 0.05 delta, held to expiry.
- **Edge harvested:** VRP on both sides, but mostly from the put side. Benefits from range-bound markets and vol mean-reversion after spikes.
- **Evidence:**
  - CNDR had the **lowest volatility of the six Cboe option-selling indexes (7.23%, 1986 to 2015)**, worst peak-to-trough about 19% vs 51% for the S&P, and 59% of months returned 0 to 2%.
  - But CNDR still had 10 months worse than -6% (vs 15 for the S&P), so tail months are not rare.
  - BFLY (ATM iron butterfly) had a much worse drawdown (-47.1%).
  - Tastytrade: high-IVR condors had return on risk of about 9.5% vs 5% in low IVR.
- **Risks and tails:** loses on large moves either way. Wings give a hard floor, but four legs cost more slippage. Vega spikes mark the position deep red even before strikes are touched, and a stop rule on marks gets triggered in exactly these moments (Aug 5 2024 is a good test).
- **$10k account:** good. A $5-wide SPY condor risks about $350 to 400; a $10-wide one about $700 to 800.
- **Alpaca automation:** very good (a 4-leg mleg). Watch fills: use mid-price limit orders with step-down.
- **Backtest parameter set:**
  - SPY, QQQ, IWM; ~45 DTE.
  - Short put and call at 16 delta (grid 10 / 16 / 20 / 25); wings at 5 delta or fixed $5 / $10.
  - TP 50% (grid 25 / 50); exit at 21 DTE; stop 2× credit (and a no-stop variant).
  - Filter variants: enter only if IV rank (computed from our own bars or VIX percentile) > 30; skip if VIX > 35.
  - Compare against a "CNDR clone": 30 DTE, 20/5 delta, hold to expiry.

## 9. Short strangles (and why we can't run them on Alpaca)

- **Mechanics:** sell an OTM put and an OTM call with no wings.
- **Typical parameters:** the tastytrade flagship: 45 DTE, 16 delta each side, 50% TP / 21 DTE.
- **Edge harvested:** the full VRP without paying for wings. The best Sharpe on paper of the premium strategies.
- **Evidence:** tastytrade's 16-delta SPY strangle studies (section 1.4). Also LJM's history shows the danger. Naked short index options are exactly what blew up in Feb 2018.
- **Margin:** Reg-T naked margin is roughly 20% of the underlying minus OTM amount plus premium (minimum ~10%). For SPY at $650, that is about $10k to 13k per strangle. Portfolio margin needs $100k+ and approval.
- **On Alpaca: not available** ("we do not support any uncovered short options strategies"). **Proxy:** a wide iron condor with wings far OTM (e.g., 3 to 5 delta) recovers most of the strangle's P/L while capping tails, and is margin-efficient under Alpaca's max-loss rule.
- **Backtest:** run the condor with 16-delta shorts and 3-delta wings as the "strangle proxy" and compare with the 5-delta version to measure the cost of protection.

## 10. Collars and protective puts

- **Mechanics:**
  - Protective put: long stock plus a long OTM put.
  - Collar: long stock, long OTM put, short OTM call (often zero-cost).
- **Typical parameters:** CLL: put 95% (quarterly), call 110% (monthly). Common retail version: 90 to 180 DTE 0.20 to 0.30-delta put financed with 30 to 45 DTE 0.20 to 0.30-delta calls.
- **Edge harvested:** none. You *pay* the VRP on the long put, partly recovered on the short call. This is insurance, not alpha.
- **Evidence:** PPUT had 18 months of -6% or worse vs 35 for the S&P (1986 to 2021), and beat other indexes by 10+ points in 2008 and 2020. Long-run returns are below the S&P (Cboe insights). CLL has beta below 0.75 and lags in bull markets.
- **Risks and tails:** low tail risk; the cost is chronic underperformance. Whipsaw if puts are rolled at bad times.
- **$10k account:** same share-ownership capital issue as covered calls.
- **Alpaca automation:** easy (single legs).
- **Backtest parameters:** only as an overlay variant for the wheel or covered-call book. Buy a 90-DTE 0.20-delta put on held shares, financed by the 30-delta monthly call. Measure drawdown reduction vs return give-up. Lower priority.

## 11. Long calls / LEAPS and the poor man's covered call (diagonal)

- **Mechanics:**
  - LEAPS: buy a deep-ITM (0.70 to 0.85 delta) call 6 to 24 months out as a stock substitute.
  - PMCC: the LEAPS plus a short 30 to 45 DTE OTM call (0.20 to 0.30 delta).
- **Typical parameters:**
  - Long call 0.75 to 0.85 delta, 180 to 400 DTE; roll the long leg when it falls under ~90 to 120 DTE.
  - Short call 0.25 to 0.30 delta, 30 to 45 DTE, at a strike above long strike + net debit. TP 50% / 21 DTE.
  - Tastytrade's SPY study compared 50-delta long calls at 60 to 300 DTE with short 30-delta ~30 DTE calls.
- **Edge harvested:** equity drift (leveraged) plus the short-call VRP. Deep-ITM LEAPS carry little extrinsic value, so you pay little VRP on the long leg.
- **Evidence:** thin. Tastytrade found the diagonal required about 1/6 of the capital of a covered call on a delta-equivalent basis. No rigorous long-horizon study found. Economically it is a leveraged covered call.
- **Risks and tails:** leverage. A 20% drop can cut the LEAPS value by 40 to 60%. Short-call assignment (you must then exercise or sell the LEAPS, a real operational risk on Alpaca). Vega loss in crashes is limited for deep ITM. A max loss of about the debit is still large.
- **$10k account:** **the best way to get equity-plus-VRP exposure at $10k.** One QQQ or IWM 0.80-delta 1-year call costs about $5k to 12k (check live).
- **Alpaca automation:** good. The short call is "covered" by the long call (a spread), so it fits level 3. Opening both together as a calendar/diagonal mleg is supported. Rolling the short leg separately may trip "uncovered leg" checks, so roll as a 2-leg mleg (close old short and open new short).
- **Backtest parameter set:** QQQ and SPY (also IWM). Long call at about 0.80 delta, nearest 300 to 400 DTE (only limited strikes and expiries exist in data, so pick the nearest available). Short 0.30-delta call ~35 DTE; TP 50% / 21 DTE; roll the long leg at 120 DTE. Optional rule: do not sell the short call when price < 50DMA (to recover upside after selloffs). Benchmark against a 0.8× leveraged long stock position with the same capital.

## 12. Calendar spreads

- **Mechanics:** sell a near-term option and buy a longer-dated option at the same strike (ATM or slightly OTM).
- **Typical parameters:** sell 21 to 30 DTE, buy 50 to 60 DTE, ATM. TP 10 to 25% of the debit; close before the front expiry. Best entered when the term structure is steep (front IV < back IV is the normal case; avoid backwardation). Earnings calendars sell the front month that includes the event.
- **Edge harvested:** theta differential, and a long-vega position that benefits from IV rising. Not a VRP harvest. It is short gamma and long vega, a relative-value trade on the term structure.
- **Evidence:** limited systematic evidence. Vendor studies show modest positive results in low-IV regimes and losses on big moves. Effectively a neutral strategy with no strong documented premium.
- **Risks and tails:** large moves in either direction (the max loss is the debit). Volatility collapse in the back month. Early assignment of the short leg.
- **$10k account:** cheap (debit of $200 to 800 on SPY).
- **Alpaca automation:** supported as mleg. **Data caveat:** pricing requires two expiries on the same day, and the back-month strike may have no trade on many days in daily bars, so the backtest will have many gaps.
- **Backtest:** lower priority. SPY ATM calendar, short 28 DTE / long 56 DTE, enter when VIX is in the 12 to 20 range and VIX < 3-month VIX (contango), TP 20%, stop at 30% of debit, exit at 7 DTE of the front leg.

## 13. Earnings strategies

### 13a. Pre-earnings long straddle (run-up capture)
- **Mechanics:** buy an ATM straddle a few days before the report. Either exit *before* the announcement (capturing the IV run-up and avoiding the crush) or hold through (betting realized move > implied).
- **Evidence:**
  - **Gao, Xing & Zhang (JFQA 2018), "Anticipating Uncertainty":** ATM straddles held from 3 days before the announcement to the announcement date earned **+3.34%** on average (highly significant), even though straddles generally lose. Effects are stronger for smaller, higher-volatility, less liquid firms, so **the effect is probably weaker for mega-caps.**
  - Dubinsky, Johannes, Kaeck & Seeger (RFS 2019) show that option-implied earnings uncertainty is large, time-varying and informative about subsequent volatility, i.e., the market prices the event in.
- **Parameters:** buy the ATM straddle in the nearest expiry after the event, 3 to 5 trading days before, and sell at the close on the day before the event.
- **Risks:** theta bleed if IV does not rise. Bid-ask on 2 legs × 2. Small per-trade edge.
- **$10k account:** affordable on most names (an AAPL straddle costs about $800 to 1,500; NVDA/META/TSLA more).
- **Automation:** needs an earnings calendar (Alpaca does not provide one; use a free source such as Nasdaq or Finnhub, or hard-code dates). Long-only, no margin issues.

### 13b. Post-earnings IV crush: short iron condor through the event
- **Mechanics:** at the close before the announcement, sell an iron condor in the first expiry after the event, with short strikes outside the implied move and wings for protection. Close at the next session (open or close).
- **Implied move:** about the ATM straddle price × 0.85, or the straddle / spot.
- **Edge harvested:** event volatility tends to be overpriced on average for mega-caps (a practitioner consensus; academic results are mixed and depend on firm size). After the report, IV collapses from, e.g., 80 to 120% to 30 to 40% for front-week options.
- **Evidence and tails:** the gap risk is real.
  - META Feb 3 2022: -26%. META Feb 2 2024: +20%.
  - NVDA has had multiple ±10 to 15% earnings moves.
  - TSLA ±10% is routine.

  With defined wings, the max loss per event is known. Expect win rates of about 60 to 75% and occasional full losses.
- **Parameters:** short strikes at 1.0× to 1.25× the implied move; wings $5 to 10 (or 2× the implied-move distance) beyond the shorts; credit at least 25% of width; exit at the next day's close (or open, using the daily bar open as an approximation); skip if the implied move is below the historical average move (no edge).
- **$10k account:** good with small widths, e.g., AAPL $5 wings risk about $350.
- **Automation:** feasible, but depends on timing. The announcement is after the close, so you must enter at 15:45 to 15:59 and exit the next morning. Alpaca has no extended hours for options, so pre-market reaction cannot be hedged.
- **Backtest parameter set:** AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, NFLX, AVGO, etc. About 10 quarters × 7 to 10 names ≈ 70 to 100 events. Enter at the close on day T-1 (for after-close reporters) or T-2 (for pre-market reporters), short 1.1× implied move, wings at 2× the implied-move distance. Exit at the T+1 close. Variants: exit at the T+1 open; strikes at 1.0× and 1.25×.

## 14. 0DTE SPY credit spreads and iron condors

- **Mechanics:** open same-day-expiry SPY (or SPX/XSP elsewhere) credit spreads or condors intraday and let them expire or close before 15:45.
- **Typical parameters:**
  - Shorts at 0.05 to 0.15 delta or 0.3 to 0.5% OTM, wings $1 to 5.
  - Entry 10:00 to 13:00 ET.
  - Stop at 1 to 2× credit or when the underlying touches the short strike.
  - Close by 15:30 to 15:45.
- **Edge harvested:** intraday VRP. Beckmeyer et al. find short 0DTE positions profitable after fees; Vilkov finds static rules weak and skew-driven.
- **Risks and tails:**
  - Gamma is extreme. A 1% move in an hour wipes out weeks of credits.
  - Macro event days (CPI, FOMC, NFP) and headline shocks (tariff days in April 2025).
  - Fills on wide spreads in fast markets.
  - Pin risk and assignment on SPY (physically settled; Alpaca liquidates if buying power is insufficient within the last hour).
- **$10k account:** cheap per trade, which makes over-trading easy.
- **Automation on Alpaca:** possible (SPY has daily expiries), but it **needs intraday data and intraday execution logic**, and must avoid the last hour because of Alpaca's liquidation process.
- **Backtest verdict:** **cannot be validated with daily bars.** A daily bar only gives open/high/low/close of the option contract (from trades), with no path information to evaluate stops or intraday entry times. A crude "open the condor at the 09:30 bar open, settle at the close" test is possible but heavily biased: the open price of a far-OTM 0DTE option is a single noisy trade. **Recommendation: defer until intraday option bars (e.g., 1-minute) are available, then test Vilkov-style conditional rules.**

## 15. Trend and momentum filters on premium selling

- **Idea:** sell puts or put spreads only when the underlying is above its 200DMA (or the 50DMA is above the 200DMA). When below, either stand aside, switch to call spreads, or reduce size.
- **Why it might work:** equity drawdowns cluster below the 200DMA (2008, 2022, 2000 to 2002), and short puts suffer most in prolonged declines. Volatility is also higher below the 200DMA, so the premium is larger but realized vol is larger still.
- **Evidence:** Option Alpha tested 45-DTE 30-delta put spreads (50% TP) with and without a 200DMA filter on SPY, GLD and TLT. Results were published in their podcast; the numeric results could not be retrieved for this report, so **treat the filter's benefit as unverified**. The broader evidence is from trend-following on equities (Faber's 10-month SMA): reduced drawdowns at the cost of whipsaws. **No peer-reviewed study specifically on 200DMA-filtered put selling was found.**
- **Caveat:** filters can also sit out the *best* selling opportunities. Just after a crash, IV is highest and the price is below the 200DMA (e.g., April to May 2025). This is the main trade-off to measure.
- **Backtest parameters:**
  - Filter A: SPY close > SMA200.
  - Filter B: close > SMA50 and SMA50 > SMA200.
  - Filter C: close > SMA200 **or** VIX > 30 and falling (i.e., VIX below its 5-day max).
  - Apply each filter to strategies 6, 8 and 4.

## 16. VIX-regime filters

- **Ideas:**
  1. Sell only when VIX is above about 15 to 18 (enough premium); reduce size or widen when VIX < 13.
  2. Stop opening new short-premium positions when VIX > 30 to 35 (crash regime), or reduce size and widen strikes.
  3. Term structure: sell only when VIX < VIX3M (contango); backwardation signals stress.
  4. IV rank or percentile > 30 to 50 (the tastytrade convention).
- **Evidence:**
  - Tastytrade: higher IVR improves return on risk (condor ROR about 9.5% vs 5%).
  - Bakshi & Kapadia: the VRP is larger in high-volatility periods. Selling after spikes is historically lucrative, but timing the first leg of a spike is what kills.
  - Term-structure studies: CXO found contango vs backwardation explained only about 1% of next-period variance in VIX-product returns, so the evidence is weak.
  - Short-vol ETP studies find low Sharpe (< 0.5) for static short-vol, and better results when the curve is in contango.
- **Practical rule (robust and simple):** do not open new positions when VIX > 35 or VIX > VIX3M. Size × 0.5 when VIX is 25 to 35. Prefer 16-delta over 30-delta when VIX < 14.
- **Backtest note:** VIX and VIX3M index levels are not in Alpaca stock bars. Proxies: VIXY/UVXY ETF price action (imperfect), or the realized vol of SPY plus our own ATM IV computed from SPY option bars. Alternatively, download free VIX/VIX3M history from Cboe as a CSV (one-time download, not live data).

---

## 17. Strategy comparison matrix

| Strategy | Main edge | Win rate (typ.) | Tail profile | Capital at $10k | Alpaca L3 | Automation | Daily-bar testability |
|---|---|---|---|---|---|---|---|
| Covered call (30-delta) | equity + small VRP | high | stock-like downside, capped upside | no | yes | easy | good |
| CSP (30-delta) | equity + VRP | ~75 to 85% | stock-like downside | no | yes | easy | good |
| Wheel | equity + VRP | high | stock-like, can get stuck | no (≥$50k) | yes | moderate | good |
| Put credit spread | index put VRP | ~70 to 85% | capped; gap = full loss | **yes** | yes | easy | **good** |
| Call credit spread | small | ~70 to 80% | capped; melt-up losses | yes | yes | easy | good |
| Iron condor | VRP both sides | ~65 to 80% | capped; two-sided | **yes** | yes | moderate | good (4 legs: data gaps) |
| Short strangle | full VRP | ~80% | **uncapped** | margin ~$10k+ each | **no** | n/a | n/a |
| Collar / protective put | none (insurance) | n/a | tail-protected | no | yes | easy | moderate (long-dated puts sparse) |
| LEAPS / PMCC | levered equity + VRP | moderate | leveraged downside | **yes** | yes | moderate | moderate (LEAPS sparse) |
| Calendar | term-structure theta | ~50 to 60% | debit at risk | yes | yes | moderate | weak (2 expiries) |
| Pre-earnings straddle | event IV run-up | ~50% | debit at risk, theta | yes | yes | needs calendar | moderate |
| Earnings IV-crush condor | event-vol overpricing | ~60 to 75% | capped; gaps | yes | yes | needs calendar | good (open/close) |
| 0DTE spreads/condors | intraday VRP | 60 to 85% | gamma explosions | yes | yes | needs intraday | **not testable** |

---

## 18. Backtest recommendations

### 18.1 Ranked parameter sets to test first

**B1. SPY/QQQ/IWM put credit spread: the baseline.** It is the cleanest VRP harvest, it fits every account size, and it has dense, liquid data.
```
underlyings: [SPY, QQQ, IWM]
entry: daily scan; one open position per underlying per expiry; target DTE 45 (allow 35-50)
short_delta: 0.25          # grid: 0.16, 0.20, 0.25, 0.30
width: 5                   # grid: SPY/QQQ 5,10 ; IWM 2,5 ; or 1% of spot
min_credit_pct_width: 0.20
take_profit: 0.50          # grid: 0.25, 0.50, 0.75, none
time_exit_dte: 21          # grid: 14, 21, none (hold to expiry, settle on underlying close)
stop_loss: 2.0x credit     # grid: 1.0x, 2.0x, none  (loss multiple of credit received)
filters: [none, close>SMA200, VIX<30 (or proxy), both]
sizing: max loss per trade 3% of equity; max total risk 40%
costs: slippage $0.01-0.02 per leg per side + $0.03/contract fees
```

**B2. Iron condor (tastytrade-style vs CNDR clone).** It tests whether the call side adds or subtracts, and how much management helps vs CNDR's passive hold.
```
underlyings: [SPY, QQQ, IWM]
dte: 45; short_delta both sides: 0.16 (grid 0.10, 0.16, 0.20); wings: 0.05-delta or fixed $5/$10
take_profit 0.50; time_exit 21 DTE; stop 2x credit / none
variant "CNDR": dte 30, short 0.20 / long 0.05, hold to expiry
variant "strangle proxy": wings at 0.03 delta
filter variants: IV-rank(own ATM IV, 1y lookback) > 30; skip if VIX>35
```

**B3. Cash-secured put and wheel (accounts of $50k or more).** Closest to PUT and BXMD, the longest-validated premiums. It also shows the assignment and roll mechanics the bot will need.
```
underlyings: [IWM, AAPL, AMZN, NVDA, GOOGL] (and SPY only if equity >= 100k)
put: 0.30 delta, ~35 DTE, TP 50%, exit 21 DTE (re-sell), accept assignment
call (after assignment): 0.30 delta, ~35 DTE, TP 50%, exit 21 DTE
variant A: call regardless of cost basis; variant B: strike >= cost basis
filter variant: no new puts when close < SMA200
benchmark: equal-weight buy & hold, same names
```

**B4. Earnings IV-crush iron condor.** A different return driver, low correlation with B1 and B2 except in crashes. It has a decent event count, and daily open/close bars capture the key prices.
```
names: AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA (+NFLX, AVGO, AMD if liquid)
implied_move = ATM straddle (first post-event expiry) mid at entry close
short strikes = spot +/- 1.1 x implied_move   # grid 1.0, 1.1, 1.25
wings = short strike +/- 1.0 x implied_move (rounded to listed strikes)
entry: close of last session before the announcement; exit: next session close (variant: open)
skip if credit < 25% of width
```
Requires a hand-curated earnings date table (date plus BMO/AMC flag) for 2024-02 to 2026-10.

**B5. PMCC on QQQ/SPY.** The only equity-plus-VRP structure that fits a $10k account. It tests whether short calls help or hurt a leveraged long in a bull market.
```
long: call nearest 0.80 delta, nearest expiry >= 300 DTE (use what exists); roll at 120 DTE
short: 0.30 delta, ~35 DTE, TP 50%, exit/roll 21 DTE; skip short call when close < SMA50
benchmark: long LEAPS alone (no short calls); long shares with same delta-dollars
```

**B6 (optional). Pre-earnings straddle run-up.** Same names as B4. Buy the ATM straddle at the T-5 close (grid T-3, T-5, T-7) and sell at the T-1 close (before the event). It is long volatility, so it partly hedges B1/B2/B4.

**B7 (optional). Covered call (BXMD clone) on SPY/QQQ.** A sanity check: our engine's results should roughly track BXMD/BXM's 2024 to 2025 calendar returns (BXMD 23.8% / 12.1%, BXM 20.1% / 8.9%). Large discrepancies point to pricing or data bugs. **This is a very valuable engine validation test.** Do the same with a PUT-like ATM monthly put on SPY vs PUT's 17.8% (2024) and 9.2% (2025). Note that SPY is not SPX and that the indexes include T-bill income.

### 18.2 Metrics to report for every run
- CAGR on allocated capital and on max-risk capital; total P/L; P/L per day in trade.
- Max drawdown (mark-to-market daily, not just realized), worst month, worst single trade, and P/L in the three stress windows: **Aug 1 to 9 2024, Jan 27 to 31 2025, Feb 19 to Apr 30 2025**.
- Sharpe and Sortino on daily equity; win rate; average win / average loss; expectancy per trade; profit factor.
- Number of trades (flag anything with fewer than 30 independent trades as anecdotal).
- Fraction of trades where pricing used a stale or missing bar (data-quality metric).
- Correlation of daily P/L across strategies (to judge diversification).

### 18.3 What a 2.7-year daily-bar backtest can show
- Whether the **engine, pricing and rules are correct**, via sanity checks against BXM/BXMD/PUT 2024 to 2025 returns.
- **Relative ranking of management rules** (50% TP vs hold, 21 DTE exit vs hold, stop vs no stop), since these effects show up within many short cycles.
- **Execution realism**: how often strikes have no trades, realistic credit at target delta, and the impact of slippage assumptions.
- **Behavior through three sharp, V-shaped vol spikes** (Aug 2024, Jan 2025, Apr 2025).
- **Capital usage and sizing** on a $10k vs $100k account.

### 18.4 What it cannot show
1. **Regime coverage:** 2024 to 2026 was mostly a bull market with short, sharp shocks. There was no 2008-style crash, no 2022-style grinding bear, and no 2018 volatility-ETP event. Short-premium strategies will probably look better than their long-run expectation. Trend filters will look useless because they never had a long bear market to avoid. **Do not conclude that a filter is worthless from this sample.** Use the Cboe index histories (PUT, BXM, CNDR, CLL daily values are free on Cboe dashboards) as a long-run reality check.
2. **Statistical power:** a 45-DTE strategy managed at 21 DTE produces roughly 30 to 60 cycles per underlying in 2.7 years, and SPY/QQQ/IWM/mega-caps are highly correlated, so effective independent samples are maybe 30 to 60 total. A Sharpe estimate from 2.7 years has a standard error of about ±0.6. **A large parameter grid will overfit.** Use a split (e.g., tune on 2024-02 to 2025-06, validate on 2025-07 to 2026-10) and prefer parameter plateaus over peaks.
3. **Intraday path:** daily bars cannot evaluate intraday stops, gap-through-stop slippage, the intraday low of a spread's mark, or 0DTE. Stops evaluated on closes understate both the frequency of stop-outs and their slippage on panic days. The close of an option bar is the *last trade*, which may be hours stale and not synchronized with the underlying close.
4. **Bid/ask:** Alpaca bars are trade-based OHLCV with no historical quotes. Mid-price is unobservable. Model fills as close ± a spread haircut (e.g., ±$0.01 to 0.02 for SPY near-the-money, ±3 to 5% of price for OTM single-name wings) and run a stress variant with double slippage. A strategy that only works at zero slippage is not real.
5. **Missing data:** far-OTM wings, LEAPS and back-month calendar legs often have no trades for days. Using "last known price" creates stale marks, so prefer recomputing a model price from IV interpolated off neighboring strikes (e.g., Black-Scholes with a smile fit from liquid strikes on the same day) and flag those trades.
6. **Greeks and IV:** these are not in the bars; they must be computed (Black-Scholes/BAW with dividends for American options). Delta targeting will be noisy. Errors of ±0.03 delta are typical.
7. **Assignment:** early assignment (especially short calls before SPY/AAPL/MSFT ex-dividend dates, and deep-ITM puts) must be modeled explicitly. The simplest conservative rule: assume assignment if a short call is ITM and its extrinsic value is less than the next dividend on the day before ex-date.
8. **Paper vs live:** Alpaca paper fills at quoted prices with no queue or partial fills, and paper assignment handling differs from live (activities appear the next day). Live slippage on multi-leg orders will be worse.
9. **Cash yield:** the Cboe indexes earn T-bill yield on collateral (about 4 to 5% per year in 2024 to 2025). Alpaca paper does not pay it. Report results with and without an assumed cash yield so comparisons with the indexes are fair.

---

## 19. Automation design notes for Alpaca
- Use `order_class: "mleg"` for all spreads, condors and diagonals: open and close as a unit, with limit at the mid, stepping toward the natural price every 30 to 60 s, giving up after N attempts. Avoid market orders on multi-leg positions.
- Roll = one mleg order that closes the old position and opens the new one (avoids the "uncovered leg" rejection that has been reported when closing one leg of a spread).
- The bot implements stops by polling position marks (stop orders are single-leg only). Apply stops only on confirmed marks, e.g., two consecutive checks 5 minutes apart, to avoid wick-outs.
- Close everything that is short and near the money by about 15:00 ET on expiry day. Never let short legs expire ITM (Alpaca exercises/assigns and may liquidate if buying power is insufficient).
- Reconcile every morning: positions, assignment and exercise activities (`/v2/account/activities` NTAs), and buying power.
- Maintain an earnings calendar and an ex-dividend calendar (neither is provided by Alpaca's trading API).
- Kill switches: no new entries if equity drawdown > 15% from peak, if VIX (or proxy) > 35, or on FOMC/CPI days for short-DTE trades.

---

## 20. Sources

**Cboe benchmark indexes and studies**
- Cboe PUT Index factsheet (as of 2026-09-30): https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_PUT-Index.pdf
- Cboe BXM Index factsheet: https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf
- Cboe BXMD Index factsheet: https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXMD-Index.pdf
- Cboe BXMW Index factsheet: https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXMW-Index.pdf
- Cboe, "Benchmark Indices Series: Income Generation and Smoother Returns with BXM, BXMD, PUT and CMBO": https://www.cboe.com/insights/posts/benchmark-indices-series-income-generation-and-smoother-returns-with-cboes-bxm-bxmd-put-and-cmbo-indices
- Cboe, "Benchmark Indices Series: Volatility Management with BFLY and CNDR": https://www.cboe.com/insights/posts/benchmark-indices-series-volatility-management-with-cboes-bfly-and-cndr-indices/
- Cboe, "Benchmark Indices Series: Hedging Downside Exposure with PPUT, CLL and CLLZ": https://www.cboe.com/insights/posts/benchmark-indices-series-hedging-downside-exposure-with-pput-cll-and-cllz-indices
- Cboe press release, Black & Szado (2016), "Performance Analysis of CBOE S&P 500 Options-Selling Indices": https://s202.q4cdn.com/174824971/files/doc_news/2016/02/study-analyzes-performance-of-cboe-spx-options-selling-indexes-pdf1691637391968.pdf
- Cboe IR, same study: https://ir.cboe.com/news/news-details/2016/Study-Analyzes-Performance-of-CBOE-SP-500-SPX-Options-Selling-Indexes-02-23-2016/default.aspx
- Cboe IR, Bondarenko (2016), "An Analysis of Index Option Writing with Monthly and Weekly Rollover": https://ir.cboe.com/news/news-details/2016/New-Study-On-Weekly-Monthly-SP-500-PutWrite-Indexes-Released-01-27-2016/default.aspx
- Indexology blog on Bondarenko PUT/WPUT: https://www.indexologyblog.com/2016/02/18/paper-by-professor-bondarenko-has-intriguing-new-analysis-of-put-and-wput-indexes/
- Cboe 2015 release introducing CNDR/BFLY etc.: https://ir.cboe.com/news/news-details/2015/CBOE-Introduces-10-Options-Based-Strategy-Performance-Benchmark-Indexes-07-29-2015/default.aspx
- CLL dashboard: https://www.cboe.com/us/indices/dashboard/CLL/

**Academic**
- Bakshi & Kapadia (2003), "Delta-Hedged Gains and the Negative Market Volatility Risk Premium," RFS 16(2): https://ideas.repec.org/a/oup/rfinst/v16y2003i2p527-566.html
- Carr & Wu (2009), "Variance Risk Premia," RFS: https://engineering.nyu.edu/sites/default/files/2019-01/CarrReviewofFinStudiesMarch2009-a.pdf
- Coval & Shumway (2001), "Expected Option Returns," JF: https://deepblue.lib.umich.edu/items/455dbe73-b56b-44f3-841f-c7751deffc7a/full
- Israelov & Nielsen (2015), "Covered Calls Uncovered," FAJ 71(6): https://rpc.cfainstitute.org/research/financial-analysts-journal/2015/covered-calls-uncovered and https://www.aqr.com/Insights/Research/Journal-Article/Covered-Calls-Uncovered
- Israelov & Nielsen (2014), "Covered Call Strategies: One Fact and Eight Myths," FAJ: https://rpc.cfainstitute.org/research/financial-analysts-journal/2014/covered-call-strategies-one-fact-and-eight-myths
- Gao, Xing & Zhang (2018), "Anticipating Uncertainty: Straddles around Earnings Announcements," JFQA 53(6): https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/anticipating-uncertainty-straddles-around-earnings-announcements/7B34877AD5E06304BA3C55FBA3219FDD ; summary: https://alphaarchitect.com/straddle-earnings-announcements-and-win/
- Dubinsky, Johannes, Kaeck & Seeger (2019), "Option Pricing of Earnings Announcement Risks," RFS 32(2): https://ideas.repec.org/a/oup/rfinst/v32y2019i2p646-687..html
- Beckmeyer, Branger & Gayda (2023), "Retail Traders Love 0DTE Options... But Should They?" SSRN 4404704: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4404704 ; coverage: https://www.bnnbloomberg.ca/day-traders-lose-358-000-per-day-gambling-on-zero-day-options-1.1910634
- Vilkov (2024), "0DTE Trading Rules" (summaries): https://hulltactical.com/?p=1919 , https://cxoadvisory.com/equity-options/0dte-strategy-outcomes
- CFA Institute, "Volmageddon and the Failure of Short Volatility Products" (FAJ 2021): https://rpc.cfainstitute.org/research/financial-analysts-journal/2021/volmageddon-failure-short-volatility-products

**Practitioner and vendor research**
- tastylive, entry/exit best practices (45 DTE / 50% / 21 DTE): https://www.tastylive.com/shows/best-practices/episodes/trade-entry-and-exit-07-08-2019
- tastylive, capital allocation and 21 DTE exits: https://www.tastylive.com/news-insights/How-Capital-Allocation-Impacts-Volatility
- tastylive, managing strangles: https://tastylive.com/shows/market-measures/episodes/managing-strangles-performance-08-05-2016 , https://tastylive.com/shows/options-jive/episodes/why-manage-at-50-not-25-for-short-strangles-09-13-2016
- tastylive, return on risk iron condors by IVR: https://www.tastylive.com/shows/market-measures/episodes/return-on-risk-iron-condors-11-07-2016
- tastylive, scaling deltas in high IV: https://www.tastylive.com/shows/market-measures/episodes/scaling-deltas-in-high-iv-09-28-2018
- tastylive, bullish diagonals / PMCC: https://tastylive.com/shows/market-measures/episodes/bullish-diagonals-the-poor-mans-covered-call-03-29-2016
- Option Alpha, trend filter on put spreads (podcast 226): https://optionalpha.com/podcast/trend-trading
- Option Alpha, 0DTE strategies: https://optionalpha.com/learn/top-0dte-options-strategies
- Lambda Finance, SPY wheel backtest (secondary, unverified): https://www.lambdafin.com/articles/wheel-strategy-spy-backtest
- QuantConnect, automating the wheel: https://www.quantconnect.com/research/17871/automating-the-wheel-strategy/
- CXO Advisory, VIX term structure: https://cxoadvisory.com/?p=20552
- LJM autopsy: https://hedgefundalpha.com/news/ljm-the-autopsy

**Alpaca**
- Options Level 3 trading (mleg, universal spread rule margin): https://docs.alpaca.markets/docs/options-level-3-trading
- Options trading overview (levels, order types, expiry handling): https://docs.alpaca.markets/docs/options-trading
- Unsupported strategies (no uncovered shorts): https://alpaca.markets/support/are-there-any-level-3-options-strategies-that-are-not-supported
- Expiration handling for long options: https://alpaca.markets/support/why-was-my-long-option-position-liquidated-or-short-option-position-closed-out-by-alpaca-on-expiration
- Non-trade activities (assignment/exercise): https://docs.alpaca.markets/docs/non-trade-activities-for-option-events
- Historical option data (since Feb 2024): https://docs.alpaca.markets/docs/historical-option-data
- Level 3 launch announcement: https://alpaca.markets/blog/level-3-options-trading-now-available-with-alpacas-trading-api/

*Notes on source quality:* the Cboe factsheets and the peer-reviewed papers are primary sources. Tastytrade and Option Alpha are vendors with a commercial interest in premium selling. Wheel backtests from blogs are unverified. Figures for CNDR returns and CLL/PPUT annualized returns were not available as text (they appear only in Cboe chart images). Download index histories from the Cboe dashboards to compute them directly. Price levels in section 2 are approximate and should be refreshed from live data. Academic articles were located via web search; full-text access was not resolved through LibKey in this session.
