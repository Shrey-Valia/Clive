import { NoAccess, Panel } from "@/components/Bits";
import { getLucidAccounts } from "@/lib/data";
import { etTime, sign, usd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Lucid() {
  const accounts = await getLucidAccounts();
  if (!accounts) return <NoAccess />;

  return (
    <>
      <div className="pagehead">
        <h1>Lucid Bot</h1><span className="fn">LUCD</span>
        <div className="right"><span className="pill off">INACTIVE</span></div>
        <p>Funded futures accounts: balance, day P&amp;L and room above each account&apos;s max-loss floor.</p>
      </div>

      <Panel n={1} title="Execution path" code="ROUTE">
        <div className="flow"><span>TRADINGVIEW PINE ALERT</span><i>→</i><span>CROSSTRADE WEBHOOK</span><i>→</i><span>TRADOVATE ORDER</span></div>
        <p className="muted">The bot is switched off. Once it&apos;s running again, each account writes to the <code>lucid_accounts</code> and <code>lucid_daily</code> tables and shows up below.</p>
      </Panel>

      <Panel n={2} title="Funded accounts" code="ACCT">
        <div className="scroll"><table>
          <thead><tr><th>Account</th><th>Plan</th><th>Link</th><th className="num">Balance</th><th className="num">Day</th><th className="num">MLL floor</th><th className="num">Room</th><th className="num">To target</th><th>Position</th><th>Updated</th></tr></thead>
          <tbody>
            {accounts.length === 0 ? (
              <tr><td colSpan={10}><div className="empty blink-cursor">NO ACCOUNTS CONNECTED</div></td></tr>
            ) : accounts.map((a) => (
              <tr key={a.account_id}>
                <td>{a.label ?? a.account_id}</td><td>{a.plan ?? "—"}</td>
                <td><span className={`pill ${a.connection === "connected" ? "ok" : "bad"}`}>{(a.connection ?? "unknown").toUpperCase()}</span></td>
                <td className="num">{usd(a.balance)}</td><td className={`num ${sign(a.day_pl)}`}>{usd(a.day_pl, true)}</td>
                <td className="num">{usd(a.max_loss_floor)}</td>
                <td className="num">{a.balance != null && a.max_loss_floor != null ? usd(Number(a.balance) - Number(a.max_loss_floor)) : "—"}</td>
                <td className="num">{a.balance != null && a.profit_target != null ? usd(Number(a.profit_target) - Number(a.balance)) : "—"}</td>
                <td>{a.open_position ?? "FLAT"}</td><td>{etTime(a.updated_at)}</td>
              </tr>))}
          </tbody>
        </table></div>
      </Panel>
    </>
  );
}
