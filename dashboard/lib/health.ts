// Health tab (/health): types, labels, fix hints and the pure derivations the page and the
// chrome pill share. Rows come from health/check.py via the health_checks table (008).
import { etParts, isTradingDay } from "@/lib/time";

export type HStatus = "ok" | "warn" | "fail" | "unknown";
export type HealthRow = {
  id?: number | string; run_at: string; host: string | null; component: string; check_name: string; status: HStatus;
  message: string | null; detail: Record<string, unknown> | null; expected_at: string | null; observed_at: string | null;
};
export type HistPoint = { run_at: string; host: string | null; component: string; check_name: string; status: HStatus };
export type SlotStatus = "OK" | "LATE" | "MISSED" | "FAILED" | "PENDING";
export type Slot = {
  bot: string; job: string; label: string; date: string; expected_at: string; observed_at: string | null;
  status: SlotStatus; delay_min: number | null; note: string; sources?: string[];
};
export type HealthData = { missing: boolean; sample: boolean; latest: HealthRow[]; history: HistPoint[]; error?: string | null };
export type HealthPill = { status: HStatus; fails: number; warns: number; stale: boolean; missing: boolean };

export const COMPONENTS: { id: string; label: string; code: string }[] = [
  { id: "scheduler", label: "Scheduler", code: "SCHD" },
  { id: "positions", label: "Positions", code: "POS" },
  { id: "accounts", label: "Accounts", code: "ACCT" },
  { id: "data", label: "Data sources", code: "DATA" },
  { id: "state", label: "State", code: "STATE" },
  { id: "supabase", label: "Supabase / Dashboard", code: "DB" },
  { id: "host", label: "Host", code: "HOST" },
];

const RANK: Record<HStatus, number> = { ok: 0, unknown: 1, warn: 2, fail: 3 };
export const worst = (s: HStatus[]): HStatus => (s.length ? s.reduce((a, b) => (RANK[b] > RANK[a] ? b : a), "ok" as HStatus) : "unknown");
export const pillCls = (s: HStatus) => (s === "fail" ? "bad" : s === "warn" ? "warn" : s === "ok" ? "ok" : "off");
export const statusText = (s: HStatus) => (s === "fail" ? "FAIL" : s === "warn" ? "WARN" : s === "ok" ? "OK" : "?");

const LABELS: Record<string, string> = {
  "shorting.open": "Shorting 09:31 open", "shorting.monitor": "Shorting monitor (15 min)", "shorting.close": "Shorting 15:40 close / cover",
  "shorting.fallback": "Shorting 15:50 fallback", "shorting.leftovers": "Shorting cover-leftovers", "shorting.sync": "Shorting 16:15 sync",
  "options.sweep": "Options 09:45 sweep", "options.intraday": "Options intraday (15 min)", "options.daily": "Options 15:45 decision",
  "options.sync": "Options 16:15 sync", "events.sync": "Events 07:00 sync", timeline: "All runs, 6 sessions",
  "shorting.leftover_shorts": "Leftover shorts", "shorting.longs_window": "Longs vs sleeve window", "shorting.gross": "Gross vs caps",
  "shorting.open_orders": "Open orders after 15:58", "options.expiry": "Spreads near expiry", "options.unpaired": "Unpaired legs",
  "shorting.account": "Shorting paper account", "options.account": "Options paper account",
  tradingview: "TradingView scanner", finra_si: "FINRA short interest", finra_shvol: "FINRA short volume", sec_ftd: "SEC fails-to-deliver",
  yahoo_rss: "Yahoo RSS news", fmp: "FMP news", alpaca_bars: "Alpaca SPY bars", alpaca_options: "Alpaca option chain",
  cboe_vix: "Cboe VIX / VIX3M", nasdaq_earnings: "Nasdaq earnings", nasdaq_econ: "Nasdaq economic", fed_calendar: "Fed calendar",
  "shorting.last_execute": "15:40 guard file", "shorting.last_open": "09:31 guard file", "shorting.next_open_picks": "Next-open picks",
  "shorting.locks": "Lock files", "shorting.overnight_longs": "Long sleeve file", "options.account_pin": "Options account pin",
  "supabase.reachable": "Supabase API", "supabase.migrations": "Migrations 004–008", "supabase.market_events": "market_events fresh",
  "dashboard.site": "Dashboard site", disk: "Disk", clock: "Clock", python: "Python", sleep_setting: "Sleep setting",
  wake_schedule: "Wake schedule", sleep_today: "Asleep today", schedule_entries: "Cron / timers", uptime: "Uptime", alive: "Probe heartbeat",
};
export const checkLabel = (name: string) => LABELS[name] ?? name.replace(/[._]/g, " ");

