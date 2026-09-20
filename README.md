# Overnight Squeeze Scanner

Screens the day's top gainers, runs each through five short-interest blocks,
classifies the setup, and ranks them for a **24-hour hold**. Writes a CSV.

## Holding period

**Enter near the close, exit ~24h later near the next close.** Every weight
in this scanner is calibrated for that horizon and nothing longer. This is
the single most important fact about how it scores.

It is why days-to-cover carries only 5% of the long score and
fails-to-deliver carries zero. Both are multi-week measures. DTC counts the
*sessions* of average volume shorts need to unwind — a DTC of 12 is a
twelve-session thesis, and a 24-hour hold collects roughly a twelfth of it.
Ranking on those would be scoring a thesis the strategy never holds long
enough to collect.

What a one-day hold actually turns on is **how the stock closed**. A +30% day
that finishes on its high and a +30% day that finishes near its low are
opposite setups for tomorrow, and nothing else in the data separates them.
Real example from one board:

| | Change | Close in range | Call |
|---|---|---|---|
| VNCE | +44.6% | **95%** | LONG |
| ELMT | +32.7% | **26%** | SHORT |

## Setup

```bash
pip3 install -r requirements.txt
```

## Run

```bash
python3 run.py
```

Writes `output/squeeze_scan_<timestamp>.csv` plus `output/latest.csv`.

| Flag | Meaning |
|---|---|
| `--top N` | how many gainers to analyse (default 10) |
| `--min-price X` | minimum share price (default 3.00) |
| `--min-mcap X` | minimum market cap (default 25,000,000) |
| `--max-price X` | maximum share price (default: **no ceiling**) |
| `--max-mcap X` | maximum market cap (default: **no ceiling**) |
| `--fast` | skip short volume + FTD (blocks 3 and 4) |
| `--no-short-data` | skip short interest too |
| `--outdir DIR` | CSV destination (default `output/`) |

Downloads are cached in `.cache/`, so re-runs on the same day are fast.

> **The default screen is floor-only and is not a penny-stock screen.** With
> no ceiling it happily returns $190 mega-caps. To actually bound it:
>
> ```bash
> python3 run.py --max-price 20 --max-mcap 500000000
> ```

## The five blocks

Fintel serves a Cloudflare challenge to non-browser clients (hard 403), so
each block goes to the underlying source Fintel itself republishes.

### 1. Short interest — `shortdata.py`
Yahoo Finance + FINRA's official bi-monthly report. Yahoo leads (it carries
float), FINRA backfills and cross-checks. `short_data_source` records which
answered.

| Short % float | Tier |
|---|---|
| < 5% | `NOT_SQUEEZE` |
| 5–10% | `MILD` |
| 10–20% | `REAL_CROWDING` |
| 20–30% | `SERIOUS_FUEL` |
| 30%+ | `EXTREME` |

| Days to cover | Tier |
|---|---|
| < 1 | `EASY_EXIT` |
| 1–2 | `LOW` |
| 2–5 | `PAINFUL` |
| 5+ | `MESSY_EXIT` |

### 2. Borrow fee + availability — **not populated**
The one block with no free source. IBKR's public shortable-stocks feed is
retired (404 on every host), its FTP is unreachable, and Fintel/Ortex gate
borrow behind paid tiers.

The columns, tiers and scoring weights all exist and are wired — `borrow_fee`
carries 15% of the squeeze score and `low_borrow_cost` 12% of exhaustion.
Today those weights simply renormalise out, which is why coverage reads 85%
and 88% rather than 100%. Drop in a paid feed and they activate with no
other change. Nothing is faked to fill the gap.

Tiers, ready for when data exists: `CHEAP` <5%, `MODERATE` 5–20%,
`BITING` 20–50%, `PAINFUL` 50–100%, `SEVERE` 100–500%, `NUCLEAR_HTB` 500%+.

