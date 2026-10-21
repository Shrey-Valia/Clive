# 0DTE and Ultra-Short-Dated Index Options: Literature Review for an Alpaca 0DTE Bot

Scope: the academic and exchange/regulator research on zero-days-to-expiry (0DTE) and 1DTE index options (SPX/SPXW, SPY, QQQ), read with one decision in mind: is a systematic 0DTE/1DTE bot worth building on Alpaca? The constraints are options level 3 (defined-risk spreads only, no naked shorts), daily option bars from 2024-02, possibly 1-minute option bars, and equity/ETF options only (SPY, QQQ; no SPX).

Method: I searched SSRN, arXiv, NBER, journal sites, Cboe, the Fed, the SEC and the BIS. I read full text wherever an open copy existed: Almeida-Freire-Hizmeri, Beckmeyer-Branger-Gayda, Dim-Eraker-Vilkov, Bandi-Fusari-Renò, Londono-Samadi, Fu-Li-Musto-Pearson (SEC DERA), Amaya-Garcia-Ares-Pearson-Vasquez (x2), Song, the BIS box, the Cboe notes, and the Vilkov replication repository. SSRN itself blocked automated access, so for Brogaard-Han-Won, Adams-Fontaine-Ornthanalai, Johannes-Kaeck-Seeger-Shah and Chong-Todorov I rely on abstracts, author pages and citing papers. Those entries are flagged **[abstract/secondary only]**. Every reference was found via web search and is **not LibKey-resolved**; the LibKey connector required authentication that was not available in this session.

---

## 0. Bottom line (read this first)

1. **A 0DTE variance risk premium (VRP) exists, but in daily terms it is tiny.** Annualized it looks huge, 4-5x the 1-month VRP. Per day it is about **1 bp of the index** (Dim-Eraker-Vilkov). Vilkov puts the median 10:00-to-close VRP at about **0.0011% of spot**. A retail trader paying a 4-leg SPY bid-ask spread cannot harvest that unconditionally.
2. **The best documented after-cost test of plain short-vol 0DTE (Almeida-Freire-Hizmeri, 2012-2025, ask/bid execution) gives a negative per-trade Sharpe at every entry time from 10:00 to 14:00** (−0.010 to −0.042) for writing the delta-hedged ATM call. The one profitable rule (a stochastic-dominance mispricing filter) **stopped working after daily expiries began in May 2022.**
3. **Vilkov's "0DTE Trading Rules" replication was corrected in August 2026.** Bid-ask costs had been under-charged by 100x. After the fix, **no strategy or basket keeps a positive net Sharpe**, and iron butterflies/condors are the worst structure (unconditional net SR −2.67 annualized; conditional OOS strategies also turn negative).
4. **Retail data agree.** Retail 0DTE traders lost $241k/day on average (2021-2023) and $350k/day after daily expiries, about 60% of it from transaction costs. Retail **iron condors have a positive median (+5.5% of margin) but a negative mean (−1.1%)**: high win rate, tail losses. This is the same shape as our −3.8%/trade result.
5. **The premium is concentrated, not uniform:** in ATM strikes (~80% of ATM 0DTE prices sit above the risk-averse upper bound, while OTM options are, if anything, *cheap*), in the afternoon (VRP rises from 10:00 to 14:00), and on macro-event expiries (FOMC-spanning VRP is 44% larger; CPI forward equity premia reached 12.2% annualized in Jan 2023). Our 1DTE condor sold the cheap part (1x-implied-move OTM wings), held through the overnight gap, and ignored event timing.

**Verdict for an Alpaca 0DTE bot:** the literature gives no basis for an unconditional systematic 0DTE seller or buyer, after costs, in any structure. A bot is worth building only as a **research harness for a few narrow conditional tests** (ATM iron fly at a fixed entry time, event-day selling, a VRP filter). It should go live only if those tests survive realistic SPY bid-ask on 1-minute data, and SPY's settlement mechanics (Section 6) make even that harder than for SPX.

---

## 1. Paper-by-paper review

### 1.1 Bandi, Fusari & Renò, "0DTE option pricing" (JHU / ESSEC)
- **Question.** How do you price 0DTE options accurately, and what do they reveal about instantaneous return and variance risk premia?
- **Data.** CBOE intraday SPX options, 2 Jan 2014 to 11 May 2023, sampled at 10:30 ET, with expiry at 16:00 (5.5-hour horizon).
- **Method.** Closed-form "Edgeworth-like" local-in-time expansion of the log-return characteristic function. Skew comes through leverage (correlation of return and volatility shocks), and ATM convexity comes through volatility-of-volatility. Benchmarks: Black-Scholes, Black-Scholes-Merton with jumps, and affine models.
- **Findings.**
  - Fit: Edgeworth with tempered-stable jumps has an implied-volatility RMSE of about 0.44 vol points, against 0.90-1.08 for BSM.
  - Hedging: it improves delta and gamma hedging, measured as the median P&L from 10:30 to close.
  - Volatility jumps matter little for 0DTE pricing. The follow-up (Bandi & Fusari 2026, arXiv 2603.29430) finds that 82% of model 0DTE prices fall inside the bid-ask.
  - Risk premia (Table 4): the **instantaneous volatility premium is strongly predictable from the 10:30 surface** (R² 21% univariate, 35% joint). It is driven by vol-of-vol and the leverage term ρσβ. The **return premium is barely predictable** (R² 1.6-2.5%), and diffusive variance carries no significant price of risk.
  - 0DTE volume has a U-shaped intraday pattern. On FOMC days the peak moves to about 14:00 and is much steeper.
- **Relevance.** The 10:30 surface carries information about the size of the volatility premium for the rest of the day, which is a conditional-selling signal. It carries almost nothing about direction.
- **Limitations.** SPX only, and quote mids. No trading strategy or transaction costs. The predictability is described by the authors as "suggestive". FOMC days are excluded from the follow-up.
- Status: cited as *Journal of Finance*, forthcoming, in Bandi & Fusari (2026). SSRN 4503344.

