// Every page reads through here, so DEMO_MODE=1 can swap Supabase + Alpaca for
// a fixture when previewing without a database. Real-mode paths never touch
// lib/demo.ts; the fixture is only reached behind `isDemo`.
import "server-only";
import { cache } from "react";
import { alpacaAccount, alpacaPositions } from "@/lib/alpaca";
import { adminClient, supabaseServer } from "@/lib/supabase/server";
import type { Account, Bot, Candidate, LogLine, LucidAccount, Order, Position, Run, Snap, Trade } from "@/lib/types";
import type { Pick } from "@/lib/analytics";
import { alerts as computeAlerts } from "@/lib/analytics";

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
};

// ------------------------------------------------------------------ queries
export async function getBots(): Promise<Bot[] | null> {
  if (isDemo) return loadDemoBots();
  const sb = await adminClient();
  if (!sb) return null;
  return ((await sb.from("bots").select("*")).data ?? []) as Bot[];
}

/** Optional forward-compatible table (see docs/reviews/BOT_CHANGES_NEEDED.md). Missing table → []. */
async function runEvents(sb: NonNullable<Awaited<ReturnType<typeof adminClient>>>, runId?: number): Promise<LogLine[]> {
  try {
    let q = sb.from("run_events").select("at,level,step,message").order("at", { ascending: false }).limit(30);
    if (runId !== undefined) q = q.eq("run_id", runId);
    const { data, error } = await q;
    if (error || !data) return [];
    return data.map((e) => ({ at: String(e.at), level: (String(e.level).toUpperCase().startsWith("ERR") ? "ERROR" : String(e.level).toUpperCase().startsWith("WARN") ? "WARN" : "INFO") as LogLine["level"],
      step: String(e.step ?? "").toUpperCase(), message: String(e.message ?? "") }));
  } catch { return []; }
}

function picksFrom(runs: Run[], cands: Candidate[]): { picks: Pick[]; held: (tickers: string[]) => [string, HeldCand][] } {
  const date = new Map(runs.map((r) => [r.id, r.run_date]));
  const sel = cands.filter((c) => c.selected && c.run_id !== null && c.run_id !== undefined && date.has(c.run_id));
  // rank slot = position among that run's picks (1 = largest size), not the raw scan rank
  const slot = new Map<string, number>();
  for (const id of new Set(sel.map((c) => c.run_id!)))
    sel.filter((c) => c.run_id === id).sort((a, b) => (b.target_pct ?? 0) - (a.target_pct ?? 0) || a.rank - b.rank).forEach((c, i) => slot.set(`${id}|${c.ticker}`, i + 1));
  const picks = sel.map((c) => ({ run_id: c.run_id!, run_date: date.get(c.run_id!)!, ticker: c.ticker, rank: slot.get(`${c.run_id}|${c.ticker}`) ?? c.rank, catalyst: c.catalyst, short_score: c.short_score }));
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
  };
}

export const getShorting = cache(async (): Promise<Shorting | null> => {
  if (isDemo) return demoShorting();
  const sb = await adminClient();
  if (!sb) return null;
  const [{ data: bot }, { data: runs }, { data: snaps }, { data: trades }, account, positions, events] = await Promise.all([
    sb.from("bots").select("*").eq("id", "shorting").maybeSingle(),
    sb.from("short_runs").select("*").order("id", { ascending: false }).limit(60),
    sb.from("equity_snapshots").select("snap_date,equity,day_pl,taken_at").eq("bot_id", "shorting").order("snap_date"),
    sb.from("short_trades").select("*").order("exit_at", { ascending: false }).limit(200),
    alpacaAccount(),
    alpacaPositions(),
    runEvents(sb),
  ]);
  const runList = (runs ?? []) as Run[];
  const run = runList[0] ?? null;
  const ids = runList.map((r) => r.id);
  const [cands, orders, selected] = await Promise.all([
    run ? sb.from("short_candidates").select("*").eq("run_id", run.id).order("rank") : Promise.resolve({ data: [] }),
    run ? sb.from("short_orders").select("*").eq("run_id", run.id).order("submitted_at") : Promise.resolve({ data: [] }),
    ids.length
      ? sb.from("short_candidates").select("run_id,rank,ticker,sector,catalyst,short_score,si_pct_float,days_to_cover,flags,target_pct,selected,close,change")
          .in("run_id", ids).eq("selected", true)
      : Promise.resolve({ data: [] }),
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
  const [{ data: cands }, { data: orders }, { data: trades }, events, { data: prev }, { data: next }] = await Promise.all([
    sb.from("short_candidates").select("*").eq("run_id", id).order("rank"),
    sb.from("short_orders").select("*").eq("run_id", id).order("submitted_at"),
    sb.from("short_trades").select("*").gte("entry_at", `${run.run_date}T00:00:00Z`).order("entry_at").limit(100),
    runEvents(sb, id),
    sb.from("short_runs").select("id").lt("id", id).order("id", { ascending: false }).limit(1).maybeSingle(),
    sb.from("short_runs").select("id").gt("id", id).order("id").limit(1).maybeSingle(),
  ]);
  return {
    simulated: false, run: run as Run,
    candidates: ((cands ?? []) as Record<string, unknown>[]).map(normCand),
    orders: ((orders ?? []) as Record<string, unknown>[]).map(normOrder),
    trades: ((trades ?? []) as Record<string, unknown>[]).map(normTrade),
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
  if (!s) return { alerts: [], email, positions: null, gross: null, equity: null, lastSync: null, tickers: [] as string[] };
  const equity = s.account ? Number(s.account.equity) : null;
  const gross = s.positions.reduce((t, p) => t + Math.abs(Number(p.market_value)), 0);
  const tickers = [...new Set([...s.positions.map((p) => p.symbol), ...s.candidates.map((c) => c.ticker), ...s.trades.slice(0, 40).map((t) => t.ticker)])];
  return {
    alerts: computeAlerts(s.bot, s.run, s.orders),
    email, positions: s.account ? s.positions.length : null,
    gross: equity ? (100 * gross) / equity : null, equity,
    lastSync: s.snaps[s.snaps.length - 1]?.taken_at ?? s.bot?.last_heartbeat ?? null,
    tickers,
  };
}
