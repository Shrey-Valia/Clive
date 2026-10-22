# Trading low-priced and micro-cap US stocks: literature review for the Clive penny-stock extension

Prepared 2026-10-22 for the backtest agent and the bot owner. Scope: listed NASDAQ/NYSE/AMEX common stocks, mostly $1 to $10, held for a day to a week, long and short, traded on Alpaca.

**Evidence tags**
- **[FT]**: I read the number in the paper's full text during this review.
- **[verify]**: the number comes from an abstract, a secondary summary (CXO Advisory, Alpha Architect, Quantpedia, CFA Digest, ETF.com, broker blogs) or my prior knowledge of the paper. Check it against the PDF before relying on it.
- Untagged statements are qualitative descriptions of a paper's design or conclusions.

Full citations with DOIs and LibKey access links are in Section 8.

---

## 0. Bottom line

1. **Our overnight-up / intraday-down pattern is a documented effect, not a data artifact, though costs eat part of it.** High-attention stocks rise overnight and reverse during the day (Berkman et al. 2012). Lou, Polk and Skouras (2019) show that this tug of war persists at the stock level. The decile with the highest past-month overnight returns keeps earning overnight (+3.47%/month 3-factor alpha) and loses intraday (-3.02%/month) **[FT]**. Two caveats:
   - Their sample excludes stocks under $5 and NYSE bottom-quintile micro-caps.
   - Measured from the opening bid-ask midpoint instead of the VWAP open, the overnight leg roughly halves, to 1.88%/month **[FT]**. Much of the measured overnight gain in thin names sits in the opening print.
2. **Long-side lottery exposure loses money over weeks.** High-MAX, high-skew, low-price "lottery" stocks underperform over the following month:
   - Bali, Cakici & Whitelaw 2011: >1%/month spread.
   - Kumar 2009 and Boyer, Mitton & Vorkink 2010: same direction.
   - Eraker & Ready 2015: OTC stocks have negative average returns.

   Any LONG penny strategy should be **short-horizon (overnight to 2 days)**, not a multi-week hold.
3. **Short-term reversal (buying big losers) does not survive costs in micro-caps.**
   - Avramov, Chordia & Goyal 2006 and Novy-Marx & Velikov 2016 find the profits sit in illiquid names and are eaten by costs.
   - Park 1995 and Cox & Peterson 1994 find much of the post-crash bounce is bid-ask bounce.
   - de Groot, Huij & Zhou 2012 find reversal survives costs (30 to 50 bps/week net) **only in large caps** [verify].
4. **News-driven moves continue; no-news moves reverse** (Chan 2003; Savor 2012). That is the cleanest academic support for a day-2 continuation long, and for preferring no-news spikes on the fade short.
5. **Opening-range breakout on "stocks in play"** has one strong but practitioner-authored result. It used 2016 to 2023 data, an opening price **> $5**, a relative-volume filter, and reported Sharpe 2.81 net of commissions only [verify]. An independent replication of the index version finds the edge is gross-only. Treat it as a hypothesis for the $2 to $10 band, not as established.
6. **The penny short side is constrained by borrow more than by alpha.** Expensive-to-borrow and supply-constrained stocks do underperform (Jones & Lamont 2002; Drechsler & Drechsler 2014; Beneish, Lee & Nichols 2015; Engelberg, Reed & Ringgenberg 2018). However:
   - The names where the fade is largest are usually hard-to-borrow, and squeeze risk is fat-tailed.
   - Alpaca's zero-fee path covers only its easy-to-borrow list (5,000+ names).
   - Sub-$5 shorts carry max($2.50/share, 100%) overnight maintenance.
7. **Most published anomalies shrink to near zero after costs and post-publication decay.** Chen & Velikov 2023 put the average at 4 bps/month [verify]. Hou, Xue & Zhang 2020 find about 65% fail once micro-caps get NYSE breakpoints and value weights [verify]. A penny strategy has to earn **well over 1% per round trip** to clear realistic spreads, not tens of basis points.

**Verdicts**
- **LONG.** Only short-horizon, event-driven longs have a defensible prior:
  - the overnight leg of attention spikes;
  - day-2 continuation after news gaps;
  - possibly ORB on stocks in play.

  Generic penny longs (low price, lottery, reversal, low float held for days) have negative or cost-dominated expected returns.
- **SHORT.** The intraday fade is real and consistent with the literature. It is tradable only in ETB names, mostly $5+, with hard risk limits. Sub-$5 spikes are mostly unshortable on Alpaca, or uneconomic once HTB locates are priced in.

---

## 1. Alpaca constraints (checked October 2026)

| Item | Status | Source |
|---|---|---|
| OTC / pink sheets | **Not supported.** Alpaca says it supports only listed US stocks (plus crypto). Forum feature requests for OTC remain open. | Alpaca "Account plans" docs; Alpaca forum threads 729, 2278, 4018 |
| Short selling | Easy-to-borrow (ETB) names have no locate and **no borrow fee**, and the list covers 5,000+ securities. Current docs also describe a **hard-to-borrow (HTB) locate workflow** for eligible margin accounts: locates in 100-share round lots, single-use, with a non-refundable locate fee plus a daily HTB fee of value x rate / 360. Earlier docs said HTB shorting was not offered, so check eligibility on the account. An open short order in a name that flips ETB to HTB overnight is cancelled before the open. | docs.alpaca.markets/us/docs/margin-and-short-selling; Alpaca blog on zero ETB fees |
| Margin minimum | $2,000 equity for margin and shorting; below that, 1x buying power. | same |
| Overnight maintenance, longs | < $2.50: **100%** (effectively no leverage); $2.50 to $6: 50%; > $6: 30%. | same |
| Overnight maintenance, shorts | < $5: **greater of $2.50/share or 100%** (a $2 short needs 125%); >= $5: greater of $5/share or 30%. | same |
| PDT rule | **Removed.** The SEC approved FINRA's Rule 4210 amendments on 2026-04-14, effective 2026-06-04. Alpaca removed the PDT flag and the $25k minimum and replaced them with intraday margin (running intraday buying power, intraday margin calls, up to a 90-day restriction after repeated failures). The minimum for 4x intraday buying power on leverage accounts is now $2,000. PDT fields were removed from the API by 2026-07-06. **For 2016 to mid-2026 backtests, PDT applied to margin accounts under $25k.** | Alpaca blog "FINRA retires the PDT rule"; FINRA RN; Schwab/tastytrade summaries |
| Extended / 24-5 hours | 24/5 trading via the Trading API, Sunday 20:00 ET to Friday 20:00 ET. Outside regular hours only **LIMIT** orders with DAY or GTC are accepted, with `extended_hours=true`. | docs.alpaca.markets/us/docs/245-trading |
| Auction orders | `opg` (market/limit on open) and `cls` (market/limit on close) time-in-force exist. Use them for the overnight leg. | Alpaca orders docs (confirm before relying) |
| Settlement | T+1 since 2024-05-28. Relevant to good-faith violations in **cash** accounts that recycle proceeds the same day. | SEC T+1 rule |
| Historical shortability | **Alpaca does not publish historical ETB/HTB status.** A 2016 to 2026 backtest cannot know which names were shortable. Proxy it (for example, price >= $5, market cap >= $300M, no SSR day), or start logging `GET /v2/assets` borrow status daily now. | inference |

---

## 2. Literature by theme

Each entry gives: **Q** (question), **Data**, **Method**, **Numbers**, **Limits**, and relevance to us (**Use**).

### 2.1 Low-price, lottery and skewness effects

**Bali, Cakici & Whitelaw (2011), "Maxing out", JFE.**
- **Q:** Do stocks with extreme recent daily returns (MAX = highest daily return in the past month) earn lower future returns?
- **Data:** CRSP NYSE/AMEX/NASDAQ, 1962 to 2005.
- **Method:** Monthly decile sorts on MAX; value-weighted (VW) and equal-weighted (EW); Fama-MacBeth controls for size, B/M, momentum, short-term reversal, liquidity and idiosyncratic volatility.
- **Numbers:** VW high-minus-low MAX return about -1.03%/month raw and about -1.18%/month 4-factor alpha [verify]. MAX subsumes the negative idiosyncratic-volatility effect.
- **Limits:** Monthly horizon. Long-short. The short leg is concentrated in small, low-priced, hard-to-borrow names.
- **Use:** Our top-gainer spikes are by construction max-MAX stocks. Over the next month they should drift down, which supports the short side and argues against multi-day longs.

**Kumar (2009), "Who gambles in the stock market?", JF.**
- **Q:** Do retail investors prefer lottery-type stocks, and how do those stocks perform?
- **Data:** About 78,000 retail accounts at a US discount broker, 1991 to 1996, plus CRSP.
- **Method:** Defines lottery stocks as low price, high idiosyncratic volatility and high idiosyncratic skewness, then links holdings to demographics and stock returns.
- **Numbers:** Lottery-type stocks have significantly negative risk-adjusted returns. Individual demand for them rises in bad economic times. Investors who overweight them underperform by roughly 2 to 3% a year [verify].
- **Limits:** The 1990s retail sample predates zero-commission and app trading.
- **Use:** It names who is on the other side of our trades (retail gamblers) and why lottery names are overpriced.

**Barberis & Huang (2008), "Stocks as lotteries", AER.**
- **Q:** What does cumulative-prospect-theory probability weighting imply for prices?
- **Method:** Theory.
- **Numbers:** Positively skewed securities become overpriced and earn negative average excess returns. The model links this to IPO underperformance, low-priced stocks and out-of-the-money options.
- **Use:** The theoretical basis for expecting penny lottery stocks to be overpriced.

**Boyer, Mitton & Vorkink (2010), "Expected idiosyncratic skewness", RFS.**
- **Data:** CRSP 1988 to 2005.
- **Method:** Cross-sectional model of expected idiosyncratic skewness (using lagged skewness, volatility, size, industry and turnover), then portfolio sorts.
- **Numbers:** The low-expected-skew quintile beats the high quintile by about 1%/month in Fama-French alpha [verify].
- **Limits:** Model-based skewness. Monthly holding.
- **Use:** Same direction as MAX. High-skew pennies are poor holds.