### 3. Short volume ratio — `shortvolume.py`
FINRA's daily off-exchange RegSHO files, averaged over the last 10 sessions.
Tiers: `LOW` <20%, `NORMAL` 20–40%, `ELEVATED` 40–50%, `HEAVY` 50–70%,
`DOMINANT` 70%+.

**This block is gated on short interest.** See "the gate" below — it is the
most important piece of logic in the scanner.

### 4. Fails-to-deliver — `ftd.py`
SEC semi-monthly files, auto-discovering the newest published period (they
run ~2 weeks behind). Judged as a **percentage of float**, since 100k fails
is enormous on a 5M float and noise on a 500M one.

### 5. Price action — `tradingview.py`
The JSON endpoint behind the market-movers-gainers page. Also supplies RSI,
relative volume, SMA20/50, ATR and gap.

## The gate

Heavy short **volume** with low short **interest** is churn, not a trap —
shorts opening and closing intraday around a small base leave nobody stuck.
On volume alone that looks identical to a trapped short. So short volume only
counts as squeeze fuel in proportion to whether real short interest sits
behind it:

```
short_vol_pressure = ramp(short_vol_ratio, 40, 75) * ramp(short_pct_float, 3, 10)
```

Same 70% short volume, two different stocks:

| Short interest | `c_short_vol_pressure` | squeeze score |
|---|---|---|
| 25% of float | 0.857 | **71.6** |
| 1.5% of float | 0.000 | **16.5** |

## Archetypes

The scores hide interactions, so every row is also classified. First match wins.

| Archetype | Condition |
|---|---|
| `STRUCTURAL_SQUEEZE` | SI ≥ 20% **and** DTC ≥ 5 |
| `CROWDED_SHORT` | SI ≥ 10% **and** DTC ≥ 2 |
| `HTB_MICROCAP` | borrow ≥ 100% **but** SI < 5% — hype, not a deep squeeze |
| `CHURN_NOT_TRAPPED` | short vol ≥ 50% **but** SI < 5% |
| `MOMENTUM_NEWS` | big move, SI < 5%, DTC < 1 |
| `NO_SQUEEZE_FUEL` / `MODERATE` | everything else |

## The call

Every name gets a **LONG** or **SHORT** next to the ticker. There is no score
gate to clear.

**Conviction** needs both a decent winning score *and* a decent margin over
the other side, because 52-vs-50 is a coin flip dressed up as a signal:

| Conviction | Winning score | Margin |
|---|---|---|
| `HIGH` | ≥ 50 | ≥ 25 |
| `MEDIUM` | ≥ 35 | ≥ 10 |
| `LOW` | anything else | |

The board ranks by conviction first, then score, so a clean well-separated
call is never buried under a higher-scoring coin flip.

## Overnight risk

You hold through one night with no ability to react. `overnight_risk` rates
the gap exposure that creates.

The asymmetric danger is a **SHORT in a tight-float, heavily-shorted name
that closed strong**. Those gap up violently on any news, and no stop
protects you through a halt or a pre-market print.

| Rating | Condition (SHORT calls) |
|---|---|
| `EXTREME` | float < 20M **and** SI ≥ 15% **and** (rvol ≥ 5 or closed ≥ 70% of range) |
| `HIGH` | tight float + crowded, or crowded + strong close |
| `ELEVATED` | SI ≥ 10% or tight float |
| `NORMAL` | neither |

For LONG calls it flags the inverse: a weak close on an extended move.

## The two scores

| `long_score` — continues tomorrow | w | | `short_score` — fades tomorrow | w |
|---|---|---|---|---|
| closed high in day's range | 25 | | closed **low** in day's range | 25 |
| relative volume | 15 | | spike magnitude | 20 |
| float tightness | 15 | | extension above SMA20 | 15 |
| short % float *(gated)* | 15 | | *low* short interest | 13 |
| close above VWAP | 12 | | blowoff volume | 12 |
| today's spike | 10 | | RSI | 10 |
| days to cover | 5 | | close below VWAP | 5 |
| borrow fee *(unavailable)* | 3 | | | |

