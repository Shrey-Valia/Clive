import { BtTag, PageHead } from "@/components/Bits";
import { OptionsLab } from "@/components/OptionsLab";
import { getBacktest } from "@/lib/options-backtest";
import { ymd } from "@/lib/format";

export const dynamic = "force-dynamic";

export default function Options() {
  const lab = getBacktest();
  return (
    <>
      <PageHead fn="BT" title="Options backtest lab"
        desc={<>Hypothetical results from the Options Bot backtester{lab.period.start ? ` · ${ymd(lab.period.start)} → ${ymd(lab.period.end)}` : ""}{lab.generated_at ? ` · generated ${ymd(lab.generated_at.slice(0, 10))}` : ""}. Not live trading.</>}
        right={<><BtTag />{lab.sample && <span className="pill bad samplebadge">SAMPLE DATA — BACKTEST NOT YET LOADED</span>}<span className="pill off">BOT PLANNED</span></>} />
      <div className="btband">
        <b>BACKTEST · HYPOTHETICAL.</b> Simulated fills, no slippage beyond the model&apos;s assumptions, and hindsight in the strategy design. Past simulated results don&apos;t predict live results.
        {lab.assumptions && <> <span className="muted">FILLS:</span> {lab.assumptions}</>}
        {lab.code_version && <> <span className="muted">CODE:</span> <code>{lab.code_version}</code></>}
      </div>
      <OptionsLab lab={lab} />
    </>
  );
}
