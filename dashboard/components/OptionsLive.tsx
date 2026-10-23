// LIVE PAPER section of /options: the running options bot on its own Alpaca
// paper account (003_options_live tables). Server component, no hooks.
import { BotPill, Empty, Panel, Sgn, Stat } from "@/components/Bits";
import { EquityChart } from "@/components/Charts";
import { EvChips } from "@/components/Events";
import type { OptionsLive as OL } from "@/lib/data";
import type { MarketEvent, OptPosition } from "@/lib/types";
import { riskDaysUntil } from "@/lib/events";
import { equitySeries } from "@/lib/analytics";
import { dayLabel, etHm, etTime, md, num, pct, usd, ymd } from "@/lib/format";
import { addDays, etParts, isTradingDay } from "@/lib/time";
import { optionsNextDecision, sleeveStatus } from "@/lib/next-action";

const dte = (expiry: string | null, today: string) => {
  if (!expiry) return null;
  return Math.round((new Date(expiry + "T12:00:00Z").getTime() - new Date(today + "T12:00:00Z").getTime()) / 864e5);
};
/** Trading sessions left until expiry, inclusive of expiry day. */
const sessionsLeft = (expiry: string | null, today: string) => {
  if (!expiry) return null;
  let n = 0;
  for (let d = addDays(today, 1); d <= expiry; d = addDays(d, 1)) if (isTradingDay(d)) n++;
  return n;
};
const legs = (p: OptPosition) => `−P${num(p.short_strike, 0)} / +P${num(p.long_strike, 0)}`;
const statusTone = (s: string | null | undefined) =>
  !s ? "off" : ["filled", "executed", "ok"].includes(s) ? "ok" : ["error", "failed", "rejected"].includes(s) ? "bad" : ["partial", "unfilled", "planned", "warning"].includes(s) ? "warn" : "off";

