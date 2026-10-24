import type { ReactNode } from "react";
import type { CountersInfo } from "../types";

export interface CountersProps {
  counters: CountersInfo | null;
  /** A fourth tile (the payout ticker), so the header stays one compact row of numbers. */
  children?: ReactNode;
}

/** Stat tiles. "Organizer messages typed" is the headline the demo keeps at 0. */
export function Counters({ counters, children }: CountersProps) {
  const tiles = [
    { label: "Organizer messages typed", value: counters?.organizer_messages_typed ?? 0, hero: true },
    { label: "Rounds paid on time", value: counters?.rounds_paid_on_time ?? 0, hero: false },
    { label: "Shortfalls covered", value: counters?.shortfalls_covered ?? 0, hero: false },
  ];
  return (
    <section className="tk-card tk-counters" aria-label="Counters">
      {tiles.map((t) => (
        <div className="tk-stat" key={t.label}>
          <span className="tk-stat-label">{t.label}</span>
          <span className={`tk-stat-value${t.hero ? " tk-stat-hero" : ""}`}>{t.value}</span>
        </div>
      ))}
      {children}
    </section>
  );
}
