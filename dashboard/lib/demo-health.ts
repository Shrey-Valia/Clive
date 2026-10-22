// DEMO_MODE sample health rows. demo-health.json is a real health/check.py --dry-run --json
// capture judged as of 2026-10-22 16:30 ET (the day the Mac slept through 15:01-16:15: the 15:40
// cover never ran and 5 shorts were held overnight), with account ids and the hostname blanked.
// A second host, a VPS probing in shadow mode, shows how rows from two hosts sit side by side.
// Only lib/data.ts imports this, behind isDemo.
import raw from "@/lib/demo-health.json";
import type { HealthData, HealthRow, HistPoint, HStatus } from "@/lib/health";

type RawRow = Omit<HealthRow, "run_at" | "host">;

const VPS: Record<string, Partial<HealthRow>> = {
  "host/disk": { status: "ok", message: "38 GB free (78%)" },
  "host/clock": { status: "ok", message: "clock offset +0.1 s vs an HTTP Date header" },
  "host/python": { status: "ok", message: "Python 3.12.3 (/usr/bin/python3)" },
  "host/schedule_entries": { status: "ok", message: "13/13 systemd timers (shadow mode: dry runs only)" },
  "host/uptime": { status: "ok", message: "up 6.2 days" },
};

export function demoHealth(now = new Date()): HealthData {
  const runAt = new Date(now.getTime() - 4 * 60_000).toISOString();
  const rows = raw as unknown as RawRow[];
  const mac: HealthRow[] = rows.map((r) => ({ ...r, run_at: runAt, host: "macbook-air" }));
  const vps: HealthRow[] = [];
  for (const r of rows) {
    const k = `${r.component}/${r.check_name}`;
    if (r.component === "host" && !VPS[k]) continue;                 // no pmset on Linux
    if (!["host", "data", "accounts", "supabase", "health"].includes(r.component)) continue;
    vps.push({ ...r, ...VPS[k], run_at: new Date(now.getTime() - 7 * 60_000).toISOString(), host: "clive-vps",
      message: r.component === "health" ? "probe ran on clive-vps in 9.8 s, 38 checks" : (VPS[k]?.message ?? r.message) });
  }
  // 24 h of 15-minute runs. The Mac's failing checks were fine until ~90 min ago, and it was asleep
  // (no probe rows at all) from 150 to 75 minutes ago.
  const history: HistPoint[] = [];
  const end = now.getTime();
  for (let t = end - 24 * 3600_000 + 15 * 60_000; t <= end; t += 15 * 60_000) {
    const ago = (end - t) / 60_000;
    const at = new Date(t).toISOString();
    if (!(ago > 75 && ago < 150)) {
      for (const r of mac) {
        const st: HStatus = r.status !== "ok" && ago > 90 && ["scheduler", "positions", "state"].includes(r.component) ? "ok" : r.status;
        history.push({ run_at: at, host: r.host, component: r.component, check_name: r.check_name, status: st });
      }
    }
    for (const r of vps) history.push({ run_at: at, host: r.host, component: r.component, check_name: r.check_name, status: r.status });
  }
  return { missing: false, sample: true, latest: [...mac, ...vps], history };
}
