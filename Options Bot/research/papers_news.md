# News, Sentiment and Information Events: Literature Review for a Retail Options Bot

Scope: research papers on entering option (and stock) trades on news, sentiment and information events, at 0-5 day and weeks-to-months horizons. The setting is a retail trader with Alpaca news (Benzinga feed, timestamped), Alpaca daily option bars, and level-3 option permissions (defined-risk spreads, long options, covered calls, cash-secured puts).

Prepared 2026-10-21. Numbers come from the papers' abstracts, working-paper PDFs or publisher pages read during this review. Where versions differ, the version is named. Citations were found via web search and were **not** resolved through LibKey (no LibKey connector session was available). DOIs marked "(verify)" were recalled or taken from a mirror and not checked against the publisher.

---

## 0. Bottom line up front

1. **The stock-level news signal is real but short and concentrated in small, illiquid names.** Ke, Kelly & Xiu (Dow Jones Newswires, 1989-2020) get an equal-weighted long-short Sharpe of 4.21 before costs but only 1.24 value-weighted. Large caps finish reacting within 1 day (11 bp); small caps take about 3 days (46 bp). Net of 10-20 bp costs with turnover control, the Sharpe peaks around 2.3. Lopez-Lira & Tang (GPT-4 on headlines, Oct 2021 - May 2024) find the tradable next-session "drift" is 0.34%/day, Sharpe 2.97 before costs. It is **wiped out at 20 bp round-trip** and decays from Sharpe 6.54 (2021Q4) to 1.22 (Jan-May 2024).
2. **Options amplify cost, not edge.** The only paper that trades option returns directly on news text is Cao, Han, Li, Yang & Zhan (2024). It finds a high-minus-low spread in delta-hedged option returns of **0.49%/month (calls) and 0.33%/month (puts)**, monthly, 1996-2022, with no transaction-cost test. Retail-relevant option spreads are about 12.6% of premium on average (Bryzgalova, Pavlova & Sikorskaya 2023). That gap is two orders of magnitude.
3. **Informed option flow before news is the strongest "options x news" evidence, but retail cannot see it in real time.** It relies on signed, open/close-tagged volume (Pan & Poteshman: >40 bp next day, >1% next week). Ge, Lin & Pearson attribute it to open-buy calls. Augustin, Brenner & Subrahmanyam find abnormal OTM call volume before about 25% of M&A deals. Alpaca daily option bars carry none of the signing.
4. **Scheduled vs unscheduled news is the key structural split.** Option *sales* predict returns only before scheduled news, because only scheduled news moves expected volatility and therefore option prices. Option *purchases* predict on news days and before unscheduled events (Fodor, Muravyev, Cremers & Weinbaum). Implied volatility (IV) is bid before scheduled events and crushed after (Dubinsky et al.; Beber & Brandt). Most individual-stock jumps happen on unscheduled news (Lee & Mykland). That implies the volatility seller loses on unscheduled news, while the long-volatility buyer usually arrives after the jump has been priced.
5. **Drift after news has largely died in large caps.** PEAD has been nonexistent for large stocks since about 2006 (Martineau 2022). Large-cap news is priced within 1 day (KKX), within an hour for prominent Bloomberg stories (Fedyk), and within milliseconds for macro releases (Chordia, Green & Kottimukkalur).
6. **Where retail sits, retail loses.** Retail option buyers lose about 5-9% around earnings and 10-14% on high-expected-volatility announcements (de Silva, Smith & So). Attention-driven Robinhood herding forecasts negative returns (Barber, Huang, Odean & Schwarz: about -4.7% over 20 days for the top buys in one version). Fading attention and hype is the most robust retail-accessible direction in this literature.

**Verdict:** No paper reviewed shows a positive after-cost edge from news-triggered *option* entries that a retail trader with daily option bars could capture. That matches our own null on mega-cap earnings structures. The defensible uses are:
(a) a **filter/veto**, as the shorting bot already uses news;
(b) a **slow, cheap-to-trade expression** of multi-day drift in the 1-5 day window after fresh, negative, unscheduled news in mid/small caps, using defined-risk debit spreads that cap the spread cost;
(c) **selling premium to attention-driven retail demand** after stale or hype headlines, with defined risk.
Every one of these has to be proven in our own backtest with realistic fills.

---

## 1. Informed trading in options before news

### 1.1 Pan & Poteshman (2006), "The Information in Option Volume for Future Stock Prices", RFS
- **Question:** Does option order flow carry information about future stock returns?
- **Data:** CBOE open-buy/open-sell volume by investor class, 1990-2001.
- **Method:** Daily put-call ratio from buyer-initiated *opening* volume; sort stocks.
- **Key numbers:** Low-PCR stocks beat high-PCR stocks by **>40 bp next day and >1% over the next week** (risk-adjusted). The effect is stronger where informed traders concentrate and in higher-leverage contracts. The authors attribute it to private information, not inefficiency.
- **Limitations:** Needs proprietary signed open/close data. Blau, Nguyen & Whitby (2014, JBF) find that unsigned public PCRs predict only fleetingly at the daily horizon. **Not reproducible from Alpaca daily bars.**

### 1.2 Cremers & Weinbaum (2010), "Deviations from Put-Call Parity and Stock Return Predictability", JFQA
- **Question:** Does the call-minus-put IV spread predict stock returns?
- **Data:** OptionMetrics, 1996-2005.
- **Key numbers:** Stocks with relatively expensive calls outperform those with expensive puts by **50 bp/week**. Predictability **decreased over the sample**, and the result is not explained by short-sale constraints. A thesis replication (2011-2015) finds a smaller 19 bp in 1 week.
- **Limitations:** Decay. Needs synchronous quotes, and end-of-day mids from daily bars are noisy for illiquid strikes.
- **Use for us:** The IV spread is computable from end-of-day option quotes if available. As a *confirmation* feature for a news-sentiment direction it is plausible. Alone it is small relative to option costs.

### 1.3 Xing, Zhang & Zhao (2010), "What Does the Individual Option Volatility Smirk Tell Us About Future Equity Returns?", JFQA
- **Key numbers:** Steepest-smirk stocks underperform flattest-smirk stocks by **10.9%/year** risk-adjusted. Persists **at least 6 months**. Interpretation: informed traders with bad news buy OTM puts, and the stock market is slow to absorb it.
- **Limitations:** Monthly signal on stocks, not option returns. Pre-2006 sample, and likely decayed like other option-implied signals.
- **Use for us:** A longer-horizon (weeks-months) negative-information filter that pairs naturally with negative news.