**Birru & Wang (2016), "Nominal price illusion", JFE.**
- **Q:** Do investors overestimate the upside (skewness) of low-priced stocks?
- **Data:** Options-implied skewness 1996 to 2012; stock splits.
- **Method:** Implied versus realized skewness by price level and around splits.
- **Numbers:**
  - Implied skewness jumps on split days, while realized skewness falls.
  - Investors overweight nominal price when forming skewness expectations.
  - A zero-cost options strategy exploiting the bias earns significant abnormal returns [verify].
  - A companion WP, "The nominal price premium", reports a 4-factor alpha of about 85 to 88 bps/month [verify].
- **Limits:** The trade is in options, not penny shares.
- **Use:** Psychological support for "cheap = perceived lottery". Expect retail overpaying for $1 to $3 names.

**Fernando, Krishnamurthy & Spindt (2004), "Are share price levels informative? Evidence from ... IPO firms", JFM.**
- **Data:** US IPOs.
- **Numbers:** IPOs with lower offer prices attract more retail and fewer institutional investors, show more underpricing and higher turnover, and have worse post-IPO performance and higher failure rates [verify].
- **Use:** A low nominal price signals a retail clientele and weaker fundamentals. This matters for recent small IPOs in our universe; see the ramp-and-dump discussion in 2.5.

**Hwang & Lu (2008/2009), "Is share price relevant?" (also circulated as the "cheap stock" low-price anomaly), SSRN.**
- **Data:** NYSE/AMEX/NASDAQ 1963 to 2006.
- **Method:** Annual sorts into price bands (<= $5, $5 to $10, $10 to $15, $15 to $20, > $20).
- **Numbers:**
  - Long <= $5 / short > $20 earns 0.53%/month with annual rebalancing and 0.58% with two-year rebalancing, **before frictions** [verify, CXO summary].
  - The effect is dominated by **January** (about 11% in January versus -0.4% in other months) [verify].
  - The authors claim it is independent of size, B/M and momentum.
- **Limits:**
  - Gross of costs.
  - A January / tax-loss effect.
  - It conflicts with Bhardwaj & Brooks (1992), who find the low-price January premium disappears after transaction costs and bid-ask bias.
- **Use:** Not a daily/weekly edge. At most a seasonal tilt (avoid shorting pennies in early January).

**Bhardwaj & Brooks (1992), "The January anomaly: effects of low share price, transaction costs, and bid-ask bias", JF.**
- **Numbers:** The January low-price premium is largely an artifact of bid-ask bias and is not exploitable net of costs [verify].
- **Use:** A methodology warning for any penny backtest using last-trade prices.

**Eraker & Ready (2015), "Do investors overpay for stocks with lottery-like payoffs? An examination of the returns of OTC stocks", JFE.**
- **Numbers:** OTC stocks have large negative average returns and extreme positive skewness. Investors pay for the lottery [verify].
- **Use:** Not tradable on Alpaca, but it shows what sits at the bottom of the low-price distribution. Uplisted ex-OTC names behave similarly.

### 2.2 Short-term reversal and short-term momentum

**Jegadeesh (1990), JF.**
- **Data:** CRSP 1934 to 1987.
- **Method:** Monthly predictive regressions; extreme decile spreads.
- **Numbers:** Extreme-decile reversal spread of about 2.5%/month [verify].
- **Limits:** Gross. EW. Bid-ask bounce. Pre-decimal.

**Lehmann (1990), "Fads, martingales, and market efficiency", QJE.**
- **Method:** Weekly contrarian portfolios.
- **Numbers:** Large positive weekly returns that persist at moderate costs for a liquid subset [verify]. Bid-ask bounce and stale prices explain part of it.
- **Limits:** 1962 to 1986 microstructure.

**Avramov, Chordia & Goyal (2006), "Liquidity and autocorrelations in individual stock returns", JF.**
- **Q:** Where do reversal profits come from?
- **Data:** NYSE/AMEX 1993 to 2001 (and other samples).
- **Method:** Weekly and monthly reversal conditioned on turnover and Amihud illiquidity.
- **Numbers:** Reversal is strongest in **high-turnover, illiquid** stocks, consistent with non-informational price pressure. Profits are small after costs and not exploitable [verify].
- **Use:** Exactly our universe: high turnover on the spike day, illiquid otherwise. Gross reversal exists, but costs bind.

**de Groot, Huij & Zhou (2012), "Another look at trading costs and short-term reversal profits", JBF.**
- **Method:** Weekly reversal with realistic costs and a low-turnover construction.
- **Numbers:** Costs mainly come from trading small caps. Restricting to **large caps** leaves 30 to 50 bps/week net [verify, abstract].
- **Use:** Reversal survives only where spreads are tight, which is not $1 to $10.

**Da, Liu & Schaumburg (2014), "A closer look at the short-term return reversal", Management Science.**
- **Method:** Decomposes the past monthly return into industry, cash-flow-news and residual (non-fundamental) components.
- **Numbers:** Reversal comes from the non-news residual. With news removed it is considerably stronger and survives costs in large caps [verify].
- **Use:** It matches Chan (2003). Condition any reversal long on **no news**.

**Medhat & Schmeling (2022), "Short-term momentum", RFS.**
- **Data:** NYSE/AMEX/NASDAQ 1963 to 2018, VW.
- **Method:** Double sort on prior-month return and turnover.
- **Numbers:** Low-turnover stocks reverse. **High-turnover stocks continue** (short-term momentum), about as profitably as standard momentum and surviving costs, but **strongest among the largest, most liquid stocks** [verify, abstract].
- **Limits:** VW; monthly horizon; not penny-specific. In penny spikes, high turnover goes with MAX and lottery, which predict *negative* returns.
- **Use:** Do not extrapolate its continuation result down to $1 to $10.

**Blitz, Huij, Lansdorp & Verbeek (2013), "Short-term residual reversal", JFM.**
- **Method:** Reversal on factor-model residuals.
- **Numbers:** Residual reversal earns higher risk-adjusted returns than conventional reversal, with lower factor exposure, and remains profitable net of costs in liquid stocks [verify].
- **Use:** If we test buying losers, rank on residual returns, not raw returns.

**Park (1995), JFQA**, and **Cox & Peterson (1994), JF.**
- **Numbers:**
  - Cox & Peterson: after large one-day drops (>= 10%), the next-day rebound is largely bid-ask bounce, and there is underperformance over days 4 to 20 [verify].
  - Park: measured on bid-ask midpoints, reversals after large price changes largely disappear [verify].
- **Use:** Buying big penny losers at the close prices in last-trade bias. Backtest on quotes or midpoints.

**Ball, Kothari & Shanken (1995), JFE.**
- **Numbers:** Contrarian profits are concentrated in very low-priced stocks and are sensitive to microstructure. Excluding sub-$1 names removes much of the effect [verify].

### 2.3 Overnight versus intraday returns

**Lou, Polk & Skouras (2019), "A tug of war: overnight versus intraday expected returns", JFE. [FT]**
- **Data:** CRSP/TAQ 1993 to 2013. **Excludes stocks priced below $5 and NYSE bottom-size-quintile stocks.** VW deciles.
- **Method:** Splits each strategy's monthly return into close-to-open and open-to-close components.
- **Numbers [FT]:**
  - **Momentum:** total 3F alpha 1.05%/month. Overnight 0.95%/month (t = 3.65); intraday 0.11% (t = 0.27). The overnight leg has about half the volatility, so roughly twice the Sharpe ratio.
  - **Stock-level tug of war:** long past-month overnight winners, short overnight losers. Next-month overnight 3F alpha **+3.47%/month (t = 16.8)**, intraday **-3.02%/month (t = -9.7)**. The mirror strategy on past intraday winners earns +2.41% intraday and -1.77% overnight.
  - **Open-price robustness:** using the opening bid-ask midpoint instead of the VWAP open, the overnight-sort strategy earns **1.88%/month overnight and -1.43% intraday**. The measured effect shrinks by about 45% once opening spread effects are removed.
  - **Short-term reversal:** no close-to-close effect in their (no-microcap, VW) sample. The decomposition shows a strong overnight reversal offset by intraday continuation.
- **Limits:** Excludes our price band. Monthly rebalancing. No trading costs.
- **Use:** This is the closest published analogue to our finding. Names with persistently high overnight returns keep them, and their intraday sessions keep fading. A long-overnight / short-intraday rotation on the same names is what the paper's clientele story predicts. The midquote result is the warning: **model the open as midpoint minus half-spread, not the opening print.**

**Berkman, Koch, Tuttle & Zhang (2012), "Paying attention: overnight returns and the hidden cost of buying at the open", JFQA.**
- **Data:** The 3,000 largest US stocks, 1996 to 2008, with intraday quotes.
- **Method:** Attention proxies (including prior-day absolute returns, news and volume), then a close-to-open versus open-to-close split.
- **Numbers [verify, secondary summary]:**
  - Average overnight return about +0.1% per night, with about -0.07% intraday reversal.
  - High-attention, low-institutional-ownership stocks: **+0.2 to 0.3% overnight, -0.2 to 0.4% intraday**.
  - Illiquid, high-short-interest, hard-to-value stocks: **+0.4 to 0.6% overnight, -0.4 to -0.7% intraday**.
  - The effect is more than twice as large in high-sentiment months and stronger on Mondays.
  - The mispricing at the open is concentrated in the first hour.
  - The implied cost to retail buyers at the open often exceeds the effective half-spread.
- **Limits:** Large-cap sample; pre-2009; effect weaker after decimalization.
- **Use:** It sets the mechanism. Retail buy orders accumulated overnight push the open up, and the open reverses. A trader who **holds overnight and sells into the open** is on the profitable side, supplying liquidity to attention-driven buyers.

**Aboody, Even-Tov, Lehavy & Trueman (2018), "Overnight returns and firm-specific investor sentiment", JFQA.**
- **Numbers:** Overnight returns are a firm-level sentiment proxy. High overnight-return stocks show **short-term persistence** (next days and weeks) and reversal over longer horizons, especially among hard-to-value and high-retail stocks [verify].
- **Use:** Supports holding the overnight leg across 1 to 3 nights, not weeks.

**Bogousslavsky (2021), "The cross-section of intraday and overnight returns", JFE.**
- **Numbers:** Anomaly returns are unevenly split across the day. Mispricing builds during the trading day and is corrected sharply in the **last half-hour** and overnight, linked to institutions' reluctance to hold risky (high-IVOL, small) positions overnight [verify].
- **Use:** End-of-day selling pressure in risky small caps is a reason the close may be a cheap entry for an overnight long. Test 15:30 to 16:00 entry timing.