Short interest still matters on the long side, but as **day-2 squeeze fuel**
in tight-float names — gated on float and volume, since on a big float with
ordinary volume it will not move anything inside 24 hours. On the short side
it appears as risk: a crowded name can gap up and take you out overnight.

High on both gets `BOTH_SIDES_HOT`. Every weight, ramp and tier is in
`shortbot/config.py`; both blocks must sum to 100.

## Data quality

The scanner is explicit about what it does not know.

- Missing inputs **drop out and remaining weights renormalise**, so a stock is
  never pushed down for a data gap. `*_data_coverage_pct` reports the gap.
- **No short data** caps `long_score` at 40 and forces any LONG call to
  LOW conviction, rather than letting the cap manufacture confidence.
- **Stale short interest** (>30 days) takes a 15% haircut on the squeeze side
  only; the exhaustion read is price-action based and always current.
- `c_*` columns expose every normalised component (0–1) behind both scores.

### Two traps the scanner now watches for

**Shorts may have already covered.** Short interest is a photograph of a
date. If the stock ripped since that date, part of the short base probably
covered *into* the move and the fuel the score is counting is spent.
`move_since_si_pct` reports the move since each ticker's own measurement
date, and a run above 25% raises a `RAN_+X%_SINCE_SI` flag.

**One catalyst, not N setups.** If most of the board sits in one sector, it
is usually a single event hitting a whole comp set — short interest did not
cause that move and is largely incidental to it. The scanner prints a
warning above 50% concentration. It has **no news or catalyst awareness**,
so this heuristic is the only guard against scoring a sector re-rating as
ten independent squeezes.

### Known lags

Every free source has one, Fintel included:

| Block | Freshness |
|---|---|
| Price action | live |
| Short volume | previous session |
| Short interest | bi-monthly, ~1–3 weeks behind |
| Fails-to-deliver | semi-monthly, ~2–4 weeks behind |
| Borrow fee | unavailable |

`short_interest_date`, `short_data_age_days` and `ftd_periods` tell you
exactly how old each number is.

## Layout

```
run.py                   CLI entry point
shortbot/config.py       thresholds, weights, ramps, tiers
shortbot/tradingview.py  block 5 - gainers + price action
shortbot/shortdata.py    block 1 - short interest (+ block 2 slot)
shortbot/shortvolume.py  block 3 - FINRA short volume
shortbot/ftd.py          block 4 - SEC fails-to-deliver
shortbot/scoring.py      tiers, scores, archetypes
shortbot/report.py       CSV + console output
.cache/                  downloaded source files
output/                  CSVs
```

Adding a borrow feed means one function returning the `EMPTY` dict shape in
`shortdata.py`, merged in `enrich()`. Nothing else changes.

## Running it unattended

Scheduled with **cron**, not launchd.

```bash
crontab -l    # see the schedule
crontab -e    # edit it
```

| Time (ET) | Job |
|---|---|
| 15:35 Mon-Fri | `daily.py --execute` — waits for 15:40 to scan, 15:45 to submit MOC |
| 16:15 Mon-Fri | `go.py` — settles the journal from actual broker fills |

Both append to `output/shortbot.log`.

### Why cron and not launchd

**launchd calendar triggers do not fire on this Mac.** Tested `launchctl
load`, `launchctl bootstrap gui/501`, and a trivial `/bin/sh` agent writing
to `/tmp` — every one showed `runs = 0`, launchd never even attempted them,
with the machine awake and the triggers correctly registered
(`launchctl print` showed the right `Weekday`/`Hour`/`Minute`). Cron fires
reliably, so that is what is used. The two `.plist` files remain in the
project, unloaded, if it is ever worth revisiting.

### Full Disk Access

`~/Desktop`, `~/Documents` and `~/Downloads` are TCC-protected, and cron gets
`Operation not permitted` there by default. `/usr/sbin/cron` has been granted
Full Disk Access (System Settings → Privacy & Security → Full Disk Access),
which is what makes the schedule above work from this location.

