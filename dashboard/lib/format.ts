export const usd = (v: number | string | null | undefined, signed = false) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  const s = Math.abs(n).toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return n < 0 ? `−${s}` : s;
  return n > 0 ? `+${s}` : n < 0 ? `−${s}` : s;
};

export const pct = (v: number | string | null | undefined, digits = 1, signed = false) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  const s = `${Math.abs(n).toFixed(digits)}%`;
  if (!signed) return n < 0 ? `−${s}` : s;
  return n > 0 ? `+${s}` : n < 0 ? `−${s}` : s;
};

export const num = (v: number | string | null | undefined, digits = 1) =>
  v === null || v === undefined || v === "" ? "—" : Number(v).toFixed(digits);

export const compact = (v: number | string | null | undefined) =>
  v === null || v === undefined ? "—" : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(Number(v));

export const sign = (v: number | string | null | undefined) =>
  v === null || v === undefined ? "" : Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "";

export const etTime = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) + " ET" : "—";

/** How stale a daily bot's heartbeat is, allowing for weekends. */
export function heartbeatAge(iso: string | null | undefined): { label: string; stale: boolean } {
  if (!iso) return { label: "never", stale: true };
  const hours = (Date.now() - new Date(iso).getTime()) / 36e5;
  const day = new Date().getDay(); // 0 Sun, 1 Mon
  const allowance = day === 1 ? 76 : day === 0 ? 52 : 28;
  const label = hours < 1 ? `${Math.round(hours * 60)} min ago` : hours < 48 ? `${Math.round(hours)} h ago` : `${Math.round(hours / 24)} days ago`;
  return { label, stale: hours > allowance };
}
