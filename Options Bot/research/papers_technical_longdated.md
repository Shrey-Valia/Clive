# Literature Review: Technical Entry Timing for Longer-Dated Options (7 DTE to LEAPS) on Large Caps and Index ETFs

Scope: research papers on (1) option return predictability, (2) technical/trend/reversal signals in the underlying, (3) papers that combine the two, and (4) maturity choice (weekly vs 30-60 DTE vs LEAPS). The trader is a retail Alpaca level-3 account: long options, covered calls, cash-secured puts, defined-risk spreads, no naked shorts. The backtester runs on daily bars.

Written 2026-10-21. Every citation was found via web search. None is LibKey-resolved. Where I could not read a paper's tables directly, the number is marked **[secondary]** (taken from the abstract, an author-hosted summary, CXO Advisory, Quantpedia or the CFA digest) or **[verify]** (from memory of the paper, not re-confirmed this session). Treat [verify] numbers as approximate.

---

## 0. Bottom line

1. **The variance risk premium (VRP) is real and large. It is concentrated at short maturities and in the next 1-3 months, and is close to zero beyond about a quarter.** Coval & Shumway (2001): zero-beta ATM S&P straddles lose about 3% per week. Dew-Becker, Giglio, Le & Rodriguez (2017): over 1996-2014, hedging news about future variance at horizons from one quarter to 14 years cost nothing on average. Only short-horizon, transitory variance carries a price. Israelov & Tummala (2017): for a given moneyness, shorter maturities mostly earn more per unit of exposure. Shorting LEAPS therefore harvests almost no VRP. Buying LEAPS is the cheapest way to hold option convexity.
2. **The best-documented technical edge for index ETFs since 2000 is short-horizon mean reversion, not trend.** Baltussen, van Bekkum & Da (2019) show that daily index autocorrelation flipped from positive (about +0.04) to negative (about -0.04) across 20 indexes as index products grew. This is the academic basis for RSI(2)/IBS dip entries on SPY/QQQ. Classic moving-average and trading-range rules (Brock, Lakonishok & LeBaron 1992) did not survive data-snooping correction or the next decade out of sample (Sullivan, Timmermann & White 1999). Zakamulin finds no statistically significant moving-average outperformance after about 1932 once data snooping is accounted for **[secondary]**.
3. **Timing the VRP with volatility-state variables works better than timing it with price-trend variables.** The VIX term-structure slope (Johnson 2017), the ex-ante VRP or IV-RV gap (Bollerslev, Tauchen & Zhou 2009; Goyal & Saretto 2009) and post-drop risk-aversion spikes (Aït-Sahalia, Karaman & Mancini 2020) all have peer-reviewed evidence. Trend filters (200DMA, 50/200 cross) have mostly practitioner-grade evidence: one NAAIM award paper, gross of costs, single sample.
4. **Cheng (2019) is a caution for "sell when fear is high" rules.** The ex-ante VIX-futures premium falls or stays flat when risk rises. So a VIX-level or backwardation gate can screen out bad days, but it does not reliably pick out good ones. This is consistent with our finding that the gates did not help.
5. **Covered calls embed an uncompensated equity-reversal bet** (Israelov & Nielsen 2015): short volatility has a Sharpe near 1.0 but supplies under 10% of the risk. Passive equity supplies most of the risk, and the reversal component supplies about a quarter of the risk of an ATM covered call for little return. This bears directly on the QQQ wheel.
6. **Where you enter in the day and the week matters for a seller.** Muravyev & Ni (2020): S&P delta-hedged option returns average -0.7% per day, split into -1.0% close-to-open and +0.3% open-to-close. Jones & Shemesh (2018): option returns are much lower over weekends and other non-trading periods. Sellers earn their premium overnight and over weekends.

**Verdict on our finalists:** the literature broadly **supports** the RSI(2)/IBS dip put-credit-spread mechanism, the 60 DTE index condor and the null regime-gate result. It is **cautious** on the QQQ wheel as built. All three results rest on a short 2.6-year sample, which is the main risk (Section 7).

---

## 1. Option returns and predictability

### 1.1 Coval & Shumway (2001), "Expected Option Returns," *Journal of Finance*
- **Question:** Are option returns consistent with CAPM-style pricing, and is volatility risk priced?
- **Data:** S&P 100 and S&P 500 index options, late 1980s to mid-1990s.
- **Method:** Returns on calls and puts by strike, plus beta-neutral ATM straddles.
- **Key numbers:** Zero-beta ATM straddles lose about **3% per week** (a replication reports -3.15%). OTM puts earn returns more negative than CAPM implies.
- **Limitations:** Early sample. Weekly returns on short-dated options are dominated by bid-ask costs. Index options only.
- **Relevance:** This is the original evidence that systematically selling index volatility earns a premium. The premium is a weekly-horizon phenomenon.

### 1.2 Goyal & Saretto (2009), "Cross-Section of Option Returns and Volatility," *JFE*
- **Question:** Does the gap between historical realized volatility (HV) and ATM implied volatility (IV) predict option returns?
- **Data:** OptionMetrics US equity options, 1996-2006 **[verify]**.
- **Method:** Monthly decile sorts on HV−IV. Long the high-(HV−IV) decile, short the low decile, using straddles and delta-hedged calls held to expiry.
- **Key numbers:** The long-short is economically and statistically significant. The straddle spread is commonly cited at about **22.7% per month** **[verify; not confirmed this session]**. The result is robust to market conditions, firm risk characteristics, industry and option liquidity, and is not explained by factor models.
- **Limitations:** Single-name and cross-sectional. Wide spreads on single-name options absorb a large share of the gross return. The test is at monthly horizon.
- **Relevance:** **IV well above recent RV is the most-replicated "cheap/rich" signal** (it is also one of the four factors in Horenstein et al.). It translates directly into a time-series filter for index trades: only sell when IV − RV is high.

### 1.3 Cao & Han (2013), "Cross Section of Option Returns and Idiosyncratic Stock Volatility," *JFE*
- **Question:** Does idiosyncratic volatility (IVOL) of the underlying predict delta-hedged option returns?
- **Data:** US equity options, 1996-2010s.
- **Method:** Monthly sorts on IVOL, using delta-hedged calls and puts.
- **Key numbers:** Delta-hedged returns **decrease monotonically in IVOL**. Limits-to-arbitrage controls weaken the relation by about 40%. The authors' explanation is that dealers charge more for options that are costly to hedge.
- **Limitations:** Single names only. The effect is partly a friction premium.
- **Relevance:** For mega caps, IVOL is low, so the premium for selling is smaller than in the cross-section. Do not expect single-name premium selling on AAPL or MSFT to beat SPY by much.

