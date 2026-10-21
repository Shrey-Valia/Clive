# Pairs Trading: Literature Review for a Retail Alpaca Backtest

Scope: equity pairs trading and the statistical arbitrage methods that grew out of it. The reader trades on Alpaca: paper account, shorts limited to easy-to-borrow (ETB) names, daily and minute bars from 2016, options level 3.

How to read the numbers:
- **[full text]** means I read the paper itself (journal PDF or the authors' working-paper PDF) and took the number from its tables or text.
- **[verify]** means the number came from an abstract, a later paper's summary, or a secondary source such as CXO Advisory, Quantpedia or a forum. Check it against the paper before relying on it.
- All sources were found via web search. None were LibKey-resolved. Working-paper numbers can differ from the published versions.

---

## 0. Bottom line

1. **The classic edge has mostly gone in US large caps.** The best direct evidence is Clegg & Krauss (2018) [full text], who use survivor-bias-free S&P 500 constituents. From January 2010 to October 2015, after 5 bp per half-turn, annualized returns were:
   - GGR distance: −0.2%
   - Two cointegration variants: −1.7% and −0.6%
   - Their own partial-cointegration method: −2.8%

   In the same window the S&P 500 returned about +13% a year. Krauss, Do & Huck (2017) [full text] report −14% to −25% a year after costs for ML stat-arb on the S&P 500 in that window.
2. **Gross returns fall sharply as you move into liquid names.** Zhu (2024) [full text, an undergraduate essay advised by Goetzmann] replicates GGR on all CRSP stocks for 2003–2023. The baseline earns 0.46% a month gross. Restricted to the top quartile by volume, it earns only 0.19% a month gross (about 2.3% a year, Sharpe 0.56) before explicit costs. Do & Faff (2012) [verify] report 0.19% a month net for their best intra-industry rules in the largest 30% of stocks over 1963–2009, with almost all of the post-1989 profit earned in the 2000–2002 bear market.
3. **Pairs trading pays in crises.** It is a liquidity-provision trade, so its returns cluster in market dislocations:
   - Clegg & Krauss: distance pairs earned +5.2% a year after costs from September 2008 to December 2009, against −3.6% for the market.
   - Do & Faff (2010) and Krauss, Do & Huck (2017) find the same pattern.

   The other side of this is crowding. In August 2007 a generic one-day contrarian stat-arb book lost about 1.2%, 2.8% and 2.8% unleveraged on August 7, 8 and 9 (roughly a quarter of capital at 8:1 leverage), then recovered most of it on August 10 (Khandani & Lo 2007) [full text].
4. **Which method does best depends on the era:**
   - Over 1962–2014, distance ≈ cointegration > copula after costs: 38 / 33 / 5 bp a month (Rad, Low & Faff 2016) [verify].
   - Partial cointegration beat both on the S&P 500 over 1990–2015: Sharpe 1.11 net against negative Sharpe for GGR and cointegration (Clegg & Krauss) [full text]. It did not survive 2010–2015.
   - Factor-residual (PCA or ETF) approaches plus ML signal extraction have the best published numbers. Guijarro-Ordonez, Pelger & Zanotti report a net Sharpe of about 1.0–1.2 for 2002–2016 on the 550 largest US stocks with 5 bp per trade plus 1 bp a day short-holding cost [full text, arXiv v2]. They use a 4-year rolling deep model, which is far beyond a retail daily backtest.
   - A parametric OU-threshold rule on the same PCA/IPCA residuals has a gross Sharpe of only about 0.6–0.97 for 2002–2016 [full text, GPZ Table I].
5. **The core risk is non-convergence.** Pairs that diverge and never come back cause most of the losses:
   - Clegg & Krauss: only 46% of GGR trades closed by convergence within the 6-month window on the S&P 500 [full text].
   - Do & Faff (2010): about 32% of distance-selected pairs never converge [verify, via the Krauss 2017 survey].
   - Divergences driven by firm-specific news tend to be permanent (Engelberg, Gao & Jagannathan 2009) [verify].

**Verdict:** A textbook GGR or cointegration pairs book on US large caps over 2016–2026 will most likely net about 0%, give or take a few percent a year, with a Sharpe below 0.5. Most of any profit will come in stress episodes such as early 2020 and 2022. It is still worth backtesting, because four modern refinements are cheap to test on daily bars:
- residuals against sector ETFs rather than single-stock pairs;
- a half-life (OU speed) filter;
- an earnings and news blackout;
- an ETB-only universe with realistic costs.

The aim of the backtest should be to find out whether anything beats zero after costs, not to tune parameters until something does.

---

## 1. Paper-by-paper review

### 1.1 Gatev, Goetzmann & Rouwenhorst (2006), "Pairs Trading: Performance of a Relative-Value Arbitrage Rule", RFS [full text, journal-version PDF]

- **Question:** Does a simple, rule-based pairs strategy earn excess returns, and is that compensation for risk or a mispricing?
- **Data:** CRSP daily, all liquid US stocks, 1962–2002. Stocks with any non-trading day are excluded.
- **Method:**
  - Formation is **12 months**: normalize cumulative total-return indexes, match each stock with the partner that minimizes the sum of squared deviations (SSD), and rank pairs.
  - Trading is **6 months**, on the top 5, top 20, and pairs 101–120.
  - **Open when the spread diverges by more than 2 formation-period σ. Close at the next crossing.** Anything still open at the end of the 6 months is closed then. Delisted stocks use the CRSP delisting return.
  - There is no stop-loss. Positions are $1 long and $1 short.
  - A "wait one day" variant enters on the day after the signal, to strip out bid-ask bounce.
  - There are six overlapping portfolios, one started each month.
- **Key numbers:**
  - Top 20, no wait: **1.44% a month** fully invested (t = 11.56), 0.81% a month on committed capital.
  - Top 20, wait one day: **0.90% a month** fully invested, 0.52% a month committed.
  - The annualized fully-invested excess return of about 11% is the headline maximum. The authors' own summary gives about 10.4% a year with 3.8% volatility for the top 20.
  - From the one-day-wait drop they infer roughly **162 bp per pair per round trip**, an effective spread of about 81 bp. That is the 1962–2002 cost level.
  - Top-5 trade statistics: 4.81 of 5 pairs open per period, **2.02 round trips per pair**, **average open duration 3.75 months**. For the top 20: 19.30 open, 1.96 round trips, 3.76 months.
  - Same-industry pairs (S&P's Utilities, Transportation, Financials, Industrials) are all significant. Utilities is best, at 1.08% a month for the top 20 with wait one day. About 71% of the stocks in top-20 pairs are utilities.
  - Restricting to the top 3 size deciles changes profits little: top 20 is down about 2 bp a month.
  - Simulated short recalls on high-volume days cost 4–13 bp a month. The top 20 still earns 85 bp a month (t = 9.07).
- **Decline:** The top-20 raw excess return fell from **118 bp a month before 1989 to about 38 bp a month after 1988**. Risk-adjusted return fell from 67 to 42 bp a month and stayed significant. The authors read this as a "dormant" common risk factor (pairs portfolios are correlated with each other) rather than plain arbitrage-away.
- **Limitations:**
  - Pre-decimalization spreads and bid-ask bounce.
  - SSD selects low-variance spreads with little profit potential.
  - No cointegration test, so some pairs are spurious.
  - Shorting assumed available.
  - 12m/6m was chosen arbitrarily, and Huck (2013) shows results are sensitive to it [verify].

### 1.2 Do & Faff (2010), "Does Simple Pairs Trading Still Work?", Financial Analysts Journal 66(4) [verify, abstract plus secondary]

- **Question:** Do GGR profits persist after 2002?
- **Data:** CRSP, 1962–2009. This is the GGR universe with seven more years.
- **Method:** The GGR rule, plus refinements: industry homogeneity, and the number of zero crossings of the spread in the formation period as a measure of how often mispricings corrected in the past.
- **Key numbers:**
  - Profitability keeps declining. The pattern of monthly excess returns quoted in Clegg & Krauss [full text] is 0.56% a month for 1989–2002 and 0.33% for 2003–2009, both gross.
  - The strategy does well in prolonged turbulent markets, including 2008–09.
  - Combining SSD, industry homogeneity and zero crossings adds about 22 bp a month for banks.
  - About 32% of distance pairs never converge (as reported in the Krauss 2017 survey).
  - The rising share of non-convergent pairs is a main cause of the decline [verify].
- **Limitations:** Pre-cost figures. Many variants were tried without data-snooping correction. Do & Faff also could not replicate the Papadakis & Wysocki event results.

### 1.3 Do & Faff (2012), "Are Pairs Trading Profits Robust to Trading Costs?", Journal of Financial Research 35(2) [verify, abstract plus CXO summary of the working paper]

- **Data:** US equities, 1963–2009.
- **Method:** 29 refinements of GGR. Costs are time-varying: commissions, market impact, and a 1% a year short fee.
- **Key numbers:**
  - One-way frictions were about 0.81% (1963–88) and 0.33% (1989–2009).
  - The 29 variants earn 0.70–1.15% a month gross (mean 0.93%) and **−0.07% to +0.35% a month net (mean 0.12%)**.
  - The baseline GGR top-20 is **unprofitable net**.
  - The best four variants are all intra-industry, with refined industry groups and excluding very-low-SSD pairs. They earn **about 0.29–0.30% a month net**, roughly 3.5% a year.
  - Restricted to the largest 30% of stocks, the net alpha is **about 24 bp a month** (abstract) or 0.19% a month raw (summary).
  - Pairs trading and industry-relative short-term reversal are **"largely unprofitable in the period post 2002"** (abstract).
- **Limitations:** Data snooping across the 29 variants. Retail frictions in 2009 were higher than assumed. Today's frictions are lower but so are gross returns.

### 1.4 Jacobs & Weber (2015), "On the Determinants of Pairs Trading Profitability", Journal of Financial Markets 23 [verify, abstract plus Krauss survey]

- **Question:** What drives the time variation in pairs profits?
- **Data:** The US plus **34 international markets**. An earlier version covers the US 1960–2008.
- **Method:** A GGR variant. Divergence events are classified by news type, investor attention and limits to arbitrage.
- **Findings:**
  - Abnormal returns are a **persistent phenomenon** internationally.
  - Profits depend on (i) what type of news caused the divergence, (ii) investor attention, with pairs opened on high-distraction "high-divergence" days converging more often, and (iii) limits to arbitrage.
  - Profitability also changes sharply from year to year, with sharp drops and recoveries.
- **Limitations:** Gross returns. The US sample ends around 2008.

### 1.5 Engelberg, Gao & Jagannathan (2009), "An Anatomy of Pairs Trading: The Role of Idiosyncratic News, Common Information and Liquidity", SSRN 1330689 [verify, abstract plus Krauss survey]

- **Data:** CRSP, 1993–2006, GGR variant.
- **Findings:**
  - Profits **decrease roughly exponentially with time since divergence**, so most of the edge is in the first days.
  - Profits are **higher** when divergence comes from a temporary liquidity shock to one leg, or from common news that one leg absorbs faster (a lead-lag effect).
  - Profits are **lower** when divergence comes from value-relevant **idiosyncratic news**, which tends to be permanent.
  - Profits are lower when the same institutions hold both stocks or the same analysts cover both.
- **Practical implication:** Use an event or news blackout. Treat divergence on an earnings date or M&A headline as a permanent break, and either skip the trade or use a tight time stop.

### 1.6 Papadakis & Wysocki (2007), "Pairs Trading and Accounting Information", working paper (Boston University / MIT) [verify, secondary only; the paper itself was not found online]

- **Data:** US, 1981–2006, GGR rules.
- **Findings:**
  - Pair openings cluster around earnings announcements and analyst forecast revisions.
  - Trades triggered by these events are **significantly less profitable** than non-event trades, consistent with post-event drift.
  - Delaying closes by up to about three weeks after events adds return.
- **Caveat:** Do & Faff (2010) could not replicate this on the extended sample. Still, the "avoid earnings-driven divergences" lesson agrees with Engelberg et al.

### 1.7 Rad, Low & Faff (2016), "The Profitability of Pairs Trading Strategies: Distance, Cointegration and Copula Methods", Quantitative Finance 16(10) [verify, abstract plus CXO summary]

- **Data:** Liquid US common stocks, **1962–2014**.
- **Method:**
  - Formation 12 months, trading 6 months, six overlapping portfolios, top 20 pairs per method.
  - Distance and cointegration open at **2σ** and close at convergence or period end.
  - Copula pairs are opened from conditional-probability (mispricing-index) signals.
  - Costs: round-trip commissions fall from 0.70% (1962) to 0.09% (recent), market impact is 0.30% before 1989 and 0.20% after, and short costs are set to zero.
- **Key numbers:**
  - Mean monthly excess return **before costs: 91 / 85 / 43 bp** (distance / cointegration / copula). **After costs: 38 / 33 / 5 bp.**
  - Net Sharpe is about **0.33 / 0.34 / 0.08** [verify, CXO].
  - Trades still open at period end have negative average returns for every method. Distance has the fewest open at period end, copula the most. Copula's unconverged trades do better than the others' unconverged trades.
  - Trading opportunities **fall sharply after 2009** for distance and cointegration: −40% and −35% from 2007–11 to 2012–14. The copula's frequency stays stable.
  - Cointegration is best in turbulent markets. Performance peaked around 1990 and has been unattractive since about 2000 (CXO's reading).
- **Limitations:** Short costs ignored. Universe includes smaller caps. Little post-2009 return detail in the summaries.

### 1.8 Krauss (2017), "Statistical Arbitrage Pairs Trading Strategies: Review and Outlook", Journal of Economic Surveys 31(2) [full text, FAU working-paper version 09/2015]

- **Scope:** More than 90 papers, grouped into distance, cointegration, time-series (OU and state space), stochastic control, and other approaches (ML, copula, PCA).
- **Conclusions:**
  - **Distance:** Simple and robust to data snooping, but **SSD picks low-variance spreads with substantial divergence risk** because there is no cointegration test. Pearson correlation does slightly better. **Quasi-multivariate pairs (a stock against a portfolio of its matches) generally beat single pairs.** Profitability declines over time.
  - **Cointegration:** More rigorous. Huck & Afawubo (2015, S&P 500) find it **significantly outperforms distance after risk loadings and costs**, and cointegrated spreads are about twice as volatile, which gives more profit per trade [verify]. Most studies **ignore multiple-testing (family-wise error)** when testing thousands of pairs for cointegration, which inflates false positives.
  - **Time series:** Elliott et al. (2005) and Avellaneda & Lee (2010) are the main large-scale applications. The recommended pipeline is: select pairs with cointegration, time trades with an OU/state-space model, and size with stochastic control.
  - Huck (2013) [verify]: GGR results on the S&P 500 are very sensitive to formation length. **6, 18 and 24 months work well and 12 months slumps.** This argues for a parameter-robustness check, not for tuning.

### 1.9 Vidyamurthy (2004), *Pairs Trading: Quantitative Methods and Analysis* (Wiley); Engle & Granger (1987); Johansen (1988, 1991) [textbook and econometrics; no performance numbers]

- **Vidyamurthy:** The practitioner's cointegration framework. The spread is p_A − γ·p_B in log prices. γ comes from a regression over the formation window, and stationarity is tested with an ADF test on the residual (the Engle-Granger two-step). The book proposes heuristics such as a high zero-crossing rate and a common-trend check. Krauss notes they have not been rigorously tested on large samples.
- **Engle-Granger (1987):** Two-step residual-based cointegration test. Simple, but the result depends on which leg is the dependent variable, and it is weak in small samples.
- **Johansen (1988, 1991):** Maximum-likelihood VECM rank test (trace and max-eigenvalue). It is symmetric across legs and handles baskets of more than two assets.
- **Practical note:** On a daily retail backtest, Engle-Granger on log prices with a 252-day window (p < 0.05) plus a half-life filter is standard. Johansen is useful for 3-asset baskets, for example a stock against two sector ETFs. With about 500 stocks there are about 125,000 pairs, so about 6,000 will pass at 5% by chance. Restricting candidates to the same industry and using a Bonferroni or FDR correction is essential.

### 1.10 Elliott, van der Hoek & Malcolm (2005), "Pairs Trading", Quantitative Finance 5(3) [verify, abstract plus secondary]

- **Method:** The spread is a mean-reverting Gaussian Markov chain (a discrete OU process) observed with Gaussian noise. A Kalman filter estimates the hidden state, and parameters are fitted by EM (Shumway-Stoffer offline, Elliott-Krishnamurthy online). The trade signal is a deviation of the observed spread from its predicted value beyond a threshold.
- **Evidence:** Methodological, with simulated or illustrative data only. There is no large empirical backtest.
- **Use:** The OU half-life ln(2)/κ is the single most useful screening statistic. Bertram (2010) derives optimal OU entry and exit bands that maximize expected return per unit time [verify]. Those bands are usually narrower than 2σ when costs are low.

### 1.11 Avellaneda & Lee (2010), "Statistical Arbitrage in the US Equities Market", Quantitative Finance 10(7) [full text, 2009 working-paper version]

- **Data:** US stocks with market cap above $1bn, 1997–2007.
- **Method:**
  - Each stock's daily return is regressed on either (a) the 15 PCA eigenportfolios of the 252-day correlation matrix, or (b) its **sector ETF** (15 sector/industry ETFs, or synthetic ETFs before 2002).
  - The cumulative residual is fitted as an OU process on a **60-day** window.
  - Trades only stocks whose **κ > 252/30 ≈ 8.4**, meaning a mean-reversion time under about 30 days, roughly a half-life under 1.5 weeks.
  - The trade signal is an **s-score** (residual minus its OU mean, divided by the equilibrium σ):
    - **open long if s < −1.25, open short if s > +1.25**
    - **close short when s < +0.75, close long when s > −0.50**
  - Costs are 5 bp per side (10 bp round trip). Positions are beta-neutral to the factor or ETF.
  - A "trading time" variant scales returns by volume.
- **Key numbers:**
  - **PCA strategies: Sharpe 1.44 net, 1997–2007**, with Sharpe above 2 in 2000–2002 and **only 0.9 in 2003–2007**.
  - **ETF-based: Sharpe 1.1 net, 1997–2007**, averaging about 0.6 in 2003–07.
  - ETF with volume information (trading time): **Sharpe 1.51 in 2003–2007**.
  - There was a large drawdown in August 2007.
- **Limitations:** Ends in 2007, and the decay after 2002 is clear. It trades a very large book with daily rebalancing, so 10 bp round trip is optimistic for retail market orders but realistic for limit orders on large caps today.
- **Why it matters for Alpaca:** The ETF hedge leg (XLK, XLF and so on) is always easy to borrow and very liquid. You can also run a **long-residual-only** book (buy cheap stocks, short the sector ETF), so you never need to borrow a single name.

### 1.12 Chen, Chen, Chen & Li (2019), "Empirical Investigation of an Equity Pairs Trading Strategy", Management Science 65(1) [full text, journal PDF]

- **Data:** CRSP monthly, **1931–2007**, with an out-of-sample extension to 2008–2015 in the online appendix.
- **Method (quasi-multivariate):**
  - For each stock, take the **50 stocks with the highest return correlation over the prior 60 months** (years t−4 to t). Their equal-weighted return is the "pairs portfolio".
  - The signal is RetDiff = β_C·(pairs return last month − r_f) − (own return last month − r_f), where β_C is the stock's beta on its pairs portfolio.
  - Each month, sort stocks into deciles. **Long decile 10, short decile 1, hold one month.**
- **Key numbers:**
  - Value-weighted D10−D1: **1.40% a month** (t = 8.81), 3-factor alpha 1.23%, 6-factor alpha **0.77%** (about 9% a year).
  - Equal-weighted: **3.17% a month**, 6-factor alpha 3.03% (about 36% a year).
  - The value-weighted spread lost money in 12 of 77 years, including 1999–2001, 2003, 2005 and **2007**.
  - Profits are explained by **short-term reversal plus one-month industry momentum**.
  - **Over 2008–2015, profits declined.** Equal-weighted results remain significant. The value-weighted figures in Table A.4 were not checked.
- **Limitations:** No transaction costs. The equal-weighted result is driven by small caps. It is a cross-sectional reversal strategy in pairs form, not a classic convergence trade.

### 1.13 Clegg & Krauss (2018), "Pairs Trading with Partial Cointegration", Quantitative Finance 18(1) [full text, FAU working paper 05/2016]

- **Model:** Partial cointegration (PCI). The spread is a mean-reverting AR(1) component plus a random walk, fitted as a state-space model by maximum likelihood with a likelihood-ratio test. This allows "mostly mean-reverting" pairs whose equilibrium drifts.
- **Data:** **S&P 500 constituents, survivor-bias-free, January 1990 – October 2015.**
- **Four systems compared:**

| | GGR | CI1 | CI2 | PCI |
|---|---|---|---|---|
| Formation (months) | 12 | 12 | 48 | 48 |
| Trading (months) | 6 | 6 | 6 | 6 |
| Pair selection | SSD | in-sample Sharpe | in-sample Sharpe | in-sample Sharpe |
| Entry / exit (σ) | 2 / 0 | 2 / 0 | 1 / −0.5 | 1 / −0.5 |
| Stop-loss | none | none | 10% pair loss, then pair disqualified | 10% pair loss, then pair disqualified |

  All four trade the top 20 pairs with costs of **5 bp per share per half-turn**.

- **Full-period results, annualized, after costs** (gross Sharpe in brackets):

| | GGR | CI1 | CI2 | PCI | Market |
|---|---|---|---|---|---|
| Mean return | 1.11% | 1.61% | 1.09% | **12.3%** | 9.6% |
| Sharpe | −0.55 (−0.36) | −0.24 | −0.37 | **1.11** (1.35) | 0.43 |
| Max drawdown | 14% | 28% | 25% | 19% | 50% |

- **Trade statistics per 6-month period:**
  - GGR: 94.5% of pairs trade, **1.41 trades per pair**, **only 46% of trades complete by convergence**, **average holding 60.6 days**.
  - Cointegration variants: 44–50% complete, 48–65 days.
  - PCI: 6.5 trades per pair, 89% complete, **16.7-day holding**.
- **Sub-periods, annualized after costs:**
  - 1990–Mar 2001: PCI +22%. GGR return +2.5%, excess −2.4%.
  - Apr 2001–Aug 2008: GGR −0.6%, CI2 −1.8%, PCI +5.2%.
  - **Sep 2008–Dec 2009: GGR +5.2%, PCI +49%** while the market fell 3.6%.
  - **Jan 2010–Oct 2015: GGR −0.2%, CI1 −1.7%, CI2 −0.6%, PCI −2.8%, market +13.1%.** "All strategies disappoint." The authors attribute this to research diffusion and arbitrage.
- **Limitations:**
  - The PCI full-sample result is driven by the 1990s and 2008–09.
  - Selecting pairs by in-sample Sharpe from 48-month windows risks overfitting.
  - Costs exclude borrow fees.

### 1.14 Krauss, Do & Huck (2017), "Deep Neural Networks, Gradient-Boosted Trees, Random Forests: Statistical Arbitrage on the S&P 500", EJOR 259(2) [full text, FAU working paper 03/2016]

- **Data:** S&P 500 constituents, survivor-free, 1992–2015.
- **Method:** Features are lagged returns over 1–240 days. Models predict the probability that each stock beats the cross-sectional median tomorrow. **Long the top k = 10, short the bottom 10, daily.** Costs are 5 bp per half-turn.
- **Key numbers:**
  - Ensemble earns **0.45% a day gross**, **0.25% a day net**, with a net Sharpe of about 1.81 for the full period.
  - Returns deteriorate after 2001.
  - **2010–2015: every model and the ensemble lose 14–25% a year after costs.** The text labels this sub-period "01/01 to 10/15", which is a typo for 01/10 to 10/15.
  - The ensemble earned more than 400% a year in 2008–09.
- **Limitations:** Daily top/bottom-10 turnover is enormous, and 5 bp is optimistic. The edge is dominated by the 1990s and crises. Main lesson: **off-the-shelf ML on returns does not work after costs in the 2010s.**

### 1.15 Guijarro-Ordonez, Pelger & Zanotti, "Deep Learning Statistical Arbitrage" (arXiv 2106.04028; SSRN 3862004; reported as forthcoming or published in Management Science [verify journal details]) [full text, arXiv v2, Oct 2022]

- **Data:** Daily, the **roughly 550 largest and most liquid US stocks**. Residuals are computed for 1998–2016, and out-of-sample trading runs 2002–2016.
- **Method:**
  1. Residuals from rolling factor models: Fama-French factors, PCA, or IPCA with 46 characteristics, using K factors.
  2. Signal: a 30-day lookback of cumulative residuals fed to a convolutional network plus transformer.
  3. Allocation: a neural policy that maximizes Sharpe, trained on a rolling 4-year window.
  4. Benchmarks: an **OU-plus-threshold** rule (the Avellaneda-Lee approach) and Fourier plus a feed-forward network.
- **Key numbers, out-of-sample 2002–2016, before costs (Table I):**

| Model | Sharpe | Mean return |
|---|---|---|
| CNN+Transformer, IPCA K=5 | 4.16 | 8.7% |
| CNN+Transformer, PCA K=3 | 3.56 | 16.0% |
| **OU+threshold, PCA K=3–8** | **0.73–0.87** | 4.4–5.2% |
| **OU+threshold, IPCA K=3–8** | **0.88–0.97** | about 3.5–3.8% |

  - **With frictions** (5 bp per unit turnover plus 1 bp a day on the short book, Table IX), CNN+Transformer on IPCA has a Sharpe of **0.94–1.24** and returns of 4.8–6.6% a year for K = 3–15.
  - Third-party summaries quoting a "net Sharpe about 2.3" do not match the arXiv v2 table.
- **Limitations:** Heavy compute and long training. The cost model is linear, with no market impact. The sample ends in 2016.
- **Practical relevance:** It shows that the factor-residual framework is the right one, and that a simple OU rule on it has a gross Sharpe under 1 in 2002–2016.

### 1.16 Khandani & Lo (2007 working paper; 2011, J. Financial Markets 14(1)), "What Happened to the Quants in August 2007?" [full text of the 2007 working paper]

- **Method:** A Lo-MacKinlay contrarian strategy: buy yesterday's losers and sell yesterday's winners, weighted by return relative to the market. It is the generic stat-arb or pairs proxy. CRSP, 1995–2007.
- **Key numbers:**
  - **Average daily return fell from 1.38% (1995) to 0.44% (2000) to 0.15% (2006) and 0.13% (2007 year to date)**, gross. The decline runs alongside the growth of long/short and market-neutral hedge fund assets to more than $160bn.
  - August 2007, unleveraged: **−1.16%, −2.83%, −2.80%** on August 7–9, then **+5.92% on August 10**. The week net was −0.43%.
  - At 8:1 leverage, about a quarter of capital was lost in three days.
  - The cause was a fire-sale unwind of crowded quant market-neutral books, followed by withdrawal of market-making capital (the 2011 version uses TAQ data).
- **Lesson for retail:** The risk is less about losing money and more about **being stopped out at the worst point of a crowding unwind**. Stop-losses calibrated on normal-period σ will trigger exactly on the days before the snap-back. Keep leverage low enough that you don't have to de-risk.

### 1.17 Recent and related work, 2017–2026

- **Stübinger, Mangold & Krauss (2018), "Statistical Arbitrage with Vine Copulas", Quantitative Finance 18(11)** [verify, abstract]: S&P 500, 1992–2015. **9.25% a year, Sharpe 1.12 after costs**, max drawdown 6.6%. Like the other FAU papers, this is likely concentrated in early years and crises.
- **Han, He & Toh (2023), "Pairs Trading via Unsupervised Learning", EJOR 307(2)** [verify, abstract]: US stocks, 1980–2020. Pairs are found by agglomerative clustering on **firm characteristics plus price data**. Abstract reports **24.8% a year, Sharpe 2.69**, robust to costs and to excluding stocks below the NYSE 20% size cutoff. Decade-by-decade results were not checked. Characteristics help pair quality, which supports industry and fundamentals matching.
- **Sarmento & Horta (2020), "Enhancing a Pairs Trading Strategy with the Application of Machine Learning", Expert Systems with Applications 158** [verify, extended abstract]: **208 commodity-linked ETFs, 5-minute bars, 2009–2018**, with costs. OPTICS clustering on PCA features for pair search, plus ARMA/LSTM forecasting. Portfolio Sharpe 3.79 against 2.59–3.58 for standard search. The forecasting rule cut decline periods by 75% but also cut profit. This is evidence that **ETF pairs within the same theme are a cleaner universe**, but the results are intraday.
- **Schizas, Thomakos & Wang (c. 2011), "Pairs Trading on International ETFs"**, FMA working paper [verify]: country-ETF pairs show positive excess returns (Quantpedia uses this as "Pairs trading with country ETFs").
- **Fil & Kristoufek (2020), "Pairs Trading in Cryptocurrency Markets", IEEE Access 8** [verify, abstract]: 26 Binance coins. **Daily distance method about −0.07% a month. 5-minute about +11.6% a month** before realistic slippage. Mean reversion in crypto is an intraday effect.
- **Zhu (2024), "Examining Pairs Trading Profitability"**, Yale senior essay (advised by Goetzmann) [full text; not peer-reviewed]:
  - CRSP, 2003–2023, GGR replication with wait one day, gross of explicit commissions.
  - Monthly excess returns: **T20 0.46%** (Sharpe 0.81), T500 0.50% (Sharpe 1.35, the headline "6.2%/yr"), same-SIC-division R20 0.47%, **top-50% volume 0.28%, top-25% volume 0.19% (Sharpe 0.56)**.
  - Extreme months are mostly driven by **splits, delistings and SPACs**. The momentum factor loads heavily.
  - **The more liquid the universe, the lower the gross edge**, which is the opposite of what a retail ETB universe needs.
- **Engelberg, Reed & Ringgenberg (2018), "Short-Selling Risk", Journal of Finance 73(2)** [verify, abstract]: the risk of loan fee spikes and **recalls** is priced. Stocks with high short-selling risk have lower returns and less efficient prices. This hits the short leg of a pair hardest exactly when the pair diverges, through squeezes.
- **Pandini (2026), "Stationary but Not Profitable: A Critical Look at Pairs Trading"**, working paper [verify, abstract only]: cointegrated pairs in liquid ETFs and equities are often statistically stationary but unprofitable once costs and parameter instability are modeled.

---

## 2. Synthesis

### (a) Does it still work after costs in US large caps, 2010s–2020s? Which method?

**Short answer: classic pairs trading earns about zero net in US large caps since about 2010. Its value is crisis-convex, and only more sophisticated residual methods have shown positive net Sharpe in the 2000s–2010s.**

| Study | Universe / period | Method | Net result in the most recent window |
|---|---|---|---|
| Clegg & Krauss 2018 | S&P 500, 2010–2015 | GGR / cointegration / PCI | −0.2% / −1.7% / −0.6% / −2.8% a year |
| Krauss, Do & Huck 2017 | S&P 500, 2010–2015 | DNN / GBT / RF ensemble | −14% to −25% a year |
| Rad, Low & Faff 2016 | All US, after 2009 | Distance / cointegration | Opportunities down 35–40%; "unattractive since about 2000" [verify] |
| Do & Faff 2012 | US, after 2002 | Refined GGR | "largely unprofitable" |
| Chen et al. 2019 | US, 2008–2015 | Quasi-multivariate | Declined; equal-weighted still significant (gross) |
| Zhu 2024 | CRSP, 2003–2023 | GGR | Gross 0.46% a month overall; 0.19% a month in the top-volume quartile |
| GPZ (arXiv 2022) | Top 550 US, 2002–2016 | Deep residual model | Net Sharpe about 1.0–1.2; OU-threshold gross Sharpe about 0.6–0.97 |

**Method ranking from the evidence, after costs, recent periods:**
1. **Factor- or ETF-residual mean reversion** (Avellaneda-Lee to GPZ). It has the best documented post-2002 performance and uses a hedge leg that is easy to borrow. A simple OU rule captures part of it, around Sharpe 0.6–0.9 gross for 2002–2016.
2. **Cointegration with a half-life filter, or partial cointegration.** Better pair quality than SSD (Huck & Afawubo 2015; Clegg & Krauss), and it wins in turbulent markets (Rad et al.). It was still about zero net in 2010–15.
3. **Distance (GGR).** Simplest and least data-snooped. It works as the control. Its net edge in large caps has been about zero since the early 2000s.
4. **Copula.** It does not beat distance or cointegration net (Rad et al.: 5 bp a month). The vine-copula results (Stübinger et al.) are not replicated in recent periods.
5. **ML on raw returns.** Krauss-Do-Huck show it collapses after costs in the 2010s. Characteristic-based clustering (Han et al. 2023) and deep residual models (GPZ) are promising but need data (fundamentals) and infrastructure beyond Alpaca bars.

### (b) Best-practice parameters, and what the evidence supports

| Parameter | Literature default | Evidence and comments |
|---|---|---|
| Formation window | 12 months (GGR, Rad, Do-Faff); 48 months for PCI; 60 months of correlations (Chen) | Huck 2013: 12m is not robust on the S&P 500 [verify]. Test 6, 12, 18 and 24 months. Do not choose the best. |
| Trading window | 6 months | GGR open duration is about 3.75 months, with about 60 days per trade on the S&P 500 (Clegg-Krauss). Re-form monthly with overlapping books. |
| Entry | 2σ of the formation-period spread (GGR, Rad, Do-Faff); 1σ for PCI and CI2; s-score 1.25 (Avellaneda-Lee) | GGR footnote: the profit-optimal trigger "may be much higher than 2σ". Avellaneda-Lee use a lower threshold with a mean-reversion speed filter. Test 1.25, 1.5, 2.0 and 2.5. |
| Exit | Spread crossing (0σ); −0.5σ (CI2, PCI); s back to 0.5–0.75 (Avellaneda-Lee) | Exiting slightly before full convergence (0.5σ) cuts holding time and the tail of non-converging trades. |
| Time stop | End of the 6-month trading period | Unconverged trades have **negative average returns** (Rad et al.). Engelberg et al.: profits decay roughly exponentially with time since divergence. **Test a time stop at 2–3× the OU half-life, or 20–30 trading days.** |
| Stop-loss | Mostly none. Clegg-Krauss CI2/PCI use a 10% pair-loss stop with pair disqualification | No paper shows σ-based stops (3–4σ) improve net returns for distance pairs. Khandani-Lo show they can lock in crowding losses. Test 3σ and 4σ spread stops and a pair-P&L stop, and disqualify the pair after a stop. |
| Same industry | Do-Faff: finer industry groups improve net profit. GGR: same-sector pairs profitable. Chen: industry momentum explains part of profit | Use GICS industry or sub-industry. For ETF residuals, map each stock to its SPDR sector ETF. |
| Liquidity / size filter | Avellaneda-Lee: above $1bn. GGR: no missing trading days. Zhu: liquidity cuts gross return | Use S&P 500 or Russell 1000 point-in-time, price above $5, ADV above $20m, Alpaca ETB. Expect a lower gross edge than the papers report. |
| Mean-reversion speed | Avellaneda-Lee: κ > 8.4 (τ < 30 days); PCI half-life about 16-day trades | **Half-life filter of 5–30 trading days** is the most useful single screen. |
| Cointegration test | Engle-Granger ADF p < 0.05 or Johansen | Correct for multiple testing (Krauss survey), or pre-filter to same industry with correlation above 0.8. |
| Costs | 5 bp per half-turn (Avellaneda-Lee, Clegg-Krauss, KDH, GPZ); 1 bp a day short holding (GPZ) | For Alpaca, test 5 and 10 bp per side plus SEC/TAF fees on sells. ETB borrow is roughly free, but model 0.25–1% a year to be conservative [verify Alpaca's ETB fee policy]. |
| Weighting | $1 long / $1 short (GGR), or a β (hedge-ratio) weighted spread | For cointegration use the γ hedge ratio. For ETF residuals use a β-neutral ETF hedge. |

### (c) Main failure modes

1. **Structural breaks and permanent divergence.**
   - 32% of distance pairs never converge (Do & Faff, via Krauss).
   - On the S&P 500 only about 46% of GGR trades complete by convergence within 6 months (Clegg-Krauss).
   - Cointegration is not persistent: the property breaks out of sample (Clegg 2014, cited in Clegg-Krauss).
   - Mitigations: half-life filter, time stop, disqualify a pair after a stop, re-test cointegration on a rolling basis.
2. **Event risk.**
   - Divergence on idiosyncratic news (earnings, guidance, M&A, FDA decisions, downgrades) is mostly permanent (Engelberg et al.; Papadakis & Wysocki).
   - M&A is the worst case. The target jumps and the acquirer's price is often pinned to it, so the spread breaks discretely.
   - Mitigations: skip entries within ±2 days of either leg's earnings; flatten before earnings; ignore gap days above 3σ of the stock's daily volatility. You need an earnings calendar, which Alpaca does not provide historically.
3. **Crowding and liquidity spirals.**
   - Khandani-Lo: the gross daily contrarian return fell about 10× from 1995 to 2007, and the August 2007 unwind produced 12σ daily losses.
   - Clegg-Krauss and KDH both blame research diffusion for the zero returns in 2010–15.
   - Expect correlated drawdowns across all pairs at once. Diversification across pairs fails on crowding days.
4. **Short-borrow costs and recalls.**
   - GGR's recall simulation costs 4–13 bp a month.
   - Engelberg-Reed-Ringgenberg: short-selling risk (fee spikes and recalls) is priced and peaks during squeezes, which is when the short leg diverges up.
   - On Alpaca, a stock can move from ETB to hard-to-borrow, and you cannot backtest ETB status historically.
   - Mitigations: restrict to mega and large caps, prefer the ETF as the short leg, and close the pair if the short leg loses ETB status.
5. **Look-ahead and selection bias.**
   - Selecting pairs or cointegration on data that overlaps the trading window.
   - Using today's S&P 500 list for 2016. Using today's ETB list.
   - Using split- or dividend-adjusted series inconsistently. Zhu shows splits and delistings create huge spurious months.
   - Choosing among 29 variants (Do-Faff) or by in-sample Sharpe (Clegg-Krauss) without a holdout.
6. **Survivorship bias.** Clegg-Krauss and KDH both stress survivor-free constituent lists. Alpaca's asset list is current only, so you need a point-in-time constituents file.
7. **Microstructure and bid-ask bounce.** GGR's no-wait returns are about 40% higher than wait-one-day. In a daily backtest, **signal on close t, trade at open t+1 or close t+1.** Never trade at the close that generated the signal.
8. **Regime dependence and factor loading.** Profits load on short-term reversal and industry momentum (Chen et al.). Zhu finds momentum can wipe out returns in 2003–23. Pairs returns are positively skewed but have fat left tails in trending markets such as 2010–2015.

### (d) Concrete testable rule sets for a daily-bar Alpaca backtest, 2016–2026

**Common infrastructure, for all rule sets:**
- **Universe:** point-in-time S&P 500 (or Russell 1000) members, price above $5, 60-day ADV above $20m, currently ETB on Alpaca (an approximation; this is look-ahead, so document it).
- **Data:** Alpaca daily bars, split- and dividend-adjusted (`adjustment=all`). Delisted names need external data or must be flagged.
- **Timing:** compute signals on close t, execute at open t+1 (or close t+1 with a slippage haircut). Report both.
- **Costs:** 5 bp per side base case and 10 bp stress case. Add sell-side SEC and FINRA TAF fees. Short borrow at 0.5% a year on the short notional.
- **Sizing:** equal dollar risk per pair, at most 20 concurrent pairs, gross leverage at most 2×, no pyramiding.
- **Event filter:** no new entry within 2 trading days before or after either leg's earnings date. Exit any open pair the day before either leg's earnings. Earnings dates need an external source.
- **Evaluation:**
  - 2016–2020 for development and 2021–2026 as a true holdout, or rolling walk-forward.
  - Report net CAGR, Sharpe, max drawdown, percentage of trades closed by convergence, average holding days, the share of P&L from March 2020 and from 2022, and factor loadings on short-term reversal and momentum.
  - Treat Sharpe as significant only above about 0.8 net, given how many variants are being tested.

**Rule set A: GGR distance, intra-industry (control)**
- Formation 12 months, trading 6 months, re-formed monthly (six overlapping books).
- Candidates: same GICS industry. Rank by SSD of normalized price, take the top 20, one partner per stock. Optional Do-Faff tweak: drop the lowest-SSD decile and require at least 6 zero crossings in formation.
- Entry |z| ≥ 2.0 (formation σ). Exit at a crossing (z = 0). Time stop at the end of the trading period.
- Variants: entry 1.5 or 2.5; exit 0.5; time stop 30 days; stop 4σ.
- Expectation from the literature: about 0 net. It is the baseline any other rule must beat.

**Rule set B: cointegration with a half-life filter and z-score bands**
- Formation 252 days, re-estimated monthly. Candidates: same GICS sub-industry with return correlation ≥ 0.7.
- Engle-Granger ADF on log prices, p < 0.01 (a tighter level to offset multiple testing). Hedge ratio γ by OLS. Optional robustness: γ from a Kalman filter (time-varying).
- OU half-life between 5 and 30 days. Keep the 20 best by ADF statistic.
- Entry |z| ≥ 2.0, where z uses the 60-day rolling mean and σ of the spread. Exit |z| ≤ 0.5.
- **Time stop at 2× half-life, at most 30 trading days. Stop-loss at |z| ≥ 3.5, after which the pair is banned until the next re-formation.**
- Variants: entry 1.5 and exit 0 (CI2-like); Johansen instead of Engle-Granger; formation 504 days.

**Rule set C: ETF-residual OU s-score (Avellaneda-Lee, adapted for Alpaca)**
- For each stock, regress daily returns on its SPDR sector ETF (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB, XLU, XLRE, XLC) over 60 days. Cumulate the residual and fit an AR(1)/OU.
- Trade only if κ > 8.4 (mean-reversion time under 30 days) and the AR(1) b is between 0 and 0.9672.
- Open long if s < −1.25, open short if s > +1.25. Close long at s > −0.50, close short at s < +0.75. Hedge the stock with β × the sector ETF.
- **Long-residual-only variant (no single-name shorts):** buy stocks with s < −1.25 and short the sector ETF. This removes single-name borrow and recall risk entirely. Run it as a separate test, because it gives up the short-side alpha.
- Variants: thresholds 1.0, 1.5 and 2.0; 30- or 90-day window; 3–5 PCA factors instead of ETFs, with the PCA fitted on a 252-day correlation matrix.
- Expectation: the best a priori candidate. Literature Sharpe was about 0.6 (ETF) to 0.9 (PCA) net in 2003–07, and the OU benchmark had a gross Sharpe of 0.6–0.97 in 2002–16. Expect a net Sharpe of 0–0.7 in 2016–26.

**Rule set D: quasi-multivariate RetDiff, monthly (Chen et al.), large-cap value-weighted**
- At each year-end, for each stock, find the 50 stocks with the highest monthly-return correlation over the prior 60 months, using 2011+ data so the backtest can start in 2016.
- Each month, RetDiff = β_C·(pairs portfolio return last month) − own return last month.
- Long the top decile and short the bottom decile, value-weighted or equal-weighted within large caps, held one month. Only ETB names on the short side.
- Turnover is low (monthly), so costs matter less.
- Expectation: weak but cheap to test. This is essentially short-term reversal plus industry momentum, so control for both.

**Rule set E (optional): ETF-to-ETF pairs** (Schizas et al.; Sarmento & Horta)
- Same-theme ETF pairs, such as country ETFs, sector against equal-weight sector (XLK vs RSPT), or SMH vs SOXX. Avoid near-identical funds like GLD vs IAU or SPY vs IVV, whose spreads are smaller than costs.
- Same rules as B. Borrow is never an issue and there is no earnings event risk.
- Daily-bar edge is likely small. Minute bars from 2016 allow an intraday version, which is where Sarmento & Horta and Fil & Kristoufek found their returns.

**How to use options (level 3)**

Level 3 on Alpaca allows multi-leg spreads, not naked short calls [verify against Alpaca's current option-level definitions].

1. **Replace a hard-to-borrow short leg with a put debit spread.** Choose 30–45 DTE, size by delta to match the stock leg's notional × hedge ratio, and roll or close on spread convergence. This gives defined risk on the short side and no recall or borrow fees.

   Costs: single-stock option spreads are typically 2–10% of premium, plus theta on a trade whose expected gross edge is about 1–3% of notional over 2–8 weeks. **On most pairs this cost would wipe out the edge.** Use it only for high-conviction, liquid-option names, where the borrow would otherwise be unavailable.
2. **Squeeze or event insurance on the short leg.** Buy a cheap OTM call (or call spread) on the short stock leg, about 10–15% OTM, covering the holding period. This caps the jump-risk tail (M&A on the short leg, squeezes) that σ stops cannot handle. Backtest cost as about 0.3–1% of notional per month.
3. **Defined-risk pair expression.** Buy a call debit spread on the cheap leg and a put debit spread on the rich leg. Max loss is the two debits, so no stop-loss is needed. Gamma and vega exposure differ between legs, so it is not a pure spread bet. Avoid holding through either leg's earnings, because of IV crush.
4. **Data constraint:** Alpaca's historical options data only begins in early 2024 [verify]. An options overlay cannot be backtested over 2016–2026 on Alpaca data. Either price it with Black-Scholes on a realized- or implied-volatility proxy, flagged as approximate, or paper-trade it forward.

**Recommendation:** Backtest A (control), B and C on stock shares with realistic costs. Add D if time allows. Add options only as tail insurance (item 2), priced conservatively, never as the core way of expressing the trade.

---

## 3. Reference list

All sources were found via web search and none were LibKey-resolved. [verify] marks references whose quoted numbers came from an abstract or a secondary source rather than the full text.

1. Gatev, E., Goetzmann, W. N., & Rouwenhorst, K. G. (2006). Pairs trading: Performance of a relative-value arbitrage rule. *Review of Financial Studies*, 19(3), 797–827. DOI: 10.1093/rfs/hhj020. NBER WP 7032. URL: https://academic.oup.com/rfs/article/19/3/797/1646694 ; PDF read: https://c.mql5.com/forextsd/forum/208/Pairs%20Trading%20-%20Performance%20of%20a%20Relative%20Value%20Arbitrage%20Rule.pdf. Found via web; not LibKey-resolved. Numbers from full text.
2. Do, B., & Faff, R. (2010). Does simple pairs trading still work? *Financial Analysts Journal*, 66(4), 83–95. DOI: 10.2469/faj.v66.n4.1. URL: https://ideas.repec.org/a/taf/ufajxx/v66y2010i4p83-95.html. Found via web; not LibKey-resolved. **[verify]**
3. Do, B., & Faff, R. (2012). Are pairs trading profits robust to trading costs? *Journal of Financial Research*, 35(2), 261–287. DOI: 10.1111/j.1475-6803.2012.01317.x. URL: https://strathprints.strath.ac.uk/41722/ ; summary: https://www.cxoadvisory.com/technical-trading/pairs-trading-net-profitability/. Found via web; not LibKey-resolved. **[verify]**
4. Jacobs, H., & Weber, M. (2015). On the determinants of pairs trading profitability. *Journal of Financial Markets*, 23, 75–97. DOI: 10.1016/j.finmar.2014.12.001. SSRN 2545440: https://papers.ssrn.com/abstract=2545440. Found via web; not LibKey-resolved. **[verify]**
5. Rad, H., Low, R. K. Y., & Faff, R. (2016). The profitability of pairs trading strategies: Distance, cointegration and copula methods. *Quantitative Finance*, 16(10), 1541–1558. DOI: 10.1080/14697688.2016.1164337. URL: https://ideas.repec.org/a/taf/quantf/v16y2016i10p1541-1558.html ; summary: https://cxoadvisory.com/technical-trading/best-stock-pairs-trading-method. Found via web; not LibKey-resolved. **[verify]**
6. Krauss, C. (2017). Statistical arbitrage pairs trading strategies: Review and outlook. *Journal of Economic Surveys*, 31(2), 513–545. DOI: 10.1111/joes.12153. WP: https://www.iwf.rw.fau.de/files/2016/03/09-2015.pdf. Found via web; not LibKey-resolved. Read in full (WP); cited sub-results such as Huck 2013 and Huck & Afawubo 2015 are **[verify]**.
7. Vidyamurthy, G. (2004). *Pairs Trading: Quantitative Methods and Analysis*. Hoboken, NJ: Wiley. ISBN 978-0-471-46067-1. Found via web; not LibKey-resolved.
8. Engle, R. F., & Granger, C. W. J. (1987). Co-integration and error correction: Representation, estimation, and testing. *Econometrica*, 55(2), 251–276. DOI: 10.2307/1913236. Found via web; not LibKey-resolved.
9. Johansen, S. (1988). Statistical analysis of cointegration vectors. *Journal of Economic Dynamics and Control*, 12(2–3), 231–254. DOI: 10.1016/0165-1889(88)90041-3. Found via web; not LibKey-resolved.
10. Johansen, S. (1991). Estimation and hypothesis testing of cointegration vectors in Gaussian vector autoregressive models. *Econometrica*, 59(6), 1551–1580. DOI: 10.2307/2938278. Found via web; not LibKey-resolved.
11. Elliott, R. J., van der Hoek, J., & Malcolm, W. P. (2005). Pairs trading. *Quantitative Finance*, 5(3), 271–276. DOI: 10.1080/14697680500149370. URL: https://ideas.repec.org/a/taf/quantf/v5y2005i3p271-276.html. Found via web; not LibKey-resolved. **[verify]**
12. Avellaneda, M., & Lee, J.-H. (2010). Statistical arbitrage in the US equities market. *Quantitative Finance*, 10(7), 761–782. DOI: 10.1080/14697680903124632. WP read: https://www.math.nyu.edu/faculty/avellane/AvellanedaLeeStatArb20090616.pdf. Found via web; not LibKey-resolved. Numbers from full text (WP).
13. Chen, H., Chen, S., Chen, Z., & Li, F. (2019). Empirical investigation of an equity pairs trading strategy. *Management Science*, 65(1), 370–389. DOI: 10.1287/mnsc.2017.2825. PDF read: https://www.pbcsf.tsinghua.edu.cn/__local/7/61/D5/20F9802D29BA087B9D8A12987BD_D53D9CBF_54542.pdf. Found via web; not LibKey-resolved. Numbers from full text. The 2008–2015 appendix was not read **[verify]**.
14. Engelberg, J., Gao, P., & Jagannathan, R. (2009). An anatomy of pairs trading: The role of idiosyncratic news, common information and liquidity. Working paper. SSRN 1330689: https://papers.ssrn.com/abstract=1330689. Found via web; not LibKey-resolved. **[verify]**
15. Papadakis, G., & Wysocki, P. (2007). Pairs trading and accounting information. Working paper, Boston University and MIT. No public URL located; described in Krauss (2017). Found via web; not LibKey-resolved. **[verify]**
16. Clegg, M., & Krauss, C. (2018). Pairs trading with partial cointegration. *Quantitative Finance*, 18(1), 121–138. DOI: 10.1080/14697688.2017.1370122. WP read: https://www.iwf.rw.fau.de/files/2016/05/05-2016.pdf. Found via web; not LibKey-resolved. Numbers from full text (WP; may differ from the journal version).
17. Krauss, C., Do, X. A., & Huck, N. (2017). Deep neural networks, gradient-boosted trees, random forests: Statistical arbitrage on the S&P 500. *European Journal of Operational Research*, 259(2), 689–702. DOI: 10.1016/j.ejor.2016.10.031. WP read: https://www.iwf.rw.fau.de/files/2016/03/03-2016.pdf. Found via web; not LibKey-resolved. Numbers from full text (WP).
18. Guijarro-Ordonez, J., Pelger, M., & Zanotti, G. (2022). Deep learning statistical arbitrage. arXiv:2106.04028 (v2, Oct 2022); SSRN 3862004; reported in *Management Science* (journal details not confirmed). URL: https://arxiv.org/abs/2106.04028. Found via web; not LibKey-resolved. Numbers from full text (arXiv v2). Journal citation **[verify]**.
19. Khandani, A. E., & Lo, A. W. (2007). What happened to the quants in August 2007? *Journal of Investment Management*, 5(4), 5–54. WP read: https://traders.berkeley.edu/papers/What%20Happened%20to%20the%20Quants%20in%20August%202007.pdf. Found via web; not LibKey-resolved. Numbers from full text (WP).
20. Khandani, A. E., & Lo, A. W. (2011). What happened to the quants in August 2007? Evidence from factors and transactions data. *Journal of Financial Markets*, 14(1), 1–46. DOI: 10.1016/j.finmar.2010.07.005. NBER WP 14465: https://www.nber.org/papers/w14465. Found via web; not LibKey-resolved. **[verify]**
21. Stübinger, J., Mangold, B., & Krauss, C. (2018). Statistical arbitrage with vine copulas. *Quantitative Finance*, 18(11), 1831–1849. DOI: 10.1080/14697688.2018.1438642. URL: https://ideas.repec.org/a/taf/quantf/v18y2018i11p1831-1849.html. Found via web; not LibKey-resolved. **[verify]**
22. Han, C., He, Z., & Toh, A. J. W. (2023). Pairs trading via unsupervised learning. *European Journal of Operational Research*, 307(2), 929–947. DOI: 10.1016/j.ejor.2022.09.041. URL: https://ideas.repec.org/a/eee/ejores/v307y2023i2p929-947.html. Found via web; not LibKey-resolved. **[verify]**
23. Sarmento, S. M., & Horta, N. (2020). Enhancing a pairs trading strategy with the application of machine learning. *Expert Systems with Applications*, 158, 113490. DOI: 10.1016/j.eswa.2020.113490. Extended abstract: https://fenix.tecnico.ulisboa.pt/downloadFile/281870113705364/ExtendedAbstract.pdf. Found via web; not LibKey-resolved. **[verify]**
24. Schizas, P., Thomakos, D. D., & Wang, T. (c. 2011). Pairs trading on international ETFs. Working paper (FMA). URL: http://www.fma.org/Denver/Papers/pairetf.pdf ; summary: https://quantpedia.com/strategies/pairs-trading-with-country-etfs. Found via web; not LibKey-resolved. **[verify]**
25. Fil, M., & Kristoufek, L. (2020). Pairs trading in cryptocurrency markets. *IEEE Access*, 8, 172644–172651. DOI: 10.1109/ACCESS.2020.3024619. URL: https://ieeexplore.ieee.org/document/9200323. Found via web; not LibKey-resolved. **[verify]**
26. Engelberg, J. E., Reed, A. V., & Ringgenberg, M. C. (2018). Short-selling risk. *Journal of Finance*, 73(2), 755–786. DOI: 10.1111/jofi.12601. WP: https://rodneywhitecenter.wharton.upenn.edu/wp-content/uploads/2014/03/riggenberg.pdf. Found via web; not LibKey-resolved. **[verify]**
27. Zhu, X. (2024). Examining pairs trading profitability. Senior essay, Department of Economics, Yale University (not peer-reviewed). URL: https://economics.yale.edu/sites/default/files/2024-05/Zhu_Pairs_Trading.pdf. Found via web; not LibKey-resolved. Numbers from full text.
28. Huck, N., & Afawubo, K. (2015). Pairs trading and selection methods: Is cointegration superior? *Applied Economics*, 47(6), 599–613. DOI: 10.1080/00036846.2014.975417. Found via web (cited in Krauss 2017); not LibKey-resolved. **[verify]**
29. Huck, N. (2013). The high sensitivity of pairs trading returns. *Applied Economics Letters*, 20(14), 1301–1304. DOI: 10.1080/13504851.2013.802124. Found via web (cited in Krauss 2017); not LibKey-resolved. **[verify]**
30. Bertram, W. K. (2010). Analytic solutions for optimal statistical arbitrage trading. *Physica A*, 389(11), 2234–2243. DOI: 10.1016/j.physa.2010.01.045. Found via web (cited in Krauss 2017); not LibKey-resolved. **[verify]**
31. Pandini, D. (2026). Stationary but not profitable: A critical look at pairs trading. Working paper. URL: https://mma.prnewswire.com/media/2965868/2026_00G__StationaryButNotProfitable_ACriticalLookatPairsTrading_DavidePandini.pdf. Found via web; not LibKey-resolved. **[verify]**

Bibliographic details for items 7–10, 28–30 (and the DOIs for 2, 3, 6, 11, 20) come from standard citation records and were not independently confirmed against the publisher pages in this session. Check them before formal citation.