**Hendershott, Livdan & Rösch (2020), "Asset pricing: a tale of night and day", JFE.**
- **Numbers:** The CAPM beta premium appears overnight, not intraday [verify]. This is background for why overnight returns carry a risk premium.

**Boyarchenko, Larsen & Whelan (2023), "The overnight drift", RFS.**
- **Numbers:** Index-level US equity overnight drift is concentrated around the European open (about 2:00 to 3:00 ET) and linked to dealer inventory [verify].
- **Use:** Index-level only. A reason to hedge the overnight leg with SPY/IWM if it is meant to be idiosyncratic.

### 2.4 Retail attention, herding, meme stocks

**Barber & Odean (2008), "All that glitters", RFS.**
- **Data:** Retail and institutional trades, 1990s.
- **Numbers:** Individuals are net buyers of attention-grabbing stocks: high abnormal volume, extreme one-day returns (both up and down) and news [verify]. Institutions are not.
- **Use:** Top gainers draw net retail buying. That is the order flow behind the overnight drift and the open spike.

**Barber, Huang, Odean & Schwarz (2022), "Attention-induced trading and returns: evidence from Robinhood users", JF.**
- **Data:** Robintrack holdings, 2018 to 2020.
- **Numbers:**
  - Stocks most bought by Robinhood users each day have **about -4.7% abnormal returns over the next 20 days** [verify].
  - Extreme herding episodes do worse; secondary sources cite up to about -20% [verify].
  - An early draft reported about -3% (5-day) and -6% for extreme herding [verify].
  - Herding is concentrated in top-gainer and top-volume names.
- **Use:** The 1 to 4 week drift after retail herding is negative, which supports the fade short and short holds for any long.

**Welch (2022), "The wisdom of the Robinhood crowd", JF.**
- **Numbers:** The aggregate Robinhood portfolio did not underperform over 2018 to 2020 and timed the COVID dip well [verify].
- **Use:** A counterweight. Retail is not uniformly wrong, and aggregate retail longs in larger names did fine. The losses are in the extreme-attention tail.

**Ozik, Sadka & Shen (2021), "Flattening the illiquidity curve: retail trading during the COVID-19 lockdown", JFQA.**
- **Numbers:** Retail trading surged during lockdown and improved liquidity in high-retail stocks. Stocks with heavy retail attention showed price pressure followed by reversal [verify].
- **Use:** 2020 to 2021 is a distinct regime. Split the backtest.

**Boehmer, Jones, Zhang & Zhang (2021), "Tracking retail investor activity", JF.**
- **Method:** Identifies marketable retail orders via sub-penny price improvement.
- **Numbers:** Retail order imbalance **positively** predicts returns over the next week or so, about 10 bps/week in the cross-section [verify]. Later work questions the accuracy of the classification.
- **Use:** Retail flow is not uniformly dumb at weekly horizons. Its negative predictive power is concentrated in attention spikes.

**Eaton, Green, Roseman & Wu (2022), "Retail trader sophistication and stock market quality: evidence from brokerage outages", JFE.**
- **Numbers:** During Robinhood outages, liquidity and volatility in high-Robinhood stocks behave as if herding app traders had been destabilizing [verify].

**Pedersen (2022), "Game on: social networks and markets", JFE.**
- **Method:** Theory of social-network-driven beliefs, with GameStop evidence.
- **Use:** Explains squeezes and attention cascades. Crowd-driven price paths can overshoot far beyond fundamental anchors, which is the short-side tail risk.

**Da, Engelberg & Gao (2011), "In search of attention", JF.**
- **Numbers:** Google Search Volume spikes predict higher prices over about 2 weeks, then reversal within the year. Strongest in smaller stocks and IPOs [verify].
- **Use:** Attention lifts prices first and reverses later. Supports short holding periods for longs.

**Seasholes & Wu (2007), "Predictable behavior, profits, and attention", JEF.**
- **Data:** Shanghai upper-price-limit events.
- **Numbers:** Attention-grabbing limit-up days bring retail buying the next day, a temporary price rise, and reversal within days. Smart traders who buy on the event day and sell into the next-day buying profit [verify].
- **Use:** The closest analogue to "buy the spike close, sell the next open". It also shows the edge belongs to whoever supplies stock to next-day attention buyers.

### 2.5 Pump-and-dump and manipulation

**Leuz, Meyer, Muhn, Soltes & Hackethal (2017/2019), "Who falls prey to the Wolf of Wall Street? Investor participation in market manipulation", NBER WP 24083.**
- **Data:** About 470 pump-and-dump schemes (421 in an earlier version, 2002 to 2015) and trades of 110,000+ clients at a German bank.
- **Numbers:** About **8% of active retail investors** joined at least one scheme, and participants **lost about 30% on average** [verify, abstract]. Participation is predicted by portfolio composition and past trading behavior more than by demographics. Some participants are speculative day traders who knowingly ride pumps.
- **Use:** Pumps are common, and the average late participant loses heavily. Day traders who exit early can win, so timing is everything.

**Aggarwal & Wu (2006), "Stock market manipulations", Journal of Business.**
- **Data:** SEC litigation cases, 1990 to 2001.
- **Numbers:** Manipulated stocks are small, illiquid and often OTC. Prices and volume rise during manipulation, then fall after it [verify]. Informed parties sell into the rise.
- **Use:** Spikes with no SEC-filed news, a tiny float, social-media promotion and recent uplisting fit the profile.

**FINRA Regulatory Notice 22-25 (November 2022) and Nasdaq/NYSE listing actions.**
- **Contents:** FINRA warns of "ramp-and-dump" small-cap IPOs. They are typically under $25M raised and under 20M shares, often with up to 90% allocated through foreign (mainly Hong Kong) broker-dealers, and promoted by social-media and messaging scams. Nasdaq has since tightened listing rules:
  - IPO float must be met from offering proceeds (2025).
  - 360-day cap on sub-$1 non-compliance.
  - No new compliance period after a recent reverse split (January 2025).
  - Proposed accelerated suspension below $0.10 for 10 days (August 2025; status [verify]).
- **Use:** Exclude or flag IPOs younger than about 6 months with float under 5M shares. Their day-1 spikes and subsequent collapses (often 70 to 95% in a session) dominate tails on both sides.

**Crypto pumps, briefly.**
- Dhawan & Putniņš (2023, Review of Finance) study hundreds of Telegram-coordinated pumps.
- Hamrick et al. (2021, IP&M) map the pump ecosystem.

Both find:
- prices spike within minutes and reverse within hours;
- organizers and the earliest participants profit;
- the average participant loses [verify].

The pattern carries over to equity social-media pumps. The window to profit as a long is minutes, and the overnight leg is exposed to collapse.

### 2.6 Gaps, opening-range breakouts and intraday momentum

**Zarattini, Barbon & Aziz (2024), "A profitable day trading strategy for the U.S. equity market", SSRN 4729284.**
- **Data:** 7,000+ US stocks, 2016 to 2023.
- **Universe filter:** open > **$5**; 14-day average volume >= 1M shares; ATR14 > $0.50; opening-range relative volume >= 100%; top 20 by relative volume ("stocks in play").
- **Rules:**
  - 5-minute opening range.
  - Long via stop order above the range high if the first candle is up; short below the range low if it is down.
  - Stop at 10% of ATR14.
  - Exit at the close.
  - Size so that a stop costs 1% of equity; leverage capped at 4x; $25k start.
  - Commission $0.0035/share.
- **Numbers:** Total net return about 1,600% versus 198% for SPY, **Sharpe 2.81**, annual alpha about 36% [verify, abstract and CXO]. 15/30/60-minute ORB variants are weaker.
- **Limits:**
  - Commissions only; no explicit spread or slippage model.
  - Authors are affiliated with a day-trading education business.
  - No out-of-sample period.
  - Price floor of $5 (our $1 to $5 band is untested).
  - The 10%-of-ATR stop is very tight and fill-sensitive on minute bars.
- **Use:** A worthwhile test, but replicate it with spread and slippage and stops filled at the next tick on 1-minute bars.

**Zarattini & Aziz (2023), "Can day trading really be profitable?", SSRN 4416622 (QQQ 5-minute ORB).**
- **Numbers:** 2016 to 2023. Annual alpha about 33% net of commissions; TQQQ version 1,484% [verify]. An independent replication on index CFDs (2015 to 2026) reproduces the gross result, but net of spread it is indistinguishable from zero in all five markets tested [verify, MQL5 blog].
- **Use:** A cautionary tale. ORB edges are of the same order as spread costs.

**Gao, Han, Li & Zhou (2018), "Market intraday momentum", JFE.**
- **Data:** SPY 1993 to 2013.
- **Numbers:** The first half-hour return (from the prior close) predicts the last half-hour return, R² about 1.6% [verify]. Stronger on high-volatility, high-volume and macro-news days.
- **Use:** Market level only. Possibly a conditioning filter: avoid initiating fade shorts in the final 30 minutes when the market's first half-hour was strongly up.

**Heston, Korajczyk & Sadka (2010), "Intraday patterns in the cross-section of stock returns", JF.**
- **Numbers:** A stock's return in a given half-hour predicts its return in the same half-hour on subsequent days, up to about 40 days [verify]. The effect is tied to recurring institutional order flow.
- **Use:** Minor. The edge is basis points, far below penny spreads.

**News versus no-news continuation:**
- **Chan (2003), JFE:** Stocks with large moves on public news (headlines) show **drift**, especially after bad news, lasting months. Large moves with **no news reverse** the next month [verify].
- **Savor (2012), JFE:** Price shocks accompanied by analyst reports drift; shocks without information reverse [verify].
- **Use:** The best support for a **day-2 continuation long on news-driven gaps** and a no-news fade. Alpaca's news API (Benzinga feed, about 2015 onward) gives a usable news flag.

**Low float.**
- **Hong, Scheinkman & Xiong (2006), "Asset float and speculative bubbles", JF:** With short-sale constraints and heterogeneous beliefs, a small float produces overpricing, high turnover and volatility, with crashes when float expands (lockups, offerings) [verify].
- There is **no peer-reviewed evidence that "low-float momentum" is a profitable long after costs.** The theory predicts a bubble-then-crash path.