### 1.4 Roll, Schwartz & Subrahmanyam (2010), "O/S: The Relative Trading Activity in Options and Stock", JFE
- **Key findings:** Option-to-stock volume (O/S) rises around earnings. Pre-announcement O/S predicts the **absolute size** of the post-announcement move, which is a volatility signal, not a direction signal.
- **Use for us:** Unsigned O/S is computable from daily bars. It is a candidate gate for *long-volatility* entries before known events. Our run-up straddle null suggests the IV already prices this in mega caps.

### 1.5 Johnson & So (2012), "The Option to Stock Volume Ratio and Future Returns", JFE
- **Key numbers:** Low-O/S decile beats high-O/S decile by about **0.34%/week**. Stronger when short-sale costs are high or option leverage is low. Interpreted as informed *negative* information routed to options because of short-sale constraints.
- **Limitations:** Weekly, 1996-2010 sample. Long-short stock returns, not option returns.

### 1.6 Ge, Lin & Pearson (2016), "Why Does the Option to Stock Volume Ratio Predict Stock Returns?", JFE
- **Key finding:** The predictive component is mainly **open-buy call** volume. Trades that create synthetic shorts are *not* more informative than synthetic longs. Leverage, not short-sale avoidance, explains it.
- **Implication:** Undercuts the "O/S = hidden bad news" story. With unsigned daily volume, retail cannot isolate the informative component.

### 1.7 Augustin, Brenner & Subrahmanyam (2019), "Informed Options Trading Prior to Takeover Announcements: Insider Trading?", Management Science
- **Data:** 1,859 U.S. takeovers, 1996-2012.
- **Key numbers:** About **25%** of targets show positive abnormal option volume pre-announcement, concentrated in **short-dated OTM calls**. More than half is unlikely to be explained by rumors or news. The SEC litigates only about **8%** of deals with such activity.
- **Limitations for us:** Tradeable only if one could flag the abnormal volume in real time and accept that one is trading against insiders. The ex-post M&A pop is tiny relative to the false positives. Using it as a short-premium *veto* is sensible: do not sell calls or put spreads into abnormal OTM call volume.

**Section takeaway:** Option flow leads news, but the clean signals use signed, opening, intraday data. With daily bars, only coarse versions (unsigned O/S, end-of-day IV spread, smirk) are available. They are weekly-to-monthly *stock* return predictors of tens of bp/week, too small to monetize through option spreads directly.

---

## 2. News and sentiment predicting returns and volatility

### 2.1 Tetlock (2007), "Giving Content to Investor Sentiment: The Role of Media in the Stock Market", JF
- **Data:** WSJ "Abreast of the Market" column, Harvard-IV negative words, 1984-1999.
- **Finding:** High pessimism predicts **downward pressure on the market, followed by reversal** to fundamentals within about a week. Extreme pessimism (high or low) predicts high volume.
- **Lesson:** Market-level media tone is a *sentiment/noise* signal that reverts, not information.

### 2.2 Tetlock, Saar-Tsechansky & Macskassy (2008), "More Than Words: Quantifying Language to Measure Firms' Fundamentals", JF
- **Data:** Firm-specific WSJ and Dow Jones News Service stories, S&P 500 firms, 1980-2004.
- **Finding:** The fraction of negative words forecasts **lower earnings**. Prices **briefly underreact** (about a day), and predictability is largest for **fundamentals-focused** stories. Trades are entered at the next open, excluding 9:00-9:30 stories, which is a useful timing convention.
- **Limitation:** The Harvard dictionary misclassifies about three-quarters of its "negative" words in finance (Loughran & McDonald 2011). Use a finance lexicon or an LLM.

### 2.3 Tetlock (2011), "All the News That's Fit to Reprint: Do Investors React to Stale Information?", RFS
- **Method:** Staleness is measured as textual similarity to the firm's previous 10 stories.
- **Finding:** Returns respond less to stale news, yet **the day-of return on stale news reverses the following week**. The reversal is larger in stocks with **heavy individual-investor trading**.
- **Direct relevance:** This is the academic basis for "fade the hype". A novelty feature is mandatory.

### 2.4 Chan (2003), "Stock Price Reaction to News and No-News: Drift and Reversal After Headlines", JFE
- **Finding:** Monthly horizon. **Drift after bad-news headlines.** **Reversal after extreme moves with no news.** Both are concentrated in small, illiquid stocks.
- **Relevance:** Our "no news" classifier label has real content: a big move with no news is the reversal candidate.

### 2.5 Boudoukh, Feldman, Kogan & Richardson (2019), "Information, Trading, and Volatility: Evidence from Firm-Specific News", RFS
- **Method:** Text classification identifies *fundamental* news types (deals, earnings, analyst, legal, product) vs unidentified news.
- **Key number:** Identified news accounts for **49.6% of overnight idiosyncratic volatility vs 12.4% intraday**. Days with identified news have much higher volatility than no-news days. Earlier NBER version: on identified-news days, volatility and the return-news link are far stronger than on unidentified days.
- **Relevance:** **Event type matters more than tone for volatility.** A news-type classifier (our "real catalyst") is a volatility predictor, which supports using it to gate premium selling.

### 2.6 Heston & Sinha (2017), "News vs. Sentiment: Predicting Stock Returns from News Stories", FAJ
- **Data:** More than 900,000 stories; Thomson Reuters neural-net sentiment.
- **Finding:** **Daily news predicts returns for only 1-2 days; weekly-aggregated news predicts for a quarter.** Positive news is absorbed quickly and **negative news slowly**. Much of the delayed response occurs at the **next earnings announcement**.
- **Relevance:** Supports two horizons. One is a 1-2 day entry on fresh daily news. The other is a weekly-aggregated sentiment score held for weeks into the next earnings date, which is the "longer horizon" use and has much lower turnover.

