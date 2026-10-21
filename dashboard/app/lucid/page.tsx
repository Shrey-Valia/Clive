import { Abbr, Empty, NoAccess, PageHead, Panel, Sgn } from "@/components/Bits";
import { getLucidAccounts } from "@/lib/data";
import { etTime, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Lucid() {
  const accounts = await getLucidAccounts();
  if (!accounts) return <NoAccess />;

  return (
    <>
      <PageHead fn="LUCD" title="Lucid Bot" desc="Funded futures accounts: balance, day P&L and room above each account's max-loss floor."
        right={<span className="pill off">INACTIVE</span>} />

      <Panel n={1} title="Execution path" code="ROUTE">
        <div className="flow"><span>TRADINGVIEW PINE ALERT</span><i>→</i><span>CROSSTRADE WEBHOOK</span><i>→</i><span>TRADOVATE ORDER</span></div>
        <p className="note">The bot is switched off. Once it&apos;s running again, each account writes to the <code>lucid_accounts</code> and <code>lucid_daily</code> tables and shows up below.</p>
      </Panel>

      <Panel n={2} hot title="Funded accounts" code="ACCT">
        {accounts.length === 0 ? <Empty>NO ACCOUNTS CONNECTED</Empty> : (
          <div className="scroll"><table className="dense fit">
            <thead><tr><th>Account</th><th>Plan</th><th>Link</th><th className="num">Balance</th><th className="num">Day</th><th className="num"><Abbr term="MLL">MLL floor</Abbr></th><th className="num">Room</th><th>Distance to MLL</th><th className="num">To target</th><th>Position</th><th>Updated ET</th><th className="fill" /></tr></thead>
            <tbody>{accounts.map((a) => {
              const room = a.balance != null && a.max_loss_floor != null ? Number(a.balance) - Number(a.max_loss_floor) : null;
              const toT = a.balance != null && a.profit_target != null ? Number(a.profit_target) - Number(a.balance) : null;
              const span = room !== null && toT !== null ? room + toT : null;
              return (
                <tr key={a.account_id}>
                  <td className="amber">{a.label ?? a.account_id}</td><td>{(a.plan ?? "—").toUpperCase()}</td>
                  <td><span className={`pill ${a.connection === "connected" ? "ok" : "bad"}`}>{(a.connection ?? "unknown").toUpperCase()}</span></td>
                  <td className="num">{usd(a.balance)}</td><td className="num"><Sgn v={a.day_pl === null ? null : Number(a.day_pl)} /></td>
                  <td className="num">{usd(a.max_loss_floor)}</td><td className="num">{usd(room)}</td>
                  <td>{span ? <span className="ex-track mll"><i className={room! / span < 0.25 ? "ex-neg" : "ex-pct"} style={{ width: `${Math.max(2, (100 * room!) / span)}%` }} /></span> : "—"}</td>
                  <td className="num">{usd(toT)}</td><td>{a.open_position ?? "FLAT"}</td><td>{etTime(a.updated_at)}</td><td className="fill" />
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </Panel>
    </>
  );
}