### 2.7 Short-selling constraints and the short side

**D'Avolio (2002), "The market for borrowing stock", JFE.**
- **Data:** A large lender's book, 2000 to 2001.
- **Numbers:** About 91% of stocks are general collateral (fee under 1%/year). About 9% are "special", averaging about 4.3%/year, and a small tail exceeds 10 to 50%/year [verify]. Specials are small, low-IO, high-dispersion, often IPO or glamour stocks. **Recalls and fee spikes cluster in exactly those names.**

**Jones & Lamont (2002), "Short-sale constraints and stock returns", JFE.**
- **Data:** NYSE loan crowd, 1926 to 1933.
- **Numbers:** Expensive-to-short stocks have high valuations and low subsequent returns, about 1 to 2%/month even after fees [verify].

**Cohen, Diether & Malloy (2007), "Supply and demand shifts in the shorting market", JF.**
- **Numbers:** Increases in shorting demand (identified from fee and quantity shifts) predict about -2 to -3% abnormal returns the next month [verify].

**Diether, Malloy & Scherbina (2002), "Differences of opinion and the cross section of stock returns", JF.**
- **Numbers:** High analyst-forecast dispersion stocks earn lower returns, about 9.5%/year lower in some sorts [verify]. The effect is concentrated in small, past-loser stocks.

**Drechsler & Drechsler (2014), "The shorting premium and asset pricing anomalies", NBER WP 20282.**
- **Numbers:** A long-cheap / short-expensive-to-borrow portfolio earns about 1.4%/month [verify]. The relation is nonlinear and concentrated in the top fifth by fee. Many anomalies' short legs are mostly high-fee stocks.
- **Use:** The alpha in fading spikes is largely the shorting premium. The investor who can borrow collects it; Alpaca's ETB list will often exclude the best candidates.

**Beneish, Lee & Nichols (2015), "In short supply: short-sellers and stock returns", JAE.**
- **Numbers:** Lendable supply, not just demand, determines specialness. Supply-constrained ("special") stocks earn lower future returns, and the constraint explains several anomalies [verify].

**Engelberg, Reed & Ringgenberg (2018), "Short-selling risk", JF.**
- **Numbers:** Stocks with more volatile loan fees and recall risk earn lower future returns, show less short selling and weaker price efficiency [verify].
- **Use:** Even if a name is ETB today, its probability of turning HTB or recalled tomorrow is a cost. Price it in through position limits.

**Diether, Lee & Werner (2009), "Short-sale strategies and return predictability", RFS.**
- **Data:** Reg SHO daily short-sale data, 2005.
- **Numbers:** Short sellers increase shorting after positive returns and predict negative next-day/next-week returns. A daily-shorting-based strategy earned about 1.4%/month [verify].
- **Use:** Professional shorts already fade spikes, so we compete with them for borrow.

**Asquith, Pathak & Ritter (2005), JFE.**
- **Numbers:** High short interest plus low institutional ownership predicts underperformance, about 2%/month EW and insignificant VW [verify].

**Lamont (2012), "Go down fighting", RAPS.**
- **Numbers:** Firms that fight short sellers (legal threats, buy-in campaigns) subsequently underperform badly, roughly 2%/month [verify].

**Nagel (2005), JFE.**
- **Numbers:** Anomalies are strongest where institutional ownership, and hence lendable supply, is low.

**Practitioner, Dimensional (secondary).**
- **Data:** 14 lending markets, 2011 to 2018.
- **Numbers:** High-fee stocks underperform over the next several days, more so in small caps. Fee persistence is not predictable [verify].

**Regulatory overlay.**
- **Reg SHO Rule 201 (SSR):** a 10% intraday drop from the prior close triggers the alternative uptick rule for the rest of that day and the next. Short sales must then be priced above the national best bid. This reduces fill quality on fade-day shorts once the fade is under way.
- **Rule 203:** locate requirement; threshold securities lists for persistent fails.

### 2.8 Transaction costs and anomaly replication

**Novy-Marx & Velikov (2016), "A taxonomy of anomalies and their trading costs", RFS.**
- **Data:** 23 anomalies, 1963 to 2013, with effective-spread cost estimates.
- **Numbers:** Strategies with turnover under about 50%/month mostly survive costs. **High-turnover strategies, short-term reversal included, are net-negative** [verify]. Buy/hold spread and banding rules cut costs materially. Micro-cap effective spreads run several percent round trip [verify].

**Chen & Velikov (2023), "Zeroing in on the expected returns of anomalies", JFQA.**
- **Numbers:** 204 anomalies. After effective-spread costs and post-publication decay, the **average anomaly nets about 4 bps/month**, the best about 10 bps after data-mining adjustment, and combinations about 20 bps [verify, abstract]. Price impact is excluded, so true net returns are lower.

**Frazzini, Israel & Moskowitz (2018), "Trading costs", SSRN (AQR).**
- **Data:** $1.7 trillion of live AQR executions, 21 markets, 19 years.
- **Numbers:** Real institutional costs are about an order of magnitude below academic estimates for patient, algorithmic trading of liquid stocks [verify].
- **Limits:** Developed-market, mostly large and mid caps. **Does not transfer to retail marketable orders in $1 to $10 spike names at the open.**

**Hou, Xue & Zhang (2020), "Replicating anomalies", RFS.**
- **Numbers:** Of 452 anomalies, about 65% fail at |t| >= 1.96 and about 82% at |t| >= 3 once micro-caps are controlled (NYSE breakpoints, VW). The "trading frictions" category, which includes short-term reversal, MAX and IVOL, fails at the highest rate (about 96%) [verify].
- **Use:** Any penny edge we find is by construction an EW micro-cap edge, the kind least likely to replicate. Demand large per-trade margins and out-of-sample confirmation.

**Spread estimation for backtests.** Corwin & Schultz (2012, JF) give a spread estimator from daily highs and lows. With Alpaca SIP quotes, compute actual NBBO spreads at 15:59 and at 9:30 to 9:31.

**Return-measurement biases.**
- Blume & Stambaugh (1983, JFE): bid-ask bounce in daily-rebalanced EW portfolios inflates returns of low-priced stocks and roughly halves the size effect once corrected [verify].
- Shumway (1997, JF) and Shumway & Warther (1999, JF): missing delisting returns. Performance delists average about -30% (NYSE/AMEX) and about -55% (NASDAQ) [verify]. Omitting them overstates penny long returns.

### 2.9 Dilution, reverse splits and delisting

**Billett, Floros & Garfinkel (2019), "At-the-market offerings", JFQA.**
- **Numbers:** ATM programs are used mostly by small, volatile firms. Issuers sell into price run-ups and high liquidity, and ATM firms underperform [verify].
- **Use:** Spikes on volume are when shelf-registered micro-caps sell stock. This is a reason the fade exists and a reason overnight longs face after-close offering announcements.

**Hillion & Vermaelen (2004), "Death spiral convertibles", JFE.**
- **Numbers:** Floating-price convertible issuers lose about a third of their value (abnormal) over the next year [verify]. Short selling by holders pressures prices.
- **Use:** Toxic financing is endemic below $5.

**Kim, Klein & Rosenfeld (2008), "Return performance surrounding reverse stock splits: can investors profit?", Financial Management.**
- **Numbers:** Significant negative long-run abnormal returns after reverse splits. Short strategies are profitable on paper but limited by borrow [verify].
- **Use:** Exclude a 10-day window after reverse splits from long setups, and check for fake "gainers" on split dates in raw bars.

### 2.10 Institutional and practitioner sources (secondary; [verify] all)

- **AQR (Frazzini, Israel & Moskowitz).** Costs are lower than academics think, but only for patient liquidity provision in liquid names. Retail marketable orders in micro-caps are the opposite case.
- **Alpha Architect.** Summaries of Lou-Polk-Skouras (overnight momentum) and Medhat-Schmeling (short-term momentum). Both stress that the effects are strongest in large, liquid stocks.
- **Quantpedia.** Lists "short-term reversal" and "overnight anomaly" strategies. Its own notes say reversal profits are concentrated in small caps and largely consumed by costs.
- **CXO Advisory.** Summaries of Berkman et al. and the Zarattini ORB papers, including the ORB filters used above.
- **CFA Institute Digest** (Birru & Wang summary) and **ETF.com / Swedroe** ("Low priced stocks no bargain"; "Beware of the low price illusion"). These reinforce that low-priced stocks underperform after costs.
- **Dimensional.** Securities-lending fees predict short-term underperformance, especially in small caps.
- **SEC / FINRA.**
  - Penny-stock rules (Exchange Act Rule 15g-9 and related; mainly OTC).
  - Reg SHO (Rules 201, 203, 204).
  - LULD Plan.
  - FINRA RN 22-25 on ramp-and-dumps.
  - SEC investor alerts on social-media pump-and-dumps and on reverse-split/dilution patterns.

I found **no peer-reviewed study specific to listed $1 to $10 top-gainers after 2016** that splits overnight and intraday returns net of costs. The owner's own 2016 to 2026 data is the primary evidence for that exact setup, and the literature above supplies priors and mechanisms.

---

## 3. Synthesis (a): which LONG effects in $1 to $10 listed stocks might survive costs

Cost hurdle: in $1 to $10 names on spike days, a realistic all-in round trip is about **0.6 to 2.0%**:
- half-spread at entry plus half-spread at exit, typically 0.2 to 1.0% each;
- impact and slippage on top;
- commission is zero on Alpaca.

Auction (open/close) execution avoids the visible spread but not impact. Use the 15:59 and 9:30 NBBO in the backtest.