const SHORT_DIR = "cd 'Stock Shorting Bot'";
const OPT_DIR = "cd 'Options Bot'";
const SLEEP_FIX = "Mac asleep? Keep it awake on a schedule: sudo pmset repeat wakeorpoweron MTWRF 09:20:00, sudo pmset -a disablesleep 1 (a closed lid no longer sleeps), keep it on power, or move the jobs to the VPS (deploy/README).";

/** What to do about a failing / warning check, in one or two sentences. */
export function fixHint(r: HealthRow): string {
  const n = r.check_name, c = r.component, m = (r.message ?? "").toLowerCase();
  if (c === "health" && n === "alive") return "The probe is not reporting: the host is asleep or offline, or its cron line is missing (health/README.md). " + SLEEP_FIX;
  if (c === "scheduler") {
    const base = m.includes("missed") ? SLEEP_FIX : "Read the job's log (tail -50 logs/cron.log). NameResolutionError / ConnectionError means the host had no network at that minute.";
    if (n === "shorting.close") return `${base} Cover the shorts now (market hours): ${SHORT_DIR} && python3 daily.py --cover-leftovers --execute. Outside hours the 09:31 open job covers leftovers first.`;
    if (n === "shorting.fallback") return m.includes("not scheduled") ? "Add the 15:50 cron line: daily.py --execute --fallback (Stock Shorting Bot README)." : base;
    if (n === "options.daily") return `${base} The day's entry decision was skipped; nothing to undo. Re-run before 15:58 only if you want today's entry: ${OPT_DIR} && python3 live/run.py --execute --now.`;
    if (n === "shorting.sync" || n === "options.sync") return `${base} Re-sync any time: ${n.startsWith("shorting") ? SHORT_DIR + " && python3 sync.py" : OPT_DIR + " && python3 live/sync.py"}.`;
    if (n === "events.sync") return `${base} Re-run: cd events && python3 sync_events.py.`;
    return base;
  }
  switch (n) {
    case "shorting.leftover_shorts": return `v2 never holds a short overnight. In market hours run ${SHORT_DIR} && python3 daily.py --cover-leftovers --execute; before the open, the 09:31 job covers leftovers first (check it ran).`;
    case "shorting.longs_window": return "Longs should be sold by 09:50. Check logs/overnight_longs.json and the 09:31 log; sell the stray longs in the Alpaca paper dashboard if the open job failed.";
    case "shorting.gross": return "Gross above the cap means positions were not covered or have run up. Cover the leftovers first; then see the Risk panel on /shorting.";
    case "shorting.open_orders": return "Nothing should be working after 15:58. Cancel the orders in the Alpaca paper dashboard (Orders), then check the 15:40 job's log.";
    case "options.expiry": return `Close it before the bell: ${OPT_DIR} && python3 live/run.py --sweep --execute, or python3 live/run.py --close-all --execute in an emergency.`;
    case "options.unpaired": return "A leg filled without its pair or was assigned. Close the naked leg in the Alpaca options paper account, then reconcile live/state/positions.json.";
    case "shorting.account": case "options.account": return "Open app.alpaca.markets (paper) and check the account. A 401 means the keys in that bot's .env were regenerated.";
    case "shorting.last_execute": return `The 15:40 job never started that day. If shorts are still open: ${SHORT_DIR} && python3 daily.py --cover-leftovers --execute (market hours).`;
    case "shorting.last_open": return "Written only when the 09:31 job sends orders. With no saved picks this is expected; otherwise read the 09:31 lines in logs/cron.log.";
    case "shorting.next_open_picks": return "No picks for the next open: the 15:45 scan did not run. The 09:31 job then enters nothing (safe); the next 15:40 run scans again.";
    case "shorting.locks": return "A crashed run left its lock. Make sure nothing runs (pgrep -fl daily.py), then delete the .lock file in Stock Shorting Bot/logs.";
    case "shorting.overnight_longs": return "The sleeve file and the account disagree. Compare logs/overnight_longs.json with the Alpaca positions; the 09:31 job sells what the file lists.";
    case "options.account_pin": return r.status === "fail"
      ? "The OPT keys point at another account, so the options bot refuses to trade. Fix Options Bot/.env, or delete live/state/account_pin.json if the switch is intended."
      : "Normal after switching the options account: the next --execute run writes the pin.";
    case "supabase.reachable": return "Supabase is down or the service key changed (Stock Shorting Bot/.env). The bots keep trading; only the dashboard goes stale.";
    case "supabase.migrations": return "Apply the missing migration in the Supabase SQL editor: dashboard/supabase/migrations/00N_*.sql (named in the message).";
    case "supabase.market_events": return "Run cd events && python3 sync_events.py and check its 07:00 cron line.";
    case "dashboard.site": return "Check the latest Netlify deploy (netlify status / the Deploys page).";
    case "disk": return "Free space: clear ~/Library/Caches, old .cache folders under the bots, or Time Machine local snapshots (tmutil listlocalsnapshots /).";
    case "clock": return "Resync the clock: sudo sntp -sS time.apple.com (macOS) or timedatectl set-ntp true (Linux).";
    case "sleep_setting": case "wake_schedule": case "sleep_today": return SLEEP_FIX;
    case "schedule_entries": return "Add the missing lines with crontab -e (each bot's README lists them; the health line is in health/README.md), or enable the VPS timers.";
    case "cboe_vix": return "The options kill switch fails closed on a stale VIX: no entries until the Cboe quote is fresh. Usually clears by itself.";
    case "finra_si": return "FINRA publishes short interest twice a month with ~8 business days lag; a few weeks old is normal.";
  }
  if (c === "data") return "The source is down, slow or rate-limiting. The bots retry and degrade; if it lasts more than a day, check the URL and headers in the bot's source module.";
  return "See the check's detail below.";
}