### 1.2 Almeida, Freire & Hizmeri, "0DTE Asset Pricing" (Princeton / Erasmus / Liverpool; draft 23 May 2025)
- **Question.** What risk premia do 0DTEs embed, and are 0DTE prices consistent with risk-averse investors?
- **Data.** CBOE 1-minute SPXW bid/ask, 6 Jan 2012 to 18 Mar 2025. There are 1,815 0DTE dates, about 40% of them after 11 May 2022. Entry times run every 30 minutes from 10:00 to 14:00 (6 to 2 hours to close), the window where relative spreads are lowest.
- **Method.** Option returns by moneyness, ATM straddles and delta-hedged calls, and a Bollerslev-Tauchen-Zhou-style VRP with realized variance from 1-minute returns. Good/bad VRP split, pricing kernel estimation, and second-order stochastic dominance (SSD) bounds.
- **Key numbers.**
  - **0DTE VRP (annualized, %-points) rises through the day**: mean 1.80 at 10:00, 1.54 at 11:00, 2.21 at 12:30, 2.96 at 14:00. All are significant, but the standard deviations are 8-14 (fat tails).
  - **1DTE VRP is the weak point of the term structure:** mean 0.558 (p = 0.011), **25th percentile 0.003**. The downside component VRP⁻ at 1DTE is *insignificant* (p = 0.188). After May 2022 the 1DTE 25th percentile is **−0.165** (Table OA.4). **This bears directly on our 1DTE result.**
  - **The upside "good" VRP dominates at 0DTE** (0.96-1.97, against 0.58-0.99 for the downside component). At 1 month it is the reverse. VRP⁺ **negatively predicts** intraday index returns (t ≈ −2 to −4, R² ≈ 1-5%).
  - Writing naked OTM calls and puts is profitable on average at the mid. ATM delta-hedged calls and straddles have significantly negative returns for buyers, most significant from 12:00 to 14:00.
  - **SSD bounds:** only about 6% of ATM 0DTE options sit inside the bounds, and about 78-85% violate the *upper* bound (too expensive). **OTM options rarely violate the upper bound and often fall *below* the lower bound (too cheap).** By contrast, 97% of 1-month options satisfy the bounds.
  - **After costs** (buy at ask, sell at bid), the benchmark **"always write delta-hedged ATM call" has a per-trade Sharpe of −0.037 (10:00), −0.039 (11:00), −0.023 (12:00), −0.010 (13:00), −0.033 (14:00)**. The SSD-violation strategy earns 0.10-0.16 after costs over the full sample, but "after 2022, the performance of the strategy mostly stagnates". Its after-cost Sharpe is about 0 on high-volume, high-realized-vol or high-attention days and about 0.25-0.45 only on low-volume, low-RV days, which are mostly pre-2022.
  - FOMC: excluding FOMC days lowers the VRP. On FOMC days, straddle returns are negative before the announcement and positive around it (small sample, not significant).
- **Limitations.** SPX, delta-hedged, single ATM option rather than multi-leg; costs are full spread; no SPY.

### 1.3 Vilkov, "0DTE Trading Rules" (Frankfurt School; SSRN 4641356; versions Nov 2023, Jan 2024, Mar 2026; replication repo corrected Aug 2026)
- **Question.** Do simple static or conditional 0DTE structures held to expiry make money?
- **Data.** SPXW 0DTE, Sept 2016 to Jan/Feb 2026. 30-minute bars at first, later Cboe 1-minute bars. Moneyness 0.98-1.02. Main entry 10:00 ET, with 13:00 and 15:00 robustness checks, held to 16:00 settlement.
- **Structures.** Calls/puts, straddles/strangles, bull/bear spreads, call/put ratio spreads, risk reversals, iron butterflies/condors.
- **Findings (paper text and results summary).**
  - Median VRP from 10:00 to expiry is about **0.0011% of the underlying**, "too small to trade after realistic friction".
  - Selling slightly OTM calls or puts wins in up to 75% of observations, but mean returns are mostly indistinguishable from zero.
  - **Realized skewness, not realized variance, drives P&L.** It explains 20-40% of P&L variation in directional spreads and risk reversals; implied vol and skew add only 2-7 pp of R².
  - ES(1%) is 0.58-1.58% of the underlying per day.
  - The sign of P&L flips between pre- and post-2022.
  - VIX regime matters: downside structures do better in high-VIX regimes, upside structures in low-VIX.
- **The pre-correction claims, now withdrawn.** The paper reported a conditional out-of-sample put-ratio spread at net SR 0.93 and a top-3 basket at 0.82.
- **The Aug 2026 KNOWN-ISSUES correction.** The half-spread had been charged at 0.022 bp instead of **2.2 bp of spot** (mean dollar spread about $1.33 on SPXW 0DTE), plus a 0.5 bp fee. Two-leg strangle entry turnover is about **24 bp** of spot. Corrected unconditional annualized Sharpe ratios:

  | Structure | SR mid | SR net (corrected) |
  |---|---|---|
  | Put ratio spread | +1.06 | −0.61 |
  | Risk reversal | +0.65 | +0.10 |
  | Bear put spread | +0.48 | −0.73 |
  | Strangle/straddle | −0.27 | −0.97 |
  | **Iron butterfly/condor** | **−0.56** | **−2.67** |

  The conditional put-ratio result goes from +0.93 to **−0.75**, and the top-3 basket from +0.82 to **−0.82**. In the author's words: "no strategy or basket retains a positive net Sharpe ratio." The cost error was found by an outside party (gex.live), and an earlier sign error on short-side costs had already been fixed in May 2026.
- **Relevance.** This is the closest academic analogue to a retail 0DTE bot. Even on SPX, which has tighter percentage spreads per unit of premium than SPY 4-leg combos, and with machine-learning conditioning, the after-cost edge is negative. **Note that even at the mid, iron condors/butterflies had a negative Sharpe.**
- **Limitations.** SPX only, held to expiry (no intraday exits), interpolated strikes. The paper revision was still in progress.

### 1.4 Dim, Eraker & Vilkov, "0DTEs: Trading, Gamma Risk and Volatility Propagation" (GWU / Wisconsin / Frankfurt; 14 May 2024; SSRN 4692190)
- **Question.** Do 0DTEs destabilize markets through dealer gamma hedging? What do they pay?
- **Data.** SPXW and SPY options (they explicitly note SPY is American and physically settled), Cboe 30-minute bars and transactions, roughly 2016 to 2023.
- **Method.** Structural VAR of intraday 0DTE volume, underlying volume and absolute returns. Regressions of intraday RV on open-interest gamma by maturity. FOMC event study.
- **Findings.**
  - 0DTEs grew from 5% (2016) to 50% (Aug 2023) of SPX option volume. The correlation of 0DTE and underlying intraday volume rose from 0.25-0.30 (pre-2021) to 0.59 (2023). The extra volatility response is only **0.15 SD**, which is economically negligible.
  - **Open-interest gamma in 0DTEs does not propagate volatility.** For options with more than 1 DTE, OI gamma is associated with *lower* intraday RV. Average 0DTE OI gamma did not grow after 2016.
  - 0DTE returns have negative means, extreme volatility and positive skewness (lottery-like). **Annualized 0DTE VRP is about 5x the 11-22 DTE VRP, but in daily terms about 0.01%**, "hardly profitable given the required delta-hedging intensity … and realistic transaction costs."
  - **FOMC:** 0DTE and 1DTE volume falls significantly in the half hour before the announcement and rebounds after it.
