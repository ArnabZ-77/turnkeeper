/**
 * EventSource client for `GET /api/events` (session from the cookie) with reconnect.
 *
 * The server (sse-starlette) sends JSON payloads that always carry `type` and `ts`. It may also
 * name the SSE event after the type, so both unnamed `message` events and named events are
 * handled. Heartbeats and unknown types surface as `{type: "unknown"}` and are ignored upstream.
 *
 * Browsers retry an EventSource on their own while it is CONNECTING; once it reaches CLOSED
 * (the server closed the stream or returned an error status) they stop, so this module reopens
 * it with exponential backoff capped at `maxBackoffMs`.
 */

import { eventsUrl } from "./api";
import { KNOWN_EVENT_TYPES, type TurnkeeperEvent } from "./types";

export type ConnectionState = "connecting" | "open" | "reconnecting" | "closed";

export interface EventStreamHandlers {
  onEvent: (event: TurnkeeperEvent) => void;
  onState?: (state: ConnectionState) => void;
}

/** The subset of EventSource this module relies on (lets tests inject a fake). */
export interface EventSourceLike {
  readyState: number;
  onopen: ((ev: Event) => void) | null;
  onmessage: ((ev: MessageEvent) => void) | null;
  onerror: ((ev: Event) => void) | null;
  addEventListener(type: string, listener: (ev: Event) => void): void;
  close(): void;
}

export interface EventStreamOptions {
  /** Upper bound for the reconnect delay in milliseconds (default 15 s). */
  maxBackoffMs?: number;
  /** Builds the EventSource for a URL; defaults to the browser's EventSource. */
  factory?: (url: string) => EventSourceLike;
}

export interface EventStream {
  close(): void;
  readonly state: ConnectionState;
}

const CLOSED = 2; // EventSource.CLOSED

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Parse one SSE `data` payload. `eventName` is the SSE event name when the server used one; it
 * is the fallback for a payload without `type`. Returns null for malformed data.
 */
export function parseEvent(raw: string, eventName?: string): TurnkeeperEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(data)) return null;
  const type = typeof data.type === "string" && data.type ? data.type : eventName;
  if (!type) return null;
  const ts = typeof data.ts === "string" ? data.ts : new Date().toISOString();
  if ((KNOWN_EVENT_TYPES as readonly string[]).includes(type)) {
    return { ...data, type, ts } as TurnkeeperEvent;
  }
  return { type: "unknown", ts, name: type, raw: data };
}

export function backoffDelay(attempt: number, maxMs: number): number {
  return Math.min(maxMs, 1000 * 2 ** Math.max(0, Math.min(attempt - 1, 6)));
}

export function openEventStream(handlers: EventStreamHandlers, options: EventStreamOptions = {}): EventStream {
  const maxBackoffMs = options.maxBackoffMs ?? 15_000;
  const factory = options.factory ?? ((url: string): EventSourceLike => new EventSource(url));

  let source: EventSourceLike | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let closed = false;
  let state: ConnectionState = "connecting";

  const setState = (next: ConnectionState) => {
    if (state === next) return;
    state = next;
    handlers.onState?.(next);
  };

  const deliver = (raw: unknown, name?: string) => {
    const event = parseEvent(typeof raw === "string" ? raw : JSON.stringify(raw), name);
    if (event) handlers.onEvent(event);
  };

  const connect = () => {
    if (closed) return;
    timer = null;
    const es = factory(eventsUrl());
    source = es;
    es.onopen = () => {
      attempt = 0;
      setState("open");
    };
    es.onmessage = (e) => deliver(e.data);
    for (const name of KNOWN_EVENT_TYPES) {
      es.addEventListener(name, (e) => deliver((e as MessageEvent).data, name));
    }
    es.onerror = () => {
      if (closed) return;
      if (es.readyState === CLOSED) {
        es.close();
        if (source === es) source = null;
        attempt += 1;
        setState("reconnecting");
        timer = setTimeout(connect, backoffDelay(attempt, maxBackoffMs));
      } else {
        // The browser is retrying on its own.
        setState("reconnecting");
      }
    };
  };

  connect();

  return {
    close() {
      closed = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      source?.close();
      source = null;
      setState("closed");
    },
    get state() {
      return state;
    },
  };
}
