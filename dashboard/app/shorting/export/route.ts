import { getShorting } from "@/lib/data";

export const dynamic = "force-dynamic";

/** 96) EXPORT: the trade blotter as CSV. Same access rules as the pages (admin-only via RLS). */
export async function GET() {
  const s = await getShorting();
  if (!s) return new Response("access denied", { status: 403 });
  const cols = ["id", "ticker", "side", "qty", "entry_at", "entry_price", "exit_at", "exit_price", "pnl", "pnl_pct"] as const;
  const esc = (v: unknown) => { const t = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
  const body = [cols.join(","), ...s.trades.map((t) => cols.map((c) => esc(t[c])).join(","))].join("\n");
  return new Response(body + "\n", {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": `attachment; filename="shorting-trades${s.simulated ? "-SIMULATED" : ""}.csv"`,
      "cache-control": "no-store",
    },
  });
}