- **Limitations.** Gamma sign is inferred, not observed. 30-minute granularity.

### 1.5 Beckmeyer, Branger & Gayda, "Retail Traders Love 0DTE Options… But Should They?" (Münster; 15 Dec 2023; SSRN 4404704)
- **Question.** How do retail traders trade 0DTE SPX options, and do they make money?
- **Data.** Cboe SPX trades Feb 2021 to Sep 2023. Retail trades are identified by the SLAN/MLAT auction codes, and multi-leg strategies by the Li (2020) algorithm. Open/Close data are used for market-maker positioning.
- **Findings.**
  - More than 75% of retail SPX option trades are 0DTE, and retail is about 6% of 0DTE volume.
  - Since mid-2022, 60-80% of retail volume is single-leg. Spreads make up about 70% of multi-leg volume, and condors/butterflies are common.
  - Most positions have 3-24 hours to maturity and are mostly ATM or slightly OTM.
  - Retail effective spreads (calls 6.1%, puts 4.9%) are smaller than non-retail (13.1%, 10.1%).
  - **Total retail loss: more than $125M. That is −$241k/day over the full sample and −$350k/day after May 2022. More than $90M (about 60%) is transaction costs.**
  - Per-trade net profit (Table 3): **iron condor mean −$15.6, median +$135**, P5 −$2,228, P95 +$1,570. Put spread mean −$20.2, median +$85.
  - Margin-adjusted returns (Table 4): **iron condor mean −1.1%, median +5.5%**, P25 −24%, P5 −100%. Put spread mean +0.1%, median +3.0%. Iron butterfly mean −0.9%. Single puts −5.6%, single calls −9.4%.
  - Regressions: debit (premium-paying) trades earn −0.293% relative to credit trades; complex trades earn +0.17% (+0.08% with day fixed effects). Trades are worse in high-IV contracts and periods. The relation between retail P&L and expensiveness (a VIX-style measure from the 9:30-9:40 0DTE surface) disappears after May 2022.
- **Critiques.**
  - Fu, Li, Musto & Pearson (SEC DERA 2025) show the OPRA trade/quote time-stamps are mis-sequenced, which inflates measured effective spreads in studies like this one.
  - Amaya et al. (2025) argue that, with true trade direction and expiry values, Cboe customer SLIM trades are on average *profitable*. That paper is about equity options generally, not 0DTE.
- **Relevance.** The iron condor's "high median, negative mean" profile is exactly what a 1σ condor produces. The cost share (60%) shows that a retail edge must first clear the spread.

### 1.6 Brogaard, Han & Won, "How Does Zero-Day-to-Expiry Options Trading Affect the Volatility of Underlying Assets?", revised as "Does 0DTE Options Trading Increase Volatility?" (SSRN 4426358; Apr 2023, rev. Jan 2026) [abstract/secondary only]
- **Question.** Does 0DTE trading raise underlying volatility?
- **Method.** Uses the staggered introduction of index weekly expiries as an instrument. The volatility measure is SPY return volatility against the 0DTE share of SPX option volume.
- **Finding.** A one-SD increase in 0DTE trading raises volatility by **about 9.1% of its mean**. The effect persists after controlling for market-maker gamma and is attributed to speculative retail traders.
- **Contrary evidence.** Dim-Eraker-Vilkov; Adams-Fontaine-Ornthanalai; Cboe; Amaya et al. A practitioner replication on 1,083 SPX sessions (2022-2026) found +0.6% (t = 0.3), which is not peer-reviewed.
- **Limitations.** Volume share is not the same as dealer positioning, and reverse causality is possible (traders use 0DTEs on volatile days).

### 1.7 Adams, Fontaine & Ornthanalai (Bank of Canada / Toronto), 0DTE and market volatility (2024; latest version Oct 2025, SSRN 5641974 per Quantpedia/SIFMA) [abstract/secondary only]
- **Question.** Do 0DTEs increase S&P 500 volatility?
- **Identification.** Before May 2022, 0DTEs existed only on some weekdays. Hedging needs are measured as in Ni-Pearson-Poteshman-White (2021), and the paper uses two-stage regressions with intraday E-mini order flow.
- **Findings.**
  - Index volatility is about **60 annualized bp *lower* on 0DTE days**, and the weekday gap disappears once 0DTEs trade daily.
  - 0DTE presence adds 0.15 pp to market-maker hedging needs (about 1% baseline). The 2SLS multiplier is about **−4** (+1 bp of hedging need goes with −4 bp of realized vol).
  - Most of the hedging need on expiry day comes from older positions rolling into 0DTE (+24 bp), not new 0DTE positions (+3 bp).
  - **Higher net gamma predicts lower volatility over the next 10 minutes**, and the effect fades within about 1 hour.
- **Relevance.** On average, dealers are long gamma and dampen intraday moves. That favors intraday range-bound outcomes on most days, which is good for short-vol sellers. On negative-gamma days the opposite holds (see Amaya et al.).

### 1.8 Amaya, Garcia-Ares, Pearson & Vasquez, "0DTE Index Options and Market Volatility: How Large is Their Impact?" (25 Jan 2025; hosted by Cboe)
- **Data.** Cboe SPX/SPXW trade records with trader capacity, July 2020 to June 2023. Market-maker net position and gamma are built at 1-minute frequency.
- **Findings.**
  - Market-maker gamma is usually positive but often negative. It usually reduces volatility, but volatility is elevated when gamma is negative.
  - The **maximum impact** of dealer gamma: **+3.3 pp** of annualized daily realized vol and **+6.4 pp** on 30-minute vol.
- **Relevance.** Negative-gamma regimes are where the short-condor tail lives. Dealer gamma cannot be computed from Alpaca data.