Moving the project somewhere outside those three directories would remove the
need for that grant entirely.

### Staying awake

The Mac must be awake at 15:35–16:16 ET. This machine is set to `sleep 1` on
both AC and battery, and **`Clamshell Sleep` means closing the lid sleeps it
outright** — no cron entry, `caffeinate` flag or `pmset` wake survives a
closed lid. That is what killed the first live run.

```bash
sudo pmset repeat wakeorpoweron MTWRF 15:25:00   # wake it
sudo pmset -c sleep 0                            # stop AC idle sleep
```

A cloud VM removes this class of problem entirely.

## Output layout

```
output/
  scans/2026-09-14/scan_1550ET.csv   one file per scrape, named for the
                                     SCRAPE time in ET (not write time)
  latest.csv                         most recent scan
  journal.csv                        one row per completed round trip
  open_trades.json                   today's book + the context that justified it
  backtest/events.csv                backtest dataset (expectancy reads this)
  shortbot.log                       daemon log
```

## Manual paper tracking

For watching it by hand without letting the bot trade:

Two runs a day, and they are **not** interchangeable:

| When | Command | Why then |
|---|---|---|
| ~15:50 ET, **before** the close | `python3 run.py && python3 track.py` | you have to pick names before the bell; prices are provisional |
| after 16:00 ET | `python3 track.py --settle` | re-marks those entries to the official close **and** closes out yesterday's |

So on any given day you run before the close to open, and after the close to
settle. Miss the pre-close run and you simply have no entry for that day —
you cannot reconstruct it later, because the scan depends on intraday state.
Miss the post-close settle and nothing is lost; it catches up next time.

Entry and exit are both marked at the **official close**. That matches the
backtest exactly (close to close) and sits within pennies of a 15:55 market
fill. Marking entry at 15:50 and exit at 16:00 would bake a ten-minute
asymmetry into every trade, so `--settle` normalises the entry once the
official close exists — the `entry_mark` column reads `PROVISIONAL` until
it does, then `OFFICIAL_CLOSE`.

Two books get logged from every scan:

| Book | Contents |
|---|---|
| `REAL` | the HIGH-conviction names your rule actually trades |
| `SHADOW` | every other name, same 5% sizing, never traded |

The shadow book matters. One HIGH name a day needs ~6 weeks to reach 30
trades, and tracking only that tier tells you whether HIGH made money — not
whether HIGH is *better than* LOW. Logging all ten answers the harder
question in days instead of months, at zero cost since none of it is traded.

`track.py` is idempotent: re-running it on the same scan will not duplicate
rows.

## Track record

```bash
python3 review.py
```

`journal.csv` pairs each trade's **predicted base rate** (`exp_avg_pct`,
recorded at entry) against its **realised result** (`pnl_pct`). That
comparison is the only honest test of whether the scanner works — `review.py`
reports the correlation between them, broken out by direction, archetype and
overnight risk.

Expect 30+ trades before any of it means much. At ~1 HIGH-conviction name
per day, that is roughly six weeks.

## Automated trading

```bash
python3 daily.py              # daemon, DRY RUN - prints the plan, sends nothing
python3 daily.py --execute    # same, but places orders on the paper account
python3 daily.py --now        # run both steps immediately instead of waiting
```

**Dry run is the default.** Nothing reaches the broker without `--execute`.
`--live` switches off paper and is separate again.

| Time (ET) | Action |
|---|---|
| 15:40 | scrape, score, write CSV |
| 15:45 | submit **market-on-close** orders |
| 15:50 | Alpaca's MOC cutoff — nothing can be submitted after this |
| 16:00 | orders fill at the **official closing price** |

### Why market-on-close

Orders go in as `TimeInForce.CLS`, so they fill at the official close. That
is what the strategy actually wants and what the backtest measured (close to
close).

A plain market order at 15:59 does neither. It either slips in the closing
scramble or — if it misses the bell — sits in `NEW` until the **next open**
and fills hours later at a completely different price. That failure is silent
unless you go looking at the order list.

