# Institutional Review of the Clive Strategy Set

Prepared 2026-10-22. This review checks every strategy the owner runs or is considering against institutional and practitioner research (AQR, Man AHL, Research Affiliates, Two Sigma, Robeco, Cboe, BIS, IMF, the Fed, SEC) and against the academic papers those firms cite. For each strategy it asks what professionals know that a retail backtest would miss.

How to read the tags:
- **[verify]** marks a number taken from a secondary source (press coverage, a blog summary, or a third-party write-up) rather than from the primary document. Check it before relying on it.
- Journal articles carry a DOI and a LibKey access link (Northeastern library). Items that could not be resolved are marked **not LibKey-resolved**.
- White papers and official reports carry their URL and publication date.
- The full reference list is in Section 9. In-text citations use author-year.

This is a research document. No code was changed and nothing was committed.

---

## 0. Executive summary

**The three most important things the institutional literature says about this book:**

1. **Every strategy here sells insurance or provides liquidity.** That includes shorting spikes, put spreads, condors, covered calls, pairs and reversal. Ilmanen (2012) finds that selling insurance and lottery tickets earns positive long-run premia, but "they tend to suffer sharp losses in bad times." All of these strategies lose money in the same states of the world: a volatility spike, a squeeze, or a liquidity crisis. The two paper bots are therefore one correlated short-convexity bet, not two diversified ones. Institutions size such a bet by its **stress loss**, not by its average P&L.
2. **Costs and capacity decide whether high-turnover edges exist at all.** Novy-Marx and Velikov (2016) find that anomalies with turnover above about 50% per month mostly fail to survive costs. Hou, Xue and Zhang (2020) find that 65% of 452 anomalies fail once microcaps are handled properly. Daily-turnover short-term strategies (the short bot, pairs, penny long/short) live in exactly the region where retail backtests are most wrong.
3. **The tail events professionals study were gap events, not trend events.** Feb 2018, Aug 2024 and Apr 2025 all hit in hours, often before the open. A spot-VIX kill switch fires after the damage. That is why the owner's switch "only helped in 2008/2020": those were slow, grinding crises. Professionals rely on **pre-sized defined risk plus a standing tail hedge**. A trigger is a backstop, not the main defense.

---

## 1. Strategy 1: Shorting top-gainer spikes (v2: short next open, cover 15:55)

### 1.1 What institutions and academics find

| Finding | Source | Numbers |
|---|---|---|
| Short-term (1-month) reversal is a real liquidity-provision premium. It survives costs only when it is done carefully. | Blitz, Huij, Lansdorp & Verbeek (2013), Robeco | Residual reversal gives risk-adjusted returns **about 2x** conventional reversal. It stays significant net of costs in **large caps post-1990**. |
| Most reversal cost drag comes from small caps. | de Groot, Huij & Zhou (2012), Robeco | Net reversal profits of **30–50 bp per week** in large caps with low-turnover construction. |
| Short-horizon anomaly profits split by time of day. Momentum is earned **overnight**; other strategies earn their abnormal returns **intraday**. | Lou, Polk & Skouras (2019) | This is a clientele effect: the investors who trade at the open differ from those who trade during the day. |
| Retail investors are net buyers of attention-grabbing stocks: big movers, high volume, in the news. | Barber & Odean (2008) | This buying pressure is what the next-day short fades. |
| Lottery-like stocks (high maximum daily return) underperform. | Bali, Cakici & Whitelaw (2011) | Stocks in the top MAX decile earn lower subsequent returns. This is the academic cousin of the "short the top gainers" premise. |
| Short sellers trade against short-term overreaction, and that trading predicts returns. | Diether, Lee & Werner (2009) | Short selling rises after positive returns and predicts negative next-period returns. |
| Short-selling risk itself is priced. Stocks with volatile loan fees earn **lower** returns and attract less shorting. | Engelberg, Reed & Ringgenberg (2018) | The risk measure is loan-fee variance. Median loan fee **11 bp**, mean **85 bp**, skewed [verify]. |
| Low-beta / defensive stocks match high-beta returns at lower risk. The "short high-beta junk" side is where the premium sits. | Frazzini, Friedman & Kim (AQR, 2012); Frazzini & Pedersen (2014) | US Defensive vs Russell 1000, 1984–2011 (hypothetical, gross): Sharpe **0.58 vs 0.38**, vol **12.5% vs 15.4%**, max DD **−35.8% vs −51.0%**, beta **0.76**. |

**This confirms the owner's own backtest.** The PROPOSAL found the overnight leg loses money (−0.49% IS / −0.14% OOS) and the open-to-15:55 leg makes money (+0.40% / +0.29%). That is the Lou-Polk-Skouras pattern: attention-driven buyers arrive overnight and at the open, and the reversal happens intraday. The v2 redesign (enter after the open, flat by the close) lines up with the best-documented version of this effect.

### 1.2 Capacity and crowding

- **Capacity is small by construction.** Novy-Marx and Velikov (2016) find that the amount of new capital a strategy can absorb before its profits fall is inversely related to its turnover. A 100%-per-day turnover strategy on $10M+ ADV small caps holds perhaps low single-digit millions before its own impact eats the edge. That smallness is one reason the edge can survive: large funds cannot harvest it.
- **Crowding sits on the other side.** Two Sigma's Venn Crowding factor (long low short interest, short high short interest) fell **4.96%** in January 2021, a **−5.7σ** event [verify], during the meme-stock squeeze (Two Sigma Venn, May 2024 factor report). Professionals treat "high short interest + retail attention" as a regime risk factor, not only as a stock filter.
- **The SEC staff report (Oct 2021)** found that GME short interest reached **122.97% of float** in January 2021 and a high of **109.26% of shares outstanding** on 31 Dec 2020. The GME intraday price rose about **2,700%**. On **27 Jan 2021 KOSS rose 480.0% in one day**, AMC 301.2% and EXPR 214.1%. Since 2020, **134 common stocks** had a one-day gain larger than GME's biggest one-day gain. The staff concluded that short covering contributed in discrete windows, but that "positive sentiment, not the buying-to-cover" sustained the rise. **Squeezes are not only a short-interest phenomenon.** A low-SI name can still go +300% on sentiment. The owner's SI/DTC exclusion filter is necessary but not sufficient.

### 1.3 How institutions manage this risk

- **Per-name stress sizing.** Long/short desks size a short so that a defined adverse move (commonly +100% for small caps, more for sub-$10 names) costs no more than a fixed budget, typically **0.5–2% of NAV per name**. At a 10% weight, a +100% move costs 10% of NAV, which is far outside institutional limits. The old 19% weight meant one KOSS day was **−98.7%** of equity.
- **Liquidity limits.** Position ≤ **1–5% of 20-day ADV**, and exit-ability ≤ 1 day at 20% participation.
- **Borrow governance.** Locates are confirmed before trading, recall risk is monitored, and names whose loan fee is rising or volatile are skipped. That is the Engelberg-Reed-Ringgenberg variable. Alpaca's `easy_to_borrow` flag is a coarse stand-in for it.
- **Hard intraday stops on squeezes.** These are not daily-bar stops; the PROPOSAL correctly found that daily-bar stops hurt. Desks use real-time buy-stops, often staged: cover a third at +X%, the rest at +2X%. They accept that halts (LULD) can gap through the stop.
- **Option tail hedges on single names** where listed options exist: cheap far-OTM calls sized to cap the loss above a strike. This is standard on concentrated shorts. It is rarely economic on illiquid small caps, which is itself a reason to keep per-name weights low.
- **Factor and crowding monitors.** The book's exposure to retail-attention, short-interest and momentum-reversal factors is tracked. Gross is cut when the crowding factor's volatility spikes.

