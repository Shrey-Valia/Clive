# charts/: indicator research and terminal-style charts

Standalone research tooling. Nothing here imports the backtest engine. The only
dependency is `optionsbot/indicators.py`.

## Files

| path | what it is |
|---|---|
| `../optionsbot/indicators.py` | Pure pandas/numpy indicators: SMA, EMA, Wilder RMA, RSI (incl. RSI(2)), MACD, true range/ATR, ADX/DMI, Bollinger (%B, bandwidth), Keltner, TTM squeeze, IBS, Donchian, realized vol (close-to-close, Parkinson, Garman-Klass, Yang-Zhang), forward return / forward RV labels, IV rank & IV percentile, VIX term ratio, z-score, momentum, drawdown, up/down streak, turn-of-month flag, 4-state regime classifier |
| `../tests/test_indicators.py` | 20 hand-computed unit tests (`python3 -m pytest tests/test_indicators.py -q`) |
| `fetch_bars.py` | Read-only Alpaca `/v2/stocks/bars` (SIP, then IEX fallback, `adjustment=all`) plus CBOE VIX/VIX3M/VIX9D CSVs. Caches to `Options Bot/.cache/charts/` (12h freshness). Reads keys from `Stock Shorting Bot/.env` and never prints them. Never calls order endpoints. |
| `signal_study.py` | Conditional forward returns (5/10/21/30d) and forward 21d RV for 9 tickers × 18 signals, plus the SPY VRP study. Writes `signal_study.csv`, `signal_study.md`, `vrp_study.csv`. |
| `make_charts.py` | Renders PNGs into `out/` (reads `signal_study.csv`, so run the study first). |

```
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
$PY charts/fetch_bars.py --refresh   # optional: force-refresh the cache
$PY charts/signal_study.py
$PY charts/make_charts.py
```

## Data coverage (verified 2026-10-21)

* Alpaca daily bars start at **2016-01-04** for all 9 tickers, even when you
  request 2000-01-01. The SIP feed works with `end` set more than 15 min in the
  past. That gives about 10.8 years and 2,715 bars per ticker.
* CBOE: VIX from 1990-01-02, VIX3M from 2009-09-18, VIX9D from 2011-01-04.
  The CDN 307-redirects to `cdn-api.cboe.com`, so follow redirects.

## Outputs in `out/`

* `<TICKER>_technical.png`: last ~18 months of price, SMA 50/200, Bollinger 20/2, squeeze markers, RSI(2) with 10/90 bands, and ATR14 as % of price.
* `SPY_vix_regime.png`: SPY (log) with backwardation shading (VIX/VIX3M > 1), VIX and VIX3M, and the ratio.
* `SPY_vrp.png`: VIX at t vs SPY realized vol over (t, t+21], plus the spread.
* `signal_bars_SPY.png`, `signal_bars_QQQ.png`: mean forward return by signal at 4 horizons, against the baseline.
* `signal_excess_heatmap_5d.png`, `signal_excess_heatmap_21d.png`: ticker × signal excess return, with |t| >= 2 cells boxed.
* `signal_fwd_vol.png`: forward-RV ratio by signal.

## Methodology notes and caveats

* `excess` = mean forward return on signal days minus the mean on non-signal
  days. `t_nw` is the Newey-West t-stat (Bartlett, lag = horizon) of a dummy
  regression, which accounts for overlapping windows.
* `episodes` counts signal onsets. Fewer than 30 is flagged `*`. Above/Below
  200DMA has only ~25 regime switches for SPY, and VIX>30 covers basically 3
  crises (2020, 2022, Apr 2025), so treat those rows as anecdotal.
* The 9 tickers are highly correlated, so the cross-ticker counts are not
  independent tests. About 3,000 tests are run, so a few |t| > 2 results will
  appear by chance.
* The sample covers a single, mostly bullish decade, and the strongest results
  come from buying after vol spikes, which worked in every crash in this window.
* Forward RV is zero-mean (sqrt of mean squared log return), annualized,
  matching how VIX/variance swaps settle. VIX is a 30-calendar-day (~21 trading
  day) measure, so the comparison is like for like.
* Turn-of-month = last trading day + first 3 trading days. The incomplete
  current month is excluded.
* Squeeze = Bollinger(20,2) inside Keltner(20, 1.5 × ATR20).
