// Pure calculations over the existing tables: health, today status, risk and
// performance. No I/O, so the same code runs on real and demo data.
import type { Account, Alert, Bot, Candidate, DatePt, LogLine, Order, Position, Run, Snap, Trade } from "@/lib/types";
import { etDateOf, heartbeatAge, usd, pct, etHm, dayLabel, md } from "@/lib/format";
import { etParts, isHoliday, isTradingDay, schedule, nextRun, hhmm, prevTradingDay } from "@/lib/time";

const RUN_STATUS_LABEL: Record<string, string> = { dry_run_done: "DONE", executed: "EXECUTED", executed_with_errors: "EXECUTED WITH ERRORS", scanned: "SCANNED", failed: "FAILED" };

const SEV = { ok: 0, off: 0, warn: 1, bad: 2 } as const;
export type Health = { label: string; cls: "ok" | "warn" | "bad" | "off" };

/**
 * Bot health = the worse of (latest short_runs row) and (bots heartbeat).
 * The run row is the run truth: sync.py's heartbeat("ok") can overwrite
 * bots.run_state after a bad run, so a failed run still shows FAILED here.
 */
export function botHealth(bot: Bot, run: Run | null, now = new Date()): Health {
  if (bot.status === "planned") return { label: "PLANNED", cls: "off" };
  if (bot.status === "inactive") return { label: "INACTIVE", cls: "off" };
  const out: Health[] = [];
  const hb = heartbeatAge(bot.last_heartbeat, now);
  if (hb.stale) out.push({ label: hb.label === "NEVER" ? "NO RUN YET" : `NO RUN · LAST ${hb.label}`, cls: "bad" });
  if (run) {
    const today = etParts(now).date;
    const recent = run.run_date >= prevTradingDay(today);
    if (run.status === "failed" && recent) out.push({ label: "RUN FAILED", cls: "bad" });
    else if (run.status === "executed_with_errors" && recent) out.push({ label: "CHECK ORDERS", cls: "warn" });
  }
  if (bot.run_state === "error") out.push({ label: "ERROR", cls: "bad" });
  else if (bot.run_state === "warning") out.push({ label: "CHECK ORDERS", cls: "warn" });
  else if (bot.run_state === "running") out.push({ label: "RUNNING", cls: "warn" });
  if (!out.length) return { label: "HEALTHY", cls: "ok" };
  return out.sort((a, b) => SEV[b.cls] - SEV[a.cls])[0];
}

export const isLive = (run: Run | null) => !!run && run.mode === "execute";

// ------------------------------------------------------------------ today
export type Today = {
  tone: "ok" | "warn" | "bad" | "off" | "idle";
  head: string;           // "TODAY TUE 10/21"
  state: string;          // "EXECUTED 15:55"
  parts: string[];        // extra facts
  countdownTo?: string;   // ISO, rendered live by the client
  countdownLabel?: string;
  dayPl: number | null; dayPct: number | null;
  rerun?: string;
};

