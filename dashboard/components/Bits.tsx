import type { Bot } from "@/lib/data";
import { heartbeatAge } from "@/lib/format";

/** One pill that answers "do I need to look at this bot?" */
export function BotPill({ bot }: { bot: Bot }) {
  if (bot.status === "planned") return <span className="pill off">PLANNED</span>;
  if (bot.status === "inactive") return <span className="pill off">INACTIVE</span>;
  const hb = heartbeatAge(bot.last_heartbeat);
  if (hb.stale) return <span className="pill bad">NO RUN {hb.label === "never" ? "YET" : `SINCE ${hb.label.toUpperCase()}`}</span>;
  if (bot.run_state === "error") return <span className="pill bad">ERROR</span>;
  if (bot.run_state === "warning") return <span className="pill warn">CHECK ORDERS</span>;
  if (bot.run_state === "running") return <span className="pill warn">RUNNING</span>;
  return <span className="pill ok">HEALTHY</span>;
}

/** Bloomberg-style panel: amber title strip with a function code on the right. */
export function Panel({ n, title, code, meta, children, className = "" }: {
  n?: number; title: string; code?: string; meta?: React.ReactNode; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-head">
        <h2>{n !== undefined && <span className="pn">{n})</span>} {title}</h2>
        {meta && <span className="meta">{meta}</span>}
        {code && <span className="code">{code}</span>}
      </div>
      <div className="panel-body">{children}</div>
    </section>
  );
}

export function NoAccess() {
  return (
    <div className="empty">
      <b>ACCESS DENIED.</b> This account isn&apos;t on the admin list. Add its email to the <code>dashboard_admins</code> table in Supabase, then reload.
    </div>
  );
}
