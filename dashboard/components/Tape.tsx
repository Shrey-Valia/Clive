import type { TapeItem } from "@/lib/data";
import { dayLabel } from "@/lib/format";

/** Scrolling ticker of the latest scan, prefixed with its date. Doubled so the loop is seamless; static under reduced motion. */
export function Tape({ date, live, items }: { date: string | null; live: boolean; items: TapeItem[] }) {
  if (!items.length) return <div className="tape"><div className="tape-idle">NO SCAN YET · NEXT SCAN 15:45 ET</div></div>;
  const row = items.map((t) => (
    <span key={t.ticker} className={t.picked ? "picked" : ""}>
      <b>{t.ticker}</b> <i className={t.change >= 0 ? "pos" : "neg"}><small>{t.change >= 0 ? "▲" : "▼"}</small>{Math.abs(t.change).toFixed(1)}%</i>
      {t.picked && <em title={live ? "Shorted" : "Would short (dry run)"}>S</em>}
    </span>
  ));
  return (
    <div className="tape" aria-label="Latest scan">
      {date && <div className="tape-date">SCAN {dayLabel(date)} · {live ? "LIVE" : "DRY"}</div>}
      <div className="tape-view">
        <div className="tape-track" style={{ animationDuration: `${items.length * 4}s` }}>
          <div className="tape-run">{row}</div>
          <div className="tape-run" aria-hidden="true">{row}</div>
        </div>
      </div>
    </div>
  );
}
