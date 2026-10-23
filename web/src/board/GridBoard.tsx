/**
 * CSS-grid rotation board: the fallback used when the Bryntum Scheduler cannot load at runtime
 * (trial expiry, blocked chunk, ...). Same data as the scheduler, same props, no dependencies.
 */

import type { BoardMapping, ContributionMeta, EventMeta, PotMeta } from "./mapping";
import { STATUS_COLOR, ROUND_COLOR } from "./mapping";
import { fmtDate, shortId } from "../format";

export interface GridBoardProps {
  mapping: BoardMapping;
  selected: EventMeta | null;
  onSelect: (meta: EventMeta | null) => void;
  /** Why the scheduler is not in use (shown as a small note). */
  reason?: string | null;
}

interface Column {
  roundN: number;
  dueAt: string;
  pot: PotMeta | null;
}

export function GridBoard({ mapping, selected, onSelect, reason }: GridBoardProps) {
  const contributions = mapping.events.filter((e): e is typeof e & { tk: ContributionMeta } => e.tk.kind === "contribution");
  const pots = mapping.events.filter((e): e is typeof e & { tk: PotMeta } => e.tk.kind === "pot");
  const columns: Column[] = [];
  for (const e of contributions) {
    if (!columns.some((c) => c.roundN === e.tk.roundN)) {
      columns.push({
        roundN: e.tk.roundN,
        dueAt: e.tk.dueAt,
        pot: pots.find((p) => p.tk.roundN === e.tk.roundN)?.tk ?? null,
      });
    }
  }
  columns.sort((a, b) => a.roundN - b.roundN);
  const isSelected = (m: EventMeta) =>
    selected !== null &&
    selected.kind === m.kind &&
    selected.roundN === m.roundN &&
    (m.kind === "pot" || (selected.kind === "contribution" && selected.member === m.member));

  return (
    <div className="tk-grid" role="table" aria-label="Rotation board (grid)">
      <div className="tk-grid-row tk-grid-head" role="row">
        <div className="tk-grid-cell tk-grid-member" role="columnheader">
          Member
        </div>
        {columns.map((c) => (
          <div className="tk-grid-cell" role="columnheader" key={c.roundN}>
            <span>Round {c.roundN}</span>
            <span className="tk-muted tk-small">due {fmtDate(c.dueAt)}</span>
            {c.pot && (
              <span className="tk-muted tk-small">
                pot → {c.pot.recipientName} · {c.pot.status}
              </span>
            )}
          </div>
        ))}
      </div>
      {mapping.resources.map((r) => (
        <div className="tk-grid-row" role="row" key={r.id}>
          <div className="tk-grid-cell tk-grid-member" role="rowheader">
            <span>
              <strong>{r.name}</strong> <span className="tk-muted">@{r.handle}</span>
            </span>
            <span className="tk-muted tk-small">
              #{r.rotationPos} · {r.wallet} · {r.vaulted ? "vaulted" : "not vaulted"}
            </span>
          </div>
          {columns.map((c) => {
            const ev = contributions.find((e) => e.tk.roundN === c.roundN && e.tk.member === r.handle);
            const pot = pots.find((p) => p.tk.roundN === c.roundN && p.resourceId === r.handle);
            return (
              <div className="tk-grid-cell" role="cell" key={c.roundN}>
                {ev && (
                  <button
                    type="button"
                    className={`tk-chip tk-chip-${ev.tk.status.toLowerCase()}`}
                    style={isSelected(ev.tk) ? { borderColor: STATUS_COLOR[ev.tk.status] } : undefined}
                    onClick={() => onSelect(isSelected(ev.tk) ? null : ev.tk)}
                    title={`${ev.tk.orderId ? `order ${ev.tk.orderId}` : "no order yet"}${ev.tk.captureId ? ` · capture ${ev.tk.captureId}` : ""}`}
                  >
                    <span className="tk-dot" style={{ background: STATUS_COLOR[ev.tk.status] }} />
                    {ev.name}
                    {ev.tk.orderId && <code className="tk-muted">{shortId(ev.tk.orderId, 8)}</code>}
                  </button>
                )}
                {pot && (
                  <button
                    type="button"
                    className={`tk-pot tk-pot-${pot.tk.status.toLowerCase()} tk-btn tk-btn-sm tk-btn-ghost`}
                    style={{ color: ROUND_COLOR[pot.tk.status] }}
                    onClick={() => onSelect(isSelected(pot.tk) ? null : pot.tk)}
                    title={pot.tk.itemId ? `payout item ${pot.tk.itemId}` : "no payout yet"}
                  >
                    {pot.name} · {pot.tk.status}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      ))}
      <p className="tk-board-note">
        Grid view{reason ? ` (scheduler unavailable: ${reason})` : ""}. Click a chip for its PayPal ids.
      </p>
    </div>
  );
}
