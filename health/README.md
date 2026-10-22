# health/check.py: the Clive health probe

A read-only probe that checks every bot, both Alpaca paper accounts, the data sources, the bots'
state files, Supabase, the dashboard site and the host itself. Each check becomes one row in
Supabase `health_checks` (migration `dashboard/supabase/migrations/008_health.sql`), and the
dashboard's **Health** tab (`/health`, F6, `HLTH <GO>`) shows them.

It never trades. Alpaca only gets GETs. The only writes are `health_checks` rows and `alerts` rows
with `bot_id = 'health'`. Every check is wrapped: one that raises becomes a `fail` row with the
error, and the probe carries on and exits 0.

```
python3 health/check.py --dry-run              # print the table, write nothing
python3 health/check.py                        # write the rows, raise / resolve alerts
python3 health/check.py --dry-run --now 2026-10-22T16:30   # judge the schedule as of an ET time
python3 health/check.py --host clive-vps       # host tag (default: $HEALTH_HOST or the hostname)
python3 health/check.py --dry-run --json       # the rows as JSON
```

Keys: `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`, `APCA_*` and `NTFY_TOPIC` come from
`Stock Shorting Bot/.env`, and `OPT_APCA_*` from `Options Bot/.env`. Secrets are never printed.

## Cron (Mac)

The probe runs every 15 minutes from 09:00 to 16:45 ET, Monday to Friday, plus 07:15 and 17:00.
The Mac's clock is ET, so add these with `crontab -e`:

```
# Clive health probe (read-only): every 15 min 09:00-16:45 ET, plus 07:15 and 17:00, Mon-Fri.
*/15 9-16 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/health' && mkdir -p logs && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 check.py >> logs/check.log 2>&1
15 7 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/health' && mkdir -p logs && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 check.py >> logs/check.log 2>&1
0 17 * * 1-5 cd '/Users/shreyvalia/Desktop/Clive-Trading Bot/health' && mkdir -p logs && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 check.py >> logs/check.log 2>&1
```

A sleeping Mac runs no probe either. That is why the dashboard flags a probe that has gone quiet
by itself: the `alive` row turns FAIL when it is more than 35 minutes old in the 09:15–17:15 window.

## VPS

The VPS kit (`deploy/`) schedules the same job: `clive-health-check.timer` →
`run-job.sh health-check`, which runs `check.py --host $(hostname)`. Its rows carry the VM's
hostname, so the Health tab shows the Mac and the VPS side by side. In shadow mode run-job blanks
`NTFY_TOPIC`, so the phone does not get every alert twice.

Note: the scheduler check reads the local cron logs only when the bots run on the same host. The
Supabase evidence (short_runs, sync_runs, options_live_runs, run_events, bots, market_events) works
from anywhere, but some jobs only leave a log line (monitor, intraday, sweep), so judge the
schedule from the host that runs the bots.

## What it checks

| component | checks |
|---|---|
| scheduler | per job, expected vs observed for today and the last 5 sessions (Alpaca calendar, half days included): shorting 09:31 open, monitor every 15 min, 15:40 close/cover, 15:50 fallback and 10/12/14:00 cover-leftovers (when scheduled), 16:15 sync; options 09:45 sweep, intraday, 15:45 decision, 16:15 sync; events 07:00. Each slot is OK, LATE (past its 3 min grace), MISSED (nothing by +5 min), FAILED (an error logged) or PENDING. The `timeline` row carries every slot in `detail.slots`. |
| positions | leftover shorts under intraday_v2, longs outside the 15:49–09:50 sleeve window, gross vs MAX_GROSS_PCT / OVERNIGHT_LONG_MAX_GROSS, open orders after 15:58; options legs with ≤ 1 trading day to expiry after 15:00, unpaired legs |
| accounts | both paper accounts: ACTIVE, not blocked, shorting on, options level 3, equity and buying power |
| data | TradingView, FINRA SI + short volume, SEC FTD, Yahoo RSS, FMP (if keyed), Alpaca SPY bar + option chain, Cboe VIX/VIX3M, Nasdaq earnings + economic, Fed calendar (latency in detail) |
| state | last_execute.json / last_open.json dates, next_open_picks.json vs the next session, stale .lock files, overnight_longs.json vs positions, the options account pin |
| supabase | API up, migrations 004–008 present, market_events updated within 26 h, the Netlify site (401 = up, protected) |
| host | disk, clock offset, Python, macOS sleep setting / repeat wake / time asleep in market hours today, cron entries (or systemd timers), uptime |
| health / summary | `alive` heartbeat; the worst status per component |

## Alerts

A check that is `fail` now and was not failing in this host's previous run (from `health_latest`,
or `logs/last_status.json` when Supabase is down) raises one `alerts` row (bot_id `health`,
level `critical`, kind `health_<component>_<check>`) and an ntfy push when `NTFY_TOPIC` is set.
An open alert of the same kind is not raised twice, and it is resolved when its check is back to
ok or warn.