### 1.4 Bali & Murray (2013), "Does Risk-Neutral Skewness Predict the Cross-Section of Equity Option Portfolio Returns?," *JFQA*
- **Method:** Constructs "skewness assets" (long one option, short another, delta- and vega-neutral) and sorts on risk-neutral skewness.
- **Key numbers:** There is a **strong negative relation** between risk-neutral skewness and the returns of the skewness assets, consistent with investors paying up for positive skew.
- **Relevance:** Selling OTM calls on names with high positive skew is compensated. This supports the low-delta covered-call leg of a wheel. It is not about timing.

### 1.5 Christoffersen, Goyenko, Jacobs & Karoui (2018), "Illiquidity Premia in the Equity Options Market," *RFS*
- **Key numbers:** Illiquid options earn higher risk-adjusted returns than liquid ones: **3.4% per day for ATM calls and 2.5% for ATM puts** (on the option-return scale). The premium compensates market makers for net long inventory.
- **Relevance:** Much of the cross-sectional option "alpha" is a liquidity premium that a retail trader pays rather than collects. Stick to SPY/QQQ and the most liquid mega caps, and model fills at the mid minus a fraction of the spread, not at the mid.

### 1.6 Muravyev & Ni (2020), "Why Do Option Returns Change Sign from Day to Night?," *JFE*
- **Data:** S&P 500 index options and equity options, intraday quotes.
- **Key numbers:** Delta-hedged S&P option returns average **-0.7% per day**, made up of **-1.0% close-to-open and +0.3% open-to-close**. The pattern holds across all maturities and moneyness and for equity options.
- **Explanation:** Option prices do not reflect that stock volatility is much higher intraday than overnight.
- **Relevance (daily bars):** A seller should **open at or near the close and close at or near the open**, never the reverse. In our daily backtester, check whether fills are modelled at the close or the next open. Changing that choice alone can move results materially.

### 1.7 Jones & Shemesh (2018), "Option Mispricing around Nontrading Periods," *JF*
- **Key numbers:** Option returns are significantly lower over non-trading periods, mostly weekends. The authors attribute this to persistent mispricing (variance treated incorrectly over market closures), not to risk.
- **Relevance:** Short-premium trades opened on Thursday or Friday capture the weekend. A 7-14 DTE trade spans one or two weekends, so the weekday of entry is a legitimate variable to test.

### 1.8 Zhan, Han, Cao & Tong (2022), "Option Return Predictability," *RFS*
- **Data:** US equity options, 1996-2010s.
- **Method:** About 10 stock characteristics, with returns on writing delta-hedged calls and puts.
- **Key numbers:** Expected returns to writing delta-hedged options are negatively related to stock price, profit margin and profitability (and related to other characteristics). Long-short option portfolios reach **annual Sharpe ratios above 2** and remain profitable after transaction costs. Two option factors explain the profits. Equity factors explain none of them.
- **Limitations:** Cross-sectional, single name. The Sharpe ratios are for diversified, delta-hedged long-short books, which a level-3 account cannot run.
- **Relevance:** Option predictability is not inherited mechanically from stock-return predictability. Price-based technical signals on the stock should therefore *not* be expected to forecast option richness directly. They time direction, not premium.

### 1.9 Horenstein, Vasquez & Xiao, "Common Factors in Equity Option Returns," *RFS* (2026, vol. 39(3))
- **Method:** Four-factor model for delta-hedged equity option returns: size, idiosyncratic volatility, the **IV−HV gap**, and a market volatility factor (the delta-hedged S&P 500 option return).
- **Relevance:** This confirms that the IV−HV gap and the market VRP are the priced dimensions. Technical price signals are not among them.

### 1.10 Heston, Jones, Khorram, Li & Mo (2023), "Option Momentum," *JF*
- **Data:** ATM straddles on US stocks, 1996-2019 **[verify end year]**.
- **Method:** Cross-sectional sorts on past straddle returns, formation windows of 6 to 36 months.
- **Key numbers:** Past winners keep outperforming past losers at 6-36 month formation horizons, with **no long-run reversal**. One-month option returns tend to **reverse** the following month (less robust). Trading costs are unrelated to the size of momentum profits. The result is robust to OTM options and delta-hedging.
- **Limitations:** Cross-sectional, so it needs a broad universe and long option history. Alpaca's option history is short.
- **Relevance:** With a small universe of mega caps you could test "sell premium on the names whose straddles lost the most over the past 12 months." This is low priority because of data limits.

---

## 2. Technical analysis in the underlying

### 2.1 Brock, Lakonishok & LeBaron (1992), *JF*
- **Data:** DJIA daily, 1897-1986. **Method:** 26 moving-average and trading-range-break rules, with bootstrap null models (random walk, AR(1), GARCH-M, EGARCH).
- **Finding:** Buy-signal returns exceed sell-signal returns and are less volatile. Sell-signal returns are negative. The results are inconsistent with all four null models.
- **Limitation:** Superseded by the two decay findings below.

### 2.2 Sullivan, Timmermann & White (1999), *JF*
- **Method:** White's Reality Check over a universe of about 8,000 rules, applied to 100 years of DJIA data.
- **Finding:** The best rule is significant in the BLL sample. **Out of sample (1987-1996) it earned 8.63% per year, which was not statistically significant** **[secondary]**.
- **Lesson:** This is the template for how much in-sample technical edge evaporates.

### 2.3 Park & Irwin (2007), *Journal of Economic Surveys*
- A survey of about 95 "modern" studies. Most report profits, but many suffer from data snooping, ex-post rule selection and ignored costs. Profitability is concentrated in pre-1990s data and in futures and FX markets more than in US stocks.

### 2.4 Han, Yang & Zhou (2013), "A New Anomaly: The Cross-Sectional Profitability of Technical Analysis," *JFQA*
- **Data:** US stocks, 1963-2009, sorted into volatility deciles. **Method:** 10-day moving average (20, 50, 100 and 200 also tested) switching between a decile portfolio and T-bills.
- **Key numbers:** MA-timing excess returns rise with volatility decile, about **8.4% to 18.7% per year** **[secondary]**. Alphas for high-volatility portfolios exceed momentum alphas.
- **Limitation:** The effect lives in high-volatility small stocks, not in SPY, QQQ or mega caps. It overlaps with short-term reversal and liquidity effects. Implementation costs are large.

