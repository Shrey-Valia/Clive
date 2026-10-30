// Every page reads through here, so DEMO_MODE=1 can swap Supabase + Alpaca for
// a fixture when previewing without a database. Real-mode paths never touch
// lib/demo.ts; the fixture is only reached behind `isDemo`.
import "server-only";
import { cache } from "react";
import { alpacaAccount, alpacaPositions } from "@/lib/alpaca";
import { adminClient, supabaseServer } from "@/lib/supabase/server";
import type { Account, Bot, BotAlert, Candidate, LogLine, LucidAccount, MarketEvent, OptOrder, OptPosition, OptRun, OptTrade, Order, Position, Run, Snap, SyncRun, Trade } from "@/lib/types";
import type { Pick } from "@/lib/analytics";
import { alerts as computeAlerts, entryDate, v2Runs } from "@/lib/analytics";
import { addDays, etParts } from "@/lib/time";
import { currentFlow } from "@/lib/next-action";
import { pillOf, type HealthData, type HealthPill, type HealthRow, type HistPoint } from "@/lib/health";

export type { Account, Bot, Candidate, LogLine, LucidAccount, Order, Position, Run, Snap, Trade } from "@/lib/types";

// Never on Vercel: demo mode also switches off sign-in.
export const isDemo = process.env.DEMO_MODE === "1" && !process.env.VERCEL;

const loadDemo = async () => (await import("@/lib/demo")).demo;
const loadDemoBots = async () => (await import("@/lib/demo")).DEMO_BOTS;

const numify = <T extends Record<string, unknown>>(row: T, keys: string[]): T => {
  const out: Record<string, unknown> = { ...row };
  for (const k of keys) if (out[k] !== null && out[k] !== undefined) out[k] = Number(out[k]);
  return out as T;
};
const CAND_NUM = ["close", "change", "prev_close", "high", "low", "market_cap", "rvol", "upper_wick", "close_in_range", "si_pct_float",
  "days_to_cover", "short_vol_ratio_latest", "short_score", "target_pct"];
const ORDER_NUM = ["qty", "filled_qty", "filled_avg_price"];
const normCand = (c: Record<string, unknown>) => numify(c, CAND_NUM) as unknown as Candidate;
const normOrder = (o: Record<string, unknown>) => numify(o, ORDER_NUM) as unknown as Order;
const normTrade = (t: Record<string, unknown>) => numify(t, ["qty", "entry_price", "exit_price", "pnl", "pnl_pct"]) as unknown as Trade;
const normSnap = (s: Record<string, unknown>) =>
  ({ ...s, equity: Number(s.equity), day_pl: s.day_pl === null || s.day_pl === undefined ? null : Number(s.day_pl) }) as Snap;

type HeldCand = Candidate & { run_date?: string };

export type Shorting = {
  simulated: boolean;
  bot: Bot | null; run: Run | null; runs: Run[]; candidates: Candidate[]; orders: Order[];
  snaps: Snap[]; trades: Trade[]; positions: Position[]; account: Account | null;
  picks: Pick[]; held: [string, HeldCand][]; events: LogLine[];
  /** intraday_v2: orders of the run whose book trades today (the latest row is tonight's scan). */
  bookOrders: Order[];
  syncs: SyncRun[]; alerts: BotAlert[];
};

type SB = NonNullable<Awaited<ReturnType<typeof adminClient>>>;
/** Optional table/column from a later migration: any error (missing table or column) → fallback. */
async function optional<T>(q: PromiseLike<{ data: unknown; error: unknown }>, fallback: T): Promise<T> {
  try { const { data, error } = await q; return error || !data ? fallback : (data as T); } catch { return fallback; }
}

// ------------------------------------------------------------------ queries
export async function getBots(): Promise<Bot[] | null> {
  if (isDemo) return loadDemoBots();
  const sb = await adminClient();
  if (!sb) return null;
  return ((await sb.from("bots").select("*")).data ?? []) as Bot[];
}