/** Rows that belong to the probe's health tab (summary rows are the probe's own roll-up). */
export const checkRows = (rows: HealthRow[]) => rows.filter((r) => r.component !== "summary");

/** Probe cadence: every 15 min 09:00–16:45 ET Mon–Fri, plus 07:15 and 17:00. */
export function probeStale(alive: HealthRow | undefined, now = new Date()): { stale: boolean; status: HStatus; minutes: number | null } {
  if (!alive) return { stale: true, status: "fail", minutes: null };
  const mins = (now.getTime() - new Date(alive.run_at).getTime()) / 60000;
  const p = etParts(now);
  const weekday = isTradingDay(p.date) || (p.dow >= 1 && p.dow <= 5);
  const window = weekday && p.mins >= 9 * 60 + 15 && p.mins <= 17 * 60 + 15;
  const limit = window ? 35 : weekday ? 16 * 60 + 30 : 72 * 60;
  return { stale: mins > limit, status: mins > limit ? (window ? "fail" : "warn") : "ok", minutes: Math.round(mins) };
}

export function hostsOf(rows: HealthRow[]): string[] {
  return [...new Set(rows.map((r) => r.host ?? "unknown"))].sort();
}

/** Display order for "what's broken": the expensive consequences first. */
const PRIORITY = ["shorting.close", "shorting.leftover_shorts", "options.expiry", "options.unpaired", "shorting.open_orders", "alive",
  "options.daily", "shorting.open", "shorting.fallback", "shorting.longs_window", "shorting.account", "options.account", "options.account_pin"];
