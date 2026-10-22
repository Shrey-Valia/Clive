# Stock Shorting Bot

The bot shorts end-of-day fades among the top 20 US gainers on the Alpaca **paper** account. Since 2026-10-22 it runs strategy v2 (`intraday_v2`, from `research/PROPOSAL.md`). It scans on day T, shorts at the T+1 open and covers at T+1 15:55. Nothing is held overnight.

| Time (ET) | Step |
|---|---|
| 09:31 (T+1) | cron runs `daily.py --open --execute`. It re-checks each saved pick (still shortable and easy to borrow, price ≥ $10, gap up ≤ 40% over the scan price, not already held), then shorts at market: 10% of equity each, gross ≤ 50%. Skips are logged. |
| 09:45–15:45 | cron runs `monitor.py` every 15 minutes. It raises alerts only and never trades (unless the optional daily-loss halt is on). |
| 15:40 | cron runs `daily.py --execute`. It reads the exchange calendar and waits. |
| 15:45 | Scan: [TradingView gainers](https://www.tradingview.com/markets/stocks-usa/market-movers-gainers/) with market cap above $25M and previous close above $3, top 20. Then short data, Alpaca borrow, ADV20 from Alpaca daily bars, headlines, scoring. Gates: score ≥ 70, price ≥ $10, ADV20 ≥ $10M. Up to 5 picks go to `logs/next_open_picks.json`. Fewer than 5, or none, is normal: the backtest made about 30 trades a year. |
| 15:55 | Cover **every** open short. The scan runs in a thread, so the cover still happens if the scan fails or hangs. No new shorts are opened. |
| 16:15 | cron runs `sync.py`: equity, positions and closed trades from Alpaca fills go to Supabase, plus a `sync_runs` row. |

On half days the 15:45 and 15:55 steps move to 12:45 and 12:55, because they are taken from the calendar's close. The 15:40 cron line would start too late for that, so a 12:40 line runs `daily.py --execute --half-day-only`, which exits at once unless today closes before 16:00. No order is sent less than 2 minutes before the close. The 09:31 job refuses to enter after 09:50, in case the Mac woke up late. On a holiday the 15:40 job writes a `short_runs` row with `status='market_closed'`.

## The checklist (`shortingbot/scoring.py`)

| # | Check | Source | How it counts |
|---|---|---|---|
| 1 | Move & volume: up 20–100% on 5–10× volume, upper wick, weak close | TradingView | 35% of the score |
| 2 | Crowding: DTC 1–5 and SI 5–15% of float is ideal | FINRA + yfinance | 20%. DTC > 10 **and** SI > 20% is **excluded** as a squeeze trap |
| 3 | Borrow | Alpaca `shortable` / `easy_to_borrow` | Gate: not shortable or hard to borrow is excluded, at the scan and again at the open |
| 4 | Fails-to-deliver | SEC | 0% in v2 (no signal in the backtest). It was 10% in v1. |
| 5 | Catalyst: real de-risking news vs hype | FMP if `FMP_API_KEY` is set, otherwise Yahoo RSS | 35%. Real catalysts (buyout, FDA approval, guidance raise, asset sale) are **excluded** |
| 6 | Liquidity (v2) | TradingView price, Alpaca daily bars | Gate: price at scan ≥ $10, 20-day average dollar volume ≥ $10M |

When a block has no data, it drops out and the other weights renormalise. Every threshold is in `shortingbot/config.py`.

## Setup

```bash
cd "Stock Shorting Bot"
pip3 install -r requirements.txt
cp .env.example .env      # Alpaca paper keys, optional FMP key, Supabase URL + service key, optional NTFY_TOPIC
python3 daily.py --now    # dry run: ranked 20, the covers it would send, the picks it would save
```

## Commands

```bash
python3 daily.py                      # dry run of the 15:40 job, waits for 15:45
python3 daily.py --now                # same, immediately. Picks go to logs/next_open_picks.dry_run.json
python3 daily.py --execute            # the 15:40 job (what cron does)
python3 daily.py --execute --half-day-only   # the 12:40 job: the same, but only on a half day
python3 daily.py --open --now         # dry run of the 09:31 job (reads the dry-run picks file if there is one)
python3 daily.py --open --execute     # the 09:31 job (what cron does)
python3 daily.py --open --now --picks FILE   # dry run against any picks file
python3 monitor.py --now              # one monitor pass, ignoring the 09:45-15:45 window
python3 sync.py                       # pull Alpaca truth into Supabase
python3 -m pytest -q tests            # offline test suite (fake broker, no network)
```

Each executed job runs once a day. A second run is refused unless you pass `--force`. The guard files are `logs/last_execute.json` (15:40) and `logs/last_open.json` (09:31). A guard with `"status": "started"` is written before the first order goes out, so a run that died mid-way also blocks a second one.

Only one executed run of a job at a time: `logs/close.lock` (the 15:40 job, and any cover, including `monitor.py`'s daily-loss flatten) and `logs/open.lock` (09:31) hold the running pid. A lock whose pid is gone is cleared automatically. A run that finds a live lock exits and raises a `job_locked` alert; a monitor flatten that finds it raises `cover_failed` and leaves the cover to the 15:55 job.

The 15:55 cover makes up to three passes before the 15:58 cutoff (`COVER_EXTRA_PASSES = 2`). Each pass cancels the open orders on the names still short, waits for them to go, re-reads positions, and buys back exactly what is still short. Every order carries a `client_order_id` like `v2-cover-2026-10-22-ASST-0`, so a POST that alpaca-py retries can't fill twice. Every Alpaca HTTP call times out after 15 seconds. If the exchange calendar can't be read at 15:40 (three tries, 5 s apart) on a weekday, a 16:00 close is assumed.

## Scheduling

```cron
31 9 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 daily.py --open --execute >> logs/cron.log 2>&1
*/15 9-15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 monitor.py >> logs/cron.log 2>&1
40 12 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 daily.py --execute --half-day-only >> logs/cron.log 2>&1
40 15 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 daily.py --execute >> logs/cron.log 2>&1
15 16 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/Stock Shorting Bot' && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 sync.py >> logs/cron.log 2>&1
```

- `monitor.py` fires every 15 minutes from 09:00 to 15:45. It exits at once outside 09:45–15:45 ET or on a closed day, and on half days it stops 15 minutes before the close.
- The 12:40 line does nothing on a regular day. On a half day it runs the whole 15:40 job (scan 12:45, cover 12:55); the 15:40 line then finds the guard and exits with "already executed today".
- The times above are ET only because this Mac's clock is ET. If the Mac's timezone changes, the cron times have to change too.
- `/usr/sbin/cron` needs Full Disk Access, because the folder is on the Desktop.
- The Mac has to be awake, plugged in, and **lid open** from 09:25 to 16:20 ET. A closed lid sleeps the Mac and no wake schedule survives it.
  ```bash
  sudo pmset repeat wakeorpoweron MTWRF 09:20:00
  sudo pmset -c sleep 0
  ```
- **If you rename or move this folder, update the crontab.**

## Controls in `config.py`

| Setting | Default | What it does |
|---|---|---|
| `STRATEGIES` | `["short_intraday"]` | The jobs `daily.py` runs. A long-side strategy can be added as another entry. |
| `SIZE_PCT_PER_NAME` | `10.0` | Equal size per name, % of equity. |
| `MAX_GROSS_PCT` | `50` | Short notional cap, open leftovers included. |
| `MIN_SHORT_SCORE`, `MIN_PRICE_AT_SCAN`, `MIN_ADV20_USD` | 70, $10, $10M | Selection gates. Set one to 0 to switch it off. |
| `MAX_GAP_UP_PCT` | `40` | A pick that opens more than this above its scan price is skipped. |
| `BROKER_STOP_PCT` | `None` (off) | Places a DAY buy-stop at fill × (1 + pct/100) with each 09:31 short. The stop is cancelled before the 15:55 cover, and a stop that already filled is handled. |
| `DAILY_LOSS_HALT_PCT` | `None` (off) | If the account is down this much on the day, at 09:31 or in `monitor.py`: cover all shorts, write `logs/halt.json`, take no new entries that day, and alert. |
| `DRAWDOWN_HALT_PCT` | `None` (off) | If equity is this far under its peak (`logs/equity_peak.json`; delete the file to reset it): no new entries, and alert. |
| `PDT_ENFORCE` | `"auto"` | Controls the old 3-day-trades-in-5-sessions cap under $25k. `auto` applies it only when the account reports PDT restrictions, or after an order was rejected for a PDT reason (`logs/pdt_detected.json`). `always` and `never` override it. When the cap applies, the 09:31 job enters at most 3 minus the day trades in the last 5 sessions, in rank order. |
| `ALERT_POSITION_LOSS_PCT`, `ALERT_BOOK_LOSS_PCT` | 25, 5 | Thresholds for `monitor.py` alerts. |

Alerts go to the Supabase `alerts` table and the log. If `NTFY_TOPIC` is set in `.env`, they are also pushed through ntfy.sh. `monitor.py` dedupes open alerts and resolves them once they clear.

FINRA retired the pattern day trader rule on 2026-06-04, and Alpaca replaced it with intraday margin. The paper account no longer reports `pattern_day_trader`, `daytrade_count` or `daytrading_buying_power`, so with `auto` the cap is off. A short rejected for buying power is skipped, and the run continues.

## Supabase

`dashboard/supabase/migrations/004_bot_ops.sql` adds:

- `bots.last_run_*` / `last_sync_*`
- the new `short_runs` columns: `trade_date`, the timestamps, `code_version`, `strategy_version`, `config`
- the `run_events`, `sync_runs` and `alerts` tables
- `short_trades.run_id`

Each `short_runs` row is one scan. It moves from `picks_saved` to `entered` to `exited`, and its `run_date` is the scan date. Until the migration is pushed, `db.py` drops the columns the database doesn't know and keeps writing. A Supabase failure never stops trading.

## Overnight long sleeve (off by default)

From the Penny Research backtest ("LS1 flip"): buy v2's own picks at the T 15:55 close, sell them at the T+1 open, then short them at 09:31 as usual. OOS 2022–2026, v2 plus the sleeve: Sharpe 1.25, CAGR 16.4%, max drawdown −11.1%. v2 alone: 1.21 / 10.7% / −8.3%. The long leg alone has a median trade of about 0 and a +2.1 skew, and its worst overnight gap was −71% (−3.6% of equity at 5%).

| Time (ET) | Step |
|---|---|
| T 15:55 | After the cover-all (which only ever buys back shorts), buy each name saved to `next_open_picks.json`: 5% of equity each, long gross ≤ 25%, a DAY limit at ask × 1.005. Skipped: a name still short or already long, dilution/offering news (`dilution`, `offering`, `pricing`, `424B`, `S-3` in the catalyst label or the headlines), price under $10, no quote, any halt. State: `logs/overnight_longs.json`. |
| T+1 09:31 | The `--open` job sells every long at market **first**, waits for the fills, then cancels, re-reads and re-sells what is left (2 more passes, until 09:50). Only then does the v2 short of the same names go out. A name still long is not shorted, and a `long_exit_failed` critical alert is raised. A name held short is still skipped. Each flip's fills are logged against the opening print and the quote midpoint at send time. |

- Orders carry `v2-long-buy-DATE-T-n` and `v2-long-sell-DATE-T-n` ids (`v2-long-flatten-…` for a halt). They go in `short_orders` with `sleeve = 'overnight_long'` and purpose `long_entry` / `long_exit` / `flatten_long`; `sync.py` tags the round trips in `short_trades` with `sleeve` (migration `007_long_sleeve.sql`).
- Halts: with a daily-loss halt or drawdown halt in force, nothing is bought. A halt flatten sells the longs as well as covering the shorts.
- `monitor.py`: `long_loss` when a long is 25% down; `long_outside_window` when any long is open after 09:50 and before 15:55. Longs then leave the `position_loss` check.
- Turning the flag off with longs still on the books does not strand them: the next `--open` sells whatever `logs/overnight_longs.json` says is open.

Settings (`config.py`): `OVERNIGHT_LONG_ENABLED` (False), `OVERNIGHT_LONG_SIZE_PCT` (5), `OVERNIGHT_LONG_MAX_GROSS` (25), `MIN_PRICE_FOR_ANY_TRADE` (10), `OVERNIGHT_LONG_SKIP_IF_NEWS`, `OVERNIGHT_LONG_ENTRY_ORDER` (`"limit"`, or `"moc"`), `OVERNIGHT_LONG_EXIT_ORDER` (`"market"`, or `"opg"`).

- `"moc"`: Alpaca refuses market-on-close orders after 15:50, so they are sent as soon as the scan is done and before the 15:55 cover. If the scan has not finished by 15:49:30, no longs that day. Any pick held in either direction, or shorted today, is skipped.
- `"opg"`: market-on-open orders must reach Alpaca by 09:28. This only works if the `--open` cron line moves to 09:20 (the job then waits for 09:31 to short). Started after 09:28, it falls back to a market sell.

To try it in a dry run without editing anything: `SHORTBOT_OVERNIGHT_LONG=1 python3 daily.py --now` (and `--open --now`). To run it, set `OVERNIGHT_LONG_ENABLED = True` in `config.py`. No cron change is needed for the default limit/market orders.

## ETB history

After every executed 15:45 scan, each scanned name's Alpaca flags are appended to `logs/etb_history.csv` (`date, ticker, price, shortable, etb`), for later research. Dry runs write `logs/etb_history.dry_run.csv`.

## Switching back to v1 (overnight hold)

The backtest says v1 loses money, so do this only on purpose.

1. In `config.py`, set:
   - `STRATEGIES = ["short_overnight"]` and `STRATEGY_VERSION = "overnight_v1"`
   - `SIZE_PCT_PER_NAME = None`, `SIZE_PCT_MAX = 19.0`, `SIZE_PCT_MIN = 15.0`, `MAX_GROSS_PCT = 100`
   - `MIN_SHORT_SCORE = 0`, `MIN_PRICE_AT_SCAN = 0`, `MIN_ADV20_USD = 0`
   - `WEIGHTS["ftd"] = 10`
2. Remove the 09:31 `--open` cron line. With `short_overnight`, `--open` does nothing.
3. The 15:40 job then nets holdings against the new top 5 at 15:55 and holds overnight, as before.

## Known limits

- Spike shorts have fat-tailed losses. The worst backtest trade lost 74%, which is −7.4% of equity at a 10% weight. Run on paper only, for at least 3 months.
- Fills at the open on small caps are worse than the backtest assumed. Watch the per-trade mean after real fills.
- Free short interest data is 1–3 weeks old. There is no borrow-fee feed.
- The catalyst check is keyword rules over headlines.

`docs/postmortem.html` covers what went wrong with the first bot.
