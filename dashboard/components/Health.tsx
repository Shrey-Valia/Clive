"use client";
// /health: banner, fix list, component grid, schedule timeline and 24 h heat strips.
// The server page hands over the effective rows (latest per check + stale-probe rows) and the
// heat strips pre-bucketed, so this island only owns the host filter and the timeline toggle.
// Color is never alone: every status cell also carries its text (OK / WARN / FAIL, MISSED...).

import { Fragment, useMemo, useState } from "react";
import {
  BOT_NAME, COMPONENTS, REPEATING, byPriority, checkLabel, fixHint, hostsOf, pillCls, slotsOf, statusText, worst,
  type HStatus, type HealthRow, type Slot, type SlotStatus,
} from "@/lib/health";
import { ago, dayLabel, etHm, md } from "@/lib/format";

type Props = { rows: HealthRow[]; heat: Record<string, string>; now: string; sample: boolean };

const SLOT_CLS: Record<SlotStatus, string> = { OK: "ok", LATE: "late", MISSED: "missed", FAILED: "failed", PENDING: "pending" };
const SLOT_RANK: Record<SlotStatus, number> = { OK: 0, PENDING: 1, LATE: 2, MISSED: 3, FAILED: 4 };
const HEAT: Record<string, HStatus | ""> = { o: "ok", w: "warn", f: "fail", u: "unknown", ".": "" };

const Chip = ({ s }: { s: HStatus }) => <span className={`hst ${s}`}>{statusText(s)}</span>;
const SlotChip = ({ s }: { s: SlotStatus }) => <span className={`slchip ${SLOT_CLS[s]}`}>{s}</span>;

