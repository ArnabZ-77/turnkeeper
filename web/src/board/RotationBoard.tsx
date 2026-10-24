/**
 * Rotation board: members x rounds. Uses the Bryntum Scheduler when its chunk loads and renders,
 * otherwise the CSS-grid board with the same data, so the page never goes blank.
 */

import { Component, useCallback, useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";
import type { BoardModel } from "../types";
import { describeError } from "../api";
import { copyText } from "../format";
import { GridBoard } from "./GridBoard";
import { detailRows, detailTitle, focusHandle, mapBoard, STATUS_COLOR, STATUS_LABEL, type EventMeta } from "./mapping";
import type { SchedulerBoardProps } from "./SchedulerBoard";
import { IconCopy } from "../components/icons";

export interface RotationBoardProps {
  board: BoardModel | null;
  /** Handles whose turn just moved (schedule_changed); highlighted and animated. */
  moved?: readonly string[];
  /** Member of the open recovery, kept in view on a short board. */
  recoveryMember?: string | null;
}

/** Phones get the list board: a time axis is unreadable in 350 px. */
export const NARROW_QUERY = "(max-width: 600px)";

function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const on = () => setMatches(mql.matches);
    on();
    mql.addEventListener?.("change", on);
    return () => mql.removeEventListener?.("change", on);
  }, [query]);
  return matches;
}

const NO_MOVES: readonly string[] = [];

type Loader = { status: "loading" } | { status: "ready"; Impl: ComponentType<SchedulerBoardProps> } | { status: "failed"; reason: string };

/** Load the scheduler chunk once per page; a failed import is remembered so we do not retry on every render. */
let loaderPromise: Promise<Loader> | null = null;
function loadScheduler(): Promise<Loader> {
  if (!loaderPromise) {
    loaderPromise = import("./SchedulerBoard")
      .then((m): Loader => ({ status: "ready", Impl: m.default }))
      .catch((e): Loader => {
        console.warn("Bryntum scheduler unavailable; using the grid board", e);
        return { status: "failed", reason: describeError(e) };
      });
  }
  return loaderPromise;
}

interface BoundaryProps {
  onError: (reason: string) => void;
  children: ReactNode;
}

/** Catches render/mount errors thrown by the scheduler (e.g. an expired trial) and reports them. */
class SchedulerBoundary extends Component<BoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.warn("Bryntum scheduler failed at runtime; using the grid board", error);
    this.props.onError(describeError(error));
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="tk-copy"
      aria-label={`Copy ${value}`}
      onClick={async () => {
        if (await copyText(value)) {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }
      }}
    >
      <IconCopy /> {copied ? "copied" : "copy"}
    </button>
  );
}

function SelectedDetail({ meta, currency, onClose }: { meta: EventMeta; currency: string; onClose: () => void }) {
  return (
    <div className="tk-card tk-selected" aria-live="polite">
      <strong>{detailTitle(meta)}</strong>
      {detailRows(meta, currency).map((r) => (
        <span key={r.k}>
          <span className="tk-muted">{r.k}: </span>
          {r.mono ? <code>{r.v}</code> : r.v}
          {r.mono && r.v !== "—" && (
            <>
              {" "}
              <CopyButton value={r.v} />
            </>
          )}
        </span>
      ))}
      <button type="button" className="tk-btn tk-btn-sm tk-btn-ghost" onClick={onClose} style={{ marginLeft: "auto" }}>
        close
      </button>
    </div>
  );
}

export function RotationBoard({ board, moved = NO_MOVES, recoveryMember = null }: RotationBoardProps) {
  const [loader, setLoader] = useState<Loader>({ status: "loading" });
  const [selected, setSelected] = useState<EventMeta | null>(null);
  const mapping = useMemo(() => mapBoard(board, moved), [board, moved]);
  const focus = useMemo(() => focusHandle(board, moved, recoveryMember), [board, moved, recoveryMember]);
  const narrow = useMediaQuery(NARROW_QUERY);

  useEffect(() => {
    let alive = true;
    loadScheduler().then((l) => {
      if (alive) setLoader(l);
    });
    return () => {
      alive = false;
    };
  }, []);

  // Drop the selection when its circle goes away (reset).
  const circleId = board?.circle?.id ?? null;
  useEffect(() => setSelected(null), [circleId]);

  const onSchedulerError = useCallback((reason: string) => setLoader({ status: "failed", reason }), []);
  const onSelect = useCallback((meta: EventMeta | null) => setSelected(meta), []);

  const currency = mapping?.currency ?? "USD";

  let body: ReactNode;
  if (!mapping) {
    body = <div className="tk-board-empty">{board ? "No rounds in this circle yet." : "Loading the circle…"}</div>;
  } else if (narrow) {
    body = <GridBoard mapping={mapping} selected={selected} onSelect={onSelect} reason={null} compact />;
  } else if (loader.status === "failed") {
    body = <GridBoard mapping={mapping} selected={selected} onSelect={onSelect} reason={loader.reason} />;
  } else if (loader.status === "ready") {
    const Impl = loader.Impl;
    body = (
      <SchedulerBoundary onError={onSchedulerError}>
        <Impl mapping={mapping} onSelect={onSelect} focus={focus} />
      </SchedulerBoundary>
    );
  } else {
    body = <GridBoard mapping={mapping} selected={selected} onSelect={onSelect} reason={null} />;
  }

  return (
    <section className="tk-card tk-board" aria-label="Rotation board">
      <h2 className="tk-card-title">
        <span>Rotation board · members × rounds</span>
        <span className="tk-legend" aria-label="Legend">
          {(Object.keys(STATUS_LABEL) as (keyof typeof STATUS_LABEL)[]).map((s) => (
            <span key={s}>
              <span className="tk-swatch" style={{ background: STATUS_COLOR[s] }} />
              {STATUS_LABEL[s]}
            </span>
          ))}
          <span title="Rounds 1-2 are replayed from the fixture: no PayPal call was made for them">
            <span className="tk-swatch tk-swatch-replay" />
            seeded
          </span>
          <span>
            <span className="tk-swatch" style={{ background: "transparent", border: "2px solid currentColor", transform: "rotate(45deg)", width: 9, height: 9 }} />
            pot milestone
          </span>
        </span>
      </h2>
      <div className="tk-board-host">{body}</div>
      {selected && mapping && <SelectedDetail meta={selected} currency={currency} onClose={() => setSelected(null)} />}
    </section>
  );
}