### 2.7 Ke, Kelly & Xiu (2019/2021), "Predicting Returns with Text Data" (SESTM), NBER WP 26186
- **Data:** Dow Jones Newswires, **25.7M articles, 1989-2020**. Trained on rolling windows, out-of-sample from the mid-2000s.
- **Timing:** Trade at the **open after the news**. Articles 9:00-9:30 are excluded. Open-to-open holding.
- **Key numbers:** Day+1 long-short **Sharpe 4.21 EW vs 1.24 VW**. The long leg has Sharpe 1.76 and the short leg 1.33. Day-1 (infeasible) Sharpe of 5.98 shows prices move *before* most news. Day-0 (infeasible) Sharpe is 11.08.
  - **Novelty:** fresh news (cosine novelty >= 0.75) earns 36 bp next day vs 22 bp for stale news. Fresh news takes 4 days to be fully priced, stale news 2 days.
  - **Size:** large caps 11 bp, done in 1 day; small caps 46 bp over 3 days.
  - **Volatility:** low-volatility firms 17 bp vs high-volatility firms 47 bp.
  - **Cost and benchmark:** SESTM beats RavenPack by 30% (EW) and 70% (VW) in Sharpe. Net of 10/20 bp costs with turnover control, the net Sharpe peaks at about 2.3. Excluding earnings-window news, Sharpe is 3.59 EW.
- **Limitations:** Profits sit in small, high-volatility stocks where options are illiquid or absent. The VW edge is modest.

### 2.8 Chen, Kelly & Xiu (2022, rev. 2026), "Expected Returns and Large Language Models", SSRN 4416687
- **Method:** LLM embeddings (ChatGPT, LLaMA, RoBERTa, BERT vs Word2vec and bag-of-words) of news text, used to forecast cross-sectional returns; **16 markets, 13 languages**.
- **Findings:** LLM embeddings beat word-based representations. News is assimilated **with an inefficient delay consistent with limits to arbitrage**. Strategies on **fresh news alerts have higher Sharpe**, and Sharpe remains meaningful after transaction costs. Exact Sharpe values were not visible in accessible versions.
- **Limitation:** Pretrained LLMs may embed post-period knowledge (see Section 6). Embeddings plus a trained head are heavier to build than prompt sentiment.

### 2.9 Lopez-Lira & Tang (2023, v6 Oct 2025), "Can ChatGPT Forecast Stock Price Movements? Return Predictability and Large Language Models", arXiv 2304.07619
- **Data:** Web-scraped headlines matched to RavenPack; **Oct 2021 - May 2024** (post-training-cutoff for GPT-4); 4,123 stocks; 159,137 firm-headline-days; 82% of headlines are overnight (before 9am or after 4pm).
- **Timing:** Headlines before 9am are traded open-to-close the same day. After-close headlines are traded at the next open to close.
- **Key numbers (GPT-4, before costs):**
  - The *initial reaction* is non-tradable: 3.06%/day, hit rate 93%.
  - The *tradable overnight drift* is **0.34%/day, hit 58%, Sharpe 2.97**. Intraday-news drift is 0.50%/day, Sharpe 2.63.
  - Other models' Sharpe: GPT-3.5 1.66, BART-large 1.05, Llama2-70b 0.97. GPT-1, GPT-2 and BERT are negative.
  - **Costs:** at 5 bp round-trip, cumulative return is >300%. At 10 bp it is >100%. **At 20 bp the strategy is unprofitable.**
  - **Decay:** annual Sharpe **6.54 (2021Q4) -> 3.68 (2022) -> 2.33 (2023) -> 1.22 (Jan-May 2024)**, which the authors link to LLM adoption.
  - **Short leg dominates:** 26 bp/day (Sharpe 2.01) vs long 8 bp/day (Sharpe 0.78). Stronger for small firms.
- **Limitations:** Equal-weighted daily rebalancing of many small names. Costs at 10-20 bp are optimistic for small caps and irrelevant to options (5-15% spreads).

### 2.10 Kirtac & Germano (2024), "Sentiment Trading with Large Language Models", Finance Research Letters
- **Data:** 965,375 U.S. news articles, 2010-2023.
- **Key numbers:** An OPT (GPT-3 family) next-day long-short strategy with 10 bp costs has **Sharpe 3.05**, vs 1.23 for the Loughran-McDonald dictionary. It returns 355% Aug 2021 - Jul 2023. Accuracy is 74.4%.
- **Limitations:** In-sample training on returns, an optimistic 10 bp cost, a short headline test window, and LLM pretraining overlapping the sample.

### 2.11 Cao, Han, Li, Yang & Zhan (2024), "Forecasting Option Returns with News", working paper (AFA)
- **The one paper that predicts option returns from news text.**
- **Data:** Newspaper articles on optionable U.S. stocks, **Jan 1996 - Nov 2022**. Monthly delta-hedged option returns, OptionMetrics.
- **Method:** Support vector regression (plus elastic net, random forest, neural networks) on word features, out-of-sample. Quintile sorts; controls for idiosyncratic volatility (IVOL), the volatility deviation (IV minus RV), illiquidity, IV uncertainty and implied skew/kurtosis.
- **Key numbers:** High-minus-low delta-hedged return of **0.49%/month (calls), 0.33%/month (puts)**, robust to factor models. The strongest channel is predicted **future IV changes** (about 20% lexical overlap), then idiosyncratic volatility, skew and kurtosis.
- **Limitations:** No transaction-cost analysis. Monthly delta-hedged returns of about 0.5% vs option half-spreads of several percent means **not tradeable for retail** as-is. The text-predicts-IV-changes channel is the useful idea: news predicts *future vol*, which matters for whether to buy or sell premium.

### 2.12 Option-market sentiment papers (smaller literature)
- Härdle and coauthors (St. Gallen EWP 1808), and Fengler and coauthors (NASDAQ news sentiment vs single-stock options): option IV and OTM put prices rise after **negative** articles, and sentiment adds predictive information beyond option variables. These are working papers with no tradeable cost analysis.

**Section takeaway:** Text sentiment predicts 1-4 day stock returns, more strongly for fresh, negative news about small and volatile firms. LLMs beat dictionaries, and dictionaries beat nothing. The tradable piece is the *drift after the first print*. It is tens of bp/day before costs, is decaying fast (Lopez-Lira & Tang Sharpe 6.5 -> 1.2 in about 2.5 years), and is killed by about 20 bp round-trip costs in the most-cited LLM study.

---

## 3. Option-market reaction to news: scheduled vs unscheduled

### 3.1 Dubinsky, Johannes, Kaeck & Seeger (2019), "Option Pricing of Earnings Announcement Risks", RFS
- **Finding:** Option prices embed a **large, time-varying anticipated earnings-day jump** that is informative about realized volatility. The term structure of IV shows a pre-announcement hump that collapses after.
- **Relevance:** Scheduled events are priced in IV in advance. This is the mechanism behind our null on mega-cap condors, flies and calendars.

