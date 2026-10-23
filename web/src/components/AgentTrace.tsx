import { useEffect, useRef } from "react";
import type { TraceEntry } from "../state";
import { fmtTime } from "../format";

export interface AgentTraceProps {
  entries: TraceEntry[];
  /** null while the session is unknown; otherwise whether the treasurer runs live against the model. */
  liveAi: boolean | null;
  onClear: () => void;
}

const KIND_LABEL: Record<TraceEntry["kind"], string> = {
  text: "agent",
  tool_call: "tool",
  tool_result: "result",
  system: "system",
};

function argsText(args: unknown): string {
  if (args === undefined || args === null) return "";
  if (typeof args === "string") return args;
  try {
    const s = JSON.stringify(args);
    return s.length > 300 ? `${s.slice(0, 300)}…` : s;
  } catch {
    return String(args);
  }
}

/** Scrolling list of `trace` events; sticks to the bottom unless the viewer scrolled up. */
export function AgentTrace({ entries, liveAi, onClear }: AgentTraceProps) {
  const listRef = useRef<HTMLOListElement | null>(null);
  const stickRef = useRef(true);

  useEffect(() => {
    const el = listRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [entries]);

  const onScroll = () => {
    const el = listRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  return (
    <section className="tk-card tk-trace" aria-label="Agent trace">
      <h2 className="tk-card-title">
        <span>Treasurer trace</span>
        <span className="tk-chips">
          {liveAi !== null && (
            <span className={`tk-chip ${liveAi ? "tk-chip-accent" : "tk-chip-pending"}`}>
              <span className="tk-dot" />
              {liveAi ? "live AI" : "replay / rules mode"}
            </span>
          )}
          <button type="button" className="tk-btn tk-btn-sm tk-btn-ghost" onClick={onClear} disabled={!entries.length}>
            clear
          </button>
        </span>
      </h2>
      <ol className="tk-trace-list" ref={listRef} onScroll={onScroll} aria-live="polite">
        {entries.length === 0 && <li className="tk-trace-empty">No agent activity yet. Run a round from the judge bar.</li>}
        {entries.map((e) => (
          <li key={e.id} className={`tk-trace-item tk-trace-${e.kind}`}>
            <span className="tk-trace-kind">{KIND_LABEL[e.kind] ?? e.kind}</span>
            <span className="tk-trace-body">
              {e.kind === "tool_call" && e.tool && <span className="tk-trace-tool">{e.tool}</span>}
              {e.kind === "tool_call" && e.tool && argsText(e.args) ? ` ${argsText(e.args)}` : ""}
              {e.kind === "tool_call" && e.tool && e.text ? "\n" : ""}
              {e.text}
              <span className="tk-trace-meta">
                {fmtTime(e.ts)}
                {typeof e.ms === "number" ? ` · ${e.ms} ms` : ""}
              </span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