### 1.9 Cboe research (Mandy Xu): "Much Ado About 0DTEs" (Sept 2023) and "0DTEs Decoded" (May 2025)
- **Volume.** SPX 0DTE averaged 1.23M contracts/day (about $500bn notional) in 2023, about 50% of SPX volume. By 2025 it was nearly 2M/day.
- **Flow.** Customer flow is "remarkably balanced". In one case, a strike with 100k contracts traded left market makers only about 3k short.
- **Dealer gamma.** Average market-maker net gamma is $170M-$670M, or 0.04-0.17% of S&P futures liquidity. The 3:30 pm median is +$173M (IQR −$1.1bn to +$2.4bn).
- **Volatility.** The close-to-close vs intraday RV spread is 2.7 vol points, unchanged. There was no rise in 2-sigma 1-minute moves, including in the last hour.
- **Composition (2025).** Retail is about 50-60% of SPX 0DTE volume, falling to 47% in the April 2025 volatility spike. More than 95% of trades are limited-risk (long options or spreads); only about 4% are naked shorts.
- **Limitations.** Exchange-authored. SPX only (SPY/QQQ excluded). No P&L analysis was found. I did not find a Cboe study of intraday SPX option P&L by participant.

### 1.10 Fu, Li, Musto & Pearson, "Hope at a Reasonable Price: Customer Use of Limit Orders in the 0DTE Market" (SEC DERA WP, Mar 2025)
- **Data.** OPRA SPXW trades and quotes, July 2020 to Sept 2023.
- **Findings.**
  - In the raw data, 38% of trades appear to print "impossibly" outside the BBO. Up to 89% are out of sequence (in July 2020) because the BBO resulting from a trade is stamped before the trade.
  - After re-sequencing: customers sit at the best bid more than half the time in slightly-OTM 0DTEs (about 60% by July 2023).
  - A midpoint limit order fills about **60%** of the time. A one-tick-improving order costs about 3.2-5.8 cents net of non-fill risk, against 10 cents for a marketable order.
  - ATM BBO was one tick wide 0.5% of the time in July 2020 and **64.7% of the time in July 2023**.
- **Relevance for the bot.** **Execution style is first-order.** Passive mid-pricing roughly halves the cost of crossing. The data sequencing issue also warns against trusting trade-print "closes" as fill prices.

### 1.11 Londono & Samadi, "The Price of Macroeconomic Uncertainty: Evidence from Daily Options" (Fed IFDP 1376, June 2023; doi 10.17016/IFDP.2023.1376)
- **Data.** Daily SPX expirations, Jan 2017 to May 2023. CPI, FOMC, NFP and advance-GDP releases from the Bloomberg calendar.
- **Findings.**
  - Expirations spanning a release are dearer than neighboring expirations: **ATM up to 7.5% more expensive**, and delta-10 puts up to 6.4% dearer than delta-10 calls relative to controls.
  - **VRP for FOMC-spanning options is 44% larger.**
  - Forward equity risk premia for CPI ranged from 54 bp (May 2017) to **12.2% annualized (12 Jan 2023)**.
  - Premia rise with risk aversion and with monetary-policy and real uncertainty.
- **Relevance.** Event days are where ex-ante premium concentrates. Whether sellers *realize* it is addressed by Johannes et al. and Song.

### 1.12 Johannes, Kaeck, Seeger & Shah, "Expected 1DTE Option Returns" (Columbia; SSRN 4729757, Feb 2024) [abstract/secondary only]
- **Data.** 5-minute SPX quotes, 2012 to Dec 2023. 1DTE returns run from 15:55 ET the day before to expiry, at mid. CPI, FOMC, GDP and NFP days are compared with other days.
- **Finding.** **Raw call and put returns are not statistically significant on most days but are highly significant on macro-announcement days.** The VRP is used to separate event effects. Related work (Johannes, Kaeck & Seeger 2026, cited by Song) finds **buying straddles loses significantly over the FOMC statement window** but is insignificant over the press conference.
- **Relevance. This is the closest academic analogue to our 1DTE close-to-close trade**: entry near the prior close, expiry the next day. On ordinary days the premium is statistically zero; it shows up on event days.

### 1.13 Song, "Pre-FOMC Uncertainty Accumulation: Evidence from 0DTE Options" (HEC Montréal, 15 Sep 2026)
- **Findings.**
  - Implied FOMC event volatility rises monotonically from 10:00 ET (0.60%) to the 14:00 release (0.78%). It collapses by more than half at the statement, rebuilds ahead of the press conference, and collapses again.
  - 71% of the priced event variance resolves at the statement.
  - Implied uncertainty forecasts realized vol only in windows containing the announcement (R² 78-85%).
  - Implied event skew is uniformly negative and does not predict returns.
  - **The pre-FOMC drift disappeared after Wednesday 0DTE expiries were listed in 2016.**
- **Relevance.** On FOMC days, the optimal timing for a short-premium position is *into* the statement, with an exit soon after. Very new paper (conference version).

### 1.14 Chong & Todorov, "Do Equity and Options Markets Agree about Volatility?" (*Journal of Finance*, forthcoming; doi 10.1111/jofi.70070; HKUST summary) [abstract/secondary only]
- **Data and method.** 0DTEs, 2020-2024. Model-free restrictions on how small, frequent moves should be priced, and pseudo-arbitrage combining 0DTEs with the underlying.
- **Finding.** 0DTEs embed large premia for large moves. Volatility-arbitrage Sharpe ratios become **economically insignificant after transaction costs and estimation risk**: "0DTEs are expensive but do not appear to be mispriced."

### 1.15 Muravyev & Ni, "Why Do Option Returns Change Sign from Day to Night?" (*JFE* 136(1), 2020, 219-238; doi 10.1016/j.jfineco.2018.12.006)
- **Finding.** Delta-hedged S&P 500 index option returns average **−0.7%/day**, made up of **−1.0% close-to-open and +0.3% open-to-close**. Options under-price the intraday/overnight volatility asymmetry: sellers are paid overnight and lose intraday.
- **Relevance.** A close-to-close 1DTE seller collects the overnight decay, but the **unhedged** version also eats overnight *gaps*. Over 2024-2026 several of the largest moves were overnight or open gaps (e.g., Aug 5 2024 and April 2025). The paper's sample predates daily expiries and uses longer-dated options, so do not assume it carries over to 0DTE.

