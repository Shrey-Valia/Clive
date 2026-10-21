# Proposal: Stock Shorting Bot strategy changes

Based on the 2016–2026 backtest. The coordinating session saved this file from the research agent's findings. None of these changes have been applied to the live bot. They are for the owner's review.

## Verdict
The live configuration loses money and can wipe out the account.
- It lost money in 10 of 11 years, and in both halves of the history.
- At 19% of equity per name, one squeeze ended the account twice:
  - KOSS on 2021-01-26: −98.7% of equity in one day.
  - TOP on 2023-04-27: −96.4%.

The small edge that does exist sits in the wrong part of the hold:

| Leg (mean gross short return) | IS 2016–21 | OOS 2022–26 |
|---|---|---|
| Overnight (15:55 → next open) | −0.49% | −0.14% |
| Next day (open → 15:55) | +0.40% | +0.29% |

The bot holds both legs, so they cancel, and costs and borrow fees then make it negative.

## Recommended changes, strongest evidence first
1. **Stop holding overnight.** Run the 15:45 scan on day T and save the picks. Short them after the T+1 open (09:31 ET) and cover at 15:55 on T+1.
   - This removes overnight gap risk and overnight borrow cost.
   - The book is flat at every rotation, so the wash-trade resize logic is no longer needed.
   - It needs a second cron entry at 09:31 and a `logs/next_open_picks.json` file. A sketch is in `research/proposed_config.py`.
2. **Cut size.** Set `SIZE_PCT_MAX = SIZE_PCT_MIN = 10.0`, which caps gross exposure at 50% (down from about 85%).
3. **Skip weak names and weak days.** Add `MIN_SHORT_SCORE = 70`. Taking fewer than 5 names, or none, is allowed. This is the only setting where the score showed reliable value.
4. **Tighten the liquidity gate.** Add `MIN_PRICE_AT_SCAN = 10` and `MIN_ADV20_USD = 10_000_000` on top of Alpaca's shortable/ETB check.
5. **Scoring.** Set `WEIGHTS["ftd"] = 0`, since FTD showed no signal in either period. Keep the catalyst and squeeze-trap exclusions. Two findings are noted for later testing and not yet changed:
   - Relative volume predicts the wrong way.
   - Raw days-to-cover is the consistent crowding penalty.
6. **Not recommended:**
   - Stops checked on daily bars. They made every variant worse.
   - An IWM hedge. Negligible effect.
   - VIX or IWM regime filters. Results were inconsistent.
   - Failed-follow-through entries. They lose money.
7. **Validate on paper for at least 3 months before trusting it.** Watch fills at the open, actual borrow availability, and whether the per-trade mean stays above about 1% after real fills.

## The proposed configuration (P7) against the current bot

| | Current IS | Current OOS | P7 IS | P7 OOS |
|---|---|---|---|---|
| Sharpe | −1.58 | −0.42 | 1.33 | 1.21 |
| CAGR | −89% | −75% | 11.0% | 10.7% |
| Max drawdown | −100% | −99.9% | −9.5% | −8.3% |
| Trades | 7,414 | 5,950 | 162 | 174 |

- **Per trade:** mean +3.4% net, median +1.6%. 9.5% of trades lose more than 10%. The worst trade lost 74%, which is −7.4% of equity at 10% weight.
- **Costs:** with costs doubled, OOS CAGR is 9.7%.
- **Caveats:**
  - P7 was assembled after looking at the grid.
  - It makes only about 30 trades a year, mostly from 2020 onward.
  - Its deflated Sharpe is 0.49 when counted against all 1,037 trials.
  - Treat it as a hypothesis to paper-trade, not a proven edge.

## Higher-capacity alternative (P3s)
Short the next day intraday, take the top 10 with ATR sizing at 50% gross, require a +20% minimum move, and apply the same strict gate.

| | IS | OOS |
|---|---|---|
| Sharpe | 1.19 | 0.65 |
| CAGR | | 6.6% |
| Max drawdown | | −13% |
| CAGR with doubled costs | | 3.1% |

1,776 trades in total.

## Caveats
- **Survivorship.** The universe includes 939 inactive names, but many delistings are still missing.
- **Shortability.** There is no historical borrow data. A price and volume gate stands in for it. Squeeze names were likely hard to borrow at the time, so the real tail may be smaller, but don't rely on that.
- **Borrow fees.** Assumed at 15% a year, or 50% for crowded or cheap names. Real fees on spiking names can run 50–300% a year.
- **Fills.** Small-cap fills at the open are worse than modeled, which is why the doubled-cost stress test matters.
- **News.** Historical headlines come from Alpaca/Benzinga, not the bot's live Yahoo/FMP feed.
