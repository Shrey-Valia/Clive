-- Event-alert hygiene (Stock Shorting Bot, config.AUTO_RESOLVE_EVENT_ALERTS): when the bot
-- auto-resolves an event alert it writes why into alerts.resolution, e.g.
-- "sync: the session is over" or "leftover check (10:00 safety net): no short from an earlier
-- session is open". Purely additive; the bot writes without the column until this is pushed
-- (db.py drops a missing column and retries). docs: Stock Shorting Bot/docs/EXECUTION_ROUND3.md.

alter table alerts add column if not exists resolution text;

-- Exchange-staged exits (config.STAGED_MOC_COVER): each order row records its time_in_force
-- ('day', 'cls' for a staged market-on-close cover, 'opg'), so the dashboard can tell a staged
-- MOC cover (purpose 'moc_cover') from a 15:55 market cover.
alter table short_orders add column if not exists tif text;

-- the sweep filters on (bot_id, kind) among open rows
create index if not exists alerts_open_kind on alerts (bot_id, kind, at) where resolved_at is null;

notify pgrst, 'reload schema';
