import Link from "next/link";
import { EquityChart } from "@/components/Charts";
import { BotPill, NoAccess, Panel } from "@/components/Bits";
import { getBots, getShorting } from "@/lib/data";
import { etTime, heartbeatAge, sign, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Overview() {
  const [bots, s] = await Promise.all([getBots(), getShorting()]);
  if (!bots || !s) return <NoAccess />;

  const byId = Object.fromEntries(bots.map((b) => [b.id, b]));
  const equity = s.account ? Number(s.account.equity) : null;
  const dayPl = s.account ? Number(s.account.equity) - Number(s.account.last_equity) : null;
  const first = s.snaps[0]?.equity ?? null;
  const openPl = s.positions.reduce((t, p) => t + Number(p.unrealized_pl), 0);
  const gross = s.positions.reduce((t, p) => t + Math.abs(Number(p.market_value)), 0);

  return (
    <>
      <div className="pagehead">
        <h1>Overview</h1><span className="fn">HOME</span>
        <p>Every bot, whether it ran, and what it made.</p>
      </div>

      <div className="stats">
        <div><b>{usd(equity)}</b><span>Shorting equity</span></div>
        <div><b className={sign(dayPl)}>{usd(dayPl, true)}</b><span>Day P&amp;L</span></div>
        <div><b className={sign(openPl)}>{usd(openPl, true)}</b><span>Open P&amp;L</span></div>
        <div><b>{equity ? `${((100 * gross) / equity).toFixed(0)}%` : "—"}</b><span>Gross short / equity</span></div>
        <div><b className={sign(first && equity ? equity - first : null)}>{first && equity ? usd(equity - first, true) : "—"}</b><span>Since tracking began</span></div>
        <div><b>{s.run?.run_date ?? "—"}</b><span>Last scan</span></div>
      </div>

      <div className="grid3">
        {["shorting", "lucid", "options"].map((id, i) => {
          const b = byId[id];
          if (!b) return null;
          return (
            <Link key={id} href={`/${id}`} className="panel botcard">
              <div className="panel-head"><h2><span className="pn">{i + 1})</span> {b.name}</h2><span className="code">{["SHRT", "LUCD", "OPTN"][i]}</span></div>
              <div className="panel-body">
                <div><BotPill bot={b} /></div>
                <p className="muted">{b.description}</p>
                {b.status === "active" ? (
                  <dl className="kv">
                    <dt>Heartbeat</dt><dd>{heartbeatAge(b.last_heartbeat).label}</dd>
                    <dt>Last msg</dt><dd>{b.last_message ?? "—"}</dd>
                    <dt>Positions</dt><dd>{s.positions.length}</dd>
                    <dt>Broker</dt><dd>ALPACA PAPER</dd>
                  </dl>
                ) : b.kind === "lucid" ? (
                  <div className="flow"><span>PINE</span><i>→</i><span>CROSSTRADE</span><i>→</i><span>TRADOVATE</span></div>
                ) : (
                  <p className="muted blink-cursor">AWAITING BUILD</p>
                )}
              </div>
            </Link>
          );
        })}
      </div>

      <Panel n={4} title={`Short book${s.run ? ` · ${s.run.run_date}` : ""}`} code="BOOK" meta="TODAY'S PICKS · SIZE BY RANK">
        {s.candidates.filter((c) => c.selected).length === 0 ? (
          <div className="empty blink-cursor">NO PICKS YET · NEXT SCAN 15:45 ET</div>
        ) : (
          <div className="scroll"><table>
            <thead><tr><th className="num">Rank</th><th>Ticker</th><th className="num">Chg</th><th className="num">Score</th><th>News</th><th className="num">Shares</th><th className="num">Size</th><th className="num">Notional</th></tr></thead>
            <tbody>{s.candidates.filter((c) => c.selected).map((c) => (
              <tr key={c.ticker}>
                <td className="num">{c.rank}</td><td className="amber">{c.ticker}</td><td className="num pos">+{Number(c.change).toFixed(1)}%</td>
                <td className="num">{Number(c.short_score).toFixed(1)}</td><td>{(c.catalyst ?? "—").replace("_", " ").toUpperCase()}</td>
                <td className="num">{Math.abs(c.target_qty ?? 0)}</td><td className="num">{c.target_pct}%</td>
                <td className="num">{usd(Math.abs(c.target_qty ?? 0) * Number(c.close))}</td>
              </tr>))}
            </tbody>
          </table></div>
        )}
      </Panel>

      <Panel n={5} title="Shorting bot equity" code="GP" meta={s.bot?.last_heartbeat ? `UPDATED ${etTime(s.bot.last_heartbeat)}` : "DAILY CLOSE · 16:15 ET SYNC"}>
        <EquityChart points={s.snaps.map((x) => ({ date: x.snap_date, value: x.equity }))} baseline={first ?? undefined} />
      </Panel>
    </>
  );
}
