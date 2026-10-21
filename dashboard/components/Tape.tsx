/** Scrolling ticker of the latest scan. Doubled so the loop is seamless; static under reduced motion. */
export function Tape({ items }: { items: { ticker: string; change: number; picked: boolean }[] }) {
  if (!items.length) return <div className="tape"><div className="tape-idle">NO SCAN TODAY · NEXT SCAN 15:45 ET</div></div>;
  const row = items.map((t) => (
    <span key={t.ticker} className={t.picked ? "picked" : ""}>
      <b>{t.ticker}</b> <i className={t.change >= 0 ? "pos" : "neg"}>{t.change >= 0 ? "▲" : "▼"}{Math.abs(t.change).toFixed(1)}%</i>
      {t.picked && <em>SHORT</em>}
    </span>
  ));
  return (
    <div className="tape" aria-label="Latest scan">
      <div className="tape-track">
        <div className="tape-run">{row}</div>
        <div className="tape-run" aria-hidden="true">{row}</div>
      </div>
    </div>
  );
}
