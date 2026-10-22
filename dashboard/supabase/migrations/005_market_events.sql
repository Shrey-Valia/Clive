-- Market events calendar: macro releases (FOMC, CPI, NFP...), market structure
-- days (OPEX, VIX expiry, holidays, half days) and company events (earnings,
-- ex-dividend, investor days). Written daily by events/sync_events.py with the
-- service-role key; the dashboard reads it as an admin. Purely additive.

create table if not exists market_events (
  id          bigint generated always as identity primary key,
  event_date  date not null,
  event_time  text,                              -- "08:30" | "14:00" | "BMO" | "AMC" | null (unknown / all day), ET wall clock
  tz          text not null default 'America/New_York',
  kind        text not null check (kind in (
                'FOMC', 'FOMC_PRESSER', 'FOMC_MINUTES', 'FED_SPEECH',
                'CPI', 'PPI', 'NFP', 'PCE', 'GDP', 'RETAIL_SALES', 'JOBLESS_CLAIMS', 'TREASURY_AUCTION',
                'OPEX', 'QUAD_WITCH', 'VIX_EXPIRY', 'HOLIDAY', 'HALF_DAY',
                'EARNINGS', 'EARNINGS_CALL', 'INVESTOR_DAY', 'CONFERENCE_TALK', 'DIVIDEND_EX')),
  ticker      text,                              -- null for macro / market-wide events
  -- coalesce(ticker, ''), kept by the trigger below so the unique key treats
  -- "no ticker" as one value. A plain column (not GENERATED) so PostgREST
  -- upserts can name it in on_conflict and send it without errors.
  ticker_key  text not null default '',
  title       text not null,
  importance  smallint not null default 2 check (importance between 1 and 3),  -- 1 low, 2 medium, 3 high
  source      text,                              -- where the row came from (fed.gov, bea.gov, nasdaq, alpaca, rule, manual...)
  url         text,
  updated_at  timestamptz not null default now(),
  constraint market_events_uniq unique (kind, ticker_key, event_date, title)
);
create index if not exists market_events_date on market_events (event_date);
create index if not exists market_events_ticker on market_events (ticker, event_date) where ticker is not null;

create or replace function market_events_fill_key() returns trigger
language plpgsql as $$
begin
  new.ticker_key := coalesce(new.ticker, '');
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists market_events_fill_key on market_events;
create trigger market_events_fill_key before insert or update on market_events
  for each row execute function market_events_fill_key();

alter table market_events enable row level security;
drop policy if exists admin_read on market_events;
create policy admin_read on market_events for select to authenticated using (is_dashboard_admin());