export function byPriority(a: HealthRow, b: HealthRow) {
  const pa = PRIORITY.indexOf(a.check_name), pb = PRIORITY.indexOf(b.check_name);
  const ra = RANK[b.status] - RANK[a.status];
  if (ra) return ra;
  if (pa !== pb) return (pa < 0 ? 99 : pa) - (pb < 0 ? 99 : pb);
  const ca = COMPONENTS.findIndex((c) => c.id === a.component), cb = COMPONENTS.findIndex((c) => c.id === b.component);
  return ca - cb || a.check_name.localeCompare(b.check_name);
}

/** The synthetic row the dashboard adds when the probe itself went quiet. */
export function staleRow(alive: HealthRow | undefined, host: string, now = new Date()): HealthRow | null {
  const s = probeStale(alive, now);
  if (!s.stale) return null;
  const since = alive ? `${s.minutes! >= 120 ? `${Math.round(s.minutes! / 60)} h` : `${s.minutes} min`}` : "ever";
  return {
    run_at: alive?.run_at ?? new Date(0).toISOString(), host, component: "health", check_name: "alive", status: s.status,
    message: alive ? `Health probe on ${host} silent for ${since} (last run ${new Date(alive.run_at).toLocaleString("en-US", { timeZone: "America/New_York", hourCycle: "h23", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })} ET): the host may be asleep or offline`
      : `No health probe run recorded on ${host}`,
    detail: null, expected_at: null, observed_at: alive?.run_at ?? null,
  };
}

/** Latest rows plus a stale-probe row per host; what every view below reads. */
export function effectiveRows(latest: HealthRow[], now = new Date()): HealthRow[] {
  const out: HealthRow[] = checkRows(latest).map((r) => ({ ...r, host: r.host ?? "unknown" }));
  for (const h of hostsOf(out)) {
    const alive = out.find((r) => r.host === h && r.component === "health" && r.check_name === "alive");
    const s = staleRow(alive, h, now);
    if (s) { const i = out.indexOf(alive!); if (i >= 0) out[i] = s; else out.push(s); }
  }
  return out;
}

export function pillOf(latest: HealthRow[] | null, missing: boolean, now = new Date()): HealthPill | null {
  if (missing) return { status: "unknown", fails: 0, warns: 0, stale: false, missing: true };
  if (!latest) return null;
  if (!latest.length) return { status: "unknown", fails: 0, warns: 0, stale: true, missing: false };
  const rows = effectiveRows(latest, now).filter((r) => r.check_name !== "timeline");
  const fails = rows.filter((r) => r.status === "fail").length, warns = rows.filter((r) => r.status === "warn").length;
  return { status: worst(rows.map((r) => r.status)), fails, warns, stale: rows.some((r) => r.check_name === "alive" && r.status !== "ok"), missing: false };
}

/** 24 h heat strip per check: worst status per 15-minute bucket, oldest first. "" = no run. */
export function heatStrips(history: HistPoint[], now = new Date(), buckets = 96): Map<string, HStatus[]> {
  const end = now.getTime(), span = 24 * 3600_000, w = span / buckets;
  const m = new Map<string, (HStatus | "")[]>();
  for (const h of history) {
    if (h.component === "summary") continue;
    const i = Math.floor((new Date(h.run_at).getTime() - (end - span)) / w);
    if (i < 0 || i >= buckets) continue;
    const k = `${h.host ?? "unknown"}|${h.component}|${h.check_name}`;
    if (!m.has(k)) m.set(k, Array(buckets).fill(""));
    const arr = m.get(k)!;
    const cur = arr[i];
    if (!cur || RANK[h.status] > RANK[cur as HStatus]) arr[i] = h.status;
  }
  return m as Map<string, HStatus[]>;
}

export const slotsOf = (r: HealthRow | undefined): Slot[] => ((r?.detail as { slots?: Slot[] } | null)?.slots ?? []);
export const REPEATING = new Set(["monitor", "intraday", "leftovers"]);
export const BOT_NAME: Record<string, string> = { shorting: "SHORTING", options: "OPTIONS", events: "EVENTS" };
