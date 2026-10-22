# Market events sync

`sync_events.py` fills the Supabase `market_events` table (dashboard migration
`dashboard/supabase/migrations/005_market_events.sql`) with the days that move
markets: Fed decisions and speeches, CPI/PPI/jobs/PCE/GDP/retail releases,
options and VIX expiries, holidays and half days, and earnings / ex-dividend
dates for the names the bots care about. The dashboard's Events strip,
calendar, `/events` page and the shorting/options event flags read this table.

- **Window:** the last 30 days through the next 90 days, in ET.
- **Idempotent:** it upserts on `(kind, ticker_key, event_date, title)`, so a
  re-run only refreshes rows. It never deletes. A release that moves to a new
  date leaves the old row in place. Delete it by hand if that matters.
- **Isolated sources:** if one source fails, the job prints a `FAIL` line and
  the other sources still sync.
- **Credentials:** read from `../Stock Shorting Bot/.env` (`SUPABASE_URL`,
  `SUPABASE_SERVICE_KEY`, and the read-only `APCA_API_KEY_ID` /
  `APCA_API_SECRET_KEY` for the market calendar). They are never printed. The
  service key is used only here, never in the browser.
- **User-Agent:** generic. `clive-events/1.0` for government sites, and a stock
  desktop Chrome string for Nasdaq, whose API rejects anything else. No
  personal email or contact details are sent.

## Run

```bash
cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/events"
python3 sync_events.py --dry-run          # fetch everything, print per-source status + counts per kind, write nothing
python3 sync_events.py                    # upsert into market_events (migration 005 must be applied first)
python3 sync_events.py --json rows.json   # also dump the merged rows
python3 sync_events.py --days-back 7 --days-ahead 30
```

It needs Python 3.9+ and `requests`. Nasdaq responses are cached per day in
`.cache/YYYY-MM-DD/`, so a second run on the same day is fast. A full cold run
makes about 200 HTTP calls and took about 7 minutes on 2026-10-22, because
Nasdaq is slow. A cached re-run takes under a minute.

## Cron (daily 07:00 ET)

This Mac's clock is on US Eastern (`date +%Z` prints `EDT`), so a plain crontab
line runs at 07:00 ET:

```cron
0 7 * * * cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/events" && mkdir -p logs && /usr/bin/env python3 sync_events.py >> logs/sync.log 2>&1
```

If the machine's zone may change, pin it. cronie and most Linux crons honour
`CRON_TZ`. macOS cron ignores it, so on a Mac keep the line above or use a
launchd `StartCalendarInterval`:

```cron
CRON_TZ=America/New_York
0 7 * * * cd "/Users/shreyvalia/Desktop/Clive-Trading Bot/events" && mkdir -p logs && /usr/bin/env python3 sync_events.py >> logs/sync.log 2>&1
```

## Sources, verified live on 2026-10-22