### 2.5 Zakamulin (2014-2017; book *Market Timing with Moving Averages*, Palgrave 2017)
- **Data:** S&P Composite from 1860, plus other indexes. **Method:** About 300 MA weighting schemes, out-of-sample selection, 0.25-0.5% one-way costs.
- **Finding:** MA timing raises the Sharpe ratio modestly (about **7-12% Sharpe improvement net, 1930-2012** **[secondary]**). The gain comes mostly from avoiding deep bear markets, with **no statistically significant outperformance post-1932** once snooping is accounted for **[secondary]**. A 7-10 month lookback is about optimal.
- **Relevance:** A 200DMA filter is a *crash-avoidance* tool. In a 2.6-year sample with no prolonged bear market it should not be expected to add anything. In 2007-2026 its value depends on 2008 and 2022, where it helps a long-delta book but whipsaws a premium seller (who loses most in V-shaped crashes that a 200DMA catches late).

### 2.6 Moskowitz, Ooi & Pedersen (2012), "Time Series Momentum," *JFE*
- **Data:** 58 liquid futures (equity indexes, FX, commodities, bonds), 1965/1985-2009.
- **Finding:** The 12-month TSMOM return is positive for **every one of the 58 contracts**. It persists for about 12 months and partly reverses afterwards. A diversified TSMOM portfolio has a high Sharpe (about 1.0+ gross at 40% vol-scaled, diversified **[verify]**) and does best in extreme markets.
- **Limitation:** The power comes from diversification across 58 assets. A single-asset equity-index TSMOM signal is much weaker (Sharpe gains of about 0.1-0.3). Hurst, Ooi & Pedersen (2017) extend this to a century of data.
- **Relevance:** TSMOM is a *direction* signal at monthly frequency. It suits choosing between bullish and bearish structures at 30-90 DTE better than daily entry timing.

### 2.7 Short-term reversal: Jegadeesh (1990, *JF*), Lehmann (1990, *QJE*)
- Weekly and monthly single-stock returns reverse (about 1.7-2.5% per month for decile spreads in older data). The effect is largely a liquidity-provision premium and is strongest in small, illiquid names.
- At the index level the post-2000 analogue is Baltussen et al.

### 2.8 Baltussen, van Bekkum & Da (2019), "Indexing and Stock Market Serial Dependence Around the World," *JFE*
- **Data:** 20 major indexes in 15 countries, daily, from the early 1950s to 2010s.
- **Finding:** Daily (and multi-day) index return autocorrelation was **positive until the 1990s and turned negative in the 2000s**: aggregated 1-day lag about **+0.038 before index futures and -0.040 in the last five years** (working-paper figures **[secondary]**). The flip is tied to growth in futures, ETFs and index funds, partly through arbitrage between index products and their constituents. Index-level mean-reversion strategies became profitable after 2000.
- **Limitation:** Autocorrelations of ±0.04 are small. A trading strategy needs leverage or a nonlinear entry, such as extreme RSI(2) readings.
- **Relevance:** **This is the strongest academic support for our RSI(2)<10 and IBS<0.2 dip entry on SPY/QQQ.** The structural cause (indexing) is still in place, so post-publication decay is less of a concern than for anomalies driven purely by mispricing.

### 2.9 Post-publication decay and multiple testing
- **McLean & Pontiff (2016), *JF*:** across 97 predictors, returns are about **26% lower out of sample and 58% lower after publication**.
- **Harvey, Liu & Zhu (2016), *RFS*:** a new factor needs **t > 3.0**, not 2.0.
- **White (2000) Reality Check and Hansen (2005) SPA test:** bootstrap tests of the best rule against the full universe of rules tried.
- **Bailey & López de Prado (2014):** the deflated Sharpe ratio adjusts for the number of trials and for non-normal returns.

---

## 3. Papers that combine trend or technical signals with options