| Candidate | Prior from literature | Expected gross | Survives costs? | Verdict |
|---|---|---|---|---|
| **Overnight leg of spikes** (buy close day 0, sell at or before the open day 1) | Strong mechanism: Berkman et al. 2012; Lou-Polk-Skouras 2019; Aboody et al. 2018; Seasholes-Wu 2007; Barber-Odean 2008. Our data shows it. | Large-cap literature: 0.2 to 0.6% per night [verify]. Penny spikes plausibly several %. | Only if the owner's measured mean exceeds about 1.5% per trade after midpoint correction. Lou-Polk-Skouras show about 45% shrinkage once the opening spread is removed **[FT]**. The left tail is after-close offering announcements and halts. | **Top priority to test.** Most likely real long edge. |
| **Day-2 continuation after news-driven gaps** | Chan 2003; Savor 2012; Da-Engelberg-Gao 2011. Information-driven moves drift; no-news moves reverse. | Modest, months-long drift in large caps. In pennies, unknown and possibly swamped by dilution (offerings after news). | Uncertain. Hold 1 to 5 days so costs are paid once. | **Test, second priority**, conditioned on a news flag and the day-1 close location. |
| **Short-term reversal (buy big losers)** | Gross effect real (Jegadeesh; Lehmann), but cost- and bounce-dominated in micro-caps (Avramov-Chordia-Goyal; Novy-Marx-Velikov; Park; Cox-Peterson; Ball-Kothari-Shanken). Survives net only in large caps (de Groot-Huij-Zhou). | 1 to 2%/month gross EW. | **No** in $1 to $10, except perhaps no-news residual losers at $5+ with limit-order entry. | **Low priority.** Run as a control. |
| **Opening-range breakout (stocks in play)** | One practitioner paper (Sharpe 2.81, > $5, commissions only) [verify]. Replication of the index variant goes to zero net. Intraday momentum evidence is market-level (Gao et al.) or tiny (Heston-Korajczyk-Sadka). | 0.2 to 0.5% per trade [verify]. | Marginal. Very sensitive to stop fills and spread. | **Test, third priority**, with honest fills. Long-only and long-short variants; $5 to $10 first, then $2 to $5. |
| **Low-float momentum** | No academic support. Theory (Hong-Scheinkman-Xiong) and evidence on pumps, MAX and lottery stocks predict overpricing and then a crash. | Fat right tail intraday, negative mean over days. | Unlikely, except as a minutes-scale intraday scalp. | **Lowest priority.** Expect it to fail. |

Lottery evidence (Bali-Cakici-Whitelaw, Kumar, Boyer-Mitton-Vorkink, Barber-Huang-Odean-Schwarz) means **every long should be flat within 1 to 5 sessions**. Expected returns turn negative over weeks.

## 4. Synthesis (b): the short side in penny stocks

1. **Alpha is there.** Spikes are max-MAX, high-attention, often supply-constrained stocks. Every strand predicts negative returns over days to a month:
   - MAX and lottery;
   - Robinhood herding: -4.7% over 20 days [verify];
   - shorting premium: about 1.4%/month [verify];
   - dilution into strength.

   The day-1 intraday fade also matches Berkman et al. and Lou-Polk-Skouras.
2. **Borrow is the binding constraint.**
   - D'Avolio and Beneish-Lee-Nichols: specials cluster in small, low-IO glamour names.
   - On Alpaca, ETB names cost nothing to borrow, but most sub-$5 spike names will not be ETB.
   - HTB locates are now offered, but fees in spike names can reach hundreds of percent annualized. Intraday-only shorts still pay the locate fee.
   - The ETB flag can flip overnight, which cancels the order.
   - Recall risk (Engelberg-Reed-Ringgenberg) is concentrated in squeeze candidates.
3. **Squeeze risk is the real cost.**
   - Low float plus social media plus high short interest (Pedersen 2022; meme episodes 2021 to 2024) produces 100 to 500%+ moves.
   - LULD pauses and halts can prevent stop execution, so a stop is not a cap.
   - SSR (Rule 201) delays entries after a 10% drop.
4. **Margin.** Overnight sub-$5 shorts need max($2.50/share, 100%) maintenance. A $2 short ties up $2.50 per share, so capital efficiency is poor. Keep penny shorts **intraday** (flat by 15:55) unless the price is >= $5.
5. **Does the fade survive?**
   - In ETB names >= $5 with high volume: plausibly yes. It matches the existing bot's backtest.
   - Below $5: mostly untradable on Alpaca. When tradable via HTB, likely negative after locate fees and squeeze tails.
   - The backtest must proxy historical shortability; see Section 1.

## 5. Synthesis (c): rule sets to backtest, ranked by strength of evidence

Common settings for all rule sets:

**Universe**
- Listed common stocks only: NASDAQ, NYSE, NYSE American.
- Exclude ETFs/ETNs, ADRs (optional flag), SPACs/units/warrants/rights (symbols ending in W, U, R or with "." classes).
- Exclude:
  - stocks with a reverse split in the prior 10 sessions;
  - IPOs under 60 sessions old (separate flag for small IPOs under $25M);
  - names halted in the last 60 minutes;
  - names on Reg SHO threshold lists (if obtainable).

**Data handling**
- Split-adjusted bars for signals.
- Raw bars plus quotes for fills.
- Include delisted symbols. Apply a delisting return of -30% (NYSE/AMEX) or -55% (NASDAQ) when a position's stock delists [verify] (Shumway).

**Fills**
- Use NBBO from Alpaca historical quotes:
  - buy at ask, sell at bid for marketable orders;
  - for MOC/MOO, use the auction print plus a stress add-on.
- Report results at 0, 25, 50, 100 and 150 bps additional round-trip slippage.
- Cap participation at 1% of the session's dollar volume and 10% of the entry minute's volume.

**Regime splits:** 2016 to 2019, 2020 to 2021 (retail mania), 2022 to 2026. Also by price band: $1 to $2, $2 to $5, $5 to $10.

**Statistics**
- Bootstrap by day.
- Report mean, median, 5th/1st percentile per trade, hit rate, worst day, and the skew of per-trade P&L.
- Deflate Sharpe for the number of variants tried.

**Sizing templates**

| | $10k account | $100k account |
|---|---|---|
| Risk per trade (stop distance x shares) | 0.75% = $75 | 0.5% = $500 |
| Max position notional | 20% = $2,000 | 7.5% = $7,500, and <= 1% of the name's 20-day average dollar volume |
| Max concurrent names | 4 | 8 |
| Overnight gross long cap | 50% of equity | 30% of equity |
| Overnight short | none below $5; ETB >= $5 only, <= 10% of equity per name | same, <= 5% per name |
| Daily loss stop | -2% equity, then flat for the day | -1.5% |
| Weekly loss stop | -5%, then halve size next week | -4% |
| Leverage | <= 1x overnight; intraday <= 2x (post-June 2026 intraday margin; PDT applied before) | same |

### Rule set 1: overnight long of attention spikes ("Night Owl"). Evidence: strongest

- **Signal (day 0, 15:50 ET):**
  - price $1 to $10;
  - return from prior close >= +20% (variants: +15%, +30%, +50%), or rank in the top 10 gainers;
  - day-0 dollar volume >= $5M ($10k account) or >= $20M ($100k);
  - RVOL (day volume / 20-day average) >= 3;
  - close in the top 30% of the day's range (variant: any).
- **Filters:**
  - exclude if an 8-K, S-1/S-3/424B or "offering" headline appeared on day 0 (Alpaca news), since offerings often price after the close;
  - exclude if day 0 was a SPAC merger or IPO day;
  - exclude names halted on day 0 (variant: allow).
- **Entry:** limit-on-close / `cls` at the 15:59 NBBO ask plus 0.5% cap. Variant: buy at 15:55 with a marketable limit.
- **Exit variants:**
  - (A) `opg` market-on-open;
  - (B) premarket limit at 08:00 to 09:25, sell at bid;
  - (C) 09:31 to 09:35 bid;
  - (D) hold through day 1 and exit at the close (control; should be worse given the intraday fade).
- **Risk:** no stop overnight (gaps). Size so a -25% gap costs <= 1% of equity, which gives position <= 4% of equity at a 25% gap assumption. Also report the empirical 1st-percentile gap.
- **Expected:** must show a mean of at least 1.5% per trade net under (A) or (B) with the midpoint-corrected open.
- **Combine with the short:** if ETB and >= $5, after exit (A) or (C) roll into the existing intraday fade short. The combined test is "long close-to-open + short open-to-close" on the same name.

### Rule set 2: intraday fade short of spikes, ETB only ("existing bot, hardened"). Evidence: strong on alpha, constrained by borrow

- **Signal:** names that met Rule 1 on day 0 (or gap >= +15% at the day-1 open).
- **Shortability:** shortable proxy (price >= $5, market cap >= $300M at day 0) in the backtest; live `borrow_status == easy_to_borrow`.
- **Entry:**
  - after the first 5-minute bar, short on a break below the 5-minute opening-range low or a VWAP rejection;
  - never in the first 2 minutes;
  - skip if the stock is already down 10% from the prior close (SSR active).
- **Stop:** max(day high so far, entry + 1.5 x ATR5min); hard stop at entry +8%.
- **Exit:** cover at 15:55 or at a -12% target (variant: trail 50% of open profit).
- **Risk:** <= 0.5% equity risk per trade, max 3 shorts, no overnight shorts below $5. Kill switch if any position is up +25% against us (a halt-through gap).

### Rule set 3: news-gap day-2 continuation long ("Follow the News"). Evidence: moderate (large-cap literature)

- **Signal:**
  - day-1 gap >= +10% at the open with a company-specific news item between the prior 16:00 and 09:30 (Benzinga via Alpaca news; exclude offering and "pricing" headlines);
  - day-1 close >= day-1 open and in the top 25% of the range;
  - day-1 RVOL >= 5;
  - price $3 to $10.
- **Entry:** day-1 close (`cls`) or day-2 09:45 if day 2 holds above the day-1 VWAP.
- **Exit:** close of day 2, 3 or 5 (three variants). Stop at a day-1 VWAP breach on a closing basis.
- **Control group:** same filters with **no news**. Literature predicts no-news names reverse, which makes them a Rule-2 candidate.
- **Risk:** <= 0.75% equity risk, max 3 names.

### Rule set 4: stocks-in-play ORB ("ORB-5"). Evidence: moderate-weak (single practitioner paper, gross-only replication)

- **Universe each morning:**
  - open $2 to $10 (run $5 to $10 separately to match the paper);
  - 14-day average volume >= 1M shares;
  - ATR14 / price >= 5% (a percentage replacement for "ATR > $0.50");
  - opening 5-minute volume / 14-day average of opening 5-minute volume >= 1.0;
  - take the top 20 by that ratio.
- **Entry:** if the first 5-minute bar closes up, buy-stop at the range high + $0.01; if down, short-sell-stop at the range low (only if ETB; otherwise long-only).
- **Stop:** 10% of ATR14 (paper). Variant: 25% of ATR14 and the opposite side of the range.
- **Exit:** 15:59.
- **Sizing:** risk 1% (paper) and 0.5% (ours); leverage <= 4x intraday after June 2026, <= 2x before.
- **Fill realism:** stop orders trigger on the 1-minute high/low and fill at the next minute's open plus half-spread. This is the critical difference from the paper.

