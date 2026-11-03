import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { backoffDelay, openEventStream, parseEvent, type EventSourceLike } from "../events";
import type { TurnkeeperEvent } from "../types";

class FakeSource implements EventSourceLike {
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  listeners = new Map<string, (ev: Event) => void>();
  closed = false;
  constructor(public url: string) {}
  addEventListener(type: string, listener: (ev: Event) => void) {
    this.listeners.set(type, listener);
  }
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  message(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }
  named(name: string, data: string) {
    this.listeners.get(name)?.({ data } as unknown as Event);
  }
  fail() {
    this.readyState = 2;
    this.onerror?.(new Event("error"));
  }
}

describe("parseEvent", () => {
  it("accepts known types, falls back to the SSE event name and flags unknown types", () => {
    expect(parseEvent('{"type":"queue","ts":"t","position":2}')).toMatchObject({ type: "queue", position: 2 });
    expect(parseEvent('{"position":1}', "queue")).toMatchObject({ type: "queue", position: 1 });
    expect(parseEvent('{"type":"hello","ts":"t"}')).toMatchObject({ type: "unknown", name: "hello" });
    expect(parseEvent("not json")).toBeNull();
    expect(parseEvent("[1,2]")).toBeNull();
    const e = parseEvent('{"type":"board"}');
    expect(typeof e?.ts).toBe("string");
  });
});

describe("backoffDelay", () => {
  it("doubles and caps", () => {
    expect(backoffDelay(1, 15_000)).toBe(1000);
    expect(backoffDelay(2, 15_000)).toBe(2000);
    expect(backoffDelay(10, 15_000)).toBe(15_000);
  });
});

describe("openEventStream", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("delivers events, reconnects after a close and stops on close()", () => {
    const sources: FakeSource[] = [];
    const events: TurnkeeperEvent[] = [];
    const states: string[] = [];
    const stream = openEventStream(
      { onEvent: (e) => events.push(e), onState: (s) => states.push(s) },
      { factory: (url) => { const s = new FakeSource(url); sources.push(s); return s; }, maxBackoffMs: 5000 },
    );
    expect(sources).toHaveLength(1);
    expect(sources[0].url).toBe("/api/events"); // no session id in the URL: the cookie carries it
    sources[0].open();
    sources[0].message('{"type":"queue","ts":"t","position":1}');
    sources[0].named("toast", '{"level":"info","text":"hi"}');
    expect(events.map((e) => e.type)).toEqual(["queue", "toast"]);
    expect(stream.state).toBe("open");

    sources[0].fail();
    expect(stream.state).toBe("reconnecting");
    expect(sources).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    expect(sources).toHaveLength(2);
    sources[1].open();
    expect(stream.state).toBe("open");

    stream.close();
    expect(sources[1].closed).toBe(true);
    expect(stream.state).toBe("closed");
    sources[1].fail();
    vi.advanceTimersByTime(10_000);
    expect(sources).toHaveLength(2);
  });
});