export function todayState(run: Run | null, orders: Order[], account: Account | null, now = new Date()): Today {
  const p = etParts(now);
  const head = `TODAY ${dayLabel(p.date)}`;
  const eq = account ? Number(account.equity) : null, last = account ? Number(account.last_equity) : null;
  const dayPl = eq !== null && last !== null ? eq - last : null;
  const dayPct = dayPl !== null && last ? (100 * dayPl) / last : null;
  const base = { head, dayPl, dayPct };
  const nr = nextRun(now);
  const lastRun = run ? `LAST RUN ${dayLabel(run.run_date)} · ${run.mode === "execute" ? "LIVE" : "DRY RUN"} · ${RUN_STATUS_LABEL[run.status] ?? run.status.replace(/_/g, " ").toUpperCase()}` : "NO RUNS RECORDED";

  if (!isTradingDay(p.date)) {
    return { ...base, tone: "off", state: isHoliday(p.date) ? "MARKET HOLIDAY" : "WEEKEND · MARKET CLOSED",
      parts: [lastRun], countdownTo: nr.at.toISOString(), countdownLabel: `NEXT ${nr.kind} ${dayLabel(nr.date)} IN` };
  }
  const s = schedule(p.date);
  if (run && run.run_date === p.date) {
    const filled = orders.filter((o) => o.status === "filled").length;
    const bad = orders.filter((o) => ["rejected", "canceled", "expired"].includes(o.status)).length;
    const opens = orders.filter((o) => o.purpose === "short" || o.purpose === "add_short");
    const t = orders.map((o) => o.submitted_at).filter(Boolean).sort()[0] ?? run.started_at;
    switch (run.status) {
      case "failed":
        return { ...base, tone: "bad", state: `FAILED: ${(run.message ?? "unknown error").toUpperCase()}`, parts: [`STARTED ${etHm(run.started_at)}`],
          rerun: "cd \"Stock Shorting Bot\" && python daily.py --execute --now" };
      case "dry_run_done":
        return { ...base, tone: "warn", state: `DRY RUN ONLY · SCANNED ${etHm(run.started_at)}`,
          parts: [`${run.n_selected ?? 0} WOULD SHORT`, "NO ORDERS SENT"] };
      case "scanned":
        return { ...base, tone: "warn", state: `SCANNED ${etHm(run.started_at)} · AWAITING TRADE ${hhmm(s.trade)}`,
          parts: [`${run.n_selected ?? 0} PICKS`], countdownTo: nr.kind === "TRADE" ? nr.at.toISOString() : undefined, countdownLabel: "TRADE IN" };
      case "executed":
      case "executed_with_errors":
        return { ...base, tone: bad ? "warn" : "ok", state: `EXECUTED ${etHm(t)}`,
          parts: [`${filled}/${orders.length || run.n_orders || 0} FILLED`, `${opens.length || run.n_selected || 0} SHORTS`, ...(bad ? [`${bad} REJECTED`] : [])] };
      default:
        return { ...base, tone: "warn", state: run.status.toUpperCase(), parts: [] };
    }
  }
  if (p.mins < s.scan) {
    return { ...base, tone: "idle", state: `SCAN ${hhmm(s.scan)} · TRADE ${hhmm(s.trade)}`, parts: [lastRun],
      countdownTo: nr.at.toISOString(), countdownLabel: `${nr.kind} IN` };
  }
  if (p.mins < s.scan + 10) return { ...base, tone: "warn", state: "SCAN DUE · WAITING FOR THE BOT", parts: [lastRun] };
  return { ...base, tone: "bad", state: `NO RUN TODAY (EXPECTED ${hhmm(s.scan)})`, parts: [lastRun],
    rerun: "cd \"Stock Shorting Bot\" && python daily.py --execute --now" };
}

/** 4-step day timeline: Scan → Trade → Cutoff → Sync. */
export type Step = { key: string; label: string; at: string; state: "done" | "pending" | "failed" | "skipped" | "late"; actual: string | null };
export function dayTimeline(run: Run | null, orders: Order[], snaps: Snap[], now = new Date()): Step[] {
  const p = etParts(now);
  const day = isTradingDay(p.date) ? p.date : null;
  const s = schedule(day ?? p.date);
  const todayRun = run && day && run.run_date === day ? run : null;
  const late = (m: number) => (day && p.mins > m + 10 ? "late" : "pending") as Step["state"];
  const subs = orders.map((o) => o.submitted_at).filter(Boolean).sort() as string[];
  const snapToday = snaps.find((x) => x.snap_date === day);
  const steps: Step[] = [
    { key: "scan", label: "SCAN", at: hhmm(s.scan), actual: todayRun ? etHm(todayRun.started_at) : null,
      state: todayRun ? (todayRun.status === "failed" && !todayRun.n_candidates ? "failed" : "done") : late(s.scan) },
    { key: "trade", label: "TRADE", at: hhmm(s.trade), actual: subs[0] ? etHm(subs[0]) : null,
      state: !todayRun ? late(s.trade) : todayRun.mode !== "execute" ? "skipped"
        : todayRun.status === "failed" ? "failed" : subs.length ? "done" : late(s.trade) },
    { key: "cutoff", label: "CUTOFF", at: hhmm(s.cutoff), actual: subs.length ? etHm(subs[subs.length - 1]) : null,
      state: !todayRun ? late(s.cutoff) : todayRun.mode !== "execute" ? "skipped"
        : todayRun.status === "failed" ? "failed" : subs.length ? "done" : late(s.cutoff) },
    { key: "sync", label: "SYNC", at: hhmm(s.sync), actual: snapToday?.taken_at ? etHm(snapToday.taken_at) : snapToday ? "OK" : null,
      state: snapToday ? "done" : late(s.sync) },
  ];
  if (!day) return steps.map((x) => ({ ...x, state: "skipped" }));
  return steps;
}