### Rule set 5: no-news residual reversal long ("Bounce"). Evidence: weak in micro-caps, included as a control

- **Signal:**
  - day-0 return <= -15%;
  - no news headline;
  - price $5 to $10 at the close (a $2 to $5 variant expected to fail);
  - day-0 dollar volume >= $5M;
  - residual return (versus a sector ETF and IWM beta) in the bottom 5% of the cross-section.
- **Entry:** limit at the 15:59 bid (passive) or `cls`.
- **Exit:** close of day 1, 3 or 5.
- **Expected:** gross positive, net near zero. Useful for validating the cost model, since it should replicate the literature's cost-killed result.

### Rule set 6: low-float intraday momentum ("Float Rocket"). Evidence: none or negative; run to bound expectations

- **Signal:**
  - float < 10M shares (requires an external float source; if unavailable, use shares outstanding < 20M);
  - price $1 to $10;
  - premarket gap >= +20%;
  - premarket volume >= 1M shares.
- **Entry:** break of the premarket high after 09:31.
- **Stop:** -5% or a VWAP loss.
- **Exit:** +10% target or 10:30 time stop. Never hold overnight.
- **Risk:** 0.5% equity per trade, max 2 per day.
- **Expected:** a positive hit rate in 2020 to 2021, negative after costs elsewhere. Do not deploy unless out-of-sample 2022 to 2026 is positive net of 100 bps.

## 6. Synthesis (d): risks and mitigations

- **Liquidity and spreads.** Spreads in $1 to $5 names often exceed 0.5 to 1% outside the spike window, and depth vanishes at the open, close and around halts.
  - Mitigation: cap participation at 1% of dollar volume and 10% of minute volume.
  - Prefer auctions for the overnight leg.
  - Never use market orders in extended hours (Alpaca only allows limits there anyway).
- **LULD halts.** Tier 2 bands are:
  - 10% for > $3;
  - 20% for $0.75 to $3;
  - lesser of $0.15 or 75% below $0.75;
  - doubled in the 09:30 to 09:45 window and, for Tier 2, the last 25 minutes [verify].

  Breaches cause 5-minute pauses (extendable), and there are news-pending (T1) halts and SEC 10-day suspensions. Stops cannot execute during halts, so the reopening gap is the real loss.
  - Mitigation: size on gap assumptions, not stop distance.
  - Exclude names with 2+ LULD pauses on the signal day from overnight holds (variant).
- **Reverse splits.** Raw-price "gainers" on split dates are artifacts, and post-split drift is negative (Kim-Klein-Rosenfeld).
  - Mitigation: use split-adjusted data for signals and exclude a 10-day post-split window.
  - Nasdaq now bars new compliance periods for firms that reverse-split in the prior year, which raises delisting risk.
- **Delistings.** Nasdaq's $1 bid rule (30 days below $1 brings a notice; compliance windows are now capped) and the proposed sub-$0.10 accelerated suspension [verify status].
  - Mitigation: exclude names below $1 and names with an active deficiency notice. Apply Shumway delisting returns in backtests.
- **Dilution, ATMs, registered directs, warrants, toxic converts.** Micro-caps sell stock into spikes (Billett-Floros-Garfinkel; Hillion-Vermaelen).
  - This is the main left-tail risk for Rule 1, through after-close "pricing of offering" releases and gap-downs of 20 to 40%.
  - Mitigation: news filter for S-3/424B/"offering"/"warrants"; prefer names without an effective shelf; cap overnight size by a gap assumption.
- **Pump-and-dump exposure.** Many spikes are promotions or ramp-and-dumps (Leuz et al.; Aggarwal-Wu; FINRA RN 22-25).
  - Long side: collapse risk overnight.
  - Short side: squeeze before the dump, HTB, and halts.
  - Mitigation:
    - exclude small recent IPOs (< 6 months, < $25M raised);
    - exclude foreign small caps with float < 5M;
    - exclude names with SEC suspensions in their history;
    - exclude names with no fundamental news but social-media volume spikes.
- **Short-specific.**
  - ETB to HTB flips cancel orders.
  - Recalls and buy-ins.
  - SSR after -10%.
  - Sub-$5 overnight maintenance of max($2.50/share, 100%).
  - Unbounded squeeze losses.
  - Mitigation: intraday-only below $5, a kill switch, max 3 concurrent shorts, never short a name halted up that day.
- **PDT and margin.**
  - Historical (2016 to 2026-06-03): margin accounts under $25k were limited to 3 day trades per 5 business days. Rule sets 2, 4 and 6 are day trades, so a $10k backtest must either cap at 3 per 5 days or assume a cash account (no shorts; T+2 to T+1 settlement limits recycling).
  - Since 2026-06-04: PDT is gone and intraday margin applies on Alpaca. Beware intraday margin calls if positions exceed intraday buying power. Repeated failures bring up to a 90-day restriction.
  - Rule set 1 (overnight hold) is not a day trade, so it was PDT-free even before.
- **Regulatory and venue risk.**
  - Exchange rule changes on reverse splits, low prices and small IPOs (2021 to 2025).
  - Possible SEC action on payment for order flow and retail routing (affects open/close dynamics).
  - Short-sale disclosure rules (SEC Rule 13f-2 / Form SHO, compliance phased from 2025 to 2026 [verify]).
  - Alpaca's own house limits, which can tighten for volatile low-priced names without notice.
- **Model risk.** Chen-Velikov and Hou-Xue-Zhang: expect live returns well below the backtest. Hold out 2024 to 2026 as true out-of-sample, and paper-trade at least 3 months before going live.

## 7. Backtest methodology checklist (for the parallel agent)

1. Use **NBBO quotes** (Alpaca historical quotes) at 15:59:00 to 16:00:00 and 09:30:00 to 09:31:00. Do not use last-trade prints (Blume-Stambaugh, Bhardwaj-Brooks, Park, Lou-Polk-Skouras midquote result).
2. Measure the overnight leg three ways:
   - close-print to open-print;
   - close-mid to open-mid;
   - executable (close ask to open bid).

   Report all three. Expect the third to be much lower.
3. Use split-adjusted signals and raw executable prices. Flag reverse-split days.
4. Include delisted tickers and apply delisting returns.
5. Shortability: you cannot observe historical ETB. Use the proxy, report results with and without sub-$5 shorts, and start logging ETB status live now.
6. Apply PDT constraints before 2026-06-04 for sub-$25k margin simulations.
7. Split by regime and price band. Multiple-testing haircut (Harvey-Liu-Zhu style t > 3).
8. Report tails: 1st-percentile trade, worst day, max drawdown, and the longest losing streak.

---

## 8. References (full citations, DOIs, access links)

### Low price, lottery, skewness