`daily.py` refuses to submit after 15:50 rather than send something that
would fill at the wrong time, and cancels any leftover unfilled orders before
it does anything else.

### Position netting, not close-then-reopen

Orders are computed by netting current holdings against the target book, so:

| Held | Target | Orders sent |
|---|---|---|
| CLNN +121 | CLNN +121 | **none** — no wash trade |
| CLNN +121 | VRA +125 | sell 121 CLNN, buy 125 VRA |
| CLNN +121 | nothing | sell 121 CLNN |
| CLNN +121 | CLNN +200 | buy 79 CLNN |

Closing everything then re-entering produces two orders for any repeated name
and Alpaca rejects the second as a wash trade. Netting makes that structurally
impossible.

### Running it twice

A live rotation writes `output/last_rotate.json` and a second run the same day
**refuses**. Without that guard, `close_all` fires before entries and sells
the position the first run just opened — booking a meaningless round trip and
then getting the re-entry rejected. Use `--force` only if you genuinely want
to flatten and re-enter.

### Shortability

Alpaca rejects shorts on non-shortable assets at submit time, too late to
react. `broker.shortable()` checks first and skips them cleanly. Most
small-caps are **not** shortable — of one day's board only RLGT was, out of
five SHORT calls.

### Sizing

Only `HIGH` conviction names get an order, at `RISK_PER_TRADE_PCT` (5%) of
equity as **notional**. Note that is position size, not stop-based risk:
this strategy has no stop. The exit is time-based the next afternoon, so
between entry and exit your downside is whatever the gap does.

`MAX_TOTAL_EXPOSURE_PCT` (25%) caps total deployment so a board full of HIGH
conviction names cannot quietly become an all-in bet. Over the cap, the
lowest-scoring names are dropped.

At a $10k account, 5% is $500 per position, so anything above ~$500/share is
skipped for being under one share and high-priced names round down hard
(ZS at $191 → 2 shares → 3.8%, not 5%).

### Credentials

`.env` in the project root, gitignored and chmod 600:

```
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
APCA_PAPER=1
```

## Not yet calibrated

Run `python3 backtest.py`. On 2,410 spike events across 600 small caps over
3 years, entering at the close and exiting the next close:

| | Result |
|---|---|
| LONG the spike | mean **+0.81%**, median −0.42%, win 46.7%, skew 12.6 |
| SHORT the spike | mean **−0.81%**, median +0.42%, win **52.2%** |
| Short tail | 11.2% of trades lose >10%, 2.8% lose >25%, worst 1% −39.8% |
| Short cumulative | **−1,952%** over 2,410 trades |

Shorting these wins *more often than not* and still loses money. You win
small and often, then give it back in the 1-in-9 that gaps against you
overnight — and on a 24h hold you cannot react to the gap.

**What predicted tomorrow:** essentially nothing.

| Feature | Pearson | Spearman | Verdict |
|---|---|---|---|
| close-in-range | −0.022 (p=0.28) | −0.014 (p=0.49) | **no signal** |
| spike size | +0.048 (p=0.02) | +0.027 (p=0.19) | marginal |
| extension above SMA20 | +0.034 (p=0.09) | +0.062 (p=0.002) | marginal, **wrong direction for shorts** |

Two findings worth internalising:

1. **Close-in-range tested flat** and had been carrying 25% of both scores.
   Now 8, kept only as setup description.
2. **Shorting non-extended spikes looked like a real edge** (+0.74%, 60.7%
   win, n=537) until split by year — 2025 alone carried it (+2.82%, 73.6%
   win) and the other three years were negative. One year of noise. Nothing
   in the weights is fitted to it.

So: these weights rank how well a name **fits a setup**. They do not
forecast. The `exp_*` columns are the measured base rate for each call and
should outrank the scores when the two disagree.

## Scope

This ranks names against criteria you defined. It is a research tool, not
advice, and it does not place trades.