// ------------------------------------------------------------------ alerts + log
export function alerts(bot: Bot | null, run: Run | null, orders: Order[], now = new Date()): Alert[] {
  const out: Alert[] = [];
  const p = etParts(now);
  if (run && run.status === "failed" && run.run_date >= prevTradingDay(p.date))
    out.push({ level: "bad", text: `RUN FAILED ${dayLabel(run.run_date)}: ${(run.message ?? "unknown error").toUpperCase()}`, href: `/shorting/runs/${run.id}` });
  const rej = orders.filter((o) => ["rejected", "canceled", "expired"].includes(o.status));
  if (run && rej.length) out.push({ level: "bad", text: `${rej.length} ORDER${rej.length > 1 ? "S" : ""} ${rej.map((o) => o.status.toUpperCase()).filter((v, i, a) => a.indexOf(v) === i).join("/")} · ${rej.map((o) => o.ticker).join(" ")}`, href: `/shorting/runs/${run.id}` });
  if (bot && bot.status === "active") {
    const hb = heartbeatAge(bot.last_heartbeat, now);
    if (hb.stale) out.push({ level: "bad", text: `STALE HEARTBEAT · BOT LAST SEEN ${hb.label}`, href: "/shorting" });
  }
  if (isTradingDay(p.date) && p.mins > schedule(p.date).scan + 10 && (!run || run.run_date !== p.date))
    out.push({ level: "warn", text: `NO RUN TODAY · EXPECTED ${hhmm(schedule(p.date).scan)} ET`, href: "/shorting" });
  return out;
}

/** System log synthesized from runs, orders, snapshots and heartbeat (plus run_events when it exists). */
export function systemLog(bot: Bot | null, runs: Run[], orders: Order[], snaps: Snap[], events: LogLine[] = []): LogLine[] {
  const out: LogLine[] = [...events];
  for (const r of runs.slice(0, 6)) {
    if (!r.started_at) continue;
    const lvl = r.status === "failed" ? "ERROR" : r.status === "executed_with_errors" ? "WARN" : "INFO";
    out.push({ at: r.started_at, level: lvl, step: "RUN",
      message: `${r.run_date} ${r.mode === "execute" ? "LIVE" : "DRY"} ${(RUN_STATUS_LABEL[r.status] ?? r.status.replace(/_/g, " ")).toLowerCase()} · ${r.n_selected ?? 0}/${r.n_candidates ?? 0} picked${r.n_orders ? ` · ${r.n_orders} orders` : ""}${r.message ? ` · ${r.message}` : ""}` });
  }
  for (const o of orders) {
    if (!o.submitted_at) continue;
    const bad = ["rejected", "canceled", "expired"].includes(o.status);
    if (bad || o.error) out.push({ at: o.submitted_at, level: "ERROR", step: "ORDER", message: `${o.purpose} ${o.ticker} ${o.qty} ${o.status}${o.error ? `: ${o.error}` : ""}` });
  }
  const lastSnap = snaps[snaps.length - 1];
  if (lastSnap?.taken_at) out.push({ at: lastSnap.taken_at, level: "INFO", step: "SYNC", message: `equity ${usd(lastSnap.equity)} · day ${usd(lastSnap.day_pl, true)}` });
  if (bot?.last_heartbeat) out.push({ at: bot.last_heartbeat, level: bot.run_state === "error" ? "ERROR" : bot.run_state === "warning" ? "WARN" : "INFO", step: "BEAT", message: bot.last_message ?? bot.run_state ?? "heartbeat" });
  return out.sort((a, b) => b.at.localeCompare(a.at)).slice(0, 14);
}

// ------------------------------------------------------------------ series
export const equitySeries = (snaps: Snap[]): DatePt[] => snaps.map((s) => ({ date: s.snap_date, value: s.equity }));

export function drawdown(eq: DatePt[]): DatePt[] {
  let peak = -Infinity;
  return eq.map((p) => { peak = Math.max(peak, p.value); return { date: p.date, value: peak > 0 ? p.value / peak - 1 : 0 }; });
}

/** Month returns (fraction) from an equity curve, chained month-end to month-end. */
export function monthlyReturns(eq: DatePt[]): { month: string; ret: number }[] {
  const out: { month: string; ret: number }[] = [];
  let prevEnd: number | null = eq[0]?.value ?? null;
  for (let i = 0; i < eq.length; i++) {
    const m = eq[i].date.slice(0, 7);
    const isEnd = i === eq.length - 1 || eq[i + 1].date.slice(0, 7) !== m;
    if (isEnd && prevEnd) { out.push({ month: m, ret: eq[i].value / prevEnd - 1 }); prevEnd = eq[i].value; }
  }
  return out;
}