### 1.4 Failure episodes to study

- **Jan 2021 (GME/KOSS/AMC).** Squeezes plus broker restrictions, with NSCC margin calls driving trading limits. A reversal short book loses on the same days that liquidity for covering disappears.
- **Momentum crashes in "junk rallies"** (Daniel & Moskowitz, 2016). After market declines, high-beta losers rebound violently: 2009, Nov 2020, Jan 2021. A book that is short the hottest names is structurally short this rebound convexity.
- **Reg SHO Rule 201.** A 10% intraday decline triggers a short-sale price test for the rest of that day and the next (cited in the SEC report). It does not affect entering on up days, but a second short after a fade may be restricted.

### 1.5 What our setup gets right and wrong

**Right:**
- The v2 intraday design matches the clientele evidence.
- The catalyst exclusion (do not fade real news) is consistent with Tetlock (2007) and with practitioner "don't fight fundamentals" rules.
- The ETB gate, the $10 price and $10M ADV gates, and the 10% sizing are all moves toward institutional practice.
- Deflated Sharpe was computed (0.49 against 1,037 trials). Most retail work never does this.

**Wrong or missing:**
1. **10% per name is still about 5x an institutional stress budget.** A +100% intraday squeeze costs 10% of NAV.
2. **Paper fills overstate shortability.** Alpaca paper trading does not model locate failures, HTB recalls or opening-auction queue position [verify]. The backtest also has no historical borrow data.
3. **No live tail control.** There is no real-time buy-stop, no option hedge, and no account-level daily loss halt.
4. **About 30 trades per year** (P7) means statistical confidence takes years to arrive. Three months of paper trading produces fewer than 10 trades and cannot validate anything.
5. **Market orders at 15:55 in small caps.** See Section 6.

---

## 2. Strategy 2: Options premium selling

### 2.1 What institutions find