### 3.2 Beber & Brandt (2006), "The Effect of Macroeconomic News on Beliefs and Preferences: Evidence from the Options Market", J. Monetary Economics
- **Data:** Treasury bond options around scheduled macro releases.
- **Finding:** Announcements **reduce implied second moments regardless of content** (uncertainty resolution, i.e. IV crush). Higher-moment changes depend on good vs bad news, consistent with time-varying risk aversion.

### 3.3 Savor & Wilson (2016), "Earnings Announcements and Systematic Risk", JF
- **Key number:** Firms scheduled to announce earn about **9.9% annualized abnormal return** in announcement months/windows. The premium is risk-based and persistent; early announcers earn more.
- **Relevance:** A *stock* drift around scheduled events, small per event (a few tens of bp). A covered call or cash-secured put tilted to scheduled announcers captures some of it, but the IV premium paid or received dominates.

### 3.4 Gao, Xing & Zhang (2018), "Anticipating Uncertainty: Straddles Around Earnings Announcements", JFQA
- **Key number:** ATM straddles bought **3 days before** and held through the announcement earn **3.34%** on average (an earlier version reports 2.3% from the day before). The return is larger for small, high-volatility, illiquid firms with **higher transaction costs**.
- **Relevance and conflict:** Our run-up straddle test on 2024-2026 mega caps found nothing after costs. That fits the paper's own heterogeneity: the edge lives where spreads are widest. Govindaraj et al. (working paper) find **negative after-cost** straddle returns around earnings and annual meetings.

### 3.5 Fodor, Muravyev, Cremers & Weinbaum, "Option Trading Activity, News Releases, and Stock Return Predictability", Management Science (2022/23)
- **Key finding:** **Option purchases predict stock returns on news days and before *unscheduled* events, not before scheduled ones. Option sales predict only before *scheduled* events.** The reason given is that only scheduled news moves *expected volatility* and therefore option prices. The authors explicitly find that trading and margin costs materially reduce profitability.
- **Relevance:** This is the cleanest statement of the scheduled/unscheduled distinction. Informed traders buy options ahead of surprises and sell premium ahead of scheduled events.

### 3.6 Lee & Mykland (2008), "Jumps in Financial Markets: A New Nonparametric Test and Jump Dynamics", RFS
- **Finding:** For individual equities, **most jumps occur with unscheduled news**, with magnitudes comparable to earnings jumps.
- **Relevance:** Unscheduled news is where short-premium positions blow up. A short-vol bot must have an *unscheduled-news kill switch*, and the news feed is good at that.

### 3.7 Donders & Vorst (1996), "The Impact of Firm Specific News on Implied Volatilities", JBF
- **Finding:** IV rises into scheduled news, peaks on the eve, and drops sharply after before reverting gradually. This is the classic pattern and the baseline for any post-event IV-crush trade.

### 3.8 Post-earnings drift and its decline: Martineau (2022), "Rest in Peace Post-Earnings Announcement Drift", Critical Finance Review
- **Finding:** Prices now fully reflect earnings surprises on the announcement day. **PEAD is nonexistent for large stocks since about 2006** and has only recently vanished in microcaps. Causes cited are decimalization and algorithmic trading.
- **Caveat:** Some 2025 papers (not reviewed in full here) argue PEAD survives with better surprise measures. Even if true, it lives in small caps.

### 3.9 Attention and retail flows
- **Barber & Odean (2008), "All That Glitters", RFS:** individuals are **net buyers of attention-grabbing stocks** (news, extreme returns, abnormal volume). This is the foundation for hype-fade.
- **Barber, Huang, Odean & Schwarz (2022), "Attention-Induced Trading and Returns: Evidence from Robinhood Users", JF:** Robinhood herding episodes forecast **negative returns**. Working versions report about -4.7% 20-day abnormal returns for the top daily buys, and about -3% (-6% for extreme herding) over 5 days.
- **Bryzgalova, Pavlova & Sikorskaya (2023), "Retail Trading in Options and the Rise of the Big Three Wholesalers", JF:** retail is **>60% of option volume** at times. Retail favors cheap short-dated (weekly) options with an **average bid-ask spread of 12.6%**, and **loses money on average**.
- **de Silva, Smith & So (2025), "Losing Is Optional: Retail Option Trading and Expected Announcement Volatility", Review of Finance:** retail buys options before high-expected-volatility earnings, overpays for IV, pays wide spreads, and holds losers. Losses are about **5-9% on average, 10-14% for high-expected-volatility announcements**.
- **Relevance:** The dumb money in news-driven options is the *buyer* of short-dated OTM options after or into hyped news. The structural counterparty trade is defined-risk premium selling on high-attention, stale or hype news, **but not** on real unscheduled catalysts (Lee & Mykland jumps).

---

## 4. Trading on news at intraday speed

### 4.1 Chordia, Green & Kottimukkalur (2018), "Rent Seeking by Low-Latency Traders: Evidence from Trading on Macroeconomic Announcements", RFS
- **Finding:** SPY and E-mini prices react to macro surprises **within 5 milliseconds**. Low-latency profits are small: about **$19k (SPY) and $50k (ES) per event**, and they have not grown as speed increased.
- **Relevance:** Retail cannot compete on the first reaction to scheduled macro news. Do not try.

### 4.2 von Beschwitz, Keim & Massa (2020), "First to 'Read' the News: News Analytics and Algorithmic Trading", Review of Asset Pricing Studies
- **Finding:** Coverage by a news-analytics vendor (RavenPack) **causally speeds up price and volume responses** but reduces liquidity. The effect is largest for **press releases**, and HFTs use analytics for directional trades on firm news.
- **Relevance:** Benzinga-style press-release headlines are exactly what machines already read. A REST-polling retail bot arrives seconds to minutes late.

### 4.3 Fedyk (2024), "Front-Page News: The Effect of News Positioning on Financial Markets", JF
- **Finding:** Bloomberg front-page placement produces **240% higher volume and 176% larger absolute returns in the first 10 minutes**. Information is **fully incorporated within an hour**.
- **Relevance:** For prominent large-cap news, the intraday window has closed long before an end-of-day option bar exists.