/** Optional forward-compatible table (see docs/reviews/BOT_CHANGES_NEEDED.md). Missing table → []. */
async function runEvents(sb: SB, runId?: number, botId = "shorting"): Promise<LogLine[]> {
  try {
    const base = () => sb.from("run_events").select("at,level,step,message").order("at", { ascending: runId !== undefined }).limit(runId !== undefined ? 200 : 30);
    let res = runId !== undefined ? await base().eq("run_id", runId) : await base().eq("bot_id", botId);
    if (res.error && runId === undefined && botId === "shorting") res = await base(); // table without bot_id: all rows are the shorting bot's
    const { data, error } = res;
    if (error || !data) return [];
    return data.map((e) => ({ at: String(e.at), level: (String(e.level).toUpperCase().startsWith("ERR") ? "ERROR" : String(e.level).toUpperCase().startsWith("WARN") ? "WARN" : "INFO") as LogLine["level"],
      step: String(e.step ?? "").toUpperCase(), message: String(e.message ?? "") }));
  } catch { return []; }
}

const syncRuns = (sb: SB, botId: string) =>
  optional<Record<string, unknown>[]>(sb.from("sync_runs").select("*").eq("bot_id", botId).order("at", { ascending: false }).limit(10), [])
    .then((rows) => rows.map((r) => ({ ...r, equity: r.equity === null || r.equity === undefined ? null : Number(r.equity) }) as unknown as SyncRun));
const openAlertRows = (sb: SB) =>
  optional<BotAlert[]>(sb.from("alerts").select("*").is("resolved_at", null).order("at", { ascending: false }).limit(50), []);

function picksFrom(runs: Run[], cands: Candidate[]): { picks: Pick[]; held: (tickers: string[]) => [string, HeldCand][] } {
  const date = new Map(runs.map((r) => [r.id, r.run_date]));
  const sel = cands.filter((c) => c.selected && c.run_id !== null && c.run_id !== undefined && date.has(c.run_id));
  // rank slot = position among that run's picks (1 = largest size), not the raw scan rank
  const slot = new Map<string, number>();
  for (const id of new Set(sel.map((c) => c.run_id!)))
    sel.filter((c) => c.run_id === id).sort((a, b) => (b.target_pct ?? 0) - (a.target_pct ?? 0) || a.rank - b.rank).forEach((c, i) => slot.set(`${id}|${c.ticker}`, i + 1));
  const runById = new Map(runs.map((r) => [r.id, r]));
  const picks = sel.map((c) => ({ run_id: c.run_id!, run_date: date.get(c.run_id!)!, entry_date: entryDate(runById.get(c.run_id!)!), ticker: c.ticker, rank: slot.get(`${c.run_id}|${c.ticker}`) ?? c.rank, catalyst: c.catalyst, short_score: c.short_score }));
  const held = (tickers: string[]) => tickers.flatMap((t): [string, HeldCand][] => {
    const c = sel.filter((x) => x.ticker === t).sort((a, b) => (b.run_id ?? 0) - (a.run_id ?? 0))[0];
    return c ? [[t, { ...c, run_date: date.get(c.run_id!) }]] : [];
  });
  return { picks, held };
}

async function demoShorting(): Promise<Shorting> {
  const d = await loadDemo();
  const bots = await loadDemoBots();
  const recent = d.runs.slice(0, 60);
  const all = recent.flatMap((r) => d.candidates(r.id));
  const { picks, held } = picksFrom(recent, all);
  return {
    simulated: true, bot: bots[0], run: d.latestRun, runs: recent, candidates: d.candidates(d.latestRun.id),
    orders: d.orders.filter((o) => o.run_id === d.latestRun.id), snaps: d.snaps, trades: d.trades, positions: d.positions,
    account: d.account, picks, held: held(d.positions.map((p) => p.symbol)), events: [],
    bookOrders: [], syncs: [],
    alerts: [{ id: "sample-1", bot_id: "shorting", at: new Date(Date.now() - 50 * 60_000).toISOString(), level: "warning", kind: "position_loss", ticker: d.positions[0]?.symbol ?? null,
      message: "SAMPLE ALERT: position down 26% from entry", resolved_at: null }],
  };
}