| Kind | Source used | Status |
|---|---|---|
| `FOMC`, `FOMC_PRESSER` | federalreserve.gov `monetarypolicy/fomccalendars.htm`. The decision day is the last day of each meeting: statement at 14:00, press conference at 14:30. A `*` meeting adds "+ SEP / dot plot". Notation votes are skipped. | **Verified.** The page lists 2026 and earlier, with no 2027 dates yet. A hardcoded 2026 list is the fallback. |
| `FOMC_MINUTES` | federalreserve.gov `json/calendar.json` "FOMC Minutes" entries, which give the scheduled dates. Otherwise the decision date + 21 days. | **Verified** (10/07, 11/18, 12/30). |
| `FED_SPEECH` | federalreserve.gov `json/calendar.json`, types Speeches and Testimony. Board members only (Chair, Vice Chairs, Governors). Importance: Chair 3, Vice Chair 2, Governor 1, testimony at least 2. | **Verified.** The Fed posts speeches only about a week ahead, so future rows appear as the daily run picks them up. Regional Fed presidents are not included. |
| `CPI`, `PPI`, `NFP` | bls.gov returns 403 to scripts. Primary source: the Nasdaq economic calendar `api.nasdaq.com/api/calendar/economicevents?date=`, which covers the past plus about 3 weeks ahead. Beyond that, a hardcoded table from BLS's 2026 schedule (`BLS_FALLBACK` in the script). | **Nasdaq verified.** It cross-checked CPI 10/14 and 11/10, NFP 10/02 and 11/06, and PPI 10/15. Table entries past the Nasdaq horizon (CPI 12/10, NFP 12/04) are **unverified** against bls.gov. PPI after 10/15 and all January 2027 BLS dates come only from Nasdaq, as they enter its horizon. |
| `PCE`, `GDP` | bea.gov `news/schedule` (upcoming), plus Nasdaq for the past 30 days. | **Verified** (GDP and PCE 10/29, 11/25, 12/23). |
| `RETAIL_SALES` | census.gov `economic-indicators/calendar-listview-{year}.html`, "Advance Monthly Sales for Retail and Food Services". | **Verified** (10/15, 11/17, 12/16, 2027-01-15). |
| `JOBLESS_CLAIMS` | Rule: every Thursday at 08:30, or Wednesday when Thursday is a holiday. Nasdaq rows confirm it. | Rule. Nasdaq-verified for recent weeks. |
| `TREASURY_AUCTION` | treasurydirect.gov `TA_WS/securities/upcoming?format=json` (10/20/30-year notes and bonds only), plus Nasdaq for recent ones. | **Verified.** Auctions are announced only about a week ahead, so the list is often short or empty. |
| `OPEX`, `QUAD_WITCH` | Rule: the third Friday, or the Thursday before when that Friday is a holiday. March, June, September and December are `QUAD_WITCH` (importance 3); other months are `OPEX`. | Rule, holiday-adjusted with the Alpaca calendar. |
| `VIX_EXPIRY` | Rule: the Wednesday 30 days before the next month's standard SPX expiry, or the prior business day if that is a holiday. | Rule. |
| `HOLIDAY`, `HALF_DAY` | Alpaca `GET /v2/calendar` (read-only paper keys). A weekday with no session is a holiday; a session with a close other than 16:00 is a half day. Names come from a built-in NYSE table, which is also the fallback. | **Verified.** It found 11/26, 12/25 and 1/1 closed, with half days 11/27 and 12/24. |
| `EARNINGS` | Nasdaq `api/calendar/earnings?date=` for every weekday in the window, filtered to the ticker scope. `event_time` is `BMO` (pre-market) or `AMC` (after hours), and null when Nasdaq gives no time. Importance is 3 for the mega caps, 2 for others. | **Verified.** Nasdaq lists dates out to roughly 6 to 8 weeks. Later dates fill in on later runs. |
| `DIVIDEND_EX` | Nasdaq `api/quote/{SYM}/dividends?assetclass=stocks` (`etf` for SPY/QQQ/IWM). Ex-dates inside the window only. | **Verified** for Nasdaq-listed names and QQQ. Nasdaq has **no dividend history for SPY or IWM** ("Non-Nasdaq symbols"), and neither do some NYSE small caps, so those have no ex-dividend rows. |
| `EARNINGS_CALL`, `INVESTOR_DAY`, `CONFERENCE_TALK` | **Manual only:** `manual_events.csv`. No free source was reliable. Nasdaq's earnings feed carries no call times, and investor-day and conference calendars sit on each company's IR site or behind paid feeds. | Manual. |

### Nasdaq economic-calendar date offset

The `date=` parameter returns the *previous* day's events: `date=2026-10-29`
returns the 10/28 FOMC decision, and `date=Fri` returns Thursday's jobless
claims. The job calibrates this offset on every run by checking which shift
puts the jobless-claims rows on Thursdays. If Nasdaq fixes the API, the job
adapts without a code change. The feed's `gmt` field is really a fixed UTC−4
clock: 08:30 EST shows as 09:30 after the November DST switch. The job converts
it to the ET wall clock.

## Ticker scope

- SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA and NFLX.
- The options-bot universe, read from `Options Bot/live/config.py` `UNIVERSE`.
- From Supabase:
  - selected `short_candidates` of the latest 3 `short_runs`
  - every `positions.ticker`; option-contract symbols are ignored
  - `options_live_positions.underlying`

If Supabase can't be reached, the job falls back to the static list.

## Manual rows

Add one row per event to `manual_events.csv`:

```
event_date,event_time,kind,ticker,title,importance,url
2026-11-18,10:00,INVESTOR_DAY,XYZ,XYZ investor day,2,https://...
```

Lines starting with `#` are ignored. `kind` must be one of the kinds in
migration 005. A manual row beats any automatic row with the same key.

## Column conventions

- `event_time` is the ET wall clock: `"08:30"`, `"14:00"`, `BMO` (before the
  open) or `AMC` (after the close). Null means all day or unknown.
- `tz` is always `America/New_York`.
- `importance` runs from 1 (low) to 3 (high).
- `ticker` is null for macro and market-wide rows. `ticker_key` is
  `coalesce(ticker, '')`, set by the job and re-derived by a trigger.
- `source` names where the row came from: `federalreserve.gov`, `bea.gov`,
  `census.gov`, `treasurydirect.gov`, `alpaca`, `nasdaq`, `rule…`,
  `bls-schedule (hardcoded)` or `manual`.
