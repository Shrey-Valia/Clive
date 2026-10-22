import { NoAccess, PageHead } from "@/components/Bits";
import { HealthBoard } from "@/components/Health";
import { getHealth } from "@/lib/data";
import { effectiveRows, heatStrips } from "@/lib/health";

export const dynamic = "force-dynamic";

const CHAR = { ok: "o", warn: "w", fail: "f", unknown: "u" } as const;

export default async function Health() {
  const h = await getHealth();
  if (!h) return <NoAccess />;
  const now = new Date();
  const head = (
    <PageHead fn="HLTH" title="System health"
      desc={<>Every check health/check.py runs, per host: did each job run on time, do positions match the plan, are the accounts, data sources, state files, Supabase and the host OK · times ET</>}
      keys={[{ n: 91, label: "FIX LIST", href: "/health#fix" }, { n: 92, label: "SCHEDULE", href: "/health#sched" }, { n: 93, label: "HISTORY", href: "/health#hist" }]} />
  );
  if (h.missing) {
    return (
      <>
        {head}
        <div className="empty line warn">
          HEALTH TABLE NOT SET UP YET · APPLY <code>dashboard/supabase/migrations/008_health.sql</code> IN THE SUPABASE SQL EDITOR, THEN RUN <code>python3 health/check.py</code> (CRON LINE IN <code>health/README.md</code>)
        </div>
        <div className="empty line">Until then: <code>python3 health/check.py --dry-run</code> on the bot machine prints the same checks as a table.</div>
      </>
    );
  }
  if (!h.latest.length) {
    return (
      <>
        {head}
        <div className="empty line warn">NO HEALTH ROWS YET · RUN <code>python3 health/check.py</code> ON THE BOT MACHINE, OR INSTALL ITS CRON LINE (<code>health/README.md</code>)</div>
      </>
    );
  }
  const rows = effectiveRows(h.latest, now);
  const heat: Record<string, string> = {};
  for (const [k, v] of heatStrips(h.history, now)) heat[k] = v.map((s) => (s ? CHAR[s] : ".")).join("");
  // keep the scheduler timeline detail, drop other bulky detail the client never needs
  const slim = rows.map((r) => (r.detail && JSON.stringify(r.detail).length > 4000 && r.check_name !== "timeline" ? { ...r, detail: { note: "detail too large to show" } } : r));
  return (
    <>
      {head}
      {h.sample && <div className="dryband">SAMPLE HEALTH ROWS · DEMO MODE · A REAL PROBE CAPTURE FROM 10/22 16:30 ET (THE MAC SLEPT, THE 15:40 COVER NEVER RAN), PLUS A SIMULATED VPS HOST</div>}
      <HealthBoard rows={slim} heat={heat} now={now.toISOString()} sample={h.sample} />
    </>
  );
}