export const getShorting = cache(async (): Promise<Shorting | null> => {
  if (isDemo) return demoShorting();
  const sb = await adminClient();
  if (!sb) return null;
  const [{ data: bot }, { data: runs }, { data: snaps }, { data: trades }, account, positions, events, syncs, openAlerts] = await Promise.all([
    sb.from("bots").select("*").eq("id", "shorting").maybeSingle(),
    sb.from("short_runs").select("*").order("id", { ascending: false }).limit(60),
    sb.from("equity_snapshots").select("snap_date,equity,day_pl,taken_at").eq("bot_id", "shorting").order("snap_date"),
    sb.from("short_trades").select("*").order("exit_at", { ascending: false }).limit(200),
    alpacaAccount(),
    alpacaPositions(),
    runEvents(sb),
    syncRuns(sb, "shorting"),
    openAlertRows(sb),
  ]);
  const runList = (runs ?? []) as Run[];
  const run = runList[0] ?? null;
  const ids = runList.map((r) => r.id);
  const book = currentFlow(runList) === "intraday_v2" ? v2Runs(runList, etParts().date).book : null;
  const [cands, orders, selected, bookOrders] = await Promise.all([
    run ? sb.from("short_candidates").select("*").eq("run_id", run.id).order("rank") : Promise.resolve({ data: [] }),
    run ? sb.from("short_orders").select("*").eq("run_id", run.id).order("submitted_at") : Promise.resolve({ data: [] }),
    ids.length
      ? sb.from("short_candidates").select("run_id,rank,ticker,sector,catalyst,short_score,si_pct_float,days_to_cover,flags,target_pct,selected,close,change")
          .in("run_id", ids).eq("selected", true)
      : Promise.resolve({ data: [] }),
    book && book.id !== run?.id ? sb.from("short_orders").select("*").eq("run_id", book.id).order("submitted_at") : Promise.resolve({ data: null }),
  ]);
  const pos = (positions ?? []) as Position[];
  const { picks, held } = picksFrom(runList, ((selected.data ?? []) as Record<string, unknown>[]).map(normCand));
  return {
    simulated: false,
    bot: bot as Bot | null, run, runs: runList,
    candidates: ((cands.data ?? []) as Record<string, unknown>[]).map(normCand),
    orders: ((orders.data ?? []) as Record<string, unknown>[]).map(normOrder),
    snaps: ((snaps ?? []) as Record<string, unknown>[]).map(normSnap),
    trades: ((trades ?? []) as Record<string, unknown>[]).map(normTrade),
    positions: pos, account: account as Account | null, picks, held: held(pos.map((p) => p.symbol)), events,
    bookOrders: bookOrders.data ? ((bookOrders.data ?? []) as Record<string, unknown>[]).map(normOrder) : ((orders.data ?? []) as Record<string, unknown>[]).map(normOrder),
    syncs, alerts: openAlerts,
  };
});

/** Run history: runs, their orders' statuses, and trades for next-day P&L. */
export async function getRuns() {
  if (isDemo) {
    const d = await loadDemo();
    return { simulated: true, runs: d.runs, orders: d.orders, trades: d.trades };
  }
  const sb = await adminClient();
  if (!sb) return null;
  const [{ data: runs }, { data: trades }] = await Promise.all([
    sb.from("short_runs").select("*").order("id", { ascending: false }).limit(120),
    sb.from("short_trades").select("*").order("exit_at", { ascending: false }).limit(1000),
  ]);
  const ids = (runs ?? []).map((r) => r.id);
  const { data: orders } = ids.length
    ? await sb.from("short_orders").select("id,run_id,ticker,status,purpose,submitted_at").in("run_id", ids)
    : { data: [] };
  return {
    simulated: false, runs: (runs ?? []) as Run[],
    orders: ((orders ?? []) as Record<string, unknown>[]).map(normOrder),
    trades: ((trades ?? []) as Record<string, unknown>[]).map(normTrade),
  };
}

