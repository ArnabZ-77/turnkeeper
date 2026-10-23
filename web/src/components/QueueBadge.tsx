import type { ConnectionState } from "../events";

export interface QueueBadgeProps {
  /** Jobs ahead in the PayPal FIFO gateway (0 = idle). */
  position: number;
  connection: ConnectionState;
}

/** Gateway spacing is about 1.5 s per call at 40/min, so the wait estimate is position * 1.5 s. */
export function queueEtaSeconds(position: number): number {
  return Math.max(0, Math.round(position * 1.5));
}

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "connecting",
  open: "live events",
  reconnecting: "reconnecting",
  closed: "disconnected",
};

export function QueueBadge({ position, connection }: QueueBadgeProps) {
  const queued = position > 0;
  return (
    <section className="tk-card" aria-label="PayPal queue">
      <h2 className="tk-card-title">
        <span>PayPal gateway</span>
        <span className={`tk-conn tk-conn-${connection}`} title="Server-sent events stream">
          <span className="tk-dot" />
          {CONNECTION_LABEL[connection]}
        </span>
      </h2>
      <div className="tk-queue" aria-live="polite">
        {queued ? (
          <>
            <span className="tk-queue-pos">{position}</span>
            <span>
              queued, position {position} <span className="tk-muted">(about {queueEtaSeconds(position)} s)</span>
            </span>
          </>
        ) : (
          <span className="tk-muted">queue idle; FIFO, 40 calls/min, sandbox only</span>
        )}
      </div>
    </section>
  );
}
