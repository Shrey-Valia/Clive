// Loader for the Options Bot backtester's export (lib/options-backtest.json,
// copied from "Options Bot/results/dashboard.json"). Tolerant of missing or
// oddly-typed fields: anything absent renders as "—", never crashes a page.
// Rates are stored as fractions (0.12 = 12%); if the file uses percents
// (max_drawdown beyond ±1) they're scaled down once here.
import raw from "@/lib/options-backtest.json";
import type { DatePt } from "@/lib/types";

export type BtMetrics = {
  cagr: number | null; total_return: number | null; max_drawdown: number | null; sharpe: number | null; sortino: number | null;
  win_rate: number | null; profit_factor: number | null; trades: number | null; [k: string]: number | null;
};
export type BtStrategy = {
  id: string; name: string; underlying: string; capital: number | null; metrics: BtMetrics; params: Record<string, unknown>;
  equity: DatePt[]; drawdown: DatePt[]; monthly: { month: string; ret: number }[]; trades_count: number | null;
  trades: Record<string, unknown>[];
};
export type BtLab = {
  sample: boolean; generated_at: string | null; period: { start: string | null; end: string | null };
  assumptions: string | null; code_version: string | null;
  strategies: BtStrategy[]; benchmark: { name: string; equity: DatePt[]; metrics: BtMetrics } | null;
};

const RATE_KEYS = ["cagr", "total_return", "max_drawdown", "win_rate"];
const n = (v: unknown): number | null => (v === null || v === undefined || v === "" || Number.isNaN(Number(v)) ? null : Number(v));
const s = (v: unknown, d = ""): string => (typeof v === "string" ? v : v === null || v === undefined ? d : String(v));
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

function pts(v: unknown): DatePt[] {
  return arr(v).flatMap((p) => {
    const o = obj(p);
    const date = s(o.date ?? o.d ?? o.t).slice(0, 10), value = n(o.value ?? o.v ?? o.equity);
    return date && value !== null ? [{ date, value }] : [];
  }).sort((a, b) => a.date.localeCompare(b.date));
}

function metrics(v: unknown, scale: number): BtMetrics {
  const o = obj(v);
  const out: Record<string, number | null> = {};
  for (const [k, x] of Object.entries(o)) out[k] = n(x);
  for (const k of ["cagr", "total_return", "max_drawdown", "sharpe", "sortino", "win_rate", "profit_factor", "trades"]) out[k] = out[k] ?? null;
  for (const k of RATE_KEYS) if (out[k] !== null) out[k] = (out[k] as number) / (k === "win_rate" && (out[k] as number) > 1 ? 100 : scale);
  if (out.max_drawdown !== null && (out.max_drawdown as number) > 0) out.max_drawdown = -(out.max_drawdown as number);
  return out as BtMetrics;
}

export function ddFrom(eq: DatePt[]): DatePt[] {
  let pk = -Infinity;
  return eq.map((p) => { pk = Math.max(pk, p.value); return { date: p.date, value: pk > 0 ? p.value / pk - 1 : 0 }; });
}
function monthlyFrom(eq: DatePt[]) {
  const out: { month: string; ret: number }[] = [];
  let prev = eq[0]?.value;
  eq.forEach((p, i) => {
    const m = p.date.slice(0, 7);
    if ((i === eq.length - 1 || eq[i + 1].date.slice(0, 7) !== m) && prev) { out.push({ month: m, ret: p.value / prev - 1 }); prev = p.value; }
  });
  return out;
}

export function normalize(input: unknown): BtLab {
  const o = obj(input);
  const strategiesRaw = arr(o.strategies);
  const pctFile = strategiesRaw.some((x) => Math.abs(n(obj(obj(x).metrics).max_drawdown) ?? 0) > 1);
  const scale = pctFile ? 100 : 1;
  const strategies = strategiesRaw.map((x, i): BtStrategy => {
    const so = obj(x);
    const equity = pts(so.equity);
    const ddRaw = pts(so.drawdown).map((p) => ({ ...p, value: (p.value > 0 ? -p.value : p.value) / (Math.abs(p.value) > 1 ? 100 : 1) }));
    const mRaw = arr(so.monthly_returns ?? so.monthly).flatMap((m) => {
      const mo = obj(m), month = s(mo.month ?? mo.m).slice(0, 7), ret = n(mo.ret ?? mo.return ?? mo.value);
      return month && ret !== null ? [{ month, ret }] : [];
    });
    const mScale = mRaw.some((m) => Math.abs(m.ret) > 1.5) ? 100 : 1;
    return {
      id: s(so.id, `s${i}`), name: s(so.name, s(so.id, `Strategy ${i + 1}`)), underlying: s(so.underlying, "—"), capital: n(so.capital),
      metrics: metrics(so.metrics, scale), params: obj(so.params),
      equity, drawdown: ddRaw.length ? ddRaw : ddFrom(equity),
      monthly: mRaw.length ? mRaw.map((m) => ({ ...m, ret: m.ret / mScale })) : monthlyFrom(equity),
      trades_count: n(so.trades_count) ?? n(obj(so.metrics).trades), trades: arr(so.trades).map(obj),
    };
  });
  const b = obj(o.benchmark);
  const bEq = pts(b.equity ?? b.curve);
  const period = obj(o.period);
  return {
    sample: o.sample === true,
    generated_at: s(o.generated_at) || null,
    period: { start: s(period.start ?? period.from) || (typeof o.period === "string" ? s(o.period) : null) || null, end: s(period.end ?? period.to) || null },
    assumptions: s(o.assumptions ?? obj(o.meta).assumptions) || null,
    code_version: s(o.code_version ?? obj(o.meta).code_version) || null,
    strategies,
    benchmark: bEq.length ? { name: s(b.name, "Benchmark"), equity: bEq, metrics: metrics(b.metrics, scale) } : null,
  };
}

export const getBacktest = (): BtLab => normalize(raw as unknown);