export async function getRun(id: number) {
  if (isDemo) {
    const d = await loadDemo();
    const run = d.runs.find((r) => r.id === id) ?? null;
    if (!run) return { simulated: true, run: null, candidates: [], orders: [], trades: [], events: [] as LogLine[], prev: null, next: null };
    const i = d.runs.indexOf(run);
    return { simulated: run.id !== d.latestRun.id, run, candidates: d.candidates(id), orders: d.orders.filter((o) => o.run_id === id),
      trades: d.trades, events: [] as LogLine[], prev: d.runs[i + 1]?.id ?? null, next: d.runs[i - 1]?.id ?? null };
  }
  const sb = await adminClient();
  if (!sb) return null;
  const { data: run } = await sb.from("short_runs").select("*").eq("id", id).maybeSingle();
  if (!run) return { simulated: false, run: null, candidates: [], orders: [], trades: [], events: [] as LogLine[], prev: null, next: null };
  const [{ data: cands }, { data: orders }, { data: trades }, byRunId, events, { data: prev }, { data: next }] = await Promise.all([
    sb.from("short_candidates").select("*").eq("run_id", id).order("rank"),
    sb.from("short_orders").select("*").eq("run_id", id).order("submitted_at"),
    sb.from("short_trades").select("*").gte("entry_at", `${run.run_date}T00:00:00Z`).order("entry_at").limit(100),
    optional<Record<string, unknown>[] | null>(sb.from("short_trades").select("*").eq("run_id", id).order("entry_at"), null),
    runEvents(sb, id),
    sb.from("short_runs").select("id").lt("id", id).order("id", { ascending: false }).limit(1).maybeSingle(),
    sb.from("short_runs").select("id").gt("id", id).order("id").limit(1).maybeSingle(),
  ]);
  return {
    simulated: false, run: run as Run,
    candidates: ((cands ?? []) as Record<string, unknown>[]).map(normCand),
    orders: ((orders ?? []) as Record<string, unknown>[]).map(normOrder),
    trades: (byRunId?.length ? byRunId : ((trades ?? []) as Record<string, unknown>[])).map(normTrade),
    events, prev: (prev?.id as number | undefined) ?? null, next: (next?.id as number | undefined) ?? null,
  };
}

export async function getLucidAccounts(): Promise<LucidAccount[] | null> {
  if (isDemo) return [];
  const sb = await adminClient();
  if (!sb) return null;
  return ((await sb.from("lucid_accounts").select("*").order("account_id")).data ?? []) as LucidAccount[];
}

export type TapeItem = { ticker: string; change: number; picked: boolean };

/** Ticker tape: the latest scan's names with their move, picks flagged. */
export async function getTape(): Promise<{ date: string | null; live: boolean; items: TapeItem[] }> {
  const s = await getShorting().catch(() => null);
  if (!s || !s.run) return { date: null, live: false, items: [] };
  return { date: s.run.run_date, live: s.run.mode === "execute",
    items: s.candidates.map((c) => ({ ticker: c.ticker, change: Number(c.change), picked: !!c.selected })) };
}