### 4.4 Jiang, Li & Wang (2021), "Pervasive Underreaction: Evidence from High-Frequency Data", JFE
- **Finding:** Decomposing returns intraday into news-driven and non-news parts, prices **drift in the direction of the initial news reaction for several days without reversal**, and a strategy remains **profitable after costs**. Drift is stronger when investors are distracted (busy news days) and when analysts adjust slowly.
- **Relevance:** This supports the *multi-day* follow-through entry, measured from the *initial reaction direction* rather than from text alone. A retail trader can enter at the next open after observing the day-0 reaction.

**Section takeaway:** Speed matters only in the first minutes to hours, and retail loses that race. The retail-accessible window is the **day+1 to day+4 drift**, especially when (i) the news is fresh, (ii) the stock is mid/small cap, (iii) the news is negative, and (iv) investor attention is diluted.

---

## 5. Synthesis

### 5.1 Is there an after-cost options edge for a retail headline bot? At which horizon and with which structure?

| Candidate structure | Evidence for | Evidence against | Verdict |
|---|---|---|---|
| **Buy volatility (straddle/strangle) after unscheduled news** | Unscheduled news causes most jumps (Lee & Mykland); news text predicts future IV changes (Cao et al.) | The jump is already in the price by the open after; IV reprices up instantly; retail long-vol loses (de Silva et al.; BPS 12.6% spreads) | **No.** Buying after the jump pays the new, higher IV. |
| **Sell volatility after unscheduled news (post-shock IV crush)** | IV mean-reverts after the event (Donders & Vorst); Tetlock 2011 stale-news reversal | Fresh fundamental news has multi-day drift and clustering vol (Boudoukh et al.; Jiang, Li & Wang); tail risk | **Only** on stale or hype news, defined-risk, after day 0. Never on "real catalyst" or fresh dilution news. Testable. |
| **Sell volatility before scheduled news** | Fodor et al. (informed option sales before scheduled events); Dubinsky et al. (IV hump) | Our own 2024-2026 mega-cap condor/fly/calendar null; Gao-Xing-Zhang (long straddle pays in small caps) | **Already tested negative** for mega caps. Do not revisit without a new conditioning variable. |
| **Directional debit spread after a sentiment shock (day+1 entry, 2-5 day hold)** | KKX: fresh news priced over 4 days, small caps 46 bp day+1; Lopez-Lira & Tang drift 0.34%/day; Heston & Sinha negative news absorbed slowly; Jiang, Li & Wang multi-day drift | Large caps priced in 1 day (11 bp); LLM edge decays fast; option spread cost about 3-10% of debit vs a 0.5-1.5% expected underlying move | **Weak but the best candidate**, restricted to **negative, fresh, unscheduled news in liquid mid caps**, using bear put debit spreads near the money with tight markets. Must beat its own spread cost. |
| **Fade hype/attention (short premium or bearish spread on attention spikes)** | Tetlock 2011 (stale news reverses within a week, stronger with retail); Barber-Odean; Barber et al. 2022 (Robinhood herding -3% to -4.7%); BPS / de Silva (retail overpays for options) | Squeezes; borrow; meme dynamics; small-cap option illiquidity | **Most robust retail-side evidence.** Fits our existing "hype" label. Express as bear call credit spreads or put debit spreads 1-5 days after a hype headline plus volume spike. |
| **Weekly-aggregated sentiment held for weeks (to next earnings)** | Heston & Sinha (weekly news predicts for a quarter); Xing-Zhang-Zhao smirk lasts 6 months | Older samples; drift has decayed | Plausible as a **low-turnover tilt** for covered call / cash-secured put strike selection, e.g. sell CSPs only on names with positive 4-week sentiment and no negative smirk. Cheap to test. |
| **Informed flow (PCR, O/S, abnormal OTM call volume)** | Pan-Poteshman; Johnson-So; Augustin et al. | Requires signed intraday data; decayed (Cremers-Weinbaum) | Use only as a **veto** (abnormal OTM call volume means do not sell calls) with daily bars. |

**Overall:** The evidence supports news as (1) a **risk filter** for premium-selling and shorting, which is the highest-value use and already done in the shorting bot, and (2) a **slow-drift directional tilt** in the 1-5 day window. It does not support news as a trigger for fast option entries. The academic stock-level edge is about 10-50 bp/day before costs and concentrated where options are least liquid. A level-3 retail account turns it into option P&L only if the structure keeps round-trip cost well under 2-3% of the expected underlying move. In practice that means liquid mid caps, near-the-money debit spreads, limit orders at or near mid, and 2-5 day holds.

### 5.2 Backtest design: Alpaca news + daily option bars

**Data and timestamp hygiene (look-ahead controls)**
1. Use Alpaca news `created_at` (first publication), **not** `updated_at`. Benzinga edits headlines and adds tickers after publication. Drop stories whose `updated_at - created_at > X` minutes if ticker tags may have been added late, or keep only the original symbol list if it can be recovered. Validate on a sample by checking live-captured stories against later historical pulls.
2. Map timestamps to sessions in **America/New_York**. Use the KKX/TSM convention: news before 9:00 ET means trade at that day's open; 9:00-16:00 means trade at the next open (with daily bars, an intraday entry cannot be priced honestly); after 16:00 means trade at the next open. Exclude 9:00-9:30 or treat as next-day, as KKX and TSM do.
3. **Option fills:** daily option bars give OHLC/VWAP of trades, not quotes. Price entries at **next-day open or VWAP plus a spread penalty**, using a modeled half-spread by moneyness, days to expiry (DTE) and underlying liquidity (minimum 5% of premium for liquid names, 10%+ for small caps, per BPS). Never fill at the close of the news day.
4. Survivorship: build the universe from **point-in-time** listings (include delisted and acquired names). Alpaca option history only reaches back to about 2024, which limits options tests to about 2 years. Use stock-only tests on the longer news history (Benzinga via Alpaca from about 2015) to establish the signal, then options only to measure cost drag.
5. **Earnings/event calendar:** tag scheduled events (earnings, FDA PDUFA, investor days) from a point-in-time calendar so the scheduled/unscheduled split is not inferred from the news itself.