Bali, T. G., Cakici, N., & Whitelaw, R. F. (2011). Maxing out: Stocks as lotteries and the cross-section of expected returns. *Journal of Financial Economics, 99*(2), 427–446. https://doi.org/10.1016/j.jfineco.2010.08.014
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19074358/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Kumar, A. (2009). Who gambles in the stock market? *Journal of Finance, 64*(4), 1889–1933. https://doi.org/10.1111/j.1540-6261.2009.01483.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955606/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Barberis, N., & Huang, M. (2008). Stocks as lotteries: The implications of probability weighting for security prices. *American Economic Review, 98*(5), 2066–2100. https://doi.org/10.1257/aer.98.5.2066
📄 **[Download PDF](https://libkey.io/libraries/545/articles/47561002/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Boyer, B., Mitton, T., & Vorkink, K. (2010). Expected idiosyncratic skewness. *Review of Financial Studies, 23*(1), 169–202. https://doi.org/10.1093/rfs/hhp041
📄 **[Download PDF](https://libkey.io/libraries/545/articles/27131057/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Birru, J., & Wang, B. (2016). Nominal price illusion. *Journal of Financial Economics, 119*(3), 578–598. https://doi.org/10.1016/j.jfineco.2016.01.027
📄 **[Download PDF](https://libkey.io/libraries/545/articles/57819285/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Fernando, C. S., Krishnamurthy, S., & Spindt, P. A. (2004). Are share price levels informative? Evidence from the ownership, pricing, turnover and performance of IPO firms. *Journal of Financial Markets, 7*(4), 377–403. https://doi.org/10.1016/j.finmar.2004.01.001
📄 **[Download PDF](https://libkey.io/libraries/545/articles/20241390/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Hwang, S., & Lu, C. (2009). Is share price relevant? SSRN Working Paper. https://doi.org/10.2139/ssrn.1341790. Not LibKey-resolved.

Bhardwaj, R. K., & Brooks, L. D. (1992). The January anomaly: Effects of low share price, transaction costs, and bid-ask bias. *Journal of Finance, 47*(2), 553–575. https://doi.org/10.1111/j.1540-6261.1992.tb04401.x
🔗 **[Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=BHARDWAJ&issn=0022-1082&title=The%20Journal%20of%20Finance&atitle=The%20January%20Anomaly%3A%20Effects%20of%20Low%20Share%20Price%2C%20Transaction%20Costs%2C%20and%20Bid%E2%80%90Ask%20Bias&volume=47&issue=2&spage=553&epage=575&date=1992-06-01&rft_id=info:doi/10.1111%2Fj.1540-6261.1992.tb04401.x&rft_id=info:pmid/&sid=LibKey)**

Eraker, B., & Ready, M. (2015). Do investors overpay for stocks with lottery-like payoffs? An examination of the returns of OTC stocks. *Journal of Financial Economics, 115*(3), 486–504. https://doi.org/10.1016/j.jfineco.2014.11.002
📄 **[Download PDF](https://libkey.io/libraries/545/articles/53403920/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Reversal and short-term momentum

Jegadeesh, N. (1990). Evidence of predictable behavior of security returns. *Journal of Finance, 45*(3), 881–898. https://doi.org/10.1111/j.1540-6261.1990.tb05110.x
🔗 **[Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=JEGADEESH&issn=0022-1082&title=The%20Journal%20of%20Finance&atitle=Evidence%20of%20Predictable%20Behavior%20of%20Security%20Returns&volume=45&issue=3&spage=881&epage=898&date=1990-07-01&rft_id=info:doi/10.1111%2Fj.1540-6261.1990.tb05110.x&rft_id=info:pmid/&sid=LibKey)**

Lehmann, B. N. (1990). Fads, martingales, and market efficiency. *Quarterly Journal of Economics, 105*(1), 1–28. https://doi.org/10.2307/2937816
🔗 **[Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Lehmann&issn=0033-5533&title=The%20Quarterly%20Journal%20of%20Economics&atitle=Fads%2C%20Martingales%2C%20and%20Market%20Efficiency&volume=105&issue=1&spage=1&epage=&date=1990-02-01&rft_id=info:doi/10.2307%2F2937816&rft_id=info:pmid/&sid=LibKey)**

Avramov, D., Chordia, T., & Goyal, A. (2006). Liquidity and autocorrelations in individual stock returns. *Journal of Finance, 61*(5), 2365–2394. https://doi.org/10.1111/j.1540-6261.2006.01060.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955294/full-text-file?utm_source=api_4212&allow_speedbump=true)**

de Groot, W., Huij, J., & Zhou, W. (2012). Another look at trading costs and short-term reversal profits. *Journal of Banking & Finance, 36*(2), 371–382. https://doi.org/10.1016/j.jbankfin.2011.07.015
📄 **[Download PDF](https://libkey.io/libraries/545/articles/12500026/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Da, Z., Liu, Q., & Schaumburg, E. (2014). A closer look at the short-term return reversal. *Management Science, 60*(3), 658–674. https://doi.org/10.1287/mnsc.2013.1766
📄 **[Download PDF](https://libkey.io/libraries/545/articles/48284416/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Medhat, M., & Schmeling, M. (2022). Short-term momentum. *Review of Financial Studies, 35*(3), 1480–1526. https://doi.org/10.1093/rfs/hhab055
📄 **[Download PDF](https://libkey.io/libraries/545/articles/476473781/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Blitz, D., Huij, J., Lansdorp, S., & Verbeek, M. (2013). Short-term residual reversal. *Journal of Financial Markets, 16*(3), 477–504. https://doi.org/10.1016/j.finmar.2012.10.005
📄 **[Download PDF](https://libkey.io/libraries/545/articles/32560944/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Park, J. (1995). A market microstructure explanation for predictable variations in stock returns following large price changes. *Journal of Financial and Quantitative Analysis, 30*(2), 241–256. https://doi.org/10.2307/2331119
🔗 **[Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Park&issn=0022-1090&title=Journal%20of%20Financial%20and%20Quantitative%20Analysis&atitle=A%20Market%20Microstructure%20Explanation%20for%20Predictable%20Variations%20in%20Stock%20Returns%20following%20Large%20Price%20Changes&volume=30&issue=2&spage=241&epage=&date=1995-06-01&rft_id=info:doi/10.2307%2F2331119&rft_id=info:pmid/&sid=LibKey)**

Cox, D. R., & Peterson, D. R. (1994). Stock returns following large one-day declines: Evidence on short-term reversals and longer-term performance. *Journal of Finance, 49*(1), 255–267. https://doi.org/10.1111/j.1540-6261.1994.tb04428.x
🔗 **[Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=COX&issn=0022-1082&title=The%20Journal%20of%20Finance&atitle=Stock%20Returns%20following%20Large%20One%E2%80%90Day%20Declines%3A%20Evidence%20on%20Short%E2%80%90Term%20Reversals%20and%20Longer%E2%80%90Term%20Performance&volume=49&issue=1&spage=255&epage=267&date=1994-03-01&rft_id=info:doi/10.1111%2Fj.1540-6261.1994.tb04428.x&rft_id=info:pmid/&sid=LibKey)**

Ball, R., Kothari, S. P., & Shanken, J. (1995). Problems in measuring portfolio performance: An application to contrarian investment strategies. *Journal of Financial Economics, 38*(1), 79–107. https://doi.org/10.1016/0304-405X(94)00806-C
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19072611/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Overnight vs intraday

Lou, D., Polk, C., & Skouras, S. (2019). A tug of war: Overnight versus intraday expected returns. *Journal of Financial Economics, 134*(1), 192–213. https://doi.org/10.1016/j.jfineco.2019.03.011
📄 **[Download PDF](https://libkey.io/libraries/545/articles/302597070/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Berkman, H., Koch, P. D., Tuttle, L., & Zhang, Y. J. (2012). Paying attention: Overnight returns and the hidden cost of buying at the open. *Journal of Financial and Quantitative Analysis, 47*(4), 715–741. https://doi.org/10.1017/S0022109012000270
📄 **[Download PDF](https://libkey.io/libraries/545/articles/40999813/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Aboody, D., Even-Tov, O., Lehavy, R., & Trueman, B. (2018). Overnight returns and firm-specific investor sentiment. *Journal of Financial and Quantitative Analysis, 53*(2), 485–505. https://doi.org/10.1017/S0022109017000989
📄 **[Download PDF](https://libkey.io/libraries/545/articles/190205403/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Bogousslavsky, V. (2021). The cross-section of intraday and overnight returns. *Journal of Financial Economics, 141*(1), 172–194. https://doi.org/10.1016/j.jfineco.2020.07.020
📄 **[Download PDF](https://libkey.io/libraries/545/articles/446716443/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Hendershott, T., Livdan, D., & Rösch, D. (2020). Asset pricing: A tale of night and day. *Journal of Financial Economics, 138*(3), 635–662. https://doi.org/10.1016/j.jfineco.2020.06.006
📄 **[Download PDF](https://libkey.io/libraries/545/articles/394325926/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Boyarchenko, N., Larsen, L. C., & Whelan, P. (2023). The overnight drift. *Review of Financial Studies, 36*(9), 3502–3547. https://doi.org/10.1093/rfs/hhad020
📄 **[Download PDF](https://libkey.io/libraries/545/articles/562895275/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Retail attention, herding, meme stocks

Barber, B. M., & Odean, T. (2008). All that glitters: The effect of attention and news on the buying behavior of individual and institutional investors. *Review of Financial Studies, 21*(2), 785–818. https://doi.org/10.1093/rfs/hhm079
📄 **[Download PDF](https://libkey.io/libraries/545/articles/27130936/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Barber, B. M., Huang, X., Odean, T., & Schwarz, C. (2022). Attention-induced trading and returns: Evidence from Robinhood users. *Journal of Finance, 77*(6), 3141–3190. https://doi.org/10.1111/jofi.13183
📄 **[Download PDF](https://libkey.io/libraries/545/articles/535675100/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Welch, I. (2022). The wisdom of the Robinhood crowd. *Journal of Finance, 77*(3), 1489–1527. https://doi.org/10.1111/jofi.13128
📄 **[Download PDF](https://libkey.io/libraries/545/articles/521691874/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Ozik, G., Sadka, R., & Shen, S. (2021). Flattening the illiquidity curve: Retail trading during the COVID-19 lockdown. *Journal of Financial and Quantitative Analysis, 56*(7), 2356–2388. https://doi.org/10.1017/S0022109021000387
📄 **[Download PDF](https://libkey.io/libraries/545/articles/486340155/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Boehmer, E., Jones, C. M., Zhang, X., & Zhang, X. (2021). Tracking retail investor activity. *Journal of Finance, 76*(5), 2249–2305. https://doi.org/10.1111/jofi.13033
📄 **[Download PDF](https://libkey.io/libraries/545/articles/468087967/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Eaton, G. W., Green, T. C., Roseman, B. S., & Wu, Y. (2022). Retail trader sophistication and stock market quality: Evidence from brokerage outages. *Journal of Financial Economics, 146*(2), 502–528. https://doi.org/10.1016/j.jfineco.2022.08.002
📄 **[Download PDF](https://libkey.io/libraries/545/articles/533664640/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Pedersen, L. H. (2022). Game on: Social networks and markets. *Journal of Financial Economics, 146*(3), 1097–1119. https://doi.org/10.1016/j.jfineco.2022.05.002
📄 **[Download PDF](https://libkey.io/libraries/545/articles/528162513/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Da, Z., Engelberg, J., & Gao, P. (2011). In search of attention. *Journal of Finance, 66*(5), 1461–1499. https://doi.org/10.1111/j.1540-6261.2011.01679.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955833/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Seasholes, M. S., & Wu, G. (2007). Predictable behavior, profits, and attention. *Journal of Empirical Finance, 14*(5), 590–610. https://doi.org/10.1016/j.jempfin.2007.03.002
📄 **[Download PDF](https://libkey.io/libraries/545/articles/7628324/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Manipulation and pumps

Leuz, C., Meyer, S., Muhn, M., Soltes, E., & Hackethal, A. (2017, rev. 2019). Who falls prey to the Wolf of Wall Street? Investor participation in market manipulation. NBER Working Paper 24083. https://doi.org/10.3386/w24083 (https://www.nber.org/papers/w24083). Not LibKey-resolved.

Aggarwal, R. K., & Wu, G. (2006). Stock market manipulations. *Journal of Business, 79*(4), 1915–1953. https://doi.org/10.1086/503652
📄 **[Download PDF](https://libkey.io/libraries/545/articles/46899070/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Dhawan, A., & Putniņš, T. J. (2023). A new wolf in town? Pump-and-dump manipulation in cryptocurrency markets. *Review of Finance, 27*(3), 935–975. https://doi.org/10.1093/rof/rfac051
📄 **[Download PDF](https://libkey.io/libraries/545/articles/532221298/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Hamrick, J. T., Rouhi, F., Mukherjee, A., Feder, A., Gandal, N., Moore, T., & Vasek, M. (2021). An examination of the cryptocurrency pump-and-dump ecosystem. *Information Processing & Management, 58*(4), 102506. https://doi.org/10.1016/j.ipm.2021.102506
📄 **[Download PDF](https://libkey.io/libraries/545/articles/449250074/full-text-file?utm_source=api_4212&allow_speedbump=true)**

FINRA (2022). Regulatory Notice 22-25: Heightened threat of fraud in small-cap IPOs ("ramp-and-dump"). https://www.finra.org/rules-guidance/notices/22-25. Regulatory document; no DOI; not LibKey-resolved.

### Gaps, ORB, intraday momentum, news

Zarattini, C., Barbon, A., & Aziz, A. (2024). A profitable day trading strategy for the U.S. equity market. SSRN Working Paper 4729284. https://doi.org/10.2139/ssrn.4729284. Not LibKey-resolved.

Zarattini, C., & Aziz, A. (2023). Can day trading really be profitable? Evidence of sustainable long-term profits from opening range breakout (ORB) day trading strategy vs. benchmark in the US stock market. SSRN Working Paper 4416622. https://doi.org/10.2139/ssrn.4416622. Not LibKey-resolved.

Gao, L., Han, Y., Li, S. Z., & Zhou, G. (2018). Market intraday momentum. *Journal of Financial Economics, 129*(2), 394–414. https://doi.org/10.1016/j.jfineco.2018.05.009
📄 **[Download PDF](https://libkey.io/libraries/545/articles/207215657/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Heston, S. L., Korajczyk, R. A., & Sadka, R. (2010). Intraday patterns in the cross-section of stock returns. *Journal of Finance, 65*(4), 1369–1407. https://doi.org/10.1111/j.1540-6261.2010.01573.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955648/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Chan, W. S. (2003). Stock price reaction to news and no-news: Drift and reversal after headlines. *Journal of Financial Economics, 70*(2), 223–260. https://doi.org/10.1016/S0304-405X(03)00146-6
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19073564/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Savor, P. G. (2012). Stock returns after major price shocks: The impact of information. *Journal of Financial Economics, 106*(3), 635–659. https://doi.org/10.1016/j.jfineco.2012.06.011
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19074626/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Hong, H., Scheinkman, J., & Xiong, W. (2006). Asset float and speculative bubbles. *Journal of Finance, 61*(3), 1073–1117. https://doi.org/10.1111/j.1540-6261.2006.00867.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955192/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Short selling

D'Avolio, G. (2002). The market for borrowing stock. *Journal of Financial Economics, 66*(2–3), 271–306. https://doi.org/10.1016/S0304-405X(02)00206-4
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19073466/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Engelberg, J. E., Reed, A. V., & Ringgenberg, M. C. (2018). Short-selling risk. *Journal of Finance, 73*(2), 755–786. https://doi.org/10.1111/jofi.12601
📄 **[Download PDF](https://libkey.io/libraries/545/articles/167902131/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Drechsler, I., & Drechsler, Q. F. S. (2014). The shorting premium and asset pricing anomalies. NBER Working Paper 20282. https://doi.org/10.3386/w20282. Not LibKey-resolved.

Beneish, M. D., Lee, C. M. C., & Nichols, D. C. (2015). In short supply: Short-sellers and stock returns. *Journal of Accounting and Economics, 60*(2–3), 33–57. https://doi.org/10.1016/j.jacceco.2015.08.001
📄 **[Download PDF](https://libkey.io/libraries/545/articles/56203432/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Diether, K. B., Malloy, C. J., & Scherbina, A. (2002). Differences of opinion and the cross section of stock returns. *Journal of Finance, 57*(5), 2113–2141. https://doi.org/10.1111/0022-1082.00490
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29954797/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Cohen, L., Diether, K. B., & Malloy, C. J. (2007). Supply and demand shifts in the shorting market. *Journal of Finance, 62*(5), 2061–2096. https://doi.org/10.1111/j.1540-6261.2007.01269.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955391/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Jones, C. M., & Lamont, O. A. (2002). Short-sale constraints and stock returns. *Journal of Financial Economics, 66*(2–3), 207–239. https://doi.org/10.1016/S0304-405X(02)00224-6
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19073464/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Diether, K. B., Lee, K.-H., & Werner, I. M. (2009). Short-sale strategies and return predictability. *Review of Financial Studies, 22*(2), 575–607. https://doi.org/10.1093/rfs/hhn047
📄 **[Download PDF](https://libkey.io/libraries/545/articles/27130977/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Asquith, P., Pathak, P. A., & Ritter, J. R. (2005). Short interest, institutional ownership, and stock returns. *Journal of Financial Economics, 78*(2), 243–276. https://doi.org/10.1016/j.jfineco.2005.01.001
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19073795/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Lamont, O. A. (2012). Go down fighting: Short sellers vs. firms. *Review of Asset Pricing Studies, 2*(1), 1–30. https://doi.org/10.1093/rapstu/ras003
📄 **[Download PDF](https://libkey.io/libraries/545/articles/28271071/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Nagel, S. (2005). Short sales, institutional investors and the cross-section of stock returns. *Journal of Financial Economics, 78*(2), 277–309. https://doi.org/10.1016/j.jfineco.2004.08.008
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19073779/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Costs, replication, measurement

Novy-Marx, R., & Velikov, M. (2016). A taxonomy of anomalies and their trading costs. *Review of Financial Studies, 29*(1), 104–147. https://doi.org/10.1093/rfs/hhv063
📄 **[Download PDF](https://libkey.io/libraries/545/articles/57275731/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Chen, A. Y., & Velikov, M. (2023). Zeroing in on the expected returns of anomalies. *Journal of Financial and Quantitative Analysis, 58*(3), 968–1004. https://doi.org/10.1017/S0022109022000874
📄 **[Download PDF](https://libkey.io/libraries/545/articles/532668704/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Frazzini, A., Israel, R., & Moskowitz, T. J. (2018). Trading costs. SSRN Working Paper 3229719. https://doi.org/10.2139/ssrn.3229719. Not LibKey-resolved.

Hou, K., Xue, C., & Zhang, L. (2020). Replicating anomalies. *Review of Financial Studies, 33*(5), 2019–2133. https://doi.org/10.1093/rfs/hhy131
📄 **[Download PDF](https://libkey.io/libraries/545/articles/251962361/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Corwin, S. A., & Schultz, P. (2012). A simple way to estimate bid-ask spreads from daily high and low prices. *Journal of Finance, 67*(2), 719–760. https://doi.org/10.1111/j.1540-6261.2012.01729.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955842/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Blume, M. E., & Stambaugh, R. F. (1983). Biases in computed returns: An application to the size effect. *Journal of Financial Economics, 12*(3), 387–404. https://doi.org/10.1016/0304-405X(83)90056-9
🔗 **[Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Blume&issn=0304-405X&title=Journal%20of%20Financial%20Economics&atitle=Biases%20in%20computed%20returns&volume=12&issue=3&spage=387&epage=404&date=1983-11-01&rft_id=info:doi/10.1016%2F0304-405X(83)90056-9&rft_id=info:pmid/&sid=LibKey)**

Shumway, T. (1997). The delisting bias in CRSP data. *Journal of Finance, 52*(1), 327–340. https://doi.org/10.1111/j.1540-6261.1997.tb03818.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29955853/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Shumway, T., & Warther, V. A. (1999). The delisting bias in CRSP's Nasdaq data and its implications for the size effect. *Journal of Finance, 54*(6), 2361–2379. https://doi.org/10.1111/0022-1082.00192
📄 **[Download PDF](https://libkey.io/libraries/545/articles/29954586/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Dilution, splits

Billett, M. T., Floros, I. V., & Garfinkel, J. A. (2019). At-the-market offerings. *Journal of Financial and Quantitative Analysis, 54*(3), 1263–1283. https://doi.org/10.1017/S0022109018000893
📄 **[Download PDF](https://libkey.io/libraries/545/articles/231519998/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Hillion, P., & Vermaelen, T. (2004). Death spiral convertibles. *Journal of Financial Economics, 71*(2), 381–415. https://doi.org/10.1016/S0304-405X(03)00209-5
📄 **[Download PDF](https://libkey.io/libraries/545/articles/19073595/full-text-file?utm_source=api_4212&allow_speedbump=true)**

Kim, S., Klein, A., & Rosenfeld, J. (2008). Return performance surrounding reverse stock splits: Can investors profit? *Financial Management, 37*(2), 173–192. https://doi.org/10.1111/j.1755-053X.2008.00009.x
📄 **[Download PDF](https://libkey.io/libraries/545/articles/23306275/full-text-file?utm_source=api_4212&allow_speedbump=true)**

### Broker, regulatory and practitioner sources (no DOI; not LibKey-resolved)

- Alpaca, Margin and Short Selling: https://docs.alpaca.markets/us/docs/margin-and-short-selling
- Alpaca, 24/5 Trading: https://docs.alpaca.markets/us/docs/245-trading
- Alpaca, Account plans (listed US stocks only): https://docs.alpaca.markets/us/docs/account-plans
- Alpaca, "FINRA Retires the PDT Rule: Introducing Alpaca's New Intraday Margin Framework": https://alpaca.markets/blog/finra-retires-the-pdt-rule-introducing-alpacas-new-intraday-margin-framework/
- Alpaca, zero ETB borrow fees: https://alpaca.markets/blog/zero-borrow-fees-on-short-selling-etb-stock-shares-alpaca-trading-api/
- Alpaca forum, OTC requests: https://forum.alpaca.markets/t/request-for-support-for-otc-pink-equities/4018
- Schwab, "SEC approves scrapping $25,000 day trader minimum": https://international.schwab.com/story/sec-approves-scrapping-25000-day-trader-minimum
- Nasdaq, "Nasdaq proposes changes to its listing standards" (2025-09-03): https://www.nasdaq.com/press-release/nasdaq-proposes-changes-its-listing-standards-2025-09-03
- CXO Advisory, summaries of Berkman et al. (2012), Zarattini-Barbon-Aziz (2024) and Hwang & Lu: https://www.cxoadvisory.com/?p=6907 ; https://www.cxoadvisory.com/individual-investing/intraday-trading-of-overactive-stocks-via-opening-range-breakout/ ; https://cxoadvisory.com/?p=2032
- MQL5 replication of the ORB index paper: https://www.mql5.com/en/blogs/post/776235
- Dimensional, "Securities lending fees as a short-term driver of stock returns": https://www.dimensional.com/us-en/insights/securities-lending-fees-as-a-short-term-driver-of-stock-returns
- CFA Institute Digest, Nominal price illusion: https://rpc.cfainstitute.org/research/cfa-digest/2016/11/nominal-price-illusion-digest-summary
- Alpha Architect, overnight vs intraday momentum: https://alphaarchitect.com/2015/02/overnight-momentum-vs-intraday-momentum/ ; short-term momentum: https://alphaarchitect.com/short-term-momentum/
- Quantpedia, short-term reversal: https://quantpedia.com/strategies/short-term-reversal-in-stocks