// ------------------------------------------------------------------ performance
export type Perf = {
  days: number; maxDd: number | null; maxDdDate: string | null; curDd: number | null; sharpe: number | null; sortino: number | null;
  best: { date: string; v: number } | null; worst: { date: string; v: number } | null;
  trades: number; wins: number; winRate: number | null; pf: number | null; avgWin: number | null; avgLoss: number | null;
  avgPct: number | null; totalPnl: number; worstTrade: Trade | null; bestTrade: Trade | null;
};

export function performance(snaps: Snap[], trades: Trade[]): Perf {
  const eq = equitySeries(snaps);
  const dd = drawdown(eq);
  let maxDd: number | null = null, maxDdDate: string | null = null;
  for (const d of dd) if (maxDd === null || d.value < maxDd) { maxDd = d.value; maxDdDate = d.date; }
  const rets: number[] = [];
  let best: Perf["best"] = null, worst: Perf["worst"] = null;
  for (let i = 1; i < snaps.length; i++) {
    const pl = snaps[i].day_pl;
    if (pl === null || !snaps[i - 1].equity) continue;
    rets.push(pl / snaps[i - 1].equity);
    if (!best || pl > best.v) best = { date: snaps[i].snap_date, v: pl };
    if (!worst || pl < worst.v) worst = { date: snaps[i].snap_date, v: pl };
  }
  const mean = rets.length ? rets.reduce((a, b) => a + b, 0) / rets.length : 0;
  const sd = rets.length > 1 ? Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (rets.length - 1)) : 0;
  const dsd = rets.length > 1 ? Math.sqrt(rets.reduce((a, b) => a + Math.min(0, b) ** 2, 0) / (rets.length - 1)) : 0;
  const winsT = trades.filter((t) => t.pnl > 0), lossT = trades.filter((t) => t.pnl < 0);
  const gw = winsT.reduce((a, t) => a + t.pnl, 0), gl = -lossT.reduce((a, t) => a + t.pnl, 0);
  return {
    days: snaps.length, maxDd, maxDdDate, curDd: dd.length ? dd[dd.length - 1].value : null,
    sharpe: rets.length >= 5 && sd > 0 ? (mean / sd) * Math.sqrt(252) : null,
    sortino: rets.length >= 5 && dsd > 0 ? (mean / dsd) * Math.sqrt(252) : null,
    best, worst,
    trades: trades.length, wins: winsT.length, winRate: trades.length ? (100 * winsT.length) / trades.length : null,
    pf: gl > 0 ? gw / gl : winsT.length ? Infinity : null,
    avgWin: winsT.length ? gw / winsT.length : null, avgLoss: lossT.length ? -gl / lossT.length : null,
    avgPct: trades.length ? trades.reduce((a, t) => a + t.pnl_pct, 0) / trades.length : null,
    totalPnl: trades.reduce((a, t) => a + t.pnl, 0),
    worstTrade: trades.length ? trades.reduce((a, b) => (b.pnl_pct < a.pnl_pct ? b : a)) : null,
    bestTrade: trades.length ? trades.reduce((a, b) => (b.pnl_pct > a.pnl_pct ? b : a)) : null,
  };
}

export type Pick = { run_id: number; run_date: string; ticker: string; rank: number; catalyst: string | null; short_score: number };
export type Bucket = { key: string; n: number; pnl: number; avgPct: number; winRate: number };

/**
 * Best-effort trade → pick join: same ticker, and the trade's ET entry date
 * equals the run date. (short_trades has no run_id yet.)
 */
export function attribute(trades: Trade[], picks: Pick[]) {
  const idx = new Map(picks.map((p) => [`${p.run_date}|${p.ticker}`, p]));
  const joined = trades.map((t) => ({ t, p: idx.get(`${etDateOf(t.entry_at)}|${t.ticker}`) ?? null }));
  const group = (keyOf: (p: Pick) => string) => {
    const m = new Map<string, Trade[]>();
    for (const { t, p } of joined) if (p) { const k = keyOf(p); m.set(k, [...(m.get(k) ?? []), t]); }
    return [...m.entries()].map(([key, ts]): Bucket => ({
      key, n: ts.length, pnl: ts.reduce((a, t) => a + t.pnl, 0),
      avgPct: ts.reduce((a, t) => a + t.pnl_pct, 0) / ts.length, winRate: (100 * ts.filter((t) => t.pnl > 0).length) / ts.length,
    })).sort((a, b) => a.key.localeCompare(b.key, undefined, { numeric: true }));
  };
  return {
    matched: joined.filter((j) => j.p).length,
    byRank: group((p) => `#${p.rank}`),
    byCatalyst: group((p) => (p.catalyst ?? "unknown").replace(/_/g, " ").toUpperCase()),
    byScore: group((p) => { const b = Math.floor(p.short_score / 10) * 10; return `${b}–${b + 9}`; }),
  };
}