**Features (per ticker-day)**
- `cls_keyword`: existing classifier label (real_catalyst / hype / dilution / no_news) plus a finer event type (earnings, guidance, M&A, FDA, offering, analyst, legal, product, macro-mention), following Boudoukh et al.
- `sent_llm`: LLM headline sentiment in {-1, 0, +1} plus confidence, from a **model with a training cutoff before the test period** or an anonymized prompt (company name and ticker replaced with "Company X", per Glasserman & Lin). Compare against Loughran-McDonald dictionary tone as the baseline.
- `novelty`: max cosine similarity of the headline+summary embedding to the ticker's previous 10 stories (Tetlock 2011) or the previous 5 days (KKX cutoff 0.75). Also count same-story duplicates across sources.
- `source/type`: Benzinga channel (press release vs Benzinga staff vs analyst ratings vs "movers"/"why is X moving"). "Why is it moving" and movers stories are **stale by construction** and should be treated as hype/attention, not information.
- `reaction0`: day-0 return (close vs prior close, or open gap for overnight news) and abnormal volume. Per Jiang, Li & Wang, drift follows the reaction direction, so require sentiment and reaction to agree.
- `attention`: abnormal stock volume, abnormal option volume (O/S vs 20-day), count of headlines today vs 20-day average, Robinhood-style top-mover flag.
- `options_state`: ATM IV, IV rank, IV minus 20-day realized vol (VRP proxy), call-put IV spread (Cremers-Weinbaum), 25-delta skew (Xing-Zhang-Zhao), quoted/approx spread.
- `scheduled_flag`: within ±2 days of a scheduled event.
- `size/liquidity`: market cap bucket, option OI and volume at target strikes.

**Entry rules to test (pre-registered)**
- R1 *Negative fresh unscheduled drift:* `sent_llm = -1` AND `novelty >= 0.75` AND `scheduled_flag = 0` AND `reaction0 <= -2%` AND mid cap (about $2-20B) AND option liquidity filter. Enter a 30-45 DTE ATM/1-strike-OTM **bear put debit spread** at the next open; hold 3-5 sessions or 50% of max profit; stop at -50% of debit.
- R2 *Hype fade:* `cls = hype` OR (`novelty < 0.5` AND attention spike >= 3x) AND `reaction0 >= +5%` AND no real-catalyst label. Enter a **bear call credit spread** (short strike about 0.20-0.30 delta, 14-30 DTE) or a put debit spread at the next open; hold 5 sessions or 50% of credit.
- R3 *Sentiment-gated premium selling (longer horizon):* sell 30-45 DTE cash-secured puts or bull put spreads only on names with positive 4-week aggregated sentiment, no negative fresh news in 5 days, no abnormal OTM call or put volume, and not within the scheduled-event window. Benchmark: the identical rule without the sentiment gate. The news gate must improve drawdown or Sharpe, not just win rate.
- Kill switch for all short-premium positions: any `real_catalyst`, `dilution` or fresh negative unscheduled headline closes or hedges the position at the next open.

**Evaluation**
- Stock-only versions first on the full Alpaca news history (2015+), with costs of 10/20/40 bp. Then option versions on 2024-2026 with modeled spreads at 50%, 75% and 100% of quoted width.
- Report per-trade expectancy net of costs, Sharpe, max drawdown, hit rate, and **the ratio of edge to spread cost**. Split by year (decay check), size bucket, scheduled/unscheduled, and LLM-in-sample vs post-cutoff.
- Placebos: shuffle sentiment across tickers within a day; lag the news by 5 days; anonymized vs named prompts.
- Multiple testing: few pre-registered rules, a holdout year (e.g. train 2015-2022 stock-only, test 2023-2026), and no strike/DTE optimization on the test set.

### 5.3 Risks
- **Crowding and decay:** The Lopez-Lira & Tang Sharpe fell from 6.54 to 1.22 between 2021Q4 and early 2024. Cremers-Weinbaum predictability declined within its own sample. PEAD is gone for large caps (Martineau). Every news-analytics vendor speeds up pricing (von Beschwitz et al.). Assume half-lives of a few years from publication, and expect any LLM-headline edge after 2024 to be smaller than published.
- **Cost asymmetry in options:** Retail option spreads average about 12.6% (BPS). Even a liquid mid-cap vertical costs about 2-6% of debit round-trip. A news drift of 0.3-0.5% in the underlying moves a 30-DTE ATM vertical by roughly 3-8%, so the edge is the same order as the cost.
- **Survivorship:** Alpaca's asset list is current. Delisted, acquired and bankrupt names (the most news-heavy negative cases) must be added back, or the short-side results will be biased.
- **Timestamp and revision risk:** Benzinga `updated_at` revisions and late ticker tagging can leak information. Historical pulls may differ from what the live stream delivered. Mitigate by recording the live stream from now on and reconciling.
- **LLM look-ahead:** LLMs memorize pre-cutoff economic data (Lopez-Lira, Tang & Zhu 2025: perfect recall of pre-cutoff values and no recall after the cutoff, and instructions to "not use future knowledge" fail). Glasserman & Lin (2023) find anonymized headlines *outperform* named ones in-sample, so naming the firm imports future knowledge for large caps. Gao, Jiang & Yan (2025) propose a lookahead-propensity test. He, Lv, Manela & Wu (2025) offer chronologically consistent models (ChronoBERT/ChronoGPT) and find the bias is modest for next-day news returns, but model-specific. **Rule:** only evaluate LLM sentiment on news dated after the model's training cutoff, or anonymize and use point-in-time models. Treat any pre-cutoff LLM backtest as an upper bound.
- **Regime and selection:** 2024-2026 options data covers one regime. Small samples per rule (tens to low hundreds of trades) make Sharpe estimates noisy. Require per-trade t-stat > 2 after costs before going live, and paper trade first.
- **Adverse selection on short premium:** abnormal OTM call volume before M&A (Augustin et al.) and unscheduled jumps (Lee & Mykland) mean the counterparty to a cheap-looking short-premium fill after news may be informed.

---

## 6. Relation to our existing work
- The shorting bot's keyword classifier is consistent with the literature. Boudoukh et al. show that *identified fundamental news* drives volatility. Chan (2003) and Tetlock (2011) show that no-news moves and stale news reverse. So vetoing shorts on "real catalyst" news and favoring "hype/no news" setups is the evidence-based use.
- Our null on mega-cap earnings condors, flies, calendars and run-up straddles fits Dubinsky et al. (scheduled risk priced in IV), Martineau (no large-cap drift), and Gao-Xing-Zhang (straddle edge only in small, costly names). The literature does not suggest a news overlay would rescue those structures on mega caps.
- Next experiments, in priority order: R2 (hype fade, reusing the existing label), R1 (negative fresh unscheduled drift in mid caps), R3 (sentiment-gated CSP/put-spread selling as a filter on the live dip put-credit-spread bot).

