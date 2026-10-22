import { BotPill, BtTag, PageHead } from "@/components/Bits";
import { OptionsLab } from "@/components/OptionsLab";
import { OptionsLive } from "@/components/OptionsLive";
import { getEvents, getOptionsLive } from "@/lib/data";
import { getBacktest } from "@/lib/options-backtest";
import { ymd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function Options() {
  const lab = getBacktest();
  const [live, ev] = await Promise.all([getOptionsLive().catch(() => null), getEvents().catch(() => null)]);
  const active = live?.bot?.status === "active";
  return (
    <>
      <PageHead fn="OPTN" title="Options bot"
        desc={<>Live paper trading on its own Alpaca paper account, then the hypothetical backtest lab{lab.period.start ? ` (${ymd(lab.period.start)} → ${ymd(lab.period.end)})` : ""}.</>}
        right={<>{live?.bot ? <BotPill bot={live.bot} /> : <span className="pill off">NO ACCESS</span>}</>}
        keys={[{ n: 95, label: "ALPACA", href: "https://app.alpaca.markets/paper/dashboard/overview", ext: true }, { n: 96, label: "LIVE", href: "#live" }, { n: 97, label: "BT LAB", href: "#lab" }, { n: 98, label: "EVENTS", href: "/events?c=opx" }]} />

      {live && (active || live.positions.length > 0 || live.runs.length > 0 || live.snaps.length > 0)
        ? <OptionsLive d={live} events={ev?.events ?? []} sample={!!ev?.sample} />
        : <div className="empty line">LIVE PAPER: {live ? "THE OPTIONS BOT HAS NO LIVE RUNS OR SYNCS YET" : "NOT AVAILABLE FOR THIS ACCOUNT"}</div>}

      <div className="labhead" id="lab">
        <span className="fn bt">BT</span><h2>Backtest lab</h2><BtTag />
        {lab.sample && <span className="pill bad samplebadge">SAMPLE DATA — BACKTEST NOT YET LOADED</span>}
        <span className="muted small">{lab.generated_at ? `GENERATED ${ymd(lab.generated_at.slice(0, 10))}` : ""}</span>
      </div>
      <div className="btband">
        <b>BACKTEST · HYPOTHETICAL.</b> Simulated fills, no slippage beyond the model&apos;s assumptions, and hindsight in the strategy design. Past simulated results don&apos;t predict live results. The LIVE PAPER section above is the real paper account.
        {lab.assumptions && <> <span className="muted">FILLS:</span> {lab.assumptions}</>}
        {lab.code_version && <> <span className="muted">CODE:</span> <code>{lab.code_version}</code></>}
      </div>
      <OptionsLab lab={lab} off={4} />
    </>
  );
}