// ------------------------------------------------------------------ risk
export type RiskRow = {
  ticker: string; mv: number; pctEq: number; unrealized: number; sector: string | null;
  si: number | null; dtc: number | null; flags: string[]; entryRun: string | null; targetPct: number | null; plpc: number;
};
export function risk(positions: Position[], account: Account | null, held: Map<string, Candidate & { run_date?: string }>) {
  const equity = account ? Number(account.equity) : 0;
  const rows: RiskRow[] = positions.map((p) => {
    const c = held.get(p.symbol);
    const mv = Math.abs(Number(p.market_value));
    return { ticker: p.symbol, mv, pctEq: equity ? (100 * mv) / equity : 0, unrealized: Number(p.unrealized_pl), plpc: Number(p.unrealized_plpc) * 100,
      sector: c?.sector ?? null, si: c?.si_pct_float ?? null, dtc: c?.days_to_cover ?? null, flags: c?.flags ?? [],
      entryRun: c?.run_date ?? null, targetPct: c?.target_pct ?? null };
  }).sort((a, b) => b.mv - a.mv);
  const gross = rows.reduce((a, r) => a + r.mv, 0);
  const sectors = new Map<string, number>();
  for (const r of rows) sectors.set(r.sector ?? "UNKNOWN", (sectors.get(r.sector ?? "UNKNOWN") ?? 0) + r.mv);
  const shortMv = positions.filter((p) => p.side === "short").reduce((a, p) => a + Math.abs(Number(p.market_value)), 0);
  return {
    equity, gross, grossPct: equity ? (100 * gross) / equity : null,
    largest: rows[0] ?? null,
    buyingPower: account ? Number(account.buying_power) : null,
    sectors: [...sectors.entries()].map(([k, v]) => ({ sector: k, mv: v, pct: gross ? (100 * v) / gross : 0 })).sort((a, b) => b.mv - a.mv),
    gap20: shortMv * 0.2, gap50: shortMv * 0.5,
    gap20Pct: equity ? (100 * shortMv * 0.2) / equity : null, gap50Pct: equity ? (100 * shortMv * 0.5) / equity : null,
    rows,
  };
}

// ------------------------------------------------------------------ runs
export type RunRow = Run & { filled: number; rejected: number; nextPnl: number | null; nextN: number };
export function runRows(runs: Run[], orders: Order[], trades: Trade[]): RunRow[] {
  return runs.map((r) => {
    const os = orders.filter((o) => o.run_id === r.id);
    const ts = trades.filter((t) => etDateOf(t.entry_at) === r.run_date);
    return { ...r, filled: os.filter((o) => o.status === "filled").length,
      rejected: os.filter((o) => ["rejected", "canceled", "expired"].includes(o.status)).length,
      nextPnl: ts.length ? ts.reduce((a, t) => a + t.pnl, 0) : null, nextN: ts.length };
  });
}

/** Scan funnel: scanned → shortable → no real catalyst → picked. */
export function funnel(cands: Candidate[]) {
  const shortable = cands.filter((c) => c.shortable !== false);
  const noCat = shortable.filter((c) => !(c.exclude_reason ?? "").includes("catalyst"));
  const picked = cands.filter((c) => c.selected);
  const dropped = (a: Candidate[], b: Candidate[]) => a.filter((x) => !b.includes(x)).map((x) => x.ticker);
  return [
    { label: "SCANNED", n: cands.length, reason: "Top end-of-day gainers from TradingView", dropped: [] as string[] },
    { label: "SHORTABLE", n: shortable.length, reason: "Dropped: not shortable on Alpaca", dropped: dropped(cands, shortable) },
    { label: "NO REAL NEWS", n: noCat.length, reason: "Dropped: real de-risking news", dropped: dropped(shortable, noCat) },
    { label: "PICKED", n: picked.length, reason: "Dropped: below the top-5 cut or other exclusion", dropped: dropped(noCat, picked) },
  ];
}

export const fmtDd = (v: number | null) => (v === null ? "—" : pct(v * 100, 1, true));
export { md };
