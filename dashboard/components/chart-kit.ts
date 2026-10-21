"use client";
// Shared chart helpers: sizing, ticks, tick formatting, tooltip, keyboard
// navigation, linked hover across charts, and the hidden table view.
import { createElement as h, useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";

export type DatePt = { date: string; value: number };
export interface ChartBase { height?: number; ariaLabel: string; hoverIndex?: number | null; onHover?: (i: number | null) => void }

export const TOK = {
  pos: "#3ddc84", neg: "#ff4d4d", amber: "#ffa028", grid: "#1a2026", rule: "#3a424a", ctx: "#5b636b", dim: "#7b828a",
  c1: "#3987e5", c2: "#d55181", c3: "#9085e9", c4: "#199e70", posFill: "rgba(61,220,132,.10)", negFill: "rgba(255,77,77,.10)",
  greens: ["#1b5e39", "#26884f", "#31b36a", "#3ddc84"], reds: ["#7f2222", "#ab2d2d", "#d83c3c", "#ff4d4d"], neutral: "#1a2026",
  ambers: ["#5f4214", "#99621a", "#d0821f", "#ffa028"], series: ["#ffa028", "#3987e5", "#d55181"],
} as const;

const useIso = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** Container width; null until measured so the chart never renders at a guessed size and jumps. */
export function useWidth(min = 200) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState<number | null>(null);
  useIso(() => {
    if (!ref.current) return;
    setW(Math.max(min, ref.current.getBoundingClientRect().width));
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.floor(e.contentRect.width))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [min]);
  return [ref, w] as const;
}

export function niceTicks(min: number, max: number, n = 4): { ticks: number[]; step: number } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) { min = 0; max = 1; }
  if (min === max) { const d = Math.abs(min) * 0.01 || 1; min -= d; max += d; }
  const raw = (max - min) / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw)!;
  const lo = Math.floor(min / step + 1e-9) * step, hi = Math.ceil(max / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return { ticks, step };
}

/** Tick label: decimals only when the step needs them, compact $10.4K for large values. */
export function fmtTick(v: number, step: number, kind: "usd" | "pct" | "num" = "usd"): string {
  const neg = v < -1e-12, a = Math.abs(v);
  const sgn = neg ? "−" : "";
  if (kind === "pct") {
    const d = step < 0.01 ? 2 : step < 0.1 ? 1 : 0; // v is a percent number
    return `${sgn}${a.toFixed(step >= 1 ? 0 : d || 1)}%`;
  }
  if (kind === "num") return `${sgn}${a.toFixed(step < 1 ? (step < 0.1 ? 2 : 1) : 0)}`;
  if (a >= 1e6) return `${sgn}$${(a / 1e6).toFixed(step >= 1e5 ? 1 : 2)}M`;
  if (a >= 1e4) return `${sgn}$${(a / 1e3).toFixed(step >= 1000 ? 0 : 1)}K`;
  return `${sgn}$${a.toFixed(step < 1 ? 2 : 0)}`;
}

export const md = (d: string) => `${d.slice(5, 7)}/${d.slice(8, 10)}`;
export const mdy = (d: string) => `${d.slice(5, 7)}/${d.slice(8, 10)}/${d.slice(2, 4)}`;
export const sUsd = (v: number, d = 2) => `${v > 0 ? "+" : v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
export const sPct = (v: number, d = 1) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%`;
export const usd0 = (v: number) => `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;

// ------------------------------------------------------------------ linked hover
const groups = new Map<string, { v: unknown; subs: Set<() => void> }>();
function grp(name: string) { let g = groups.get(name); if (!g) { g = { v: null, subs: new Set() }; groups.set(name, g); } return g; }

/** Hover state; charts passing the same `group` share it (linked crosshair). */
export function useHover<T = number>(group?: string): [T | null, (v: T | null) => void] {
  const [local, setLocal] = useState<T | null>(null);
  const g = group ? grp(group) : null;
  const sub = useCallback((cb: () => void) => { if (!g) return () => {}; g.subs.add(cb); return () => { g.subs.delete(cb); }; }, [g]);
  const shared = useSyncExternalStore(sub, () => (g ? (g.v as T | null) : null), () => null);
  const set = useCallback((v: T | null) => {
    if (!g) return setLocal(v);
    if (g.v === v) return;
    g.v = v; g.subs.forEach((f) => f());
  }, [g]);
  return g ? [shared, set] : [local, set];
}

/** Arrow / Home / End / Escape over n points. */
export function chartKeys(n: number, hover: number | null, set: (i: number | null) => void) {
  return (e: React.KeyboardEvent) => {
    const cur = hover ?? n - 1;
    const step = e.shiftKey ? 5 : 1;
    if (e.key === "ArrowLeft") set(Math.max(0, cur - step));
    else if (e.key === "ArrowRight") set(Math.min(n - 1, hover === null ? n - 1 : cur + step));
    else if (e.key === "Home") set(0);
    else if (e.key === "End") set(n - 1);
    else if (e.key === "Escape") set(null);
    else return;
    e.preventDefault();
  };
}

/** Tooltip clamped inside the chart; flips left of the point past mid-width and below it near the top. */
export function Tip({ x, y, w, children }: { x: number; y: number; w: number; children: ReactNode }) {
  const right = x > w * 0.6;
  const below = y < 70;
  const style: React.CSSProperties = {
    left: Math.max(4, Math.min(w - 4, x + (right ? -12 : 12))),
    top: below ? y + 14 : Math.max(4, y - 14),
    transform: `translate(${right ? "-100%" : "0"}, ${below ? "0" : "-100%"})`,
  };
  return h("div", { className: "tip", style, role: "status", "aria-live": "polite" }, children);
}

/** Screen-reader table view of a chart. */
export function ChartTable({ caption, cols, rows }: { caption: string; cols: string[]; rows: (string | number)[][] }) {
  return h("table", { className: "sr-only" },
    h("caption", null, caption),
    h("thead", null, h("tr", null, cols.map((c) => h("th", { key: c, scope: "col" }, c)))),
    h("tbody", null, rows.map((r, i) => h("tr", { key: i }, r.map((c, j) => h("td", { key: j }, c))))));
}

export function Empty({ text, height = 120 }: { text: string; height?: number }) {
  return h("div", { className: "chart-empty", style: { minHeight: height } }, text);
}