| Finding | Source | Numbers |
|---|---|---|
| The VRP exists. Implied volatility exceeds realized on average. | Bondarenko (Cboe, May 2019); Carr & Wu (2009); Coval & Shumway (2001) | VIX averaged **19.3%** vs S&P realized **15.1%** in 1990–2018, a VRP of **4.2 vol points**. |
| The put-write index (PUT) has a better Sharpe than the S&P 500, but much worse skew. | Bondarenko (Cboe, 2019) | June 1986–Dec 2018: PUT vol **9.95% vs 14.93%**, beta **0.56**, monthly alpha **0.2%**, **skewness −2.09 vs −0.81**. Cumulative return **1,835%**. |
| Weekly vs monthly put-writing. | Bondarenko (Cboe, Jan 2016) | 2006–2015: gross premium **39.3%/yr (WPUT) vs 24.1% (PUT)**. CAGR **5.6% (WPUT) vs 6.6% (PUT) vs 7.1% (SPX TR)**. Max DD **−24.2% / −32.7% / −50.9%**. Short-dated selling collects more premium but does **not** compound faster. |
| Covered calls are mostly long equity. | Israelov & Nielsen (2015), AQR | The short-vol component has a Sharpe near **1.0** but only about **10% of total risk**. An implicit equity-reversal (timing) exposure adds about **25% of risk** with little return. Hedging that exposure improves the Sharpe. |
| Collars are expected to underperform their underlying, and net-long-vol collars *pay* the VRP. | Israelov & Klein (2016), AQR | Collars earn less equity premium and have historically done poorly relative to other ways of de-risking (the test case is the Cboe CLL 95-110 index). |
| Protective puts are expensive protection. | Israelov, "Pathetic Protection" (2019); Bondarenko (2014), "Why are put options so expensive?" | Rolling puts lose money net. Simply holding less equity is usually the cheaper hedge. |
| Rolling long puts as crisis protection has negative cost-adjusted returns. Trend following and volatility targeting did better. | Harvey et al. (2019), Man AHL | Covers 8 S&P drawdowns >15% and 3 recessions since 1985 [verify, from the book summary]. |
| Iron-condor index (CNDR: sell 20Δ, buy 5Δ wings, monthly). | Cboe | Lowest vol of the Cboe option-selling indexes, about **7.2%**, worst drawdown about **19%** (from the owner's prior research note, sourced to Cboe) [verify]. Lags the S&P badly in bull markets. |

### 2.2 Capacity and crowding

- **The index VRP is deep but crowded.** The Fed's Sept 2023 SCOOS says 0DTE options are "about half of all trading volume of S&P 500 index options." Cboe reports Q1-2025 0DTE at about **55% of SPX volume** [verify, via FOW]. Cboe's own analysis finds customer 0DTE flow "extremely balanced," with net market-maker gamma hedging at most **0.2% of SPX daily liquidity**. The volume is large, but so far it does not mechanically amplify moves.
- **Retail is a big net seller of short-dated premium.** Bryzgalova, Pavlova & Sikorskaya (2023) document the scale of retail options trading routed through the three big wholesalers and its cost to retail. Beckmeyer, Branger & Gayda (2023) find retail 0DTE traders lost about **$358k per day** after daily expiries were introduced [verify, via Bloomberg]. When many sellers crowd the same 7–14 DTE, 10–20Δ strikes, the premium per unit of tail risk shrinks.
- **Volmageddon (5 Feb 2018) is the canonical crowding failure** (BIS QR, March 2018):
  - The VIX rose **20 points**, the largest one-day rise since 1987, while the S&P fell **4.1%** (a 3.8σ move).
  - Leveraged and inverse VIX ETPs (about **$4bn** of assets) all had to buy VIX futures in the last hour. **115,862 VIX futures traded in one minute at 16:08.**
  - **XIV lost 84%** and was terminated.
  - Augustin, Cheng & Van den Bergen (2021) document the feedback loop.
  - IMF GFSR (Apr 2018) and the IMF blog (3 May 2018): retail short-VIX strategies were unwound, but "other low-volatility strategies remain widespread, particularly among institutional investors."
- **The VIX spike of 5 Aug 2024 was the largest one-day spike on record** (BIS Bulletin 95, Todorov & Vilkov, Oct 2024). It was driven largely by **dealers widening put quotes pre-market**, since VIX is computed from quotes, not trades. The intraday peak was about **65** [verify]. BIS Bulletin 90 puts the yen carry trade at about **$250bn** and TOPIX fell **12%** that day. **Lesson for kill switches:** a spot-VIX trigger can fire on a quote artifact at 9:30 and sell at the worst prices of the year.
- **Apr 2025 tariff shock:** VIX about **60** intraday on 7 Apr [verify]. SPX fell roughly 19–21% from the Feb high, then rallied about 9.5% in one session on 9 Apr. Record options volume was **101m contracts** on 4 Apr [verify, Cboe Index Insights]. Strategies that de-risked on a VIX trigger locked in losses and missed the V-shaped recovery.

### 2.3 How institutions manage short volatility

- **Size by stress, not by margin.** Institutional VRP mandates (AQR, Man and pension overlay managers) cap the loss under scenarios such as: SPX −10% overnight gap with VIX +20–30 points; the Oct 1987 replay (−20%); Feb 2018; Mar 2020. Typical limits are **stress loss ≤ 1–2x the target annual vol** of the sleeve.
- **Vol targeting and volatility-managed exposure.** Moreira & Muir (2017) show that scaling exposure inversely to recent variance raises Sharpe ratios for equity factors. Harvey et al. (2018, Man AHL) find vol targeting improves Sharpe for equity and credit (US equities **0.40 → about 0.48–0.51** [verify]) and **reduces extreme returns in every asset class**. For short-vol, vol targeting pushes in the *opposite* direction to "dip-timed" entries. Professionals usually resolve this by keeping notional constant in vega terms: they sell fewer contracts when IV is high even though the premium is richer.
- **Vega, gamma and correlation limits.** SPY, QQQ and IWM short puts are not three bets. In a crash their correlations go to about 0.9+. Desks aggregate by beta-weighted SPX delta, vega and gamma, and cap the total.
- **Exit management.** The institutional and Cboe benchmark convention is monthly roll, not hold-to-zero. Practitioner programs typically close at 50–80% of max profit or before the final week, because gamma per dollar of remaining premium explodes near expiry. Holding a 0.10Δ condor to expiry harvests the last few cents of premium at the highest gamma risk.
- **Tail hedges.** Typical budgets are **25–100 bp of NAV per year** on long VIX calls or call spreads, SPX put spreads, or (Man AHL's preference) a trend-following overlay (Harvey et al., 2019). Defined-risk wings are already a hedge; the extra overlay covers the correlation and gap risk across underlyings.
- **Leverage discipline.** Buffett's Berkshire earned a Sharpe of **0.79** with leverage of about **1.7x** (Frazzini, Kabiller & Pedersen, 2018). Even the best long-horizon investor runs moderate leverage. Short-vol books that look like Sharpe 2 on paper usually hide leverage on a negatively skewed payoff.

### 2.4 Strategy by strategy

**(a) Dip-timed SPY/QQQ/IWM 7–14 DTE put credit spread (live on paper)**
- *Right:* defined risk; highly liquid underlyings; short-dated, where premium per day is richest (WPUT gross premium 39% vs 24%); entering after dips sells when IV and the VRP are elevated, which is consistent with VRP research.
- *Wrong:*
  - Dip-buying with short gamma concentrates exposure right before vol clusters (Aug 2024 and Apr 2025 both had second legs). Moreira-Muir and Man AHL say to cut size when vol is high, so dip timing must come with *smaller* size.
  - Three highly correlated underlyings are effectively one position.
  - 7–14 DTE gamma means a 3–5% gap can take a spread from max profit to max loss overnight. The spread width *is* the risk budget, so it must be sized as if every spread can hit max loss at once.

**(b) Held-to-expiry 60 DTE 0.10Δ SPY iron condor**
- *Right:* far-OTM strikes and defined risk.
- *Wrong:*
  - Selling 0.10Δ collects little premium. The Bondarenko and Israelov-Nielsen evidence says most of the VRP sits nearer the money, and 10Δ wings are where crash-fearing demand is richest *and* most dangerous.
  - Holding to expiry deviates from CNDR and practitioner convention. It adds expiry-week gamma and pin risk.
  - SPY options are American-style and SPY pays quarterly dividends, so calls carry early-exercise and assignment risk around ex-dividend dates. SPX (European, cash-settled, Section 1256 tax treatment) is the institutional default.

**(c) Low-delta-call QQQ wheel and covered call**
- *Israelov & Nielsen:* a covered call is mostly equity beta. Low-delta calls make it *even more* equity-dominated: the short-vol slice collects little, while the cap still binds in rallies.
- The wheel's put leg is a cash-secured put (PUT index-like). Its assignment-then-sell-calls logic embeds the same "equity reversal" timing exposure that AQR shows adds risk without return.
- *Professional fix:* treat it as "QQQ beta + a separately sized short-vol sleeve," and size the equity beta deliberately.

**(d) Collars**
- *Israelov & Klein (2016):* a collar underperforms the index; if it is net long vol it **pays** the VRP; and it has done worse than simply holding less equity.
- Keep collars only for a concentrated position you cannot sell (for tax reasons, say). Do not use them as a return strategy.

**(e) Regime filters and VIX kill switches**
- The owner's finding that they only helped in 2008 and 2020 matches the institutional view. Slow trend and vol regime filters help in **grinding** bears (2008, 2022, and the second half of Mar 2020). They cannot help with **gaps** (Feb 2018, Aug 2024, Apr 2025), which happen between bars.
- BIS 95 adds that the spot-VIX level can be a quote artifact pre-market. Use VIX futures or term structure (VIX/VIX3M > 1), SPX realized vol, or the SPX price versus a moving average, confirmed after 9:45 ET.
- Kill switches are insurance with a cost: whipsaw exits in V-shaped recoveries, as in Apr 2025. Evaluate them on *drawdown reduction per unit of return given up*, never on raw return.

---

## 3. Strategy 3: Pairs trading and stat-arb (our backtest: about 0 after costs since 2010)

### 3.1 What institutions find

- **Gatev, Goetzmann & Rouwenhorst (2006):** distance-method pairs earned annualized excess returns of up to about **11%** in 1962–2002 [verify the exact figure in the paper]. That is the baseline everyone cites.
- **Do & Faff (2010):** the study "confirms the continuing downward trend in profitability." Pairs still work in prolonged turbulence such as 2008. Their follow-up on trading costs finds pairs and contrarian profits "largely unprofitable after 2002" once commissions, impact and short fees are included [verify, from the abstract summary].
- **Khandani & Lo (2007 JIM; 2011 JFM):** a simple contrarian long/short portfolio's daily returns decayed sharply from the mid-1990s to 2007 [verify the exact decay figures in the paper]. The week of **6 Aug 2007**, crowded market-neutral books unwound; losses spread through stop-loss and de-leveraging rules, and a "significant rebound" followed on **10 Aug**. The 2011 version dates the unwind's start to July 2007.
- **Brunnermeier & Pedersen (2009):** liquidity-provision strategies are short funding liquidity. They lose precisely when dealers' and arbitrageurs' capital is constrained.
- **Craftsmanship (Israel, Jiang & Ross, 2018, AQR):** signal definition, weighting, unintended-exposure control and rebalance frequency can matter as much as the choice of style. Institutional stat-arb survives through craftsmanship: residualized signals, sector neutrality, cost-aware optimization and patient execution.
- **Residual reversal (Blitz et al., 2013, Robeco):** reversal computed on factor-residual returns doubles risk-adjusted returns and survives costs in large caps. In institutional practice this is the modern descendant of pairs trading.

### 3.2 Capacity, crowding and risk management

- Stat-arb is the textbook crowded strategy (Aug 2007). Institutions monitor crowding with short-interest and holdings overlap, run at **4–8x gross with a beta-neutral, sector-neutral book**, set **daily VaR limits**, and run de-grossing playbooks when realized P&L volatility exceeds 2–3x normal.
- Their edge is in **costs**: AQR's live data (Frazzini, Israel & Moskowitz, 2018; $1.7tn of trades, 1998–2016) show actual institutional costs are "an order of magnitude smaller" than academic estimates. Retail costs are not.

### 3.3 Our setup

A near-zero after-cost result since 2010 **agrees with the literature**; it is not a bug. Without institutional cost levels, residualization and gross leverage, distance or cointegration pairs is a no-edge, short-liquidity strategy.

**Recommendation:** shelve it. If revisited, test *large-cap, industry-neutral residual reversal at weekly rebalance* (the Blitz/de Groot design) and require it to beat 2x realistic costs.

---

## 4. Strategy 4: Penny-stock long/short (being backtested tonight)

### 4.1 What institutions and academics find

- **Hou, Xue & Zhang (2020):** with NYSE breakpoints and value weights, **65% of 452 anomalies fail** a t ≥ 1.96 hurdle and **82% fail** a t ≥ 2.78 multiple-testing hurdle. Microcaps are **about 3% of market cap but about 60% of the names**. Most "anomalies" are microcap artifacts that cannot be traded at scale.
- **Novy-Marx & Velikov (2016):** strategies with >50% monthly turnover rarely survive costs. Penny-stock spreads are often several percent of price.
- **Chen & Velikov (2023):** after costs and post-publication decay, the expected net returns of the average published anomaly are close to zero, a few bp per month [verify the exact figure].
- **Ang, Shtauber & Tetlock (2013):** OTC and penny stocks have an illiquidity premium "several times" the listed-market one and a momentum premium **3x lower**. The premia are largest among retail-held, non-disclosing stocks. The premium exists *because* you cannot easily trade or short these stocks.
- **Bali, Cakici & Whitelaw (2011):** lottery-like stocks underperform. A **long** penny-stock leg is long the lottery demand that loses money on average.
- **Engelberg, Reed & Ringgenberg (2018):** volatile loan fees are a priced risk. Penny-stock borrow is scarce, recallable and expensive.

### 4.2 Capacity, crowding and risk management

Institutions mostly **do not run** this strategy. Mandates commonly bar stocks under $5 (or $1), and prime brokers rarely lend them at reasonable fees. Microcap short sellers who do (activist shorts, specialist funds) run very small books with locates confirmed per trade. They expect recalls and halts and accept LULD gaps.

### 4.3 What a retail penny backtest misses

1. Borrow availability and fees. Penny-stock fees can run tens of percent annualized or more [verify per name]. There is no historical borrow data.
2. Effective spreads and impact at the open and close, often 1–5% per side.
3. Delisting returns and survivorship (missing delistings bias the short side upward).
4. Halts (LULD, regulatory), pump-and-dump dynamics, and Reg SHO Rule 201.
5. Broker restrictions: Alpaca may block shorting below set price levels [verify current Alpaca policy].

**Gate before believing any result:** the backtest must show positive net returns with borrow at ≥ 25% annualized, spreads at ≥ 2% round trip, and delisting returns of −30% or worse for the long leg. It must also survive a Hou-Xue-Zhang-style multiple-testing hurdle (t ≥ 3).

---

## 5. Strategy 5: News and sentiment-driven entries

- **Tetlock (2007):** high media pessimism predicts downward pressure on prices followed by **reversal**, and unusually high or low pessimism predicts high trading volume. Sentiment is a **liquidity and noise** signal, mostly not information.
- **Barber & Odean (2008):** news drives *retail* buying. The tradable edge for a slow trader is to **fade** attention, not to chase it.
- **Institutional practice:** news alpha from headline speed decays in seconds to minutes and belongs to firms with low-latency machine-readable feeds. A retail Yahoo RSS or FMP feed is late by construction.
- **Useful roles for news** in this book are (1) as an **exclusion filter** (do not short a stock with real de-risking news; the owner already does this) and (2) as a **risk flag**: halt pending news, earnings within N days, FDA dates.
- **Right:** using news as an exclusion. **Wrong:** using it as a primary entry signal on a retail feed. Any LLM or sentiment score should be tested against a "no-news" baseline with timestamps aligned to *when the bot actually saw the item*, not to the article's stated publication time.

---

## 6. Strategy 6: Execution

### 6.1 What institutions find

- **Frazzini, Israel & Moskowitz (2018):** AQR's live data on $1.7tn of trades, 1998–2016, across 21 markets. Actual costs are about **10x smaller** than literature estimates for a patient institutional trader, and price impact is concave in size. The lesson cuts both ways: costs are largely **controllable through execution**, and a retail trader who crosses the spread with market orders pays the "literature" cost, not the AQR cost.
- **Muravyev & Pearson (2020):** option traders who time their executions pay effective spreads **under 40% of the conventional measure**. The multi-leg limit ladder (start at mid, walk toward the natural) is the right idea and is supported by evidence.
- **Close and open auctions:** institutions concentrate liquidity-seeking volume in the closing auction (MOC/LOC), and use LOO/MOO orders or VWAP over the first minutes for the open. Nasdaq and NYSE MOC entry cutoffs fall before the 16:00 close (typically 15:50 ET) [verify current exchange cutoffs and Alpaca `cls` support].
- **BIS (Mar 2018):** the 5 Feb 2018 blow-up was a **same-direction, same-time, end-of-day liquidity demand** (16:08 volume spike). Any rule-based "everyone trades at 15:55" flow is predictable and front-runnable.

### 6.2 Our setup

| Practice | Verdict | Change |
|---|---|---|
| Market orders at 15:55 to cover small-cap shorts | **Wrong.** You cross a wide spread in the last minutes, when small-cap spreads widen. | Use **MOC by 15:50** (Alpaca `time_in_force=cls`) [verify]. Or use marketable limits at NBBO ± a cap, with MOC as fallback. Log the slippage either way. |
| Opening-auction fills for v2 short entries | **Mixed.** The auction gives one price, but for retail-attention names the open is the most volatile print of the day. | Use **LOO** with a limit (e.g., no worse than prior close +X%), or enter at 09:35–09:45 with limit orders. Measure each against a 09:31 VWAP benchmark. |
| Multi-leg limit ladders for spreads | **Right** (Muravyev-Pearson). | Record mid-at-decision vs fill per order, and stop walking past a set fraction of the width. |
| Measuring live vs backtest | **Missing.** | Implementation-shortfall log: decision price, arrival price, fill, and the backtest-assumed fill for every order. |

---

## 7. Cross-cutting failure episodes and what each teaches

| Episode | What happened | Who got hurt | Lesson for this book |
|---|---|---|---|
| **Aug 2007 quant quake** | Crowded market-neutral books unwound over 6–9 Aug; rebound on 10 Aug (Khandani & Lo). | Stat-arb and pairs. | Crowded liquidity provision loses together. Stop-loss de-leveraging deepens the hole and then misses the rebound. |
| **2008** | Grinding bear market and vol regime. | PUT index max DD **−32.7%** (2006–2015 window) vs SPX **−50.9%**. | Slow regime filters help here. This is the owner's "VIX switch works in 2008" case. |
| **5 Feb 2018 Volmageddon** | VIX +20 pts; inverse VIX ETPs (about $4bn) forced to buy; XIV −84%. | Short-vol ETPs, retail short-vol. | Short-vol plus daily rebalancing plus crowding is a doom loop. Size for a 2x vol overnight. |
| **Mar 2020** | VIX about 82; liquidity crisis; spreads blew out. | All short-vol; PUT/BXM gave back gains. | Defined-risk spreads limit the loss. Undefined-risk positions and margin calls do not. |
| **Jan 2021 GameStop** | SI 109–123%; KOSS +480% in one day; broker restrictions. | Shorts in high-SI, retail-attention names; Two Sigma Crowding factor −5.7σ [verify]. | Per-name stress sizing; sentiment squeezes hit low-SI names too. |
| **5 Aug 2024 yen carry** | Largest one-day VIX spike on record, partly a pre-market quote effect (BIS 95); TOPIX −12%. | Short-dated short-vol, carry. | A spot-VIX kill switch can fire on quote artifacts. Confirm signals after the open. |
| **Apr 2025 tariff shock** | VIX about 60 [verify]; SPX about −20% from peak then +9.5% in a day. | Dip sellers sized too large; de-risk-then-miss-the-rebound. | Kill switches cost money in V-recoveries. Pre-sized defined risk beats reactive exits. |

---

## 8. Professional risk framework for the two paper bots and the candidates

The numbers below are for an account of about $100k. Scale linearly. Percentages are of account NAV at the start of the day.

### 8.1 Position sizing

**Short bot (v2 intraday):**
- Size each name by **squeeze stress**: weight = (per-name loss budget) / (stress move).
  - With a **1.5% NAV** budget and a **+100%** stress move, the weight is **1.5%**. That is too small to matter at 5 names.
  - A practical compromise for paper: **5% per name** (a +100% squeeze costs 5% NAV; a KOSS-type +480% costs 24%) **plus** a hard real-time buy-stop at **+30–40% from entry**, so the realistic per-name loss is about 2% NAV unless the stock halts and gaps.
  - Do not use 10% without a stop.
- Scale weights by inverse recent volatility (ATR or 20-day realized), capped at the per-name max.

**Options bot:**
- Size every spread at **max loss = width − credit**, and assume **all open short-vol positions can hit max loss together**.
- Cap the **aggregate max loss** of all open short-vol positions at **10% of NAV**, and **≤ 3% NAV** per expiry cycle.
- Run a vega target (for example, −$X vega per $100k) that is held constant or *reduced* when VIX is high, per Moreira-Muir and Man AHL. Do not sell more contracts just because the premium is fatter.

**Candidates** (penny long/short, pairs, news): **zero live allocation** until they pass the gates in 8.7. In paper trading, use per-name caps of ≤ 2%.

### 8.2 Portfolio-level gross and net limits

- **Short bot:** gross ≤ **50%** (P7 recommendation), with a hard limit at 60%. Net short ≤ 50%. Optional beta hedge with IWM or SPY if net short beta > 0.5 (the backtest found the effect negligible, so it is a risk choice, not a return choice).
- **Options bot:** beta-weighted SPX delta within **±25% NAV**; aggregate short vega ≤ the set limit; **SPY + QQQ + IWM counted as one exposure**.
- **Combined account:** both books are short convexity, so set a **combined stress limit**: under "SPX −10% gap, VIX +25, small-cap momentum +30%," the total loss must be ≤ **15% NAV**. Recompute it daily and block new entries that would breach it.

### 8.3 Per-name and concentration caps

- Short bot:
  - ≤ 5% NAV per name.
  - ≤ 2% of 20-day ADV per position.
  - ≤ 2 names per GICS industry.
  - No names with an earnings or FDA event within 2 trading days.
  - Borrow must be ETB at the time of entry. Skip if Alpaca shortability changed intraday.
- Options:
  - ≤ 1 open spread per underlying per expiry.
  - No short strikes within 1.0x the expected move (IV × √T) of spot at entry.
  - Avoid SPY call legs across ex-dividend dates, or use SPX/XSP (European, cash-settled).

### 8.4 Tail hedges

- **Short bot:**
  - A real-time buy-stop on every short, placed as a broker-side stop order so it works if the bot crashes.
  - Where liquid listed options exist and cost < 15% of the expected trade edge, buy far-OTM calls (strike about +50–100%, nearest expiry) on the largest positions.
  - Otherwise rely on the smaller sizing. Professionals size to the hedge they *can* buy.
- **Options bot:**
  - Defined-risk wings on every position; no naked short options.
  - A standing **25–75 bp per year** tail budget on 1–3-month SPX put spreads (e.g., 10Δ/5Δ) or VIX call spreads (e.g., 25/40 strikes), sized so that in a Feb-2018 or Aug-2024 replay the hedge offsets ≥ 30% of the book's stress loss.
  - Alternative per Man AHL: a small trend-following overlay, which has protected in extended sell-offs at lower carry cost.

### 8.5 Kill-switch policy (layered; account-level beats strategy-level)

1. **Hard account limits** (automatic, no override that day):
   - Daily P&L ≤ **−3% NAV**: flatten intraday shorts, block new options entries.
   - Weekly ≤ **−6%**: half size for two weeks.
   - Drawdown from high-water mark ≤ **−10%**: half size.
   - Drawdown ≤ **−15%**: stop everything, write a post-mortem, and restart only with a written review.
2. **Strategy limits:**
   - Short bot: ≥ 3 consecutive losing days with a cumulative loss > 4% NAV pauses it for 5 sessions, since crowding and junk-rally regimes cluster.
   - Options: stop **new** entries when VIX/VIX3M > 1.0 at the 09:45 ET or later check, or when 5-day SPX realized vol > 30. **Never** act on pre-market VIX prints (BIS 95).
   - Existing spreads are managed by price (8.5.3), not by VIX.
3. **Position rules:**
   - Close an options spread when its short strike's delta > 0.35–0.40 or the loss reaches 2x the credit.
   - Take profits at 50–60% of max profit.
   - Close or roll by 2 DTE for 7–14 DTE spreads, and by 21 DTE for 45–60 DTE condors. **Do not hold to expiry.**
4. **Operational kills:**
   - Data staleness > N minutes, an order-reject streak, a short position not flat at 15:58, a broker connectivity failure, or a run-guard conflict all halt trading and page the owner.

### 8.6 Monitoring and alerting

- **Real-time (pushed to phone or Slack):**
  - Fills, rejects and partial fills.
  - Any short up > +20% from entry.
  - Daily P&L crossing −1.5% and −3%.
  - Stress-limit utilization > 80%.
  - Short-strike breach.
  - Shortability or ETB flag changes.
  - Book not flat by 15:58.
  - Cron heartbeat missing.
- **Daily (16:15 sync):**
  - Implementation-shortfall report per order: decision, arrival and fill price against the backtest-assumed price.
  - Exposure report: gross, net, beta-SPX delta, vega, gamma and stress loss.
  - Borrow report.
  - Reconciliation of broker state against Supabase.
- **Weekly:**
  - Live-vs-backtest tracking: realized mean per trade against the backtest distribution (z-score).
  - Hit rate.
  - Slippage ratio (live / assumed).
  - Factor exposures (market, size, momentum, short-interest crowding).
- **Monthly:** re-run the backtest on the latest month with live fills substituted, and log the gap.

### 8.7 Paper-trading graduation criteria (before any live money)

All criteria must hold at once:

1. **Sample size.**
   - Short bot (about 30 trades/yr under P7): **≥ 60 trades** or **≥ 9 months**, whichever is later. Three months is not enough.
   - Put-spread bot: **≥ 40 closed spreads** spanning **≥ one VIX > 25 episode**.
   - If no stress episode occurs, run a historical stress replay with the live code path.
2. **Statistical consistency.** Live mean per-trade return within the backtest's 80% confidence band. Live hit rate within ±10 pp of the backtest. Prefer a deflated Sharpe for the live period ≥ 0.
3. **Execution.** Median live slippage ≤ **1.5x** the backtest cost assumption, and the 90th percentile ≤ 3x. If slippage exceeds that, re-run the backtest with the measured costs. The strategy graduates only if it is still positive at 1.5x the measured costs.
4. **Risk.**
   - Max paper drawdown ≤ **1.5x** the backtest's expected max drawdown for a window of the same length.
   - No single trade loses > 3% NAV.
   - No limit breaches in the final 60 days.
5. **Operations.** Zero unplanned missed runs, unflat books or reconciliation mismatches in the final **30 trading days**.
6. **Borrow realism (short bot).** Record every intended short that would have failed a real locate or been HTB (use the real-time ETB flag and log it). The strategy passes only if the result stays positive with those trades removed.
7. **Go-live ladder:**
   - Start at **25%** of the target size.
   - Step to 50% and then 100% only after each step completes **3 months** (or 20 trades) still meeting criteria 2–5.
   - Fall back one step on any hard-limit breach.

---

## 9. References

Access-link convention: "Download PDF" is a LibKey full-text link; "Access Options" is the Northeastern link resolver; "View Article" is a publisher page. All were resolved through LibKey unless marked otherwise.

### 9.1 Journal articles

1. Ang, A., Shtauber, A. A., & Tetlock, P. C. (2013). Asset pricing in the dark: The cross-section of OTC stocks. *Review of Financial Studies*, 26(12), 2985–3028. https://doi.org/10.1093/rfs/hht053
   [Download PDF](https://libkey.io/libraries/545/articles/48497548/full-text-file?utm_source=api_4212&allow_speedbump=true)
2. Asness, C., Frazzini, A., Israel, R., & Moskowitz, T. (2014). Fact, fiction, and momentum investing. *Journal of Portfolio Management*, 40(5), 75–92. https://doi.org/10.3905/jpm.2014.40.5.075
   [Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Asness&issn=0095-4918&title=The%20Journal%20of%20Portfolio%20Management&atitle=Fact%2C%20Fiction%2C%20and%20Momentum%20Investing&volume=40&issue=5&spage=75&epage=92&date=2014-09-30&rft_id=info:doi/10.3905%2Fjpm.2014.40.5.075&rft_id=info:pmid/&sid=LibKey)
3. Augustin, P., Cheng, I.-H., & Van den Bergen, L. (2021). Volmageddon and the failure of short volatility products. *Financial Analysts Journal*, 77(3), 35–51. https://doi.org/10.1080/0015198X.2021.1913040
   [Download PDF](https://libkey.io/libraries/545/articles/475430111/full-text-file?utm_source=api_4212&allow_speedbump=true)
4. Bali, T. G., Cakici, N., & Whitelaw, R. F. (2011). Maxing out: Stocks as lotteries and the cross-section of expected returns. *Journal of Financial Economics*, 99(2), 427–446. https://doi.org/10.1016/j.jfineco.2010.08.014
   [Download PDF](https://libkey.io/libraries/545/articles/19074358/full-text-file?utm_source=api_4212&allow_speedbump=true)
5. Barber, B. M., & Odean, T. (2008). All that glitters: The effect of attention and news on the buying behavior of individual and institutional investors. *Review of Financial Studies*, 21(2), 785–818. https://doi.org/10.1093/rfs/hhm079
   [Download PDF](https://libkey.io/libraries/545/articles/27130936/full-text-file?utm_source=api_4212&allow_speedbump=true)
6. Blitz, D., Huij, J., Lansdorp, S., & Verbeek, M. (2013). Short-term residual reversal. *Journal of Financial Markets*, 16(3), 477–504. https://doi.org/10.1016/j.finmar.2012.10.005
   [Download PDF](https://libkey.io/libraries/545/articles/32560944/full-text-file?utm_source=api_4212&allow_speedbump=true)
7. Bondarenko, O. (2014). Why are put options so expensive? *Quarterly Journal of Finance*, 4(3), 1450015. https://doi.org/10.1142/S2010139214500153
   [Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Bondarenko&issn=2010-1392&title=Quarterly%20Journal%20of%20Finance&atitle=Why%20Are%20Put%20Options%20So%20Expensive%3F&volume=4&issue=3&spage=1450015&epage=&date=2014-09-01&rft_id=info:doi/10.1142%2FS2010139214500153&rft_id=info:pmid/&sid=LibKey)
8. Brunnermeier, M. K., & Pedersen, L. H. (2009). Market liquidity and funding liquidity. *Review of Financial Studies*, 22(6), 2201–2238. https://doi.org/10.1093/rfs/hhn098
   [Download PDF](https://libkey.io/libraries/545/articles/27131033/full-text-file?utm_source=api_4212&allow_speedbump=true)
9. Bryzgalova, S., Pavlova, A., & Sikorskaya, T. (2023). Retail trading in options and the rise of the big three wholesalers. *Journal of Finance*, 78(6), 3465–3514. https://doi.org/10.1111/jofi.13285
   [Download PDF](https://libkey.io/libraries/545/articles/587567127/full-text-file?utm_source=api_4212&allow_speedbump=true)
10. Carr, P., & Wu, L. (2009). Variance risk premiums. *Review of Financial Studies*, 22(3), 1311–1341. https://doi.org/10.1093/rfs/hhn038
    [Download PDF](https://libkey.io/libraries/545/articles/27131017/full-text-file?utm_source=api_4212&allow_speedbump=true)
11. Chen, A. Y., & Velikov, M. (2023). Zeroing in on the expected returns of anomalies. *Journal of Financial and Quantitative Analysis*, 58(3), 968–1004. https://doi.org/10.1017/S0022109022000874
    [Download PDF](https://libkey.io/libraries/545/articles/532668704/full-text-file?utm_source=api_4212&allow_speedbump=true)
12. Coval, J. D., & Shumway, T. (2001). Expected option returns. *Journal of Finance*, 56(3), 983–1009. https://doi.org/10.1111/0022-1082.00352
    [Download PDF](https://libkey.io/libraries/545/articles/29954678/full-text-file?utm_source=api_4212&allow_speedbump=true)
13. Daniel, K., & Moskowitz, T. J. (2016). Momentum crashes. *Journal of Financial Economics*, 122(2), 221–247. https://doi.org/10.1016/j.jfineco.2015.12.002
    [Download PDF](https://libkey.io/libraries/545/articles/60246770/full-text-file?utm_source=api_4212&allow_speedbump=true)
14. de Groot, W., Huij, J., & Zhou, W. (2012). Another look at trading costs and short-term reversal profits. *Journal of Banking & Finance*, 36(2), 371–382. https://doi.org/10.1016/j.jbankfin.2011.07.015
    [Download PDF](https://libkey.io/libraries/545/articles/12500026/full-text-file?utm_source=api_4212&allow_speedbump=true)
15. Diether, K. B., Lee, K.-H., & Werner, I. M. (2009). Short-sale strategies and return predictability. *Review of Financial Studies*, 22(2), 575–607. https://doi.org/10.1093/rfs/hhn047
    [Download PDF](https://libkey.io/libraries/545/articles/27130977/full-text-file?utm_source=api_4212&allow_speedbump=true)
16. Do, B., & Faff, R. (2010). Does simple pairs trading still work? *Financial Analysts Journal*, 66(4), 83–95. https://doi.org/10.2469/faj.v66.n4.1
    [Download PDF](https://libkey.io/libraries/545/articles/100473937/full-text-file?utm_source=api_4212&allow_speedbump=true)
17. Engelberg, J. E., Reed, A. V., & Ringgenberg, M. C. (2018). Short-selling risk. *Journal of Finance*, 73(2), 755–786. https://doi.org/10.1111/jofi.12601
    [Download PDF](https://libkey.io/libraries/545/articles/167902131/full-text-file?utm_source=api_4212&allow_speedbump=true)
18. Frazzini, A., Kabiller, D., & Pedersen, L. H. (2018). Buffett's alpha. *Financial Analysts Journal*, 74(4), 35–55. https://doi.org/10.2469/faj.v74.n4.3
    [Download PDF](https://libkey.io/libraries/545/articles/231647431/full-text-file?utm_source=api_4212&allow_speedbump=true)
19. Frazzini, A., & Pedersen, L. H. (2014). Betting against beta. *Journal of Financial Economics*, 111(1), 1–25. https://doi.org/10.1016/j.jfineco.2013.10.005
    [Download PDF](https://libkey.io/libraries/545/articles/48213414/full-text-file?utm_source=api_4212&allow_speedbump=true)
20. Gatev, E., Goetzmann, W. N., & Rouwenhorst, K. G. (2006). Pairs trading: Performance of a relative-value arbitrage rule. *Review of Financial Studies*, 19(3), 797–827. https://doi.org/10.1093/rfs/hhj020
    [Download PDF](https://libkey.io/libraries/545/articles/27130801/full-text-file?utm_source=api_4212&allow_speedbump=true)
21. Harvey, C. R., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M., & Van Hemert, O. (2018). The impact of volatility targeting. *Journal of Portfolio Management*, 45(1), 14–33. https://doi.org/10.3905/jpm.2018.45.1.014
    [Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Harvey&issn=0095-4918&title=The%20Journal%20of%20Portfolio%20Management&atitle=The%20Impact%20of%20Volatility%20Targeting&volume=45&issue=1&spage=14&epage=33&date=2018-10-31&rft_id=info:doi/10.3905%2Fjpm.2018.45.1.014&rft_id=info:pmid/&sid=LibKey)
22. Harvey, C. R., Hoyle, E., Rattray, S., Sargaison, M., Taylor, D., & Van Hemert, O. (2019). The best of strategies for the worst of times: Can portfolios be crisis proofed? *Journal of Portfolio Management*, 45(5), 7–28. https://doi.org/10.3905/jpm.2019.45.5.007
    [Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Harvey&issn=0095-4918&title=The%20Journal%20of%20Portfolio%20Management&atitle=The%20Best%20of%20Strategies%20for%20the%20Worst%20of%20Times%3A%20Can%20Portfolios%20Be%20Crisis%20Proofed%3F&volume=45&issue=5&spage=7&epage=28&date=2019-06-30&rft_id=info:doi/10.3905%2Fjpm.2019.45.5.007&rft_id=info:pmid/&sid=LibKey)
23. Hou, K., Xue, C., & Zhang, L. (2020). Replicating anomalies. *Review of Financial Studies*, 33(5), 2019–2133. https://doi.org/10.1093/rfs/hhy131
    [Download PDF](https://libkey.io/libraries/545/articles/251962361/full-text-file?utm_source=api_4212&allow_speedbump=true)
24. Ilmanen, A. (2012). Do financial markets reward buying or selling insurance and lottery tickets? *Financial Analysts Journal*, 68(5), 26–36. https://doi.org/10.2469/faj.v68.n5.7
    [Download PDF](https://libkey.io/libraries/545/articles/100474072/full-text-file?utm_source=api_4212&allow_speedbump=true)
25. Israel, R., Jiang, S., & Ross, A. (2018). Craftsmanship alpha: An application to style investing. *Journal of Portfolio Management*, 44(2), 23–39. https://doi.org/10.3905/jpm.2018.44.2.023
    [Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Israel&issn=0095-4918&title=The%20Journal%20of%20Portfolio%20Management&atitle=Craftsmanship%20Alpha%3A%20An%20Application%20to%20Style%20Investing&volume=44&issue=2&spage=23&epage=39&date=2017-12-31&rft_id=info:doi/10.3905%2Fjpm.2018.44.2.023&rft_id=info:pmid/&sid=LibKey)
26. Israelov, R., & Klein, M. (2016). Risk and return of equity index collar strategies. *Journal of Alternative Investments*, 19(1), 41–54. https://doi.org/10.3905/jai.2016.19.1.041
    [Access Options](https://onesearch.library.northeastern.edu/openurl/01NEU_INST/01NEU_INST:NUL?genre=article&aulast=Israelov&issn=1520-3255&title=The%20Journal%20of%20Alternative%20Investments&atitle=Risk%20and%20Return%20of%20Equity%20Index%20Collar%20Strategies&volume=19&issue=1&spage=41&epage=54&date=2016-06-30&rft_id=info:doi/10.3905%2Fjai.2016.19.1.041&rft_id=info:pmid/&sid=LibKey)
27. Israelov, R., & Nielsen, L. N. (2015). Covered calls uncovered. *Financial Analysts Journal*, 71(6), 44–57. https://doi.org/10.2469/faj.v71.n6.1
    [Download PDF](https://libkey.io/libraries/545/articles/100474232/full-text-file?utm_source=api_4212&allow_speedbump=true)
28. Israelov, R. (2019). Pathetic protection: The elusive benefits of protective puts. *Journal of Alternative Investments*, Winter 2019. DOI not confirmed. **Not LibKey-resolved.** AQR listing: https://www.aqr.com/Insights/Research (search the title).
29. Khandani, A. E., & Lo, A. W. (2007). What happened to the quants in August 2007? *Journal of Investment Management*, 5(4), 29–78. No DOI. **Not LibKey-resolved.** https://web.mit.edu/~alo/www/Papers/august07.html
30. Khandani, A. E., & Lo, A. W. (2011). What happened to the quants in August 2007? Evidence from factors and transactions data. *Journal of Financial Markets*, 14(1), 1–46. https://doi.org/10.1016/j.finmar.2010.07.005
    [Download PDF](https://libkey.io/libraries/545/articles/20241542/full-text-file?utm_source=api_4212&allow_speedbump=true)
31. Lou, D., Polk, C., & Skouras, S. (2019). A tug of war: Overnight versus intraday expected returns. *Journal of Financial Economics*, 134(1), 192–213. https://doi.org/10.1016/j.jfineco.2019.03.011
    [Download PDF](https://libkey.io/libraries/545/articles/302597070/full-text-file?utm_source=api_4212&allow_speedbump=true)
32. Moreira, A., & Muir, T. (2017). Volatility-managed portfolios. *Journal of Finance*, 72(4), 1611–1644. https://doi.org/10.1111/jofi.12513
    [Download PDF](https://libkey.io/libraries/545/articles/89148290/full-text-file?utm_source=api_4212&allow_speedbump=true)
33. Muravyev, D., & Pearson, N. D. (2020). Options trading costs are lower than you think. *Review of Financial Studies*, 33(11), 4973–5014. https://doi.org/10.1093/rfs/hhaa010
    [Download PDF](https://libkey.io/libraries/545/articles/368250873/full-text-file?utm_source=api_4212&allow_speedbump=true)
34. Novy-Marx, R., & Velikov, M. (2016). A taxonomy of anomalies and their trading costs. *Review of Financial Studies*, 29(1), 104–147. https://doi.org/10.1093/rfs/hhv063
    [Download PDF](https://libkey.io/libraries/545/articles/57275731/full-text-file?utm_source=api_4212&allow_speedbump=true)
35. Tetlock, P. C. (2007). Giving content to investor sentiment: The role of media in the stock market. *Journal of Finance*, 62(3), 1139–1168. https://doi.org/10.1111/j.1540-6261.2007.01232.x
    [Download PDF](https://libkey.io/libraries/545/articles/29955358/full-text-file?utm_source=api_4212&allow_speedbump=true)

### 9.2 Working papers (no journal DOI; not LibKey-resolved)

36. Frazzini, A., Israel, R., & Moskowitz, T. J. (2018). Trading costs. Working paper, dated 7 Apr 2018. SSRN 3229719. https://papers.ssrn.com/abstract=3229719
37. Beckmeyer, H., Branger, N., & Gayda, L. (2023). Retail traders love 0DTE options... but should they? Working paper, 30 Mar 2023. SSRN 4404704. https://papers.ssrn.com/abstract=4404704
38. Blitz, D., Honarvar, I., & van der Grient, B. (2023). Reversing the trend of short-term reversal. Robeco, Oct 2023. https://www.robeco.com/en-int/insights/2023/10/reversing-the-trend-of-short-term-reversal

### 9.3 Institutional white papers and practitioner research (URL and date)

39. Frazzini, A., Friedman, J., & Kim, H. *Understanding Defensive Equity.* AQR white paper, Summer 2012. https://www.aqr.com/-/media/AQR/Documents/Insights/White-Papers/Understanding-Defensive-Equity.pdf
40. Israelov, R., & Tummala, H. *Understanding the Volatility Risk Premium.* AQR white paper, 11 May 2018. https://www.aqr.com/Insights/Research/White-Papers/Understanding-the-Volatility-Risk-Premium
41. Bondarenko, O. *Historical Performance of Put-Writing Strategies.* Cboe-sponsored study; Cboe Insights post, 29 May 2019. https://www.cboe.com/insights/posts/white-paper-shows-volatility-risk-premium-facilitated-higher-risk-adjusted-returns-for-put-index/
42. Bondarenko, O. *An Analysis of Index Option Writing with Monthly and Weekly Rollover.* Cboe-sponsored study; Cboe press release, 27 Jan 2016. https://ir.cboe.com/news/news-details/2016/New-Study-On-Weekly-Monthly-SP-500-PutWrite-Indexes-Released-01-27-2016/default.aspx
43. Cboe. *Benchmark Indices Series: Volatility Management with Cboe's BFLY and CNDR Indices.* Cboe Insights (undated post). https://www.cboe.com/insights/posts/benchmark-indices-series-volatility-management-with-cboes-bfly-and-cndr-indices/
44. Cboe. *0DTEs Decoded: Positioning, Trends, and Market Impact.* Cboe Insights (2024–25). https://www.cboe.com/insights/posts/0-dt-es-decoded-positioning-trends-and-market-impact
45. Cboe. *Volatility Insights: Much Ado About 0DTEs – Evaluating the Market Impact of SPX 0DTE Options.* Cboe Insights, 2023. https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options
46. Cboe. *Index Insights: April* (April 2025 review). https://www.cboe.com/insights/posts/index-insights-april
47. Arnott, R. et al. *How Can "Smart Beta" Go Horribly Wrong?* Research Affiliates, Feb 2016. https://www.researchaffiliates.com (title search). The low-vol valuation and return correlation of −0.38 is from secondary coverage [verify].
48. Two Sigma Venn. *A Special Edition on Crowding and the Return of Meme Stocks: May 2024 Factor Performance Report.* June 2024. https://www.venn.twosigma.com/insights/may-2024-factor-performance
49. Robeco. *Short-term residual reversal* (research summary), 17 Nov 2011. https://www.robeco.com/en-int/insights/2011/11/short-term-residual-reversal
50. Man Group. Man Institute: trend-following and crisis-protection research. https://www.man.com/insights/trend-following-generate-alpha (undated landing page). No Man Group article specifically on "overnight returns" was located; that topic is covered here by Lou, Polk & Skouras (2019).

### 9.4 Official sector

51. BIS. *The equity market turbulence of 5 February – the role of exchange-traded volatility products.* BIS Quarterly Review, March 2018 box. Published 11 Mar 2018, updated 21 Mar 2018. https://www.bis.org/publ/qtrpdf/r_qt1803t.htm
52. BIS. *The market turbulence and carry trade unwind of August 2024.* BIS Bulletin No 90, 27 Aug 2024. https://www.bis.org/publ/bisbull90.htm
53. Todorov, K., & Vilkov, G. *Anatomy of the VIX spike in August 2024.* BIS Bulletin No 95, 29 Oct 2024. https://www.bis.org/publ/bisbull95.htm
54. IMF. *Global Financial Stability Report, April 2018: A Bumpy Road Ahead.* 2 Apr 2018. https://www.imf.org/en/Publications/GFSR/Issues/2018/04/02/Global-Financial-Stability-Report-April-2018. IMF Blog, *Volatility Strikes Back*, 3 May 2018. https://www.imf.org/en/Blogs/Articles/2018/05/03/volatility-strikes-back
55. Federal Reserve Board. *Senior Credit Officer Opinion Survey on Dealer Financing Terms*, Sept 2023 (special questions on 0DTE). https://www.federalreserve.gov/data/scoos/files/scoos_202309.pdf. No FEDS Note dedicated to 0DTE was located.
56. U.S. SEC Staff. *Staff Report on Equity and Options Market Structure Conditions in Early 2021.* 18 Oct 2021. https://www.sec.gov/files/staff-report-equity-options-market-struction-conditions-early-2021.pdf

### 9.5 Not obtained (gaps)

- Goldman Sachs, JPMorgan and Morgan Stanley derivatives strategy notes on 0DTE, gamma and retail flows are client-only. The figure that retail is about 6% of 0DTE volume is attributed to J.P. Morgan in a secondary academic draft [verify].
- OCC and Nasdaq Economic Research items were not retrieved in this pass.
- Ilmanen's *Expected Returns* (Wiley, 2011), chapters on volatility selling, is a book with no DOI and is not LibKey-resolved. Its conclusions are summarized through Ilmanen (2012) above.
