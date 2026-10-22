-- Health checks: health/check.py (a read-only probe, every 15 min on each host) writes one row per
-- check per run with the service-role key; the dashboard's /health tab reads them as an admin.
-- Purely additive. Same access model as 001: RLS on, admins read, nobody writes from the browser.
--
-- component: scheduler | positions | accounts | data | state | supabase | host | health | summary
-- check_name: e.g. shorting.close, shorting.leftover_shorts, cboe_vix, sleep_today, alive
-- host: the probe's hostname, so the Mac's rows and the VPS's rows stay apart.
-- The probe also raises alerts rows (004) with bot_id 'health' for new failures and resolves them.

create table if not exists health_checks (
  id          bigint generated always as identity primary key,
  run_at      timestamptz not null default now(),
  host        text,
  component   text not null,
  check_name  text not null,
  status      text not null check (status in ('ok', 'warn', 'fail', 'unknown')),
  message     text,
  detail      jsonb,
  expected_at timestamptz,          -- scheduler: when the run was due
  observed_at timestamptz           -- scheduler: when it was seen (null = never)
);
create index if not exists health_checks_latest on health_checks (component, check_name, run_at desc);
create index if not exists health_checks_run_at on health_checks (run_at desc);

-- The latest row per (component, check_name, host). Per host, so a Mac and a VPS probing at the
-- same time each keep their own row; the dashboard groups by host.
create or replace view health_latest with (security_invoker = true) as
  select distinct on (component, check_name, coalesce(host, ''))
         id, run_at, host, component, check_name, status, message, detail, expected_at, observed_at
    from health_checks
   order by component, check_name, coalesce(host, ''), run_at desc;

alter table health_checks enable row level security;
drop policy if exists admin_read on health_checks;
create policy admin_read on health_checks for select to authenticated using (is_dashboard_admin());

-- Optional housekeeping (run by hand or from pg_cron): keep 30 days.
--   delete from health_checks where run_at < now() - interval '30 days';

notify pgrst, 'reload schema';