### 3.1 Israelov & Nielsen (2015), "Covered Calls Uncovered," *FAJ*
- **Data:** BXM-style one-month ATM and 2% OTM S&P 500 covered calls, about 1996-2014 **[verify]**.
- **Method:** Splits returns into passive equity, equity "timing" (the short call's delta shrinks as the market rallies and grows as it falls, which amounts to an implicit **reversal** bet) and short volatility.
- **Key numbers:** The short-volatility component has a **Sharpe near 1.0, supplies under 10% of the risk and earns about 2% per year**. The equity-reversal component supplies **about one quarter of the risk of an ATM covered call for almost no return**. A delta-hedged, "risk-managed" covered call has a higher Sharpe, lower volatility and lower downside beta.
- **Relevance:** In a QQQ wheel, the trouble is the uncompensated reversal exposure. Strong QQQ trends (2023-2025) are exactly where a covered call gives up upside (calls assigned) and a put-write catches the falling knife. A **low-delta call** reduces the reversal exposure, which is directionally the fix Israelov & Nielsen recommend. Delta-neutralizing the equity leg is a cleaner version.

### 3.2 Israelov & Klein (2016), "Risk and Return of Equity Index Collar Strategies," *Journal of Alternative Investments*
- Collars earn less than the index, mostly because they collect less equity premium and are net long volatility. Historically they perform poorly against other ways of reducing downside.
- **Relevance:** Do not buy LEAPS puts as permanent protection. Long-dated insurance is cheap relative to short-dated insurance (Dew-Becker et al.), but it still costs equity premium.

### 3.3 Israelov & Tummala (2017), "Which Index Options Should You Sell?," AQR working paper
- **Data:** S&P 500 options, March 1996 to December 2015, monthly expirations held to expiry. **Method:** Delta-hedged OTM puts and calls at -2.5 to +1.0 standard deviations of moneyness and 1, 2, 3, 6 and 12-month maturities. Risk is measured three ways: beta-adjusted volatility, 0.1% VaR, and a 20% one-day stress test.
- **Key findings:**
  - For a given moneyness, **shorter maturities mostly earn more**.
  - Near-the-money beats deep OTM, and puts beat calls.
  - **Longer maturities have lower stress-test losses** and lower volatility.
  - Per unit of stress loss, the best options to sell are **front-month, near-the-money, moderately below spot**.
  - Results are similar in both subperiods. All numbers are gross.
- **Relevance:** The front month (about 30 DTE) maximizes premium per unit of tail risk. Our 60 DTE 0.10Δ condor sits at a lower-return, lower-stress-loss point. A 10-delta short is "deep OTM," which this paper rates as worse per unit of risk than 25-35 delta. Our high Sharpe at 0.10Δ may reflect a sample without a large crash.

### 3.4 Hill, Balasubramanian, Gregory & Tierens (2006), "Finding Alpha via Covered Index Writing," *FAJ*
- BXM beat the S&P 500 over 15+ years with lower standard deviation. Alternative strikes adjusted to the volatility regime (OTM in some regimes) improved results.
- **Limitation:** Goldman Sachs authors, and an in-sample choice of strike.

### 3.5 Yang (2010), "Buy-Write or Put-Write: An Active Portfolio to Strike it Right," NAAIM Wagner Award runner-up
- **Data:** 1988-2009, SPTR, BXM and PUT. **Rule:** sell ATM puts after a 50/200 golden cross and ATM calls after a death cross, with a 50/50 stock/cash mix.
- **Key numbers:** **16.3% gross annualized, Sharpe 0.95**, versus about 10% for SPTR.
- **Limitations:** Gross of costs, one sample, a practitioner competition paper. It underperforms in sharp declines (1998, 2008). This is weak evidence.
- **Relevance:** This is the only direct "trend switch between covered call and put-write" study I found. It is worth one test, not worth trusting.

### 3.6 Bondarenko (Cboe white papers, 2016 and 2019): PUT and WPUT
- **2016 paper (weekly vs monthly rollover):**
  - Over 2006-2015 the average annual gross premium collected was **24.1% for PUT (monthly ATM) and 39.3% for WPUT (weekly ATM)**.
  - Yet the compound returns were **PUT 6.6% and WPUT 5.6%, versus 7.1% for SPX TR** (Feb 2006 to Dec 2015) **[secondary, CXO]**.
  - Maximum drawdowns: **WPUT 24.2%, PUT 32.7%, SPX 50.9%**.
  - PUT skewness was -2.09.
- **2019 paper (1986-2018):** PUT compounded 10.13% versus 9.85% for the S&P 500. Average VIX was 19.3% against 15.1% realized volatility, a **4.2-point VRP**.
- **Limitations:** Cboe-sponsored, gross of costs, index-level ATM puts.
- **Relevance:** **Weekly rolling collects far more premium but does not compound better than monthly.** More gamma per unit of theta means more path risk and more trading costs. This is direct evidence against unconditional weekly harvesting and in favour of about 30 DTE.

### 3.7 Timing the VRP
- **Bollerslev, Tauchen & Zhou (2009), *RFS*:** VIX² minus realized variance predicts *equity* returns, most strongly at the **quarterly** horizon. It explains more than 15% of quarterly excess-return variation over 1990-2005 (working-paper figure **[secondary]**), and more than 25% together with P/E. High VRP means higher future equity returns, which supports selling *puts* (long delta plus short vol) when VRP is high.
- **Cheng (2019), "The VIX Premium," *RFS*:** ex-ante premiums on VIX futures **fall or stay flat when ex-ante risk rises**. The ex-ante premium predicts ex-post VIX-futures returns with a coefficient near 1. The explanation is that hedging demand falls exactly when risk rises. **Implication:** "VIX spiked, so sell" is not reliably rewarded at the moment of the spike.
- **Johnson (2017), "Risk Premia and the VIX Term Structure," *JFQA*:** the second principal component of the VIX term structure (SLOPE) predicts excess returns of S&P variance swaps, VIX futures and **S&P straddles at all maturities**. Low or inverted slope predicts high returns to long variance, which means poor returns to sellers. Data are daily, 1996-2013 **[secondary]**. SLOPE subsumes the other VRP proxies. **This is the academic basis for a VIX/VIX3M gate**, yet our 2007-2026 test found no improvement (reconciled in Section 6).
- **Aït-Sahalia, Karaman & Mancini (2020), *Journal of Econometrics*:** the variance-swap term structure has a significant jump component. Willingness to insure against volatility **rises after market drops**, and the effect is **stronger at short maturities and more persistent at long maturities**.
  - **Relevance:** This is the mechanism behind our dip finalist. After a down move, short-dated puts become temporarily rich, and short-term index reversal (Baltussen) then works in the seller's favour.
- **Moreira & Muir (2017), *JF*:** scaling exposure inversely to recent variance raises factor Sharpe ratios, because expected returns do not rise in proportion to volatility. Cederburg et al. (2020) find that much of this is not implementable out of sample. **Relevance:** size by 1/IV or 1/RV rather than gating on/off.
- **Ilmanen (2012), *FAJ*:** across many markets, selling insurance and lottery tickets earned positive long-run premiums, with sharp losses in bad times. Taleb's 2013 comment and the reply are worth reading for tail-risk framing.

### 3.8 Skew and term-structure signals (cross-section)
- **Xing, Zhang & Zhao (2010), *JFQA*:** steepest-smirk stocks (OTM put IV minus ATM call IV) **underperform the flattest by 10.9% per year risk-adjusted**. Predictability lasts at least 6 months and is tied to subsequent bad earnings news. **Use:** avoid selling puts or wheeling names with an unusually steep put smirk relative to their own history.
- **Vasquez (2017), *JFQA*:** firms with a **steep upward IV term structure** (long IV above short IV) have straddles that outperform flat or inverted ones. The spread survives factor and jump controls. **Use:** an inverted single-name term structure (front IV rich) is the better time to sell front-month premium. The time-series analogue for the index is Johnson's SLOPE.

### 3.9 Weekly options and LEAPS pricing
- **Andersen, Fusari & Todorov (2017), *JF*:** weekly SPX options isolate short-term jump risk. Negative jump tail risk varies independently of volatility and predicts equity returns. Weekly-option premium is largely compensation for jump (gap) risk, which defined-risk spreads cap.
- **Dew-Becker, Giglio, Le & Rodriguez (2017), *JFE* (NBER WP 21182):** over 1996-2014, variance claims at maturities from **one quarter to 14 years** earned roughly zero premium. Only unexpected, transitory realized variance is priced. Others cite this as "costless to hedge variance beyond about 2 months."
  - **LEAPS implication:** a short LEAPS straddle or strangle earns about no VRP and carries big vega and crash risk.
  - **The flip side:** long LEAPS calls or puts are "fairly priced" vol, so a LEAPS call used as a stock substitute (poor man's covered call, diagonal) does not bleed much VRP on the long leg.

---

## 4. Synthesis (a): which technical entry signals are robust for timing options

| Signal | Evidence quality | Mechanism for options | Verdict |
|---|---|---|---|
| Short-horizon index mean reversion (RSI(2), IBS, N-day down) | Strong for indexes after 2000 (Baltussen et al. 2019, structural cause) | Puts are richest right after drops (Aït-Sahalia et al.), then the index reverts | **Robust for SPY/QQQ short puts or put spreads, 7-30 DTE.** Weaker for single names, where reversal is a liquidity premium |
| IV − trailing RV (VRP) | Strong (Goyal & Saretto; Bollerslev et al.; Horenstein et al. factor) | Direct measure of premium richness | **Robust.** Best used as an additive filter or for sizing |
| VIX term-structure slope (VIX/VIX3M) | Strong academically (Johnson 2017), but Cheng (2019) warns about spikes | Inverted slope means poor short-vol returns | **Mixed in practice.** It avoids some bad entries but rarely changes held-to-expiry outcomes. Our null result is consistent |
| Put skew steepness (single name) | Good (Xing et al. 2010) | Informed put buying ahead of bad news | Useful as an **exclusion** filter for wheel names |
| 200DMA / 50-200 cross | Weak after 1990 for equities (Sullivan et al.; Zakamulin; Park & Irwin) | Crash avoidance, but late in V-shaped crashes | **Mostly noise for a premium seller.** Keep only as a hard "no new short puts during deep bear markets" switch, if at all |
| 12-month TSMOM on a single index | Moderate (Moskowitz et al., but the strength comes from diversification) | Direction tilt for 60-90 DTE structures | Low-to-moderate. Use for choosing call vs put side, not daily timing |
| MA timing in volatility-sorted stocks (Han et al.) | Good in small, high-volatility stocks | n/a for mega caps | **Not applicable** to our universe |
| Option momentum (Heston et al.) | Strong, cross-sectional | Persistent straddle-return differences | Needs a broad single-name option history. Out of reach for now |
| Close-entry / open-exit; Thursday-Friday entry | Strong (Muravyev & Ni; Jones & Shemesh) | Premium accrues overnight and over weekends | **Robust and cheap to test** |
| RSI > 70 "sell calls into strength" | No academic support; Israelov & Nielsen implies it is an uncompensated reversal bet | — | Noise |

---

## 5. Synthesis (b): holding period

- **Weekly (7 DTE or less):**
  - Highest premium per day, but the highest gamma and jump exposure (Andersen et al.) and the highest friction per unit of premium.
  - Over 2006-2015, WPUT collected 39% gross premium per year against 24% for PUT, yet compounded *lower* (5.6% vs 6.6%).
  - Unconditional weekly harvesting is not better than monthly. It can work **conditionally**: a short-maturity premium that spikes after drops and then reverts is exactly the setup our 7-14 DTE dip spread exploits.
- **30-60 DTE:**
  - Israelov & Tummala's best risk-adjusted point is front-month (about 30 DTE), near-the-money and modestly below spot.
  - 60 DTE trades some return for lower stress loss and volatility.
  - This band captures the priced, transitory part of variance (Dew-Becker et al.: priced at short horizons, not beyond about 1-3 months), and costs per day are lower than for weeklies.
  - **The literature supports 30-60 DTE as the default for unconditional VRP harvesting.**
- **LEAPS (6-24 months):**
  - The VRP is close to zero at these horizons, so a short LEAPS position is a pure short-vega, short-crash bet with no premium.
  - Long LEAPS are a reasonable stock substitute or hedge, because the long leg overpays little.
  - A **diagonal (long LEAPS call plus short 30-45 DTE call)** matches the term structure of the premium: buy where the VRP is zero and sell where it is largest.

---

## 6. Reconciling our results

1. **RSI(2)<10 & IBS<0.2 above the 200DMA → 7-14 DTE put credit spread (OOS Sharpe 1.01, p = 0.001 vs unconditional):**
   - **Supported.** Two independent mechanisms are documented:
     - Index mean reversion after 2000 (Baltussen et al.).
     - Short-maturity put premia rising after drops (Aït-Sahalia et al.).
   - The 200DMA clause is the weakest part (Section 4). It probably just avoids bear-market dips, where reversal is weaker. Test removing it.
   - **Caveat:** p = 0.001 against unconditional entry is one comparison. If 20-50 variants (thresholds, DTE, widths) were tried, a Reality Check or SPA test is needed (Section 7).
2. **Held-to-expiry 60 DTE 0.10Δ SPY iron condor (OOS Sharpe 1.46):**
   - **Partly supported.** The maturity is in the band where VRP harvesting is well documented and stress losses are lower (Israelov & Tummala).
   - **However, 0.10Δ is deep OTM.** Per unit of stress risk, the literature prefers nearer-the-money shorts, and the call wing earns less than the put wing (puts beat calls).
   - A Sharpe of 1.46 over about 2.6 years from roughly 13-15 non-overlapping 60-day cycles has a standard error of about 0.6. **It is not statistically distinguishable from the BXM/PUT-type long-run Sharpe of about 0.5-0.7.**
   - The sample includes August 2024 and the April 2025 shock, but no 2008, 2018 (Volmageddon) or 2020-style event at a 60-day horizon.
3. **Low-delta-call QQQ wheel:**
   - **Mixed.** Bali & Murray support selling OTM calls with positive skew. Israelov & Nielsen show the low delta is the right direction, because it cuts the uncompensated reversal exposure.
   - But the wheel's put leg sells puts into falling markets, and QQQ is the higher-IVOL, higher-skew index.
   - The literature suggests most of the wheel's return is QQQ beta, not option alpha. Judge it against QQQ buy-and-hold on a beta-adjusted basis.
4. **Regime gates (200DMA, VIX/VIX3M) didn't help on 2007-2026 Cboe index data:**
   - **Consistent with the literature, once properly interpreted.** Trend gates have weak post-1990 evidence.
   - Johnson's SLOPE predicts *average* returns. But the largest short-vol losses (Feb 2018, the start of Mar 2020, Aug 2024) began **from contango**, so a backwardation gate fires after the damage. And Cheng shows premiums do not rise with risk.
   - For a held-to-expiry index strategy, an on/off gate mostly reduces the number of trades. Gates matter more for **sizing** (Moreira & Muir style) and for **choosing DTE** than for on/off.

---

## 7. Synthesis (d): overfitting risks specific to us

1. **Short OOS window:** 2024-03 to 2026-10 is about 2.6 years. The standard error of an annualized Sharpe is about √(1/years) ≈ 0.62 before allowing for fat tails and negative skew, which make it worse. A Sharpe of 1.0-1.5 is only about 1.6-2.4 standard errors from zero, below HLZ's t > 3 bar.
2. **Selection across variants:** walk-forward guards against within-strategy parameter fitting, but not against choosing the finalist from many strategy families (spreads, wheel, condor, event, signal-timed). Run White's Reality Check or Hansen's SPA over every family and variant tried, and report a deflated Sharpe ratio (Bailey & López de Prado 2014).
3. **Skew:** premium-selling returns are left-skewed (PUT skewness is -2.09). Sharpe overstates their quality. Report the Sortino ratio, worst 1%/5% trade, max drawdown and Stutzer index, and stress-test with a 1987-style gap (Israelov & Tummala's 20% one-day test) on the 7-14 DTE spreads.
4. **Fill model:** Christoffersen et al. and Muravyev & Ni show that liquidity and timing within the day move option returns by amounts similar to the edge. Re-run the finalists with fills at mid ± 25-50% of the spread and at the close vs the next open.
5. **Indicator threshold mining:** RSI(2)<10 and IBS<0.2 are Connors-style practitioner thresholds. Check a neighbourhood (RSI 5-20, IBS 0.1-0.3). A real effect should degrade smoothly, not fall off a cliff.
6. **Publication and crowding decay:** McLean & Pontiff's 58% post-publication decline applies to price-based anomalies. The index-reversal effect has a structural driver, which helps, but RSI(2) dip-buying on SPY is heavily crowded.
7. **Regime dependence:** 2024-2026 was a strong bull market with sharp V-shaped dips, which is ideal for both dip put spreads and condors. Validate on 2007-2023 with Cboe or OptionMetrics-style history before going live, as the long-history study did for the gates.

---

## 8. Synthesis (c): concrete testable rules for the daily-bar Alpaca backtester

Ranked by evidence quality and cost to implement. Each rule should be tested as a change to an existing finalist, using the same walk-forward split, with a Reality Check or SPA test over all variants tried.

**R1. VRP filter on dip entries** (Goyal & Saretto; Bollerslev, Tauchen & Zhou; Horenstein et al.).
On a qualifying RSI(2)/IBS dip day, enter only if `VIX_close − RV10(SPY)·100 > k`, with k ∈ {0, 2, 4} vol points, where RV10 is annualized 10-day close-to-close volatility. For QQQ use VXN. As a softer variant, keep all entries but scale position size by `clip((VIX − RV10)/5, 0.5, 1.5)`. Expectation: fewer trades, higher mean per trade, and a large reduction in tail losses on dips that are the start of a volatility regime change (RV catching up to IV).

**R2. Close-entry / open-exit and weekday of entry** (Muravyev & Ni; Jones & Shemesh).
(a) Model entries at the close of the signal day (Alpaca allows trading up to 16:00 ET, so place the order about 15:45 ET) rather than at the next open, and run exits and profit-takes at the open. (b) Compare signals fired Monday-Wednesday with entries delayed to Thursday or Friday close versus immediate entry. Expectation: a measurable rise in short-premium P&L for close entries. This is cheap to test and has little parameter risk.

**R3. Maturity by term structure** (Johnson 2017; Aït-Sahalia et al. 2020; Israelov & Tummala 2017; Bondarenko).
On a dip signal, pick the expiry by `VIX9D/VIX` (or VIX/VIX3M):
- If the curve is in contango (VIX9D/VIX < 1), sell the 7-14 DTE put spread as now.
- If it is inverted (> 1), sell 30-45 DTE at the same delta, which captures the post-drop premium in its more persistent part and lowers gamma.

For the unconditional condor, compare 30, 45 and 60 DTE with short deltas of 0.10, 0.16 and 0.25. The literature predicts better return per unit of stress at 30-45 DTE and 0.16-0.25Δ, offset by higher realized drawdown.

**Further rules, lower priority:**
- **R4. Constant-delta covered call for the QQQ wheel** (Israelov & Nielsen 2015). Roll the short call up and out whenever its delta exceeds 0.40, and down when it falls below 0.10, to keep the call delta roughly constant at about 0.20. This removes most of the uncompensated reversal exposure. Compare with the current wheel and with QQQ buy-and-hold on beta-adjusted returns.
- **R5. Skew exclusion for single-name puts and wheels** (Xing, Zhang & Zhao 2010). Skip new short puts when the name's 25Δ put IV − 50Δ call IV is above its 1-year 80th percentile. This requires option-chain history from Alpaca or Cboe.
- **R6. Diagonal with a LEAPS long leg** (Dew-Becker et al.). Buy a 0.70-0.80Δ call 9-15 months out on SPY or QQQ and sell a 0.20-0.30Δ call at 30-45 DTE each month. Compare with the wheel on capital efficiency and drawdown. This buys vol where the VRP is about zero and sells it where the VRP is largest.
- **R7. Trend switch between call side and put side** (Yang 2010; Moskowitz, Ooi & Pedersen 2012). For the 60 DTE structure, when 12-month TSMOM is positive (or SPY is above the 200DMA), skew the condor to put-heavy (short put at 0.16Δ, short call at 0.08Δ), and the reverse when negative. Low prior: expect at most a small gain and treat any large improvement as suspect.
- **R8. Size by inverse volatility instead of an on/off gate** (Moreira & Muir 2017). Contracts ∝ 1/VIX (or 1/RV20), normalized to the same average risk as the current run. This tests whether the null gate result changes when the gate becomes a dial.

---

## 9. References

All found via web; none LibKey-resolved. DOIs marked "verify" were taken from memory or a secondary listing and were not confirmed at the publisher this session.

1. Aït-Sahalia, Y., Karaman, M., & Mancini, L. (2020). The term structure of equity and variance risk premia. *Journal of Econometrics*, 219(2), 204-230. DOI: 10.1016/j.jeconom.2020.03.001 (verify). URL: https://collaborate.princeton.edu/en/publications/the-term-structure-of-equity-and-variance-risk-premia/ — found via web; not LibKey-resolved.
2. Andersen, T. G., Fusari, N., & Todorov, V. (2017). Short-term market risks implied by weekly options. *Journal of Finance*, 72(3), 1335-1386. DOI: 10.1111/jofi.12486. URL: https://ideas.repec.org/a/bla/jfinan/v72y2017i3p1335-1386.html — found via web; not LibKey-resolved.
3. Bailey, D. H., & López de Prado, M. (2014). The deflated Sharpe ratio: Correcting for selection bias, backtest overfitting and non-normality. *Journal of Portfolio Management*, 40(5), 94-107. DOI: 10.3905/jpm.2014.40.5.094. URL: https://papers.ssrn.com/abstract=2460551 — found via web; not LibKey-resolved.
4. Bali, T. G., & Murray, S. (2013). Does risk-neutral skewness predict the cross-section of equity option portfolio returns? *Journal of Financial and Quantitative Analysis*, 48(4), 1145-1171. DOI: 10.1017/S0022109013000410. URL: https://optionmetrics.com/research/t-g-bali-and-s-murrary-does-risk-neutral-skewness-predict-the-cross-section-of-equity-option-portfolio-returns — found via web; not LibKey-resolved.
5. Baltussen, G., van Bekkum, S., & Da, Z. (2019). Indexing and stock market serial dependence around the world. *Journal of Financial Economics*, 132(1), 26-48. DOI: 10.1016/j.jfineco.2018.07.016. URL: https://www3.nd.edu/~zda/Indexing.pdf — found via web; not LibKey-resolved.
6. Bollerslev, T., Tauchen, G., & Zhou, H. (2009). Expected stock returns and variance risk premia. *Review of Financial Studies*, 22(11), 4463-4492. DOI: 10.1093/rfs/hhp008. URL: https://papers.ssrn.com/abstract=948309 — found via web; not LibKey-resolved.
7. Bondarenko, O. (2016). An analysis of index option writing with monthly and weekly rollover. Cboe-sponsored white paper. URL: https://ir.cboe.com/news/news-details/2016/New-Study-On-Weekly-Monthly-SP-500-PutWrite-Indexes-Released-01-27-2016/default.aspx — found via web; not LibKey-resolved.
8. Bondarenko, O. (2019). Historical performance of put-writing strategies. Cboe-sponsored white paper. URL: https://www.cboe.com/insights/posts/white-paper-shows-volatility-risk-premium-facilitated-higher-risk-adjusted-returns-for-put-index/ — found via web; not LibKey-resolved.
9. Brock, W., Lakonishok, J., & LeBaron, B. (1992). Simple technical trading rules and the stochastic properties of stock returns. *Journal of Finance*, 47(5), 1731-1764. DOI: 10.1111/j.1540-6261.1992.tb04681.x. URL: https://ideas.repec.org/a/bla/jfinan/v47y1992i5p1731-64.html — found via web; not LibKey-resolved.
10. Cao, J., & Han, B. (2013). Cross section of option returns and idiosyncratic stock volatility. *Journal of Financial Economics*, 108(1), 231-249. DOI: 10.1016/j.jfineco.2012.11.010. URL: https://optionmetrics.com/research/j-cao-and-b-han-cross-section-of-option-returns-and-idiosyncratic-stock-volatility/ — found via web; not LibKey-resolved.
11. Cheng, I.-H. (2019). The VIX premium. *Review of Financial Studies*, 32(1), 180-227. DOI: 10.1093/rfs/hhy062. URL: https://ideas.repec.org/a/oup/rfinst/v32y2019i1p180-227..html — found via web; not LibKey-resolved.
12. Christoffersen, P., Goyenko, R., Jacobs, K., & Karoui, M. (2018). Illiquidity premia in the equity options market. *Review of Financial Studies*, 31(3), 811-851. DOI: 10.1093/rfs/hhx113. URL: https://repec.econ.au.dk/repec/creates/rp/13/rp13_48.pdf — found via web; not LibKey-resolved.
13. Coval, J. D., & Shumway, T. (2001). Expected option returns. *Journal of Finance*, 56(3), 983-1009. DOI: 10.1111/0022-1082.00352. URL: https://deepblue.lib.umich.edu/items/455dbe73-b56b-44f3-841f-c7751deffc7a — found via web; not LibKey-resolved.
14. Dew-Becker, I., Giglio, S., Le, A., & Rodriguez, M. (2017). The price of variance risk. *Journal of Financial Economics*, 123(2), 225-250. DOI: 10.1016/j.jfineco.2016.04.003 (verify). URL: https://www.nber.org/papers/w21182.pdf — found via web; not LibKey-resolved.
15. Goyal, A., & Saretto, A. (2009). Cross-section of option returns and volatility. *Journal of Financial Economics*, 94(2), 310-326. DOI: 10.1016/j.jfineco.2009.01.001. URL: https://www.cis.upenn.edu/~mkearns/finread/CrossOptions.pdf — found via web; not LibKey-resolved.
16. Han, Y., Yang, K., & Zhou, G. (2013). A new anomaly: The cross-sectional profitability of technical analysis. *Journal of Financial and Quantitative Analysis*, 48(5), 1433-1461. DOI: 10.1017/S0022109013000586. URL: https://ideas.repec.org/a/cup/jfinqa/v48y2013i05p1433-1461_00.html — found via web; not LibKey-resolved.
17. Hansen, P. R. (2005). A test for superior predictive ability. *Journal of Business & Economic Statistics*, 23(4), 365-380. DOI: 10.1198/073500105000000063. URL: https://doi.org/10.1198/073500105000000063 — found via web; not LibKey-resolved.
18. Harvey, C. R., Liu, Y., & Zhu, H. (2016). ... and the cross-section of expected returns. *Review of Financial Studies*, 29(1), 5-68. DOI: 10.1093/rfs/hhv059. URL: https://www.nber.org/papers/w20592 — found via web; not LibKey-resolved.
19. Heston, S. L., Jones, C. S., Khorram, M., Li, S., & Mo, H. (2023). Option momentum. *Journal of Finance*, 78(6), 3141-3192. DOI: 10.1111/jofi.13279. URL: https://alphaarchitect.com/option-momentum/ — found via web; not LibKey-resolved.
20. Hill, J. M., Balasubramanian, V., Gregory, K., & Tierens, I. (2006). Finding alpha via covered index writing. *Financial Analysts Journal*, 62(5), 29-46. DOI: 10.2469/faj.v62.n5.4281. URL: https://rpc.cfainstitute.org/research/financial-analysts-journal/2006/finding-alpha-via-covered-index-writing — found via web; not LibKey-resolved.
21. Horenstein, A., Vasquez, A., & Xiao, X. (2026). Common factors in equity option returns. *Review of Financial Studies*, 39(3), 835-874. DOI: 10.1093/rfs/hhaf060 (verify). URL: https://www.jbs.cam.ac.uk/people/xiao-xiao/ — found via web; not LibKey-resolved.
22. Hurst, B., Ooi, Y. H., & Pedersen, L. H. (2017). A century of evidence on trend-following investing. *Journal of Portfolio Management*, 44(1), 15-29. DOI: 10.3905/jpm.2017.44.1.015. URL: https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing — found via web; not LibKey-resolved.
23. Ilmanen, A. (2012). Do financial markets reward buying or selling insurance and lottery tickets? *Financial Analysts Journal*, 68(5), 26-36. DOI: 10.2469/faj.v68.n5.7 (verify). URL: https://www.aqr.com/Insights/Research/Journal-Article/Do-Financial-Markets-Reward-Buying-or-Selling-Insurance-and-Lottery-Tickets — found via web; not LibKey-resolved.
24. Israelov, R., & Klein, M. (2016). Risk and return of equity index collar strategies. *Journal of Alternative Investments*, 19(1), 41-54. DOI: 10.3905/jai.2016.19.1.041 (verify). URL: https://www.aqr.com/Insights/Research/Journal-Article/Risk-and-Return-of-Equity-Index-Collar-Strategies — found via web; not LibKey-resolved.
25. Israelov, R., & Nielsen, L. N. (2015). Covered calls uncovered. *Financial Analysts Journal*, 71(6), 44-57. DOI: 10.2469/faj.v71.n6.6. URL: https://www.aqr.com/Insights/Research/Journal-Article/Covered-Calls-Uncovered — found via web; not LibKey-resolved.
26. Israelov, R., & Tummala, H. (2017). Which index options should you sell? AQR / SSRN working paper. URL: https://www.aqr.com/Insights/Research/Working-Paper/Which-Index-Options-Should-You-Sell — found via web; not LibKey-resolved.
27. Jegadeesh, N. (1990). Evidence of predictable behavior of security returns. *Journal of Finance*, 45(3), 881-898. DOI: 10.1111/j.1540-6261.1990.tb05110.x. URL: https://doi.org/10.1111/j.1540-6261.1990.tb05110.x — found via web; not LibKey-resolved.
28. Johnson, T. L. (2017). Risk premia and the VIX term structure. *Journal of Financial and Quantitative Analysis*, 52(6), 2461-2490. DOI: 10.1017/S0022109017000825 (verify). URL: https://ideas.repec.org/a/cup/jfinqa/v52y2017i06p2461-2490_00.html — found via web; not LibKey-resolved.
29. Jones, C. S., & Shemesh, J. (2018). Option mispricing around nontrading periods. *Journal of Finance*, 73(2), 861-900. DOI: 10.1111/jofi.12603. URL: https://ideas.repec.org/a/bla/jfinan/v73y2018i2p861-900.html — found via web; not LibKey-resolved.
30. Lehmann, B. N. (1990). Fads, martingales, and market efficiency. *Quarterly Journal of Economics*, 105(1), 1-28. DOI: 10.2307/2937816. URL: https://doi.org/10.2307/2937816 — found via web; not LibKey-resolved.
31. McLean, R. D., & Pontiff, J. (2016). Does academic research destroy stock return predictability? *Journal of Finance*, 71(1), 5-32. DOI: 10.1111/jofi.12365. URL: https://www.gwern.net/doc/economics/2016-mclean.pdf — found via web; not LibKey-resolved.
32. Moreira, A., & Muir, T. (2017). Volatility-managed portfolios. *Journal of Finance*, 72(4), 1611-1644. DOI: 10.1111/jofi.12513. URL: https://www.nber.org/papers/22208 — found via web; not LibKey-resolved.
33. Moskowitz, T. J., Ooi, Y. H., & Pedersen, L. H. (2012). Time series momentum. *Journal of Financial Economics*, 104(2), 228-250. DOI: 10.1016/j.jfineco.2011.11.003. URL: https://papers.ssrn.com/abstract=2089463 — found via web; not LibKey-resolved.
34. Muravyev, D., & Ni, X. (2020). Why do option returns change sign from day to night? *Journal of Financial Economics*, 136(1), 219-238. DOI: 10.1016/j.jfineco.2018.12.006. URL: https://ideas.repec.org/a/eee/jfinec/v136y2020i1p219-238.html — found via web; not LibKey-resolved.
35. Park, C.-H., & Irwin, S. H. (2007). What do we know about the profitability of technical analysis? *Journal of Economic Surveys*, 21(4), 786-826. DOI: 10.1111/j.1467-6419.2007.00519.x. URL: https://ideas.repec.org/a/bla/jecsur/v21y2007i4p786-826.html — found via web; not LibKey-resolved.
36. Sullivan, R., Timmermann, A., & White, H. (1999). Data-snooping, technical trading rule performance, and the bootstrap. *Journal of Finance*, 54(5), 1647-1691. DOI: 10.1111/0022-1082.00163. URL: https://www.fmg.ac.uk/sites/default/files/2020-11/dp303.pdf — found via web; not LibKey-resolved.
37. Vasquez, A. (2017). Equity volatility term structures and the cross section of option returns. *Journal of Financial and Quantitative Analysis*, 52(6), 2727-2754. DOI: 10.1017/S002210901700079X (verify). URL: https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/equity-volatility-term-structures-and-the-cross-section-of-option-returns/F0A40E99FD2458367DD9A56A89783D38 — found via web; not LibKey-resolved.
38. White, H. (2000). A reality check for data snooping. *Econometrica*, 68(5), 1097-1126. DOI: 10.1111/1468-0262.00152. URL: https://doi.org/10.1111/1468-0262.00152 — found via web; not LibKey-resolved.
39. Xing, Y., Zhang, X., & Zhao, R. (2010). What does the individual option volatility smirk tell us about future equity returns? *Journal of Financial and Quantitative Analysis*, 45(3), 641-662. DOI: 10.1017/S0022109010000220. URL: https://ideas.repec.org/a/cup/jfinqa/v45y2010i03p641-662_00.html — found via web; not LibKey-resolved.
40. Yang, G. (2010). Buy-write or put-write: An active portfolio to strike it right. NAAIM Wagner Award paper (runner-up). Summary URL: https://www.cxoadvisory.com/technical-trading/selling-calls-or-puts-according-to-trend/ — found via web; not LibKey-resolved.
41. Zakamulin, V. (2017). *Market Timing with Moving Averages: The Anatomy and Performance of Trading Rules*. Palgrave Macmillan. DOI: 10.1007/978-3-319-60970-6 (verify). URL: https://www.cxoadvisory.com/technical-trading/net-performance-of-sma-and-intrinsic-momentum-timing-strategies — found via web; not LibKey-resolved.
42. Zhan, X., Han, B., Cao, J., & Tong, Q. (2022). Option return predictability. *Review of Financial Studies*, 35(3), 1394-1442. DOI: 10.1093/rfs/hhab067. URL: https://ink.library.smu.edu.sg/lkcsb_research/4907 — found via web; not LibKey-resolved.
43. Cederburg, S., O'Doherty, M. S., Wang, F., & Yan, X. S. (2020). On the performance of volatility-managed portfolios. *Journal of Financial Economics*, 138(1), 95-117. DOI: 10.1016/j.jfineco.2020.04.015 (verify). URL: https://doi.org/10.1016/j.jfineco.2020.04.015 — found via web; not LibKey-resolved.
