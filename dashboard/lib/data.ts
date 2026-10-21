// Every page reads through here, so DEMO_MODE=1 can swap Supabase + Alpaca for
// a captured real scan when previewing without a database.
import "server-only";
import { alpacaAccount, alpacaPositions, type AlpacaAccount, type AlpacaPosition } from "@/lib/alpaca";
import { adminClient } from "@/lib/supabase/server";
import demoScan from "@/lib/demo-scan.json";

// Never on Vercel: demo mode also switches off sign-in.
export const isDemo = process.env.DEMO_MODE === "1" && !process.env.VERCEL;

export type Bot = {
  id: string; name: string; kind: string; status: string; description: string | null;
  run_state: string | null; last_message: string | null; last_heartbeat: string | null;
};
export type Snap = { snap_date: string; equity: number; day_pl: number | null };
export type Run = {
  id: number; run_date: string; mode: string; status: string; started_at: string | null;
  n_candidates: number | null; n_selected: number | null; message: string | null;
};
export type Candidate = {
  rank: number; ticker: string; company: string | null; sector: string | null; close: number; change: number;
  market_cap: number; rvol: number | null; upper_wick: number | null; close_in_range: number | null;
  si_pct_float: number | null; days_to_cover: number | null; short_vol_ratio_latest: number | null;
  ftd_days: number | null; shortable: boolean | null; easy_to_borrow: boolean | null; catalyst: string | null;
  headlines: string[] | null; short_score: number; components: Record<string, number | null> | null;
  flags: string[] | null; exclude_reason: string | null; selected: boolean; target_pct: number | null; target_qty: number | null;
};
export type Order = {
  id: string; ticker: string; side: string; qty: number; purpose: string; status: string;
  submitted_at: string | null; filled_avg_price: number | null; filled_at: string | null; error: string | null;
};
export type Trade = {
  id: string; ticker: string; side: string; qty: number; entry_at: string; entry_price: number;
  exit_at: string; exit_price: number; pnl: number; pnl_pct: number;
};
export type LucidAccount = {
  account_id: string; label: string | null; plan: string | null; connection: string | null; balance: number | null;
  day_pl: number | null; max_loss_floor: number | null; profit_target: number | null; open_position: string | null; updated_at: string | null;
};

// ------------------------------------------------------------------ demo fixture
const DEMO_BOTS: Bot[] = [
  { id: "shorting", name: "Stock Shorting Bot", kind: "shorting", status: "active",
    description: "Shorts the top 5 end-of-day gainer fades on Alpaca paper at 3:55 PM ET.",
    run_state: "ok", last_message: "dry run, 5 picks", last_heartbeat: new Date().toISOString() },
  { id: "lucid", name: "Lucid Bot", kind: "lucid", status: "inactive",
    description: "Pine Script alerts → CrossTrade webhook → Tradovate funded accounts.",
    run_state: null, last_message: null, last_heartbeat: null },
  { id: "options", name: "Options Bot", kind: "options", status: "planned", description: "Future project.",
    run_state: null, last_message: null, last_heartbeat: null },
];
const demoCandidates = demoScan.candidates as unknown as Candidate[];
const demoEquity = demoScan.equity;
const DEMO_RUN: Run = { id: 0, run_date: "2026-10-20", mode: "dry_run", status: "dry_run_done",
  started_at: "2026-10-20T19:45:00Z", n_candidates: demoCandidates.length,
  n_selected: demoCandidates.filter((c) => c.selected).length, message: null };
const demoPositions: AlpacaPosition[] = demoCandidates.filter((c) => c.selected).map((c) => {
  const qty = Math.abs(c.target_qty ?? 0);
  return { symbol: c.ticker, qty: String(-qty), side: "short", avg_entry_price: String(c.close),
    current_price: String(c.close), market_value: String(-qty * c.close), unrealized_pl: "0",
    unrealized_plpc: "0", unrealized_intraday_pl: "0" };
});

// ------------------------------------------------------------------ queries
export async function getBots(): Promise<Bot[] | null> {
  if (isDemo) return DEMO_BOTS;
  const sb = await adminClient();
  if (!sb) return null;
  return ((await sb.from("bots").select("*")).data ?? []) as Bot[];
}

export async function getShorting() {
  if (isDemo) {
    return {
      bot: DEMO_BOTS[0], run: DEMO_RUN, candidates: demoCandidates, orders: [] as Order[], snaps: [] as Snap[],
      trades: [] as Trade[], positions: demoPositions,
      account: { equity: String(demoEquity), last_equity: String(demoEquity), cash: String(demoEquity),
        buying_power: "0", short_market_value: "0" } as AlpacaAccount,
    };
  }
  const sb = await adminClient();
  if (!sb) return null;
  const [{ data: bot }, { data: run }, { data: snaps }, { data: trades }, account, positions] = await Promise.all([
    sb.from("bots").select("*").eq("id", "shorting").maybeSingle(),
    sb.from("short_runs").select("*").order("id", { ascending: false }).limit(1).maybeSingle(),
    sb.from("equity_snapshots").select("snap_date,equity,day_pl").eq("bot_id", "shorting").order("snap_date"),
    sb.from("short_trades").select("*").order("exit_at", { ascending: false }).limit(200),
    alpacaAccount(),
    alpacaPositions(),
  ]);
  const [cands, orders] = run
    ? await Promise.all([
        sb.from("short_candidates").select("*").eq("run_id", run.id).order("rank"),
        sb.from("short_orders").select("*").eq("run_id", run.id).order("submitted_at"),
      ])
    : [{ data: [] }, { data: [] }];
  return {
    bot: bot as Bot | null, run: run as Run | null, candidates: (cands.data ?? []) as Candidate[],
    orders: (orders.data ?? []) as Order[],
    snaps: (snaps ?? []).map((s) => ({ ...s, equity: Number(s.equity), day_pl: s.day_pl === null ? null : Number(s.day_pl) })) as Snap[],
    trades: (trades ?? []).map((t) => ({ ...t, pnl: Number(t.pnl), pnl_pct: Number(t.pnl_pct) })) as Trade[],
    positions: positions ?? [], account,
  };
}

export async function getLucidAccounts(): Promise<LucidAccount[] | null> {
  if (isDemo) return [];
  const sb = await adminClient();
  if (!sb) return null;
  return ((await sb.from("lucid_accounts").select("*").order("account_id")).data ?? []) as LucidAccount[];
}

/** Ticker tape: today's scanned names with their move, picks flagged. */
export async function getTape(): Promise<{ ticker: string; change: number; picked: boolean }[]> {
  if (isDemo) return demoCandidates.map((c) => ({ ticker: c.ticker, change: Number(c.change), picked: c.selected }));
  const sb = await adminClient();
  if (!sb) return [];
  const { data: run } = await sb.from("short_runs").select("id").order("id", { ascending: false }).limit(1).maybeSingle();
  if (!run) return [];
  const { data } = await sb.from("short_candidates").select("ticker,change,selected").eq("run_id", run.id).order("rank");
  return (data ?? []).map((c) => ({ ticker: c.ticker, change: Number(c.change), picked: !!c.selected }));
}
