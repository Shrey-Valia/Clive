-- Clive Trading dashboard schema.
-- Bots write with the service-role key (bypasses RLS). The dashboard reads as a
-- signed-in user, and only emails listed in dashboard_admins can read anything.

create table if not exists dashboard_admins (
  email text primary key
);

create or replace function is_dashboard_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from dashboard_admins where email = auth.jwt() ->> 'email');
$$;

-- ------------------------------------------------------------------ bots
create table if not exists bots (
  id             text primary key,
  name           text not null,
  kind           text not null check (kind in ('shorting', 'lucid', 'options')),
  status         text not null check (status in ('active', 'inactive', 'planned')),
  description    text,
  run_state      text,            -- ok | running | warning | error | idle, set by the bot
  last_message   text,
  last_heartbeat timestamptz
);

insert into bots (id, name, kind, status, description) values
  ('shorting', 'Stock Shorting Bot', 'shorting', 'active',
   'Shorts the top 5 end-of-day gainer fades on Alpaca paper at 3:55 PM ET.'),
  ('lucid', 'Lucid Bot', 'lucid', 'inactive',
   'Pine Script alerts → CrossTrade webhook → Tradovate funded accounts.'),
  ('options', 'Options Bot', 'options', 'planned', 'Future project.')
on conflict (id) do nothing;

-- ------------------------------------------------------------------ shorting bot
create table if not exists short_runs (
  id           bigint generated always as identity primary key,
  run_date     date not null,
  mode         text not null,            -- dry_run | execute
  status       text not null,            -- scanned | dry_run_done | executed | executed_with_errors | failed
  started_at   timestamptz,
  equity       numeric,
  n_candidates int,
  n_selected   int,
  n_orders     int,
  message      text
);
create index if not exists short_runs_date on short_runs (run_date desc);

create table if not exists short_candidates (
  id                      bigint generated always as identity primary key,
  run_id                  bigint references short_runs (id) on delete cascade,
  rank                    int,
  ticker                  text not null,
  company                 text,
  sector                  text,
  close                   numeric,
  prev_close              numeric,
  change                  numeric,
  high                    numeric,
  low                     numeric,
  vwap                    numeric,
  market_cap              numeric,
  volume                  numeric,
  rvol                    numeric,
  upper_wick              numeric,
  close_in_range          numeric,
  si_pct_float            numeric,
  days_to_cover           numeric,
  float_shares            numeric,
  si_date                 date,
  si_source               text,
  short_vol_ratio_latest  numeric,
  short_vol_ratio_avg     numeric,
  ftd_days                int,
  ftd_max_shares          numeric,
  shortable               boolean,
  easy_to_borrow          boolean,
  catalyst                text,
  headlines               jsonb,
  short_score             numeric,
  components              jsonb,
  coverage                numeric,
  flags                   text[],
  exclude_reason          text,
  selected                boolean default false,
  target_pct              numeric,
  target_qty              int
);
create index if not exists short_candidates_run on short_candidates (run_id, rank);

create table if not exists short_orders (
  id               text primary key,      -- Alpaca order id
  run_id           bigint references short_runs (id) on delete set null,
  ticker           text not null,
  side             text,
  qty              numeric,
  purpose          text,                  -- cover | close_long | trim_short | short | add_short
  status           text,
  submitted_at     timestamptz,
  filled_qty       numeric,
  filled_avg_price numeric,
  filled_at        timestamptz,
  error            text
);

create table if not exists short_trades (
  id          text primary key,
  ticker      text not null,
  side        text not null,              -- short | long
  qty         numeric,
  entry_at    timestamptz,
  entry_price numeric,
  exit_at     timestamptz,
  exit_price  numeric,
  pnl         numeric,
  pnl_pct     numeric
);
create index if not exists short_trades_exit on short_trades (exit_at desc);

create table if not exists equity_snapshots (
  bot_id             text references bots (id),
  snap_date          date not null,
  equity             numeric,
  cash               numeric,
  day_pl             numeric,
  long_market_value  numeric,
  short_market_value numeric,
  taken_at           timestamptz,
  primary key (bot_id, snap_date)
);

create table if not exists positions (
  bot_id          text references bots (id),
  ticker          text not null,
  qty             numeric,
  side            text,
  avg_entry_price numeric,
  current_price   numeric,
  market_value    numeric,
  unrealized_pl   numeric,
  unrealized_plpc numeric,
  updated_at      timestamptz,
  primary key (bot_id, ticker)
);

-- ------------------------------------------------------------------ lucid bot (filled once it is restarted)
create table if not exists lucid_accounts (
  account_id        text primary key,     -- Tradovate account name
  label             text,
  plan              text,                 -- e.g. Flex 50K Eval / Funded
  connection        text,                 -- connected | disconnected
  balance           numeric,
  day_pl            numeric,
  max_loss_floor    numeric,
  profit_target     numeric,
  open_position     text,
  updated_at        timestamptz
);

create table if not exists lucid_daily (
  account_id   text references lucid_accounts (account_id) on delete cascade,
  day          date not null,
  realized_pl  numeric,
  high_water   numeric,
  trades       int,
  primary key (account_id, day)
);

-- ------------------------------------------------------------------ RLS: admins read, nobody writes from the browser
do $$
declare t text;
begin
  foreach t in array array['dashboard_admins','bots','short_runs','short_candidates','short_orders',
                           'short_trades','equity_snapshots','positions','lucid_accounts','lucid_daily']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists admin_read on %I', t);
    execute format('create policy admin_read on %I for select to authenticated using (is_dashboard_admin())', t);
  end loop;
end $$;

-- Add yourself (run once, with your own email):
-- insert into dashboard_admins (email) values ('you@example.com');
