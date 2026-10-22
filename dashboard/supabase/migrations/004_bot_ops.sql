-- Stock Shorting Bot v2 ("intraday_v2") ops schema. Purely additive; same access model as 001:
-- the bot writes with the service-role key, the dashboard reads as an admin.
-- The bot writes these columns before this migration is pushed: db.py drops any column
-- PostgREST reports missing and retries, so nothing breaks in between.

-- ------------------------------------------------------------------ heartbeat split
-- daily.py writes the run pair, sync.py the sync pair. run_state stays, as the worse of
-- the two (error > warning > running > ok > idle), for anything still reading it.
alter table bots add column if not exists last_run_status  text;
alter table bots add column if not exists last_run_at      timestamptz;
alter table bots add column if not exists last_sync_status text;
alter table bots add column if not exists last_sync_at     timestamptz;

update bots set description =
  'Scans the top gainer fades at 3:45 PM ET, shorts the picks at the next open (9:31) and covers at 3:55 PM. Alpaca paper.'
where id = 'shorting';

-- ------------------------------------------------------------------ short_runs: one row per scan
-- v2 lifecycle of a row: running -> picks_saved (or failed) on scan day T,
--   -> entered | entered_with_errors | no_entries at the T+1 09:31 open,
--   -> exited | exited_with_errors at T+1 15:55.
-- Also market_closed (holidays, one row per closed weekday), and the v1 statuses
-- scanned | dry_run_done | executed | executed_with_errors.
-- run_date stays the scan date; trade_date is the session the picks are traded in.
alter table short_runs add column if not exists trade_date       date;
alter table short_runs add column if not exists scan_at          timestamptz;
alter table short_runs add column if not exists entry_at         timestamptz;
alter table short_runs add column if not exists exit_at          timestamptz;
alter table short_runs add column if not exists finished_at      timestamptz;
alter table short_runs add column if not exists session_close    timestamptz;   -- 13:00 on half days
alter table short_runs add column if not exists cutoff_at        timestamptz;
alter table short_runs add column if not exists code_version     text;          -- git sha, -dirty if uncommitted
alter table short_runs add column if not exists strategy_version text;          -- intraday_v2 | overnight_v1
alter table short_runs add column if not exists config           jsonb;         -- every config.py setting
create index if not exists short_runs_trade_date on short_runs (trade_date desc);

alter table short_candidates add column if not exists adv20_usd numeric;        -- 20-day avg dollar volume

-- purpose gains 'stop' (optional broker-side buy-stop, config.BROKER_STOP_PCT)
alter table short_orders add column if not exists stop_price numeric;

-- ------------------------------------------------------------------ run_events: the bot's log, per run
create table if not exists run_events (
  id      bigint generated always as identity primary key,
  run_id  bigint references short_runs (id) on delete cascade,
  bot_id  text,
  at      timestamptz not null default now(),
  level   text not null,            -- INFO | WARNING | ERROR
  step    text,                     -- setup | scan | source | score | picks | cover | entry | alert | finish
  message text
);
create index if not exists run_events_run on run_events (run_id, at);

-- ------------------------------------------------------------------ sync_runs: one row per sync.py
create table if not exists sync_runs (
  id          bigint generated always as identity primary key,
  bot_id      text,
  at          timestamptz not null default now(),
  status      text not null,        -- ok | error
  equity      numeric,
  n_positions int,
  n_trades    int,
  message     text
);
create index if not exists sync_runs_at on sync_runs (bot_id, at desc);

-- ------------------------------------------------------------------ short_trades -> the run that opened them
alter table short_trades add column if not exists run_id bigint references short_runs (id) on delete set null;
create index if not exists short_trades_run on short_trades (run_id);

-- ------------------------------------------------------------------ alerts
-- monitor.py: position_loss (per ticker), book_loss. Open while resolved_at is null, deduped.
-- monitor.py also: drawdown_halt (deduped), daily_loss_halt.
-- daily.py: pdt_block, pdt_limit, cover_failed, scan_failed, stale_picks, no_picks, overnight_leftover,
--   stop_failed, daily_loss_halt, drawdown_halt, run_failed.
create table if not exists alerts (
  id          bigint generated always as identity primary key,
  bot_id      text,
  at          timestamptz not null default now(),
  level       text,                 -- info | warning | critical
  kind        text,
  ticker      text,
  message     text,
  resolved_at timestamptz
);
create index if not exists alerts_open on alerts (bot_id, at desc) where resolved_at is null;

-- ------------------------------------------------------------------ RLS: admins read, nobody writes from the browser
do $$
declare t text;
begin
  foreach t in array array['run_events','sync_runs','alerts']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists admin_read on %I', t);
    execute format('create policy admin_read on %I for select to authenticated using (is_dashboard_admin())', t);
  end loop;
end $$;

-- PostgREST caches the schema; make the new columns visible to the bot right away.
notify pgrst, 'reload schema';