/** Everything the chrome (alert banner, status bar, command bar) needs. Shares getShorting's per-request cache. */
export async function getChrome() {
  const s = await getShorting().catch(() => null);
  let email: string | null = null;
  if (!isDemo) {
    try { email = (await (await supabaseServer()).auth.getUser()).data.user?.email ?? null; } catch { email = null; }
  }
  if (!s) return { alerts: [], email, positions: null, gross: null, equity: null, lastSync: null, tickers: [] as string[], flow: "intraday_v2" as const };
  const equity = s.account ? Number(s.account.equity) : null;
  const gross = s.positions.reduce((t, p) => t + Math.abs(Number(p.market_value)), 0);
  const tickers = [...new Set([...s.positions.map((p) => p.symbol), ...s.candidates.map((c) => c.ticker), ...s.trades.slice(0, 40).map((t) => t.ticker)])];
  return {
    alerts: computeAlerts(s.bot, s.run, s.orders, new Date(), { runs: s.runs, open: s.alerts }),
    email, positions: s.account ? s.positions.length : null,
    gross: equity ? (100 * gross) / equity : null, equity,
    lastSync: s.syncs[0]?.at ?? s.bot?.last_sync_at ?? s.snaps[s.snaps.length - 1]?.taken_at ?? s.bot?.last_heartbeat ?? null,
    tickers, flow: currentFlow(s.runs),
  };
}

// ------------------------------------------------------------------ market events (005)
export type EventsData = { events: MarketEvent[]; sample: boolean; missing: boolean; from: string; to: string; updated: string | null };

/** Calendar rows from 30 days back to 90 ahead (what sync_events.py keeps filled). Missing table → empty, flagged. */
export const getEvents = cache(async (): Promise<EventsData | null> => {
  const today = etParts().date;
  const from = addDays(today, -30), to = addDays(today, 90);
  if (isDemo) {
    const s = await getShorting().catch(() => null);
    const { demoEvents } = await import("@/lib/demo-events");
    const picks = s?.candidates.filter((c) => c.selected).map((c) => c.ticker) ?? [];
    return { events: demoEvents(from, to, { held: s?.positions.map((p) => p.symbol) ?? [], picks }, today), sample: true, missing: false, from, to, updated: null };
  }
  const sb = await adminClient();
  if (!sb) return null;
  try {
    const { data, error } = await sb.from("market_events").select("*").gte("event_date", from).lte("event_date", to).order("event_date").limit(5000);
    if (error) return { events: [], sample: false, missing: true, from, to, updated: null };
    const rows = (data ?? []) as MarketEvent[];
    const updated = rows.reduce<string | null>((m, r) => (r.updated_at && (!m || r.updated_at > m) ? r.updated_at : m), null);
    return { events: rows.map((r) => ({ ...r, importance: r.importance === null ? null : Number(r.importance) })), sample: false, missing: false, from, to, updated };
  } catch {
    return { events: [], sample: false, missing: true, from, to, updated: null };
  }
});

// ------------------------------------------------------------------ live options paper bot (003)
export type OptionsLive = {
  simulated: boolean; bot: Bot | null; snaps: Snap[]; positions: OptPosition[]; runs: OptRun[]; orders: OptOrder[]; trades: OptTrade[];
  events: LogLine[]; syncs: SyncRun[]; alerts: BotAlert[]; missing: boolean;
};
const OPOS_NUM = ["short_strike", "long_strike", "qty", "entry_credit", "mark_debit", "unrealized_pl", "max_loss"];
const OTR_NUM = ["short_strike", "long_strike", "qty", "entry_credit", "exit_debit", "pnl", "pnl_pct"];
const ORUN_NUM = ["equity", "vix", "vix3m", "n_exits", "n_entries"];
const OORD_NUM = ["qty", "filled_qty", "avg_price", "attempts"];