### 1.16 Baltussen, Da, Lammers & Martens, "Hedging Demand and Market Intraday Momentum" (*JFE* 142, 2021, 377-403; doi 10.1016/j.jfineco.2021.04.029)
- **Data.** 60+ futures, 1974-2020.
- **Finding.** The return from the prior close to the last half hour predicts the **last-30-minute return**. This is linked to short-gamma hedging (options and leveraged ETFs), and the predictability reverses over the following days.
- **Relevance.** On trend days a short-gamma 0DTE position faces adverse drift into the close. This argues for exiting by 15:30 (which is also Alpaca's SPY/QQQ expiry-day cutoff).

### 1.17 VIX1D papers
- **Albers (2025, *J. Futures Markets* 45(11), 2092-2108; doi 10.1002/fut.70023).** VIX1D **overstates** next-day S&P volatility, which is the VRP. A risk-premium-adjusted VIX1D beats HAR and HAR-VIX1D forecasts.
- **Zhang, Ruan & Zhang (2023, *Finance Research Letters*; doi 10.1016/j.frl.2023.104629).** VIX1D improves 1-day-ahead HAR-RV forecasts more than VIX does, but its predictive power decays faster.
- **Relevance.** The VIX1D-implied variance minus a HAR forecast from 1-minute SPY bars gives a testable ex-ante daily VRP. VIX1D also has a mechanical overnight roll artifact: it opens on average about 3.2 points below the prior close, per Cboe via Bloomberg.

### 1.18 BIS Quarterly Review: Todorov & Vilkov, Box B "What could explain the recent drop in VIX?" (March 2024)
- I could not locate a 2023 BIS box on 0DTE; this March 2024 box is the BIS item on 0DTE.
- **Findings.**
  - SPX 0DTE share went from 5% (2016) to more than 50% (Aug 2023).
  - 0DTE leverage exceeds 400.
  - **Investing in 0DTE options loses money on average: annualized −32,000%, with rare highs of +79,000%.** For 1-month options the figures are −550% and +2,500%.
  - 0DTE growth did not pull exposure away from 1-month options, so it is not the reason VIX fell. Dealer hedging of yield-enhancing structured products is the more likely volatility dampener.

### 1.19 Other items noted (not central)
- Andersen, Fusari & Todorov (2017, *JF* 72(3)), weekly options: short-dated options price jump risk, and the left-tail jump premium dominates.
- Bogousslavsky & Muravyev, "An Anatomy of Retail Option Trading": small average losses on single-leg trades.
- Bryzgalova, Pavlova & Sikorskaya (2023, *JF*): retail option losses; contested by Amaya et al. (2025).
- Practitioner sources (Option Alpha, tastylive, Hull Tactical, FlashAlpha) report high win rates for mid-morning iron flies and condors, but none control for costs over long samples. **They are excluded from the evidence base.**

---

## 2. Synthesis

### (a) Is there a documented after-cost edge for systematic 0DTE sellers or buyers?

**Buyers:** no. Every source finds negative average returns for long 0DTE premium:
- Beckmeyer et al.: −5.6% (puts) and −9.4% (calls) of margin per trade, with debit trades −0.29% worse.
- BIS: −32,000% annualized.
- Almeida et al.: ATM straddles and delta-hedged calls are significantly negative.

The exception is pre-FOMC to post-statement windows, where the evidence is thin and insignificant.

**Sellers:** a gross premium exists, but **no robust after-cost edge for unconditional sellers is documented in any structure.**

| Structure | Best evidence | After-cost verdict |
|---|---|---|
| ATM short straddle / iron fly (delta-hedged ATM write) | Almeida et al., SPX 2012-2025, ask/bid | Per-trade Sharpe −0.01 to −0.04 at every entry 10:00-14:00 |
| Iron condor / butterfly | Vilkov (corrected), SPX 2016-2026 | SR −0.56 at mid, −2.67 net (worst structure) |
| Iron condor (retail, realized) | Beckmeyer et al. | Mean −1.1% of margin, median +5.5% |
| Put/call credit spreads | Beckmeyer (retail): put spread mean +0.1%, median +3%; Vilkov: bear put spread net −0.73 | About zero at best |
| Put ratio spread / risk reversal | Vilkov | Ratio spread −0.61 net (and needs naked exposure; not level-3 eligible). Risk reversal +0.10 net, the only non-negative one but insignificant, and it needs a naked short leg |
| Vol arbitrage (0DTE plus underlying) | Chong-Todorov | Economically insignificant after costs |
| SSD-mispricing filter | Almeida et al. | Positive before 2022, gone since daily expiries |

**Entry time.** The VRP per hour is larger later in the day: annualized VRP rises from 1.8 at 10:00 to 3.0 at 14:00, and ATM straddle negativity is most significant from 12:00 to 14:00. Spreads are narrowest in roughly the 10:00-14:00 window (Almeida et al.). No paper shows that a particular entry time turns the after-cost sign positive. **Delta and moneyness:** the premium is ATM. OTM 0DTE options are not over-priced relative to risk-averse bounds and are often under-priced (Almeida, Table 4), so 1σ OTM short strikes sell the cheap part of the smile. **Wing width:** the literature does not test it directly, but narrower wings cost more premium per unit of risk because the wing is closer to ATM. Under fat tails, a condor's loss distribution is dominated by max-loss days (Beckmeyer P5 = −100%).

### (b) What do technical or intraday signals add?
- **Realized skewness drives P&L** (Vilkov: 20-40% of the variance of directional-spread P&L). Realized variance matters much less, and implied measures add only 2-7 pp of R². Forecasting intraday skewness and direction is the binding problem, and nobody reports a reliable forecaster that survives costs.
- **The volatility premium is predictable; the return premium is not.** The 10:30 surface (leverage and vol-of-vol) predicts the remainder-of-day volatility premium with R² of about 21-35% (Bandi et al.). That supports a *sizing/filter* signal ("sell only when the surface says the VRP is large"), not a directional one.
- **VRP⁺ predicts lower intraday returns** (Almeida et al.), a weak bearish tilt with R² of 1-5%.
- **VIX1D vs a realized-vol forecast:** VIX1D over-predicts RV (Albers). The VIX1D²-minus-HAR spread is a natural ex-ante VRP. It has not been tested as a 0DTE trade filter after costs in the papers found.
- **Intraday momentum into the close** (Baltussen et al.) and **dealer gamma** (Adams et al.: positive gamma lowers 10-60-minute volatility; Amaya et al.: negative gamma raises it by up to 6.4 vol points over 30 minutes) are documented. Dealer gamma is not observable from Alpaca data.
- **Opening range, VWAP, gaps:** **No peer-reviewed 0DTE paper tests opening-range or VWAP rules.** Practitioner claims are not evidence. The closest academic hook is the opening gap/overnight return as a predictor of intraday variance and of last-half-hour drift.

### (c) Macro events (CPI, FOMC, NFP)
- **Ex ante:** options spanning releases are richer. ATM is up to 7.5% dearer, FOMC VRP is 44% larger, and CPI-day forward equity premia reached 12.2% annualized in early 2023 (Londono-Samadi).
- **Realized:** 1DTE option returns are only significant on announcement days (Johannes et al.). Straddle buyers lose over the FOMC statement window (Johannes-Kaeck-Seeger 2026, via Song). On FOMC days, implied event vol accumulates from 10:00 to 14:00 and then collapses: 71% of the priced variance resolves at the statement (Song).
- **Microstructure:** 0DTE/1DTE volume drops in the 30 minutes before FOMC and rebounds after (Dim et al.). The volume peak shifts to about 14:00 (Bandi et al.).
- **Implication:** if any short-premium edge survives, it is most likely in **event-spanning trades** (sell into the event, exit soon after resolution). The ordinary-day premium is statistically indistinguishable from zero at 1DTE. The tail risk is fat on exactly these days, though, so sizing must assume max loss.

### (d) Concrete testable rules for an Alpaca daily-bar + 1-minute-bar backtest

Data caveats first. Alpaca option *bars* are built from trades, not quotes. Their "close" can be stale for OTM wings, and Fu et al. show trade/quote sequencing is unreliable even on OPRA. Use bar VWAP or close only as a mid proxy, and charge an explicit half-spread per leg that you calibrate from live Alpaca quote snapshots, for example 1-2 cents per leg for SPY near-ATM 0DTE and more for wings. Report results at 0x, 1x and 2x that cost. Cluster standard errors by date, because SPY and QQQ trades on the same day are highly correlated, so the effective N is about the number of days, not trades.

1. **R1: ATM iron butterfly versus 1σ condor, intraday, fixed entry.**
   - Entry: SPY 0DTE at 10:00, 12:00 or 14:00 ET (1-minute bars).
   - Structure: short ATM straddle, long wings at ±k × the remaining-day implied move (k = 1, 1.5) or fixed $3/$5/$10. Compare with shorts at ±1σ.
   - Exit: a hard time exit at 15:25 ET (Alpaca rejects SPY/QQQ orders on expiry day after 15:30), plus variants with a profit-take (25-50% of credit) and no stop.
   - Prediction from the literature: the ATM fly beats the 1σ condor at the mid, and both are about zero or negative after costs.
2. **R2: event-day selling.**
   - Sell an SPY 0DTE iron fly at 10:00 on FOMC days and exit at 14:05-14:15, just after the statement, versus holding to 15:25.
   - Separately, sell at 10:00 on CPI/NFP days, after the 8:30 release.
   - Compare with matched non-event days.
   - Also test the 1DTE version: enter at the close before CPI/NFP/FOMC, exit at the next day's 10:00 or 15:25.
   - The literature predicts the premium concentrates here (Londono-Samadi, Johannes et al., Song).
3. **R3: VRP filter.**
   - Signal: ex-ante VRP = (VIX1D/100)² × (remaining-day fraction) − HAR-RV forecast of remaining-day variance, built from 1-minute SPY returns. VIX1D comes as a free Cboe CSV; it is not on Alpaca. Alternatively, use the SPY 0DTE ATM straddle price squared at entry as the implied variance.
   - Rule: trade R1 only in the top tercile of the VRP signal, and skip days with an opening gap above about 1σ or a VIX regime jump.
   - Out-of-sample: use an expanding window with no parameter search on the test set.
4. **R4: skew-aware strikes.** Place shorts by delta (for example 15-25Δ) instead of a symmetric ±1× move, or skip the call side in up-trends, because a symmetric move ignores skew and drift. Test put-only versus call-only credit spreads separately.
5. **R5: our 1DTE close-to-close book, decomposed.** Split P&L into (i) the overnight leg (close to next open, mark at the 9:31 1-minute bar) and (ii) the intraday leg. Split again by put side vs call side, by event vs non-event day, and by gap size. This tells you whether the losses are gap-driven (Muravyev-Ni logic) or drift/skew-driven.

**What cannot be tested well on Alpaca:**
- True historical bid/ask and fill rates for 4-leg combos. Alpaca's historical options data are bars and trades; verify whether historical quotes exist. Midpoint-limit fill probability (about 60% on SPXW per Fu et al.) cannot be inferred.
- Anything before 2024-02, so no regime coverage of 2020-2022, the pre-daily-expiry era where most "edges" lived.
- SPX/XSP cash-settled European options, which Alpaca does not list.
- Dealer gamma and customer open/close flow (Cboe data).
- After-hours settlement and pin outcomes, exercise-by-exception, and early assignment around SPY/QQQ ex-dividend dates.
- Queue position and partial fills.

### (e) Risks specific to the implementation
- **Gap risk (1DTE and anything held overnight).** Overnight moves are unhedgeable with options level 3 and no futures. A 1σ condor's max loss is hit on roughly 2σ+ days, and those cluster in volatility episodes (Aug 2024, Apr 2025). The ES(1%) of 0DTE structures is 0.6-1.6% of spot per day (Vilkov). Size so that a max-loss day is tolerable.
- **Pin and settlement risk (SPY and QQQ are American, physically settled; SPX/XSP are European, cash-settled).**
  - OCC auto-exercises anything $0.01 or more in the money.
  - SPY trades after hours, and holders can submit contrary exercise instructions until the OCC cutoff (about 17:30 ET; Alpaca's do-not-exercise deadline is 16:30). So a short strike near the 16:00 close can be assigned *after* the long wing expires worthless, leaving a 100-share position per contract (about $55-60k notional per SPY contract against a $500 max-risk spread) over the weekend or night.
  - Alpaca **rejects new orders on expiring SPY/QQQ contracts after 15:30 ET** (15:15 for other equity options). Alpaca may liquidate ITM positions if buying power is insufficient. To exercise a long put, the account needs available shares, otherwise Alpaca places a do-not-exercise on it.
  - Practical rule: **flatten every expiring spread with any leg within about 0.3% of spot by 15:25.** Our backtest's "settle at the next close" assumption is not implementable as written on Alpaca.
- **Early assignment.** American short calls are at risk on the day before SPY/QQQ ex-dividend dates when the call is ITM and its extrinsic value is below the dividend. Short ITM puts can be assigned early after large down moves.
- **Liquidity at entry and exit.** SPY 0DTE near-ATM spreads are a penny or two, but wings and late-day quotes widen. Four legs means four half-spreads at entry, and four more at exit if you close early rather than expire. On SPXW, the mean half-spread of about 2.2 bp of spot was enough to flip every strategy negative (Vilkov). Using passive limit orders at mid matters a lot (Fu et al.), but non-fills bias the realized sample toward adverse days.
- **Regime and structural break.** Several results changed sign or vanished after May 2022 (Almeida: SSD strategy; Vilkov: P&L sign). Our 2024-2026 data sit entirely in the post-break, high-competition regime.

---

## 3. Reconciling our result: 1DTE SPY/QQQ iron condors, −3.8% of max risk per trade (1,377 trades, t = −3.4), negative even at zero slippage

Our trade: at the close, sell 1DTE SPY/QQQ iron condors with shorts at ±1× the implied move and $5 wings, and settle at the next close. The literature explains the loss, and a positive result would have been the surprise. Most important first:

1. **We sold the part of the smile that is not overpriced.** The 0DTE premium is ATM: about 80% of ATM prices exceed the risk-averse upper bound, while OTM 0DTE options are often *below* the lower bound (Almeida et al.). Shorts at 1× the implied move, about 16-20 delta and roughly 0.8-1.2% OTM on SPY, plus $5 long wings, net out most of the ATM richness. Vilkov's corrected panel shows iron condors/butterflies with a **negative Sharpe even at the mid (−0.56)**, which matches our "still negative at zero slippage".
2. **1DTE is the weakest point of the VRP term structure.** In Almeida et al., the 1DTE VRP has its 25th percentile at about 0, its downside component is insignificant, and after May 2022 the 25th percentile is negative. Johannes et al. find 1DTE option returns insignificant on ordinary days. Dim et al. put the daily 0DTE VRP at about 1 bp. Gross expected premium on a typical day is about zero, before any drift or cost.
3. **A symmetric ±1× move ignores skew and drift.** The risk-neutral distribution is left-skewed, so the call at +1× the move is priced at lower IV than the put. In a 2024-2026 market with strong upward drift, especially in QQQ, the cheap call side is breached more often than priced. Almeida's result that 0DTE prices put a premium on *upside* variation (VRP⁺) means call-side compensation exists ATM, but not at a fixed symmetric strike once drift is accounted for. **Check: R5's put-side vs call-side split.** Prediction: most losses come from the call side on up-trend days and from the put side on a few gap-down days.
4. **Close-to-close holds the overnight gap unhedged.** Muravyev-Ni say sellers are paid for overnight variance on a *delta-hedged* basis. Our unhedged condor takes the gap directly, and 2024-2026 had several large overnight or open gaps. QQQ also carries after-hours mega-cap earnings and 8:30 macro prints. Max-loss days (P5 = −100% of margin in Beckmeyer's retail condors) dominate the mean, which gives the "high win rate, negative mean" profile.
5. **There was no event conditioning.** The premium that does exist concentrates on CPI/FOMC/NFP-spanning expiries (Londono-Samadi; Johannes et al.). An unconditional daily book is mostly ordinary days with zero premium.
6. **Measurement and statistics.**
   - "Zero slippage" on Alpaca daily bars is not a mid-quote backtest. Trade-based closes on OTM wings can be stale, often from hours earlier than the 16:00 underlying close. That adds noise and a possible bias, because entry credits are computed from prints that do not reflect the 16:00 smile.
   - SPY and QQQ condors on the same date are highly correlated (ρ ≈ 0.9). The 1,377 trades are roughly 690 independent days, so the date-clustered t-statistic is probably around −2.4 to −3 rather than −3.4. That is still negative, but less certain.
   - The magnitude (−3.8% of max risk) is in line with a strategy whose gross premium is about zero, carrying four legs of SPY spread, plus a drift/skew loss of a few percent of risk.

**Net:** our result matches the corrected academic consensus. Unconditional, OTM, defined-risk short-vol on ultra-short index options has no after-cost edge in the post-2022 regime, and condors are the worst of the standard structures.

---

## 4. Verdict and recommendations
- **Do not deploy an unconditional 0DTE/1DTE condor or credit-spread seller.** No paper supports it after costs, the best-documented academic attempt flipped negative once costs were charged correctly, and our own backtest agrees.
- **A 0DTE bot is worthwhile only as a tightly scoped experiment.** Run R1-R3 on 1-minute SPY data with explicit per-leg costs, date-clustered inference, and a pre-registered out-of-sample split (2024-02 to 2025-06 in-sample, the rest out-of-sample). Promote to paper trading only if the net Sharpe is above about 1 annualized at 1x cost *and* still positive at 2x cost. Never hold expiring SPY/QQQ legs past 15:25 ET.
- **If anything works, expect it to be event-day ATM iron flies or a VRP-filtered ATM fly with a midday entry.** It will not be OTM condors, and it will not be technical-signal directional bets (no peer-reviewed support for opening-range or VWAP rules in 0DTE).

---

## 5. References
All entries were found via web search and are **not LibKey-resolved**. "Full text read" means I read an open PDF copy; "abstract/secondary" means I relied on the abstract, the author's repository or citing papers.

1. Almeida, C., Freire, G., & Hizmeri, R. (2025). *0DTE Asset Pricing*. Working paper, draft May 23, 2025 (first draft Jan 2024). SSRN 4701401. Full text read: https://www.fma.org/assets/docs/Derivatives2025/Almeida.pdf; earlier version: https://www.paris-december.eu/sites/default/files//papers/2024/5431_gustavobfreire_2024_complete.pdf. Found via web; not LibKey-resolved.
2. Bandi, F. M., Fusari, N., & Renò, R. (2024). *0DTE Option Pricing*. Working paper, draft Feb 19, 2024; cited as *Journal of Finance*, forthcoming. SSRN 4503344, doi:10.2139/ssrn.4503344. Full text read (WFA portal): https://westernfinance-portal.org/viewpaper?n=915376; SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4503344. Found via web; not LibKey-resolved.
3. Bandi, F. M., & Fusari, N. (2026). *Ultra-short-term volatility surfaces*. arXiv:2603.29430. https://arxiv.org/abs/2603.29430. Found via web; not LibKey-resolved.
4. Vilkov, G. (2023/2026). *0DTE Trading Rules* (later subtitled *Tail Risk, Implementation, and Tactical Timing*). SSRN 4641356, doi:10.2139/ssrn.4641356. https://ssrn.com/abstract=4641356. Replication package and Aug 2026 cost correction: https://github.com/vilkovgr/0dte-strategies (KNOWN-ISSUES.md; docs/agent-context/results-summary.md). Abstract/secondary plus repository. Found via web; not LibKey-resolved.
5. Dim, C., Eraker, B., & Vilkov, G. (2024). *0DTEs: Trading, Gamma Risk and Volatility Propagation*. Working paper, May 14, 2024. SSRN 4692190. Full text read: https://westernfinance-portal.org/viewpaper?n=950096. Found via web; not LibKey-resolved.
6. Beckmeyer, H., Branger, N., & Gayda, L. (2023). *Retail Traders Love 0DTE Options… But Should They?* Working paper, Dec 15, 2023 (first version Mar 30, 2023). SSRN 4404704, doi:10.2139/ssrn.4404704. Full text read: https://wp.lancs.ac.uk/fofi2024/files/2024/04/FoFI-2024-146-Leander-Gayda.pdf. Found via web; not LibKey-resolved.
7. Brogaard, J., Han, J., & Won, P. Y. (2023, rev. 2026). *How Does Zero-Day-to-Expiry Options Trading Affect the Volatility of Underlying Assets?* (revised as *Does 0DTE Options Trading Increase Volatility?*). SSRN 4426358. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4426358. Abstract/secondary only. Found via web; not LibKey-resolved.
8. Adams, G., Fontaine, J.-S., & Ornthanalai, C. (2024; rev. Oct 2025). *Do S&P 500 Options Increase Market Volatility? Evidence from 0DTEs*. SSRN 5641974 (per Quantpedia/SIFMA; exact title of the 2024 version unverified). Summary: https://quantpedia.com/do-sp500-0dtes-options-increase-market-volatility/. Abstract/secondary only. Found via web; not LibKey-resolved.
9. Amaya, D., Garcia-Ares, P. A., Pearson, N. D., & Vasquez, A. (2025). *0DTE Index Options and Market Volatility: How Large is Their Impact?* Working paper, Jan 25, 2025. Full text read: https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf. Found via web; not LibKey-resolved.
10. Amaya, D., Garcia-Ares, P. A., Pearson, N. D., & Vasquez, A. (2025). *New Evidence on the Performance of Customer Options Trades*. Working paper, Apr 2025. https://cdn.cboe.com/resources/education/research_publications/Retail_Profitability.pdf. Found via web; not LibKey-resolved.
11. Fu, L., Li, S., Musto, D. K., & Pearson, N. D. (2025). *Hope at a Reasonable Price: Customer Use of Limit Orders in the 0DTE Market*. SEC DERA Working Paper, Mar 2025. Full text read: https://www.sec.gov/files/dera-hope-reasonable-prc-2503.pdf. Found via web; not LibKey-resolved.
12. Londono, J. M., & Samadi, M. (2023). *The Price of Macroeconomic Uncertainty: Evidence from Daily Options*. International Finance Discussion Papers 1376, Board of Governors of the Federal Reserve System. doi:10.17016/IFDP.2023.1376. Full text read: https://www.federalreserve.gov/econres/ifdp/files/ifdp1376.pdf. Found via web; not LibKey-resolved.
13. Johannes, M., Kaeck, A., Seeger, N., & Shah, N. (2024). *Expected 1DTE Option Returns*. Columbia Business School Research Paper; SSRN 4729757, doi:10.2139/ssrn.4729757. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4729757. Abstract/secondary only. Found via web; not LibKey-resolved.
14. Song, Y. (2026). *Pre-FOMC Uncertainty Accumulation: Evidence from 0DTE Options*. Working paper, Sep 15, 2026 (AFA submission). Full text read: https://afajof.org/management/viewp.php?n=260400. Found via web; not LibKey-resolved.
15. Chong, C., & Todorov, V. (forthcoming). *Do Equity and Options Markets Agree about Volatility?* *Journal of Finance*. doi:10.1111/jofi.70070. https://onlinelibrary.wiley.com/doi/10.1111/jofi.70070; summary: https://bm.hkust.edu.hk/bizinsight/2026/04/are-0dte-options-mispriced (related SSRN 4933153). Abstract/secondary only. Found via web; not LibKey-resolved.
16. Muravyev, D., & Ni, X. (2020). Why do option returns change sign from day to night? *Journal of Financial Economics*, 136(1), 219-238. doi:10.1016/j.jfineco.2018.12.006. https://ideas.repec.org/a/eee/jfinec/v136y2020i1p219-238.html. Abstract only. Found via web; not LibKey-resolved.
17. Baltussen, G., Da, Z., Lammers, S., & Martens, M. (2021). Hedging demand and market intraday momentum. *Journal of Financial Economics*, 142(1), 377-403. doi:10.1016/j.jfineco.2021.04.029. Working copy: https://www3.nd.edu/~zda/intramom.pdf. Abstract/secondary only. Found via web; not LibKey-resolved.
18. Todorov, K., & Vilkov, G. (2024). Box B: What could explain the recent drop in VIX? *BIS Quarterly Review*, March 2024, 6-8. Full text read: https://www.bis.org/publications/markets-count-smooth-landing_1.pdf. Found via web; not LibKey-resolved.
19. Xu, M. (2023). *Volatility Insights: Much Ado About 0DTEs - Evaluating the Market Impact of SPX 0DTE Options*. Cboe Global Markets, Sept 2023. https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options. Found via web; not LibKey-resolved.
20. Xu, M. (2025). *0DTEs Decoded: Positioning, Trends, and Market Impact*. Cboe Global Markets, May 2, 2025. https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact. Found via web; not LibKey-resolved.
21. Albers, S. (2025). Does the VIX1D forecast next-day volatility? *Journal of Futures Markets*, 45(11), 2092-2108. doi:10.1002/fut.70023. https://ideas.repec.org/a/wly/jfutmk/v45y2025i11p2092-2108.html. Abstract only; the exact article title should be verified. Found via web; not LibKey-resolved.
22. Zhang, Ruan & Zhang (2023). VIX1D and one-day-ahead HAR-RV forecasting. *Finance Research Letters*. doi:10.1016/j.frl.2023.104629. Abstract/secondary only; the exact title and full author names should be verified. Found via web; not LibKey-resolved.
23. Andersen, T. G., Fusari, N., & Todorov, V. (2017). Short-term market risks implied by weekly options. *Journal of Finance*, 72(3), 1335-1386. doi:10.1111/jofi.12486. Background only. Found via web; not LibKey-resolved.
24. Alpaca Markets support: "What are the cutoff times for trading 0DTE options on Alpaca?" https://alpaca.markets/support/what-are-the-cutoff-times-for-trading-0dte-options-on-alpaca, and "Why was my long option position liquidated…" https://alpaca.markets/support/why-was-my-long-option-position-liquidated-or-short-option-position-closed-out-by-alpaca-on-expiration. Broker documentation, not research. Found via web; not LibKey-resolved.