---

## References
All entries: **found via web; not LibKey-resolved.**

1. Augustin, P., Brenner, M., & Subrahmanyam, M. G. (2019). Informed options trading prior to takeover announcements: Insider trading? *Management Science*, 65(12), 5697-5720. https://doi.org/10.1287/mnsc.2018.3122. Found via web; not LibKey-resolved.
2. Barber, B. M., & Odean, T. (2008). All that glitters: The effect of attention and news on the buying behavior of individual and institutional investors. *Review of Financial Studies*, 21(2), 785-818. https://doi.org/10.1093/rfs/hhm079 (verify). Found via web; not LibKey-resolved.
3. Barber, B. M., Huang, X., Odean, T., & Schwarz, C. (2022). Attention-induced trading and returns: Evidence from Robinhood users. *Journal of Finance*, 77(6), 3141-3190. https://doi.org/10.1111/jofi.13183. Found via web; not LibKey-resolved.
4. Beber, A., & Brandt, M. W. (2006). The effect of macroeconomic news on beliefs and preferences: Evidence from the options market. *Journal of Monetary Economics*, 53(8), 1997-2039. https://doi.org/10.1016/j.jmoneco.2006.05.013. NBER WP 9914: https://www.nber.org/papers/w9914. Found via web; not LibKey-resolved.
5. Blau, B. M., Nguyen, N., & Whitby, R. J. (2014). The information content of option ratios. *Journal of Banking & Finance*, 43, 179-187. https://ideas.repec.org/a/eee/jbfina/v43y2014icp179-187.html. Found via web; not LibKey-resolved.
6. Boudoukh, J., Feldman, R., Kogan, S., & Richardson, M. (2019). Information, trading, and volatility: Evidence from firm-specific news. *Review of Financial Studies*, 32(3), 992-1033. https://doi.org/10.1093/rfs/hhy083. Found via web; not LibKey-resolved.
7. Bryzgalova, S., Pavlova, A., & Sikorskaya, T. (2023). Retail trading in options and the rise of the big three wholesalers. *Journal of Finance*, 78(6), 3465-3514. https://doi.org/10.1111/jofi.13285. https://lbsresearch.london.edu/id/eprint/2827/. Found via web; not LibKey-resolved.
8. Cao, J., Han, B., Li, G., Yang, R., & Zhan, X. (2024). Forecasting option returns with news. Working paper, February 2024. https://afajof.org/management/viewp.php?n=82124. Found via web; not LibKey-resolved.
9. Chan, W. S. (2003). Stock price reaction to news and no-news: Drift and reversal after headlines. *Journal of Financial Economics*, 70(2), 223-260. https://doi.org/10.1016/S0304-405X(03)00146-6. Found via web; not LibKey-resolved.
10. Chen, Y., Kelly, B. T., & Xiu, D. (2022, rev. 2026). Expected returns and large language models. SSRN Working Paper 4416687. https://doi.org/10.2139/ssrn.4416687. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416687. Found via web; not LibKey-resolved.
11. Chordia, T., Green, T. C., & Kottimukkalur, B. (2018). Rent seeking by low-latency traders: Evidence from trading on macroeconomic announcements. *Review of Financial Studies*, 31(12), 4650-4687. https://doi.org/10.1093/rfs/hhy025. Found via web; not LibKey-resolved.
12. Cremers, M., & Weinbaum, D. (2010). Deviations from put-call parity and stock return predictability. *Journal of Financial and Quantitative Analysis*, 45(2), 335-367. https://doi.org/10.1017/S002210901000013X (verify). https://ideas.repec.org/a/cup/jfinqa/v45y2010i02p335-367_00.html. Found via web; not LibKey-resolved.
13. de Silva, T., Smith, K., & So, E. C. (2025). Losing is optional: Retail option trading and expected announcement volatility. *Review of Finance* (forthcoming/2025). https://www.gsb.stanford.edu/faculty-research/publications/losing-optional-retail-option-trading-expected-announcement. Found via web; not LibKey-resolved.
14. Donders, M. W. M., & Vorst, T. C. F. (1996). The impact of firm specific news on implied volatilities. *Journal of Banking & Finance*, 20(9), 1447-1461. https://doi.org/10.1016/0378-4266(96)00011-8 (verify). https://repub.eur.nl/pub/69503. Found via web; not LibKey-resolved.
15. Dubinsky, A., Johannes, M., Kaeck, A., & Seeger, N. J. (2019). Option pricing of earnings announcement risks. *Review of Financial Studies*, 32(2), 646-687. https://doi.org/10.1093/rfs/hhy060. Found via web; not LibKey-resolved.
16. Fedyk, A. (2024). Front-page news: The effect of news positioning on financial markets. *Journal of Finance*, 79(1). https://doi.org/10.1111/jofi.13287. Found via web; not LibKey-resolved.
17. Fodor, A., Muravyev, D., Cremers, M., & Weinbaum, D. (2022/2023). Option trading activity, news releases, and stock return predictability. *Management Science* (DOI not verified). https://whitman.syracuse.edu/about/newsroom/whitman-news/news-detail/2022/08/16/option-trading-activity-news-releases-and-stock-return-predictability. Found via web; not LibKey-resolved.
18. Gao, C., Xing, Y., & Zhang, X. (2018). Anticipating uncertainty: Straddles around earnings announcements. *Journal of Financial and Quantitative Analysis*, 53(6). DOI not verified. https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/anticipating-uncertainty-straddles-around-earnings-announcements/7B34877AD5E06304BA3C55FBA3219FDD. Found via web; not LibKey-resolved.
19. Gao, Z., Jiang, W., & Yan, Y. (2025). A test of lookahead bias in LLM forecasts. arXiv:2512.23847. https://arxiv.org/abs/2512.23847. Found via web; not LibKey-resolved.
20. Ge, L., Lin, T.-C., & Pearson, N. D. (2016). Why does the option to stock volume ratio predict stock returns? *Journal of Financial Economics*, 120(3), 601-622. https://doi.org/10.1016/j.jfineco.2015.08.019 (verify). https://ideas.repec.org/a/eee/jfinec/v120y2016i3p601-622.html. Found via web; not LibKey-resolved.
21. Glasserman, P., & Lin, C. (2023). Assessing look-ahead bias in stock return predictions generated by GPT sentiment analysis. arXiv:2309.17322. https://arxiv.org/abs/2309.17322. Found via web; not LibKey-resolved.
22. Govindaraj, S., and coauthors (working paper). Straddles around earnings announcements and annual shareholder meetings. SSRN 2501750. https://wallstreethorizon.com/upload/SSRN-id2501750_govindaraj.pdf. Found via web; not LibKey-resolved.
23. Härdle, W. K., and coauthors (2018). Distilling sentiment from news and single-stock option prices. University of St. Gallen Economics Working Paper 1808. https://ux-tauri.unisg.ch/RePEc/usg/econwp/EWP-1808.pdf. Found via web; not LibKey-resolved.
24. He, S., Lv, L., Manela, A., & Wu, J. (2025). Chronologically consistent large language models. arXiv:2502.21206. https://arxiv.org/abs/2502.21206. Found via web; not LibKey-resolved.
25. Heston, S. L., & Sinha, N. R. (2017). News vs. sentiment: Predicting stock returns from news stories. *Financial Analysts Journal*, 73(3), 67-83. https://doi.org/10.2469/faj.v73.n3.3. Found via web; not LibKey-resolved.
26. Jiang, H., Li, S. Z., & Wang, H. (2021). Pervasive underreaction: Evidence from high-frequency data. *Journal of Financial Economics*, 141(2), 573-599. https://doi.org/10.1016/j.jfineco.2021.04.003. Found via web; not LibKey-resolved.
27. Johnson, T. L., & So, E. C. (2012). The option to stock volume ratio and future returns. *Journal of Financial Economics*, 106(2), 262-286. https://doi.org/10.1016/j.jfineco.2012.05.008. Found via web; not LibKey-resolved.
28. Ke, Z. T., Kelly, B. T., & Xiu, D. (2019, rev. 2021). Predicting returns with text data. NBER Working Paper 26186. https://doi.org/10.3386/w26186. https://www.nber.org/papers/w26186. Found via web; not LibKey-resolved.
29. Kirtac, K., & Germano, G. (2024). Sentiment trading with large language models. *Finance Research Letters*, 62, 105227. https://doi.org/10.1016/j.frl.2024.105227. Found via web; not LibKey-resolved.
30. Lee, S. S., & Mykland, P. A. (2008). Jumps in financial markets: A new nonparametric test and jump dynamics. *Review of Financial Studies*, 21(6), 2535-2563. https://doi.org/10.1093/rfs/hhm056 (verify). Found via web; not LibKey-resolved.
31. Lopez-Lira, A., & Tang, Y. (2023, v6 2025). Can ChatGPT forecast stock price movements? Return predictability and large language models. arXiv:2304.07619. https://doi.org/10.48550/arXiv.2304.07619. https://arxiv.org/abs/2304.07619. Found via web; not LibKey-resolved.
32. Lopez-Lira, A., Tang, Y., & Zhu, M. (2025). The memorization problem: Can we trust LLMs' economic forecasts? arXiv:2504.14765; SSRN 5217505. https://papers.ssrn.com/abstract=5217505. Found via web; not LibKey-resolved.
33. Loughran, T., & McDonald, B. (2011). When is a liability not a liability? Textual analysis, dictionaries, and 10-Ks. *Journal of Finance*, 66(1), 35-65. https://doi.org/10.1111/j.1540-6261.2010.01625.x. Found via web; not LibKey-resolved.
34. Martineau, C. (2022). Rest in peace post-earnings announcement drift. *Critical Finance Review*, 11(3-4), 613-646. https://doi.org/10.1561/104.00000122. https://cfr.ivo-welch.info/published/papers/martineau2021rest.pdf. Found via web; not LibKey-resolved.
35. Pan, J., & Poteshman, A. M. (2006). The information in option volume for future stock prices. *Review of Financial Studies*, 19(3), 871-908. https://doi.org/10.1093/rfs/hhj024 (verify). NBER WP 10925: https://www.nber.org/papers/w10925. Found via web; not LibKey-resolved.
36. Roll, R., Schwartz, E., & Subrahmanyam, A. (2010). O/S: The relative trading activity in options and stock. *Journal of Financial Economics*, 96(1), 1-17. https://doi.org/10.1016/j.jfineco.2009.11.004. Found via web; not LibKey-resolved.
37. Savor, P., & Wilson, M. (2016). Earnings announcements and systematic risk. *Journal of Finance*, 71(1), 83-138. https://doi.org/10.1111/jofi.12361. Found via web; not LibKey-resolved.
38. Somoza, L., Didisheim, A., & Fraschini, M. (2025). AI's predictable memory in financial analysis. *Economics Letters*, 256. https://faculty.essec.edu/research/16148-ais-predictable-memory-in-financial-analysis. Found via web; not LibKey-resolved.
39. Tetlock, P. C. (2007). Giving content to investor sentiment: The role of media in the stock market. *Journal of Finance*, 62(3), 1139-1168. https://doi.org/10.1111/j.1540-6261.2007.01232.x (verify). https://www.columbia.edu/~pt2238/papers/Tetlock_Media_Sentiment_JF.pdf. Found via web; not LibKey-resolved.
40. Tetlock, P. C. (2011). All the news that's fit to reprint: Do investors react to stale information? *Review of Financial Studies*, 24(5), 1481-1512. https://doi.org/10.1093/rfs/hhq141 (verify). https://ideas.repec.org/a/oup/rfinst/v24y2011i5p1481-1512.html. Found via web; not LibKey-resolved.
41. Tetlock, P. C., Saar-Tsechansky, M., & Macskassy, S. (2008). More than words: Quantifying language to measure firms' fundamentals. *Journal of Finance*, 63(3), 1437-1467. https://doi.org/10.1111/j.1540-6261.2008.01362.x (verify). Found via web; not LibKey-resolved.
42. von Beschwitz, B., Keim, D. B., & Massa, M. (2020). First to "read" the news: News analytics and algorithmic trading. *Review of Asset Pricing Studies*, 10(1), 122-178. https://doi.org/10.1093/rapstu/raz007. Found via web; not LibKey-resolved.
43. Xing, Y., Zhang, X., & Zhao, R. (2010). What does the individual option volatility smirk tell us about future equity returns? *Journal of Financial and Quantitative Analysis*, 45(3), 641-662. https://doi.org/10.1017/S0022109010000220. Found via web; not LibKey-resolved.