export const getOptionsLive = cache(async (): Promise<OptionsLive | null> => {
  if (isDemo) {
    const { demoOptionsLive } = await import("@/lib/demo-options-live");
    const bots = await loadDemoBots();
    const d = demoOptionsLive(etParts().date);
    return { simulated: true, bot: { ...bots.find((b) => b.id === "options")!, status: "active", run_state: "ok", last_heartbeat: d.snaps[d.snaps.length - 1].taken_at ?? null,
      last_message: "synced (simulated)" }, ...d, events: [], syncs: [], alerts: [], missing: false };
  }
  const sb = await adminClient();
  if (!sb) return null;
  const [{ data: bot }, { data: snaps }, positions, runs, trades, events, syncs, alertRows] = await Promise.all([
    sb.from("bots").select("*").eq("id", "options").maybeSingle(),
    sb.from("equity_snapshots").select("snap_date,equity,day_pl,taken_at").eq("bot_id", "options").order("snap_date"),
    optional<Record<string, unknown>[] | null>(sb.from("options_live_positions").select("*").eq("bot_id", "options").order("expiry"), null),
    optional<Record<string, unknown>[]>(sb.from("options_live_runs").select("*").order("id", { ascending: false }).limit(20), []),
    optional<Record<string, unknown>[]>(sb.from("options_live_trades").select("*").order("closed_at", { ascending: false }).limit(100), []),
    runEvents(sb, undefined, "options"),
    syncRuns(sb, "options"),
    openAlertRows(sb),
  ]);
  const runIds = runs.slice(0, 5).map((r) => r.id as number);
  const orders = runIds.length
    ? await optional<Record<string, unknown>[]>(sb.from("options_live_orders").select("*").in("run_id", runIds).order("submitted_at", { ascending: false }), [])
    : [];
  return {
    simulated: false, bot: bot as Bot | null, missing: positions === null,
    snaps: ((snaps ?? []) as Record<string, unknown>[]).map(normSnap),
    positions: (positions ?? []).map((r) => numify(r, OPOS_NUM) as unknown as OptPosition),
    runs: runs.map((r) => numify(r, ORUN_NUM) as unknown as OptRun),
    orders: orders.map((r) => numify(r, OORD_NUM) as unknown as OptOrder),
    trades: trades.map((r) => numify(r, OTR_NUM) as unknown as OptTrade),
    events: events.filter(() => true), syncs, alerts: alertRows.filter((a) => a.bot_id === "options"),
  };
});

// ------------------------------------------------------------------ health checks (008)
/** Latest row per check and host (health_latest) plus 24 h of history. 008 not applied → missing. */
export const getHealth = cache(async (): Promise<HealthData | null> => {
  if (isDemo) {
    const { demoHealth } = await import("@/lib/demo-health");
    return demoHealth();
  }
  const sb = await adminClient();
  if (!sb) return null;
  try {
    const { data, error } = await sb.from("health_latest").select("*").limit(3000);
    if (error) return { missing: true, sample: false, latest: [], history: [], error: error.message };
    const since = new Date(Date.now() - 24 * 3600_000).toISOString();
    const history: HistPoint[] = [];
    for (let page = 0; page < 20; page++) {
      const { data: h, error: he } = await sb.from("health_checks").select("run_at,host,component,check_name,status")
        .gte("run_at", since).neq("component", "summary").order("run_at").range(page * 1000, page * 1000 + 999);
      if (he || !h) break;
      history.push(...(h as HistPoint[]));
      if (h.length < 1000) break;
    }
    return { missing: false, sample: false, latest: (data ?? []) as HealthRow[], history };
  } catch (e) {
    return { missing: true, sample: false, latest: [], history: [], error: String(e) };
  }
});

/** The chrome's health pill. Only the latest rows (no history); null when not an admin. */
export const getHealthPill = cache(async (): Promise<HealthPill | null> => {
  if (isDemo) return pillOf((await getHealth())?.latest ?? null, false);
  const sb = await adminClient().catch(() => null);
  if (!sb) return null;
  try {
    const { data, error } = await sb.from("health_latest").select("run_at,host,component,check_name,status").limit(3000);
    if (error) return pillOf(null, true);
    return pillOf((data ?? []) as HealthRow[], false);
  } catch { return null; }
});
