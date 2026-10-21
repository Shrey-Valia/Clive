// Formatting. House style: ET 24h clock, dates MM/DD/YY, ages "24M AGO",
// every signed value prefixed + or − (U+2212), zero rendered dim.
import { etParts, isTradingDay, prevTradingDay } from "@/lib/time";

type N = number | string | null | undefined;
const isNil = (v: N) => v === null || v === undefined || v === "" || Number.isNaN(Number(v));

export const usd = (v: N, signed = false, digits = 2) => {
  if (isNil(v)) return "—";
  const n = Number(v);
  const s = Math.abs(n).toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: digits, maximumFractionDigits: digits });
  if (!signed) return n < 0 ? `−${s}` : s;
  return n > 0 ? `+${s}` : n < 0 ? `−${s}` : s;
};

/** Compact money: $10.4K, −$1.2M. */
export const usdK = (v: N, signed = false) => {
  if (isNil(v)) return "—";
  const n = Number(v);
  const a = Math.abs(n);
  const s = a >= 1e6 ? `$${(a / 1e6).toFixed(2)}M` : a >= 1e4 ? `$${(a / 1e3).toFixed(1)}K` : `$${a.toFixed(0)}`;
  return n < 0 ? `−${s}` : signed && n > 0 ? `+${s}` : s;
};

export const pct = (v: N, digits = 1, signed = false) => {
  if (isNil(v)) return "—";
  const n = Number(v);
  const s = `${Math.abs(n).toFixed(digits)}%`;
  if (!signed) return n < 0 ? `−${s}` : s;
  return n > 0 ? `+${s}` : n < 0 ? `−${s}` : s;
};

export const num = (v: N, digits = 1) => (isNil(v) ? "—" : Number(v).toFixed(digits));

export const compact = (v: N) =>
  isNil(v) ? "—" : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(Number(v));

/** CSS class for a signed value: pos / neg / zero. */
export const sign = (v: N) => (isNil(v) ? "" : Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "zero");

const fmt = (iso: string, o: Intl.DateTimeFormatOptions) =>
  new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", hourCycle: "h23", ...o });

/** "10/21 15:45" in ET. */
export const etTime = (iso: string | null | undefined) =>
  iso ? fmt(iso, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).replace(",", "") : "—";

/** "15:45" in ET. */
export const etHm = (iso: string | null | undefined) => (iso ? fmt(iso, { hour: "2-digit", minute: "2-digit" }) : "—");

/** A YYYY-MM-DD date as MM/DD/YY. */
export const ymd = (d: string | null | undefined) => {
  if (!d) return "—";
  const [y, m, dd] = d.slice(0, 10).split("-");
  return `${m}/${dd}/${y.slice(2)}`;
};
/** A YYYY-MM-DD date as MM/DD. */
export const md = (d: string | null | undefined) => (d ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : "—");
/** "TUE 10/21" */
export const dayLabel = (d: string) =>
  `${["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"][new Date(d + "T12:00:00Z").getUTCDay()]} ${md(d)}`;

/** ET calendar date of an ISO timestamp. */
export const etDateOf = (iso: string | null | undefined) => (iso ? etParts(new Date(iso)).date : null);

/** "24M AGO", "3H AGO", "2D AGO". */
export function ago(iso: string | null | undefined, now = Date.now()) {
  if (!iso) return "NEVER";
  const m = Math.max(0, (now - new Date(iso).getTime()) / 60000);
  if (m < 1) return "JUST NOW";
  if (m < 60) return `${Math.round(m)}M AGO`;
  if (m < 48 * 60) return `${Math.round(m / 60)}H AGO`;
  return `${Math.round(m / 1440)}D AGO`;
}

/**
 * How stale a daily bot's heartbeat is. The bot beats at least once per trading
 * day (scan ~15:45, sync ~16:15 ET), so it's stale when it hasn't beaten since
 * the previous trading session's run, computed in ET (not the browser's zone).
 */
export function heartbeatAge(iso: string | null | undefined, now = new Date()): { label: string; stale: boolean } {
  if (!iso) return { label: "NEVER", stale: true };
  const p = etParts(now);
  // the most recent session whose 16:30 has passed must have a beat on/after its scan
  let expect = isTradingDay(p.date) && p.mins >= 16 * 60 + 30 ? p.date : prevTradingDay(p.date);
  if (!isTradingDay(expect)) expect = prevTradingDay(expect);
  const beatDate = etParts(new Date(iso)).date;
  return { label: ago(iso, now.getTime()), stale: beatDate < expect };
}

export const upper = (s: string | null | undefined) => (s ? s.replace(/_/g, " ").toUpperCase() : "—");