export function HealthBoard({ rows, heat, now, sample }: Props) {
  const hosts = useMemo(() => hostsOf(rows), [rows]);
  const [host, setHost] = useState<string>("ALL");
  const [range, setRange] = useState<"today" | "5d">("today");
  const multi = hosts.length > 1;
  const shown = useMemo(() => rows.filter((r) => host === "ALL" || r.host === host), [rows, host]);
  const checks = shown.filter((r) => r.check_name !== "timeline");
  const fails = checks.filter((r) => r.status === "fail").sort(byPriority);
  const warns = checks.filter((r) => r.status === "warn").sort(byPriority);
  const nowMs = new Date(now).getTime();
  const lastRun = shown.reduce<string | null>((m, r) => (r.component !== "health" && (!m || r.run_at > m) ? r.run_at : m), null);
  const top = fails[0] ?? null;
  const tlHost = host !== "ALL" ? host : (hosts.find((h) => rows.some((r) => r.host === h && r.check_name === "timeline" && slotsOf(r).length)) ?? hosts[0]);
  const tl = rows.find((r) => r.host === tlHost && r.check_name === "timeline");
  const tone = fails.length ? "bad" : warns.length ? "warn" : "ok";

  return (
    <>
      <div className={`hbanner ${tone}`} role="status" aria-live="polite">
        <div className="hb-state">
          <b>{fails.length ? `${fails.length} FAILING` : "ALL SYSTEMS OK"}</b>
          {warns.length > 0 && <span className="hb-warn">{warns.length} WARN</span>}
          {sample && <span className="simtag">SAMPLE</span>}
        </div>
        <div className="hb-msg">
          {top ? <>{multi && <em className="hhost">{top.host}</em>}{top.message}</>
            : warns[0] ? <>No failures. Top warning: {warns[0].message}</> : <>Every check passed{lastRun ? ` · last probe ${ago(lastRun, nowMs).toLowerCase()}` : ""}.</>}
        </div>
        <div className="hb-comps">
          {COMPONENTS.map((c) => {
            const st = worst(checks.filter((r) => r.component === c.id).map((r) => r.status));
            return <a key={c.id} href={`#hc-${c.id}`} className={`hb-comp ${st}`}><b>{c.code}</b> {statusText(st)}</a>;
          })}
          <span className="muted small">{lastRun ? `PROBE ${ago(lastRun, nowMs)}` : "NO PROBE RUN"} · {hosts.length} HOST{hosts.length === 1 ? "" : "S"}</span>
        </div>
      </div>

      <div className="hostbar" role="group" aria-label="Host">
        <span className="muted small">HOST</span>
        {multi && <button type="button" className={`chip${host === "ALL" ? " on" : ""}`} onClick={() => setHost("ALL")}>ALL</button>}
        {hosts.map((h) => {
          const st = worst(rows.filter((r) => r.host === h && r.check_name !== "timeline").map((r) => r.status));
          const alive = rows.find((r) => r.host === h && r.check_name === "alive");
          return (
            <button key={h} type="button" className={`chip${host === h || !multi ? " on" : ""}`} onClick={() => setHost(h)} aria-pressed={host === h}>
              <span className={`hdot ${st}`} aria-hidden="true" />{h} <span className="hb-age">{alive ? ago(alive.run_at, nowMs) : "—"}</span>
            </button>
          );
        })}
      </div>

      <section className="panel" id="fix" data-panel={1}>
        <div className="panel-head"><h2><span className="pn">1)</span> What&apos;s broken and how to fix it</h2>
          <span className="meta">{fails.length} FAIL · {warns.length} WARN</span><span className="code">FIX</span></div>
        <div className="panel-body">
          {!fails.length && !warns.length && <div className="empty line">NOTHING BROKEN · EVERY CHECK PASSED</div>}
          <FixList rows={fails} multi={multi} />
          {warns.length > 0 && (
            <details className="fx-warns" open={!fails.length}>
              <summary>{warns.length} WARNING{warns.length === 1 ? "" : "S"} · NOT BROKEN, WORTH A LOOK</summary>
              <FixList rows={warns} multi={multi} start={fails.length} />
            </details>
          )}
        </div>
      </section>
      <div className="hgrid" data-panel={2} id="p2">
        {COMPONENTS.map((c) => {
          const list = checks.filter((r) => r.component === c.id || (c.id === "host" && r.component === "health")).sort(byPriority);
          const st = worst(list.map((r) => r.status));
          const newest = list.reduce<string | null>((m, r) => (!m || r.run_at > m ? r.run_at : m), null);
          return (
            <section key={c.id} id={`hc-${c.id}`} className={`panel hcard ${st}`}>
              <div className="panel-head">
                <h2>{c.label}</h2><span className={`pill ${pillCls(st)}`}>{statusText(st)}</span>
                <span className="meta">{newest ? `CHECKED ${ago(newest, nowMs)}` : "NOT CHECKED"}</span><span className="code">{c.code}</span>
              </div>
              <ul className="hchecks">
                {!list.length && <li className="muted">NO ROWS FOR THIS COMPONENT</li>}
                {list.map((r) => (
                  <li key={`${r.host}|${r.check_name}`} className={r.status}>
                    <Chip s={r.status} />
                    <span className="hc-l">{checkLabel(r.check_name)}{multi && host === "ALL" && <em className="hhost">{r.host}</em>}</span>
                    <span className="hc-m" title={r.message ?? ""}>{r.message}</span>
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>

      <section className="panel" id="sched" data-panel={3}>
        <div className="panel-head">
          <h2><span className="pn">3)</span> Schedule · expected vs observed</h2>
          <span className="meta">
            <button type="button" className={`seg${range === "today" ? " on" : ""}`} onClick={() => setRange("today")} aria-pressed={range === "today"}>TODAY</button>
            <button type="button" className={`seg${range === "5d" ? " on" : ""}`} onClick={() => setRange("5d")} aria-pressed={range === "5d"}>LAST 5 SESSIONS</button>
            {tl && <span>HOST {tl.host} · {ago(tl.run_at, nowMs)}</span>}
          </span>
          <span className="code">SCHD</span>
        </div>
        <div className="panel-body">
          {!tl ? <div className="empty line">NO SCHEDULER ROWS YET</div>
            : range === "today" ? <TodayTable tl={tl} now={nowMs} /> : <SessionsMatrix tl={tl} />}
          <SlotLegend />
        </div>
      </section>

      <section className="panel" id="hist" data-panel={4}>
        <div className="panel-head"><h2><span className="pn">4)</span> Check history · last 24 h</h2>
          <span className="meta">ONE CELL PER 15 MIN · WORST STATUS IN THE CELL · BLANK = NO PROBE RUN</span><span className="code">HIST</span></div>
        <div className="panel-body">
          <HeatStrips rows={checks} heat={heat} multi={multi && host === "ALL"} now={nowMs} />
        </div>
      </section>
    </>
  );
}


function FixList({ rows, multi, start = 0 }: { rows: HealthRow[]; multi: boolean; start?: number }) {
  if (!rows.length) return null;
  return (
    <ol className="fixlist">
            {rows.map((r, i) => (
              <li key={`${r.host}|${r.component}|${r.check_name}`} className={r.status}>
                <div className="fx-head">
                  <span className="fx-n">{start + i + 1}</span><Chip s={r.status} />
                  <span className="fx-comp">{COMPONENTS.find((c) => c.id === r.component)?.code ?? r.component.toUpperCase()}</span>
                  <b>{checkLabel(r.check_name)}</b>
                  {multi && <em className="hhost">{r.host}</em>}
                  {(r.expected_at || r.observed_at) && (
                    <span className="fx-times muted">{r.expected_at ? `DUE ${md(r.expected_at.slice(0, 10))} ${etHm(r.expected_at)}` : ""}{r.observed_at ? ` · SEEN ${etHm(r.observed_at)}` : r.expected_at ? " · NEVER SEEN" : ""}</span>
                  )}
                </div>
                <p className="fx-msg">{r.message}</p>
                <p className="fx-fix"><span>FIX ›</span> {fixHint(r)}</p>
                {r.detail && Object.keys(r.detail).length > 0 && (
                  <details className="fx-detail"><summary>DETAIL</summary><pre>{detailText(r.detail)}</pre></details>
                )}
              </li>
            ))}
          </ol>
  );
}

function detailText(d: Record<string, unknown>) {
  const { slots, traceback, ...rest } = d as Record<string, unknown> & { slots?: unknown; traceback?: unknown };
  void slots; void traceback;
  const s = JSON.stringify(rest, null, 1);
  return s.length > 1800 ? s.slice(0, 1800) + "\n…" : s;
}

function SlotLegend() {
  return (
    <div className="legendrow small muted">
      {(["OK", "LATE", "MISSED", "FAILED", "PENDING"] as SlotStatus[]).map((s) => <span key={s}><SlotChip s={s} /> {{
        OK: "ran on time", LATE: "ran after its grace", MISSED: "no run seen (log or Supabase)", FAILED: "an error was logged", PENDING: "not due yet",
      }[s]}</span>)}
    </div>
  );
}

type Group = { key: string; bot: string; job: string; slots: Slot[] };
function groups(slots: Slot[]): Group[] {
  const m = new Map<string, Group>();
  for (const s of slots) {
    const k = `${s.bot}|${s.job}`;
    if (!m.has(k)) m.set(k, { key: k, bot: s.bot, job: s.job, slots: [] });
    m.get(k)!.slots.push(s);
  }
  return [...m.values()].sort((a, b) => a.slots[0].expected_at.localeCompare(b.slots[0].expected_at) || a.bot.localeCompare(b.bot));
}
const worstSlot = (s: Slot[]) => s.reduce<SlotStatus>((a, b) => (SLOT_RANK[b.status] > SLOT_RANK[a] ? b.status : a), "OK");
const jobName = (s: Slot) => s.label.split(" ").slice(1).join(" ").toUpperCase();

function Cells({ slots }: { slots: Slot[] }) {
  return (
    <span className="slcells" role="img" aria-label={slots.map((s) => `${s.label} ${s.status}`).join(", ")}>
      {slots.map((s) => <i key={s.expected_at} className={SLOT_CLS[s.status]} title={`${s.date} ${s.label}: ${s.status}${s.observed_at ? ` · seen ${etHm(s.observed_at)}` : ""}${s.note ? ` · ${s.note}` : ""}`} />)}
    </span>
  );
}

function TodayTable({ tl, now }: { tl: HealthRow; now: number }) {
  const d = tl.detail as { today?: string; sessions?: string[] } | null;
  const all = slotsOf(tl);
  const day = all.some((s) => s.date === d?.today) ? d!.today! : (d?.sessions ?? []).slice(-1)[0];
  const today = all.filter((s) => s.date === day);
  const gs = groups(today);
  if (!today.length) return <div className="empty line">NO JOBS EXPECTED ON {day ?? "THIS DAY"} · MARKET CLOSED</div>;
  const rows: { key: string; t: number; el: React.ReactNode }[] = [];
  for (const g of gs) {
    if (REPEATING.has(g.job)) {
      const done = g.slots.filter((s) => s.status !== "PENDING");
      const ok = done.filter((s) => s.status === "OK").length;
      const w = worstSlot(g.slots);
      rows.push({ key: g.key, t: new Date(g.slots[0].expected_at).getTime(), el: (
        <tr key={g.key} className={w === "MISSED" || w === "FAILED" ? "failrow" : ""}>
          <td>{etHm(g.slots[0].expected_at)}–{etHm(g.slots[g.slots.length - 1].expected_at)}</td>
          <td className="amber">{BOT_NAME[g.bot] ?? g.bot.toUpperCase()}</td>
          <td>{jobName(g.slots[0])} ×{g.slots.length}</td>
          <td>{ok}/{done.length} OK</td>
          <td><SlotChip s={w} /></td>
          <td className="fill"><Cells slots={g.slots} /></td>
        </tr>) });
    } else {
      for (const s of g.slots) rows.push({ key: `${g.key}|${s.expected_at}`, t: new Date(s.expected_at).getTime(), el: (
        <tr key={`${g.key}|${s.expected_at}`} className={s.status === "MISSED" || s.status === "FAILED" ? "failrow" : ""}>
          <td><b>{etHm(s.expected_at)}</b></td>
          <td className="amber">{BOT_NAME[s.bot] ?? s.bot.toUpperCase()}</td>
          <td>{jobName(s)}</td>
          <td>{s.observed_at ? <>{etHm(s.observed_at)}{s.delay_min !== null && s.delay_min > 1 ? <span className="muted"> +{Math.round(s.delay_min)}M</span> : null}</> : <span className="muted">—</span>}</td>
          <td><SlotChip s={s.status} /></td>
          <td className="wrapcell fill"><div title={s.note}>{s.note}</div></td>
        </tr>) });
    }
  }
  rows.sort((a, b) => a.t - b.t);
  return (
    <div className="scroll">
      <div className="subhead">{dayLabel(day)} · {today.length} EXPECTED RUNS · NOW {etHm(new Date(now).toISOString())} ET</div>
      <table className="dense htl">
        <thead><tr><th>Expected ET</th><th>Bot</th><th>Job</th><th>Observed</th><th>Status</th><th className="fill">Note</th></tr></thead>
        <tbody>{rows.map((r) => <Fragment key={r.key}>{r.el}</Fragment>)}</tbody>
      </table>
    </div>
  );
}

function SessionsMatrix({ tl }: { tl: HealthRow }) {
  const all = slotsOf(tl);
  const d = tl.detail as { sessions?: string[]; half_days?: string[] } | null;
  const sessions = d?.sessions ?? [...new Set(all.map((s) => s.date))].sort();
  const gs = groups(all).sort((a, b) => {
    const ta = etHm(a.slots[0].expected_at), tb = etHm(b.slots[0].expected_at);
    return ta.localeCompare(tb) || a.bot.localeCompare(b.bot);
  });
  return (
    <div className="scroll">
      <table className="dense hmx">
        <thead><tr><th>Job</th>{sessions.map((s) => <th key={s} className="c">{dayLabel(s)}{d?.half_days?.includes(s) ? " ½" : ""}</th>)}</tr></thead>
        <tbody>
          {gs.map((g) => (
            <tr key={g.key}>
              <td><span className="amber">{BOT_NAME[g.bot] ?? g.bot}</span> {g.slots[0].label.toUpperCase()}{REPEATING.has(g.job) ? " …" : ""}</td>
              {sessions.map((day) => {
                const ss = g.slots.filter((s) => s.date === day);
                if (!ss.length) return <td key={day} className="c muted" title="not tracked (before the job was first seen, or not scheduled)">—</td>;
                if (REPEATING.has(g.job)) return <td key={day} className="c"><Cells slots={ss} /></td>;
                const s = ss[0];
                return <td key={day} className="c" title={`${s.note}${s.observed_at ? ` · seen ${etHm(s.observed_at)}` : ""}`}><SlotChip s={s.status} /></td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HeatStrips({ rows, heat, multi, now }: { rows: HealthRow[]; heat: Record<string, string>; multi: boolean; now: number }) {
  const ticks = [24, 18, 12, 6, 0].map((h) => ({ h, label: h === 0 ? "NOW" : etHm(new Date(now - h * 3600_000).toISOString()) }));
  return (
    <div className="heat">
      <div className="heat-axis" aria-hidden="true"><span /><span className="heat-ticks">{ticks.map((t) => <em key={t.h}>{t.label}</em>)}</span><span /></div>
      {COMPONENTS.concat([{ id: "health", label: "Probe", code: "HLTH" }]).map((c) => {
        const list = rows.filter((r) => r.component === c.id).sort(byPriority);
        if (!list.length) return null;
        return (
          <Fragment key={c.id}>
            <div className="heat-group">{c.label.toUpperCase()}</div>
            {list.map((r) => {
              const s = heat[`${r.host}|${r.component}|${r.check_name}`] ?? "";
              const cells = s ? s.split("") : [];
              const counts = { f: cells.filter((x) => x === "f").length, w: cells.filter((x) => x === "w").length };
              return (
                <div key={`${r.host}|${r.check_name}`} className="heat-row">
                  <span className="heat-l">{checkLabel(r.check_name)}{multi && <em className="hhost">{r.host}</em>}</span>
                  <span className="heat-cells" role="img" aria-label={`${checkLabel(r.check_name)}: ${counts.f} failing and ${counts.w} warning intervals in 24 hours`}>
                    {cells.length ? cells.map((x, i) => <i key={i} className={HEAT[x] || "none"} />) : <span className="muted small">NO HISTORY</span>}
                  </span>
                  <Chip s={r.status} />
                </div>
              );
            })}
          </Fragment>
        );
      })}
    </div>
  );
}