export function OptionsLive({ d, events, sample }: { d: OL; events: MarketEvent[]; sample: boolean }) {
  const today = etParts().date;
  const eq = equitySeries(d.snaps);
  const last = d.snaps[d.snaps.length - 1] ?? null;
  const openPl = d.positions.reduce((a, p) => a + (p.unrealized_pl ?? 0), 0);
  const atRisk = d.positions.reduce((a, p) => a + (p.max_loss ?? 0), 0);
  const credit = d.positions.reduce((a, p) => a + (p.entry_credit ?? 0) * 100 * (p.qty ?? 0), 0);
  const closedPl = d.trades.reduce((a, t) => a + (t.pnl ?? 0), 0);
  const wins = d.trades.filter((t) => (t.pnl ?? 0) > 0).length;
  const todayRuns = d.runs.filter((r) => r.run_date === today);
  const run = todayRuns[0] ?? null;
  const sim = d.simulated;
  const nd = optionsNextDecision(d.runs);
  const sleeves = sleeveStatus(d.positions, d.runs);
  // macro risk days between now and the furthest open expiry
  const lastExp = d.positions.map((p) => p.expiry).filter(Boolean).sort().pop() ?? null;
  const riskDays = lastExp ? riskDaysUntil(events, today, lastExp) : [];
  const runLine = run
    ? <>{(run.kind ?? "daily").toUpperCase()} RUN {etHm(run.started_at)} · <span className={`pill ${statusTone(run.status)}`}>{run.status.toUpperCase()}</span> · {run.mode === "execute" ? "LIVE PAPER" : "DRY RUN"} · {run.n_entries ?? 0} ENTRIES · {run.n_exits ?? 0} EXITS{run.vix !== null ? ` · VIX ${num(run.vix, 1)}` : ""}{run.kill_switch ? " · KILL SWITCH ON" : ""}{run.message ? ` · ${run.message.toUpperCase()}` : ""}</>
    : isTradingDay(today) ? <>NO RUN TODAY YET · DAILY RUN {nd.date === today ? nd.run : "15:45"} ET{d.runs[0] ? ` · LAST ${dayLabel(d.runs[0].run_date)} ${d.runs[0].status.toUpperCase()}` : ""}</>
    : <>MARKET CLOSED TODAY{d.runs[0] ? ` · LAST RUN ${dayLabel(d.runs[0].run_date)} ${d.runs[0].status.toUpperCase()}` : ""}</>;

  return (
    <section className="livesec" id="live" aria-label="Live paper trading">
      <div className="livehead">
        <span className="livetag">LIVE PAPER</span>
        <b>OPTIONS BOT · SEPARATE ALPACA PAPER ACCOUNT</b>
        {d.bot && <BotPill bot={d.bot} />}
        {sim && <span className="simtag">SIMULATED</span>}
        <span className="muted small">{d.bot?.description ?? ""}</span>
      </div>
      {d.missing && <div className="empty line warn">options_live_* TABLES NOT FOUND · APPLY <code>supabase/migrations/003_options_live.sql</code></div>}
      <div className={`today ${run ? (statusTone(run.status) === "bad" ? "bad" : statusTone(run.status) === "ok" ? "ok" : "warn") : "idle"}`} role="status">
        <b className="t-head">TODAY {dayLabel(today)}</b><span className="t-state">{runLine}</span>
        <span className="t-pl">DAY <Sgn v={last && last.snap_date === today ? last.day_pl : null} /></span>
      </div>
      <div className="stats">
        <Stat label="EQUITY" hero sub={last ? `SYNC ${etTime(last.taken_at ?? null)} ET${sim ? " · SIM" : ""}` : "NO SYNC YET"}>{usd(last?.equity ?? null)}</Stat>
        <Stat label="OPEN SPREADS" sub={`CREDIT ${usd(credit, false, 0)}`}>{d.positions.length}</Stat>
        <Stat label="OPEN P&L" term="OPEN P&L"><Sgn v={d.positions.length ? openPl : null} /></Stat>
        <Stat label="MAX LOSS AT RISK" sub={last?.equity ? `${pct((100 * atRisk) / last.equity, 1)} OF EQUITY` : undefined}>{usd(atRisk, false, 0)}</Stat>
        <Stat label="CLOSED P&L" sub={d.trades.length ? `${wins}/${d.trades.length} WINS` : "NO CLOSED TRADES"}><Sgn v={d.trades.length ? closedPl : null} /></Stat>
      </div>

      <Panel title="Next decision · sleeves" code="NEXT" sim={sim}
        meta={`RUN ${nd.run} ET · DECIDES ${nd.decide}${nd.half ? " · HALF DAY" : ""} · 12:45 / 12:55 ON HALF DAYS`}>
        <div className="stats inner">
          <Stat label="NEXT DECISION" sub={`${nd.date === today ? "TODAY" : dayLabel(nd.date)} · DECIDES ${nd.decide} ET`}>{nd.run} ET</Stat>
          {nd.doneToday && <Stat label="TODAY" sub="DAILY RUN RECORDED">DONE</Stat>}
          {sleeves.map((x) => (
            <Stat key={x.id} label={x.label} sub={x.lastAction ? `LAST PLAN: ${x.lastAction.toUpperCase()}` : "NO PLAN RECORDED YET"}>
              {x.open ? <>{x.open} OPEN · <Sgn v={x.openPl} digits={0} /></> : "FLAT"}
            </Stat>
          ))}
        </div>
        <p className="note">09:45 expiry sweep · intraday exits every 15 min 10:00–15:30 · daily run {nd.run} ET (decision {nd.decide}) · 16:15 sync. Condor and dip share one max-loss budget.</p>
      </Panel>

      <Panel n={1} hot title="Open spreads" code="SPRD" sim={sim} meta={`${d.positions.length} OPEN · CREDIT AND MAX LOSS IN $ · DTE = CALENDAR DAYS`}>
        {riskDays.length > 0 && (
          <div className="evflag small"><span className="muted">FOMC · CPI · JOBS · OPEX BEFORE THE LAST EXPIRY {md(lastExp)}</span> {sample && <span className="simtag">SAMPLE</span>} <EvChips events={riskDays} /></div>
        )}
        {d.positions.length === 0 ? <Empty>FLAT · NO OPEN SPREADS</Empty> : (
          <div className="scroll"><table className="dense fit">
            <thead><tr><th>Spread</th><th>Legs</th><th className="num">Qty</th><th>Opened</th><th>Expiry</th><th className="num">DTE</th>
              <th className="num">Credit</th><th className="num">Mark</th><th className="num">Max loss</th><th className="num">P&amp;L</th><th className="num">% Max</th><th>Events to expiry</th><th className="fill" /></tr></thead>
            <tbody>{d.positions.map((p) => {
              const ev = p.expiry ? riskDaysUntil(events, today, p.expiry) : [];
              const credit$ = (p.entry_credit ?? 0) * 100 * (p.qty ?? 0);
              const n = dte(p.expiry, today);
              return (
                <tr key={p.spread_id} data-ticker={p.underlying ?? undefined}>
                  <td><span className="amber b">{p.underlying}</span> <span className="muted small">{p.spread_id}</span></td>
                  <td title={`${p.short_symbol ?? ""} / ${p.long_symbol ?? ""}`}>{legs(p)}</td>
                  <td className="num">{p.qty ?? "—"}</td><td>{ymd(p.entry_date)}</td><td>{ymd(p.expiry)}</td>
                  <td className={`num${n !== null && n <= 2 ? " warn" : ""}`}>{n ?? "—"}<span className="muted small"> · {sessionsLeft(p.expiry, today) ?? "—"}S</span></td>
                  <td className="num">{usd(credit$, false, 0)} <span className="muted small">{num(p.entry_credit, 2)}</span></td>
                  <td className="num">{num(p.mark_debit, 2)}</td>
                  <td className="num">{usd(p.max_loss, false, 0)}</td>
                  <td className="num"><Sgn v={p.unrealized_pl} /></td>
                  <td className="num"><Sgn v={p.unrealized_pl !== null && p.max_loss ? (100 * p.unrealized_pl) / p.max_loss : null} kind="pct" /></td>
                  <td><EvChips events={ev} /></td><td className="fill" />
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Panel>

      <div className="grid2">
        <Panel n={2} title="Live equity" code="GP" sim={sim} meta={last ? `DAILY SYNC · ${d.snaps.length} DAYS` : "AFTER THE FIRST SYNC"}>
          <EquityChart points={eq} baseline={d.snaps[0]?.equity} ariaLabel="Options paper account equity by day" group="optlive" dayPl={d.snaps.map((x) => x.day_pl)} height={180} />
        </Panel>
        <Panel n={3} title="Runs & orders" code="ORD" sim={sim} meta="LAST 5 RUNS · LIMIT LADDER PER SPREAD">
          {d.runs.length === 0 ? <Empty>NO RUNS YET</Empty> : (
            <div className="scroll"><table className="dense fit">
              <thead><tr><th>Run</th><th>Kind</th><th>Status</th><th className="num">VIX</th><th className="num">In</th><th className="num">Out</th><th className="fill" /></tr></thead>
              <tbody>{d.runs.slice(0, 5).map((r) => (
                <tr key={r.id}><td>{dayLabel(r.run_date)} <span className="muted small">{etHm(r.started_at)}</span></td><td>{(r.kind ?? "").toUpperCase()}{r.mode !== "execute" ? " · DRY" : ""}</td>
                  <td><span className={`pill ${statusTone(r.status)}`}>{r.status.toUpperCase()}</span></td><td className="num">{num(r.vix, 1)}</td>
                  <td className="num">{r.n_entries ?? 0}</td><td className="num">{r.n_exits ?? 0}</td><td className="fill" /></tr>
              ))}</tbody>
            </table></div>
          )}
          {d.orders.length > 0 && (
            <div className="scroll"><table className="dense fit">
              <thead><tr><th>Sent ET</th><th>Spread</th><th>Purpose</th><th className="num">Qty</th><th>Status</th><th className="num">Avg</th><th>Ladder</th><th className="fill" /></tr></thead>
              <tbody>{d.orders.slice(0, 12).map((o) => (
                <tr key={o.id}><td>{etTime(o.submitted_at)}</td><td className="small">{o.spread_id}</td><td>{(o.purpose ?? "").toUpperCase()}</td>
                  <td className="num">{o.filled_qty ?? 0}/{o.qty ?? 0}</td><td><span className={`pill ${statusTone(o.status)}`}>{(o.status ?? "—").toUpperCase()}</span>{o.error && <div className="small neg">{o.error}</div>}</td>
                  <td className="num">{num(o.avg_price, 2)}</td><td className="small muted">{(o.limit_prices ?? []).map((x) => Number(x).toFixed(2)).join(" → ")}</td><td className="fill" /></tr>
              ))}</tbody>
            </table></div>
          )}
        </Panel>
      </div>

      <Panel n={4} title="Closed spreads" code="BLTR" sim={sim} meta={`${d.trades.length} TRADES · % = P&L ÷ MAX LOSS AT ENTRY`}>
        {d.trades.length === 0 ? <Empty>NO CLOSED SPREADS YET</Empty> : (
          <div className="scroll"><table className="dense fit">
            <thead><tr><th>Spread</th><th>Strikes</th><th className="num">Qty</th><th>Opened</th><th>Closed</th><th className="num">Credit</th><th className="num">Debit</th><th className="num">P&amp;L</th><th className="num">%</th><th>Exit</th><th className="fill" /></tr></thead>
            <tbody>{d.trades.slice(0, 40).map((t) => (
              <tr key={t.id} data-ticker={t.underlying ?? undefined}>
                <td><span className="amber">{t.underlying}</span> <span className="muted small">{ymd(t.expiry)}</span></td>
                <td>−P{num(t.short_strike, 0)} / +P{num(t.long_strike, 0)}</td><td className="num">{t.qty ?? "—"}</td>
                <td>{ymd(t.opened_at)}</td><td>{ymd(t.closed_at)}</td>
                <td className="num">{num(t.entry_credit, 2)}</td><td className="num">{num(t.exit_debit, 2)}</td>
                <td className="num"><Sgn v={t.pnl} /></td><td className="num"><Sgn v={t.pnl_pct === null ? null : t.pnl_pct * 100} kind="pct" /></td>
                <td className="small">{(t.exit_reason ?? "—").replace(/_/g, " ").toUpperCase()}</td><td className="fill" />
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Panel>
    </section>
  );
}
