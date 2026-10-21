import { Panel } from "@/components/Bits";

export default function Options() {
  return (
    <>
      <div className="pagehead">
        <h1>Options Bot</h1><span className="fn">OPTN</span>
        <div className="right"><span className="pill off">PLANNED</span></div>
        <p>A future project. Positions, Greeks and performance will appear here once it&apos;s built.</p>
      </div>
      <Panel n={1} title="Status" code="OPTN">
        <div className="empty blink-cursor">AWAITING BUILD</div>
      </Panel>
    </>
  );
}
