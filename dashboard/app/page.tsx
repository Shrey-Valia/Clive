import Link from "next/link";
import { DrawdownChart, EquityChart } from "@/components/Charts";
import { AlertsTable, BotPill, DirChip, Empty, ModePill, NoAccess, PageHead, Panel, Sgn, Stat, TodayBanner } from "@/components/Bits";
import { EvChips, EvLegend, SampleTag, TwoWeeks } from "@/components/Events";
import { MonthCalendar } from "@/components/EventsClient";
import { ScoreContributionBar, Sparkline } from "@/components/Viz";
import { Num } from "@/components/Live";
import { getBots, getEvents, getOptionsLive, getShorting } from "@/lib/data";
import { equitySeries, isLive, runFlow, systemLog, todayState } from "@/lib/analytics";
import { companyEventsWithin } from "@/lib/events";
import { etParts } from "@/lib/time";
import { CATALYST } from "@/lib/glossary";
import { ago, etHm, etTime, md, num, usd, ymd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const [bots, s, ev, opt] = await Promise.all([getBots(), getShorting(), getEvents(), getOptionsLive().catch(() => null)]);
  if (!bots || !s) return <NoAccess />;
  const todayEt = etParts().date;
  const events = ev?.events ?? [];
  const v2 = runFlow(s.run) === "intraday_v2";
  const sim = s.simulated;
  const byId = Object.fromEntries(bots.map((b) => [b.id, b]));
  const equity = s.account ? Number(s.account.equity) : null;
  const today = todayState(s.run, s.orders, s.account, new Date(), { runs: s.runs, bookOrders: s.bookOrders });
  const first = s.snaps[0]?.equity ?? null;
  const openPl = s.positions.reduce((t, p) => t + Number(p.unrealized_pl), 0);
  const basis = s.positions.reduce((t, p) => t + Math.abs(Number(p.avg_entry_price) * Number(p.qty)), 0);
  const gross = s.positions.reduce((t, p) => t + Math.abs(Number(p.market_value)), 0);
  const live = isLive(s.run);
  const picks = s.candidates.filter((c) => c.selected).sort((a, b) => a.rank - b.rank);
  const last5 = s.snaps.slice(-6).map((x) => x.day_pl ?? 0).slice(1);
  const eq = equitySeries(s.snaps);
  const log = systemLog(s.bot, s.runs, s.orders, s.snaps, s.events, s.syncs).slice(0, 8);
  const optEq = opt?.snaps.length ? opt.snaps[opt.snaps.length - 1] : null;
  const shorting = byId.shorting;
  const others = bots.filter((b) => b.id !== "shorting").sort((a, b) => a.id.localeCompare(b.id));

  return (
    <>
      <PageHead fn="HOME" title="Overview" desc="Every bot, whether it ran today, and what it made."
        keys={[{ n: 95, label: "ALPACA", href: "https://app.alpaca.markets/paper/dashboard/overview", ext: true }, { n: 97, label: "RUNS", href: "/shorting/runs" }, { n: 98, label: "BT LAB", href: "/options" }]} />

      <TodayBanner t={today} />

      <div className="stats">
        <Stat label="DAY P&L · SHORTING" term="DAY P&L" hero sub={<Sgn v={today.dayPct} kind="pct" digits={2} />}><Num v={today.dayPl}><Sgn v={today.dayPl} /></Num></Stat>
        <Stat label="EQUITY" sub={sim ? "SIMULATED" : s.account ? `LIVE · ${etHm(new Date().toISOString())} ET` : "ALPACA OFFLINE"}>{usd(equity)}</Stat>
        <Stat label="OPEN P&L" term="OPEN P&L" sub={<Sgn v={basis ? (100 * openPl) / basis : null} kind="pct" />}><Sgn v={s.positions.length ? openPl : null} /></Stat>
        <Stat label="GROSS" term="GROSS" sub={`${s.positions.length} POSITIONS`}>{equity ? `${((100 * gross) / equity).toFixed(0)}%` : "—"}</Stat>
        <Stat label="SINCE START" sub={first && equity ? <Sgn v={(100 * (equity - first)) / first} kind="pct" digits={2} /> : "NEEDS 2 DAILY SYNCS"}>
          {first && equity ? <Sgn v={equity - first} /> : "—"}
        </Stat>
        <Stat label="LAST SCAN" sub={s.run ? <>{live ? "LIVE" : "DRY RUN"} · {s.run.n_selected ?? 0} PICKS</> : undefined}>{s.run ? ymd(s.run.run_date) : "—"}</Stat>
      </div>

      {shorting && (
        <Link href="/shorting" className="panel botcard" data-panel={1} id="p1">
          <div className="panel-head"><h2><span className="pn">1)</span> {shorting.name}</h2><BotPill bot={shorting} run={s.run} /><ModePill run={s.run} /><span className="code">SHRT</span></div>
          <div className="panel-body botrow">
            <dl className="kv">
              <dt>HEARTBEAT</dt><dd>{ago(shorting.last_heartbeat)}</dd>
              <dt>LAST MSG</dt><dd className="clip">{shorting.last_message ?? "—"}</dd>
              <dt>POSITIONS</dt><dd>{s.positions.length}{sim ? " · SIM" : ""}</dd>
              <dt>BROKER</dt><dd>ALPACA PAPER</dd>
            </dl>
            <div className="spark-tile">
              <span className="st-l">LAST 5 DAYS P&amp;L{sim ? " · SIMULATED" : ""}</span>
              <Sparkline values={last5.reduce<number[]>((a, v) => [...a, (a[a.length - 1] ?? 0) + v], [])} width={140} height={32} baseline={0} ariaLabel="Cumulative P&L, last 5 days" />
              <Sgn v={last5.length ? last5.reduce((a, b) => a + b, 0) : null} />
            </div>
          </div>
        </Link>
      )}
      <div className="slimbots">
        {others.map((b, i) => (
          <Link key={b.id} href={`/${b.id}`} className="slimbot" data-panel={i + 2}>
            <span className="pn">{i + 2})</span><b>{b.name}</b><BotPill bot={b} />
            <span className="muted clip">{b.kind === "options"
              ? b.status === "active" ? <>LIVE PAPER{opt?.simulated ? " · SIMULATED" : ""} · EQUITY {usd(optEq?.equity ?? null)} · {opt?.positions.length ?? 0} OPEN SPREADS · DAY <Sgn v={optEq?.day_pl ?? null} /></> : "BACKTEST LAB · HYPOTHETICAL RESULTS"
              : b.description}</span>
            <span className="code">{b.kind === "options" ? "OPTN" : "LUCD"}</span>
          </Link>
        ))}
      </div>

      <Panel n={4} hot title={<>Short book{s.run ? ` · ${ymd(s.run.run_date)}` : ""}</>} code="BOOK"
        meta={<>{s.run && <ModePill run={s.run} />} {v2 ? `${live ? "SHORT" : "WOULD SHORT"} AT THE NEXT 09:31 OPEN · COVER 15:55` : live ? "SHORTED AT 15:55 · SIZE BY RANK" : "DRY RUN · NOTHING WAS SENT"}</>}>
        {picks.length === 0 ? (
          <Empty>NO PICKS YET · NEXT SCAN 15:45 ET</Empty>
        ) : (
          <>
            <div className="scroll desk-only"><table className="dense fit">
              <thead><tr><th className="num">Rank</th><th>Ticker</th><th className="num">Chg</th><th className="num">Score</th><th>Breakdown</th><th>News</th><th>Decision</th><th>Events ≤2 sessions</th><th className="num">Shares</th><th className="num">Size</th><th className="num">Notional</th><th className="fill" /></tr></thead>
              <tbody>{picks.map((c) => (
                <tr key={c.ticker} data-ticker={c.ticker} className="picked">
                  <td className="num">{c.rank}</td><td className="amber b">{c.ticker}</td><td className="num pos">+{Number(c.change).toFixed(1)}%</td>
                  <td className="num score" style={{ background: `rgba(255,160,40,${(Math.max(0, Math.min(1, (c.short_score - 30) / 70)) * 0.35).toFixed(3)})` }}><b>{num(c.short_score)}</b></td>
                  <td><ScoreContributionBar components={c.components} score={c.short_score} picked /></td>
                  <td>{CATALYST[c.catalyst ?? ""]?.label ?? "—"}</td>
                  <td><DirChip side={(c.target_qty ?? -1) < 0 ? "short" : "long"} would={!live} /></td>
                  <td><EvChips events={companyEventsWithin(events, c.ticker, todayEt)} /></td>
                  <td className="num">{Math.abs(c.target_qty ?? 0)}</td><td className="num">{num(c.target_pct, 0)}%</td>
                  <td className="num">{usd(Math.abs(c.target_qty ?? 0) * Number(c.close))}</td><td className="fill" />
                </tr>))}
              </tbody>
            </table></div>
            <div className="mob-only cards">
              {picks.map((c) => (
                <div key={c.ticker} className="card picked" data-ticker={c.ticker}>
                  <div className="card-top"><span className="muted">#{c.rank}</span><span className="b amber">{c.ticker}</span><span className="pos">+{Number(c.change).toFixed(1)}%</span>
                    <DirChip side={(c.target_qty ?? -1) < 0 ? "short" : "long"} would={!live} /><EvChips events={companyEventsWithin(events, c.ticker, todayEt)} /></div>
                  <ScoreContributionBar components={c.components} score={c.short_score} width="100%" picked />
                  <div className="card-kv"><span>SCORE <b>{num(c.short_score)}</b></span><span>SHARES <b>{Math.abs(c.target_qty ?? 0)}</b></span><span>SIZE <b>{num(c.target_pct, 0)}%</b></span><span>NOTIONAL <b>{usd(Math.abs(c.target_qty ?? 0) * Number(c.close), false, 0)}</b></span></div>
                </div>
              ))}
            </div>
          </>
        )}
      </Panel>

      <Panel n={7} id="cal" title="Market events" code="EVT" meta={<>{ev?.sample && <SampleTag />}<EvLegend compact /><Link href="/events">ALL EVENTS ›</Link></>}>
        {!ev ? <Empty>EVENTS UNAVAILABLE</Empty> : ev.missing ? (
          <div className="empty line warn">market_events TABLE NOT FOUND · APPLY MIGRATION 005, THEN RUN events/sync_events.py</div>
        ) : (
          <div className="grid2 tight">
            <div><h3 className="subhead">THIS WEEK AND NEXT</h3><TwoWeeks events={events} today={todayEt} /></div>
            <div><MonthCalendar events={events} today={todayEt} from={ev.from} to={ev.to} maxChips={3} /></div>
          </div>
        )}
      </Panel>

      {s.alerts.length > 0 && (
        <Panel n={8} id="alerts" title="Open alerts" code="ALRT" meta={`${s.alerts.length} OPEN${sim ? " · SAMPLE" : ""}`}>
          <AlertsTable rows={s.alerts} />
        </Panel>
      )}

      <div className="grid2">
        <Panel n={5} title="Shorting equity" code="GP" sim={sim} meta={s.snaps.length ? `LAST SYNC ${etTime(s.snaps[s.snaps.length - 1].taken_at ?? null)} ET` : "DAILY CLOSE · 16:15 ET SYNC"}>
          <EquityChart points={eq} baseline={first ?? undefined} ariaLabel="Shorting bot equity by day" group="ov" dayPl={s.snaps.map((x) => x.day_pl)} />
          <DrawdownChart equity={eq} compact ariaLabel="Drawdown from peak" group="ov" />
        </Panel>
        <Panel n={6} title="System log" code="LOG" meta={<Link href="/shorting#log">FULL LOG ›</Link>}>
          {log.length === 0 ? <Empty>NO EVENTS YET</Empty> : (
            <ol className="syslog">{log.map((l, i) => (
              <li key={i} className={l.level.toLowerCase()}><time>{etTime(l.at)}</time><b>{l.level}</b><span className="step">{l.step}</span><span>{l.message}</span></li>
            ))}</ol>
          )}
        </Panel>
      </div>
    </>
  );
}
