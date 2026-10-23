import { useEffect, useRef } from "react";
import type { InboxMessage } from "../types";
import { fmtTime } from "../format";
import { isHindi, sortedInbox, subtitleFor } from "../recovery";

export interface PhoneInboxProps {
  /** Display name of the member whose phone this is (e.g. "Ravi"). */
  memberName: string;
  messages: InboxMessage[];
  /** English translations the server returned, by message id. */
  translations: Record<number, string>;
}

const SOURCE_LABEL: Record<string, string> = {
  rules: "Turnkeeper (rules)",
  agent: "Turnkeeper (AI)",
  judge: "sent in Judge Mode",
  member: "",
};

/**
 * A phone-frame chat: the member's own replies on the right, the circle's messages on the left.
 * Hindi lines carry an English subtitle (or an honest "no translation" note).
 */
export function PhoneInbox({ memberName, messages, translations }: PhoneInboxProps) {
  const listRef = useRef<HTMLOListElement | null>(null);
  const ordered = sortedInbox(messages);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [ordered.length]);

  return (
    <section className="tk-card tk-phone-card" aria-label={`${memberName}'s phone`}>
      <h2 className="tk-card-title">
        <span>{memberName}'s phone</span>
        <span className="tk-muted tk-small">member inbox</span>
      </h2>
      <div className="tk-phone">
        <div className="tk-phone-notch" aria-hidden="true" />
        <div className="tk-phone-head">
          <span className="tk-phone-avatar" aria-hidden="true">
            TK
          </span>
          <span>
            <strong>Temple circle</strong>
            <span className="tk-phone-sub">Turnkeeper treasurer</span>
          </span>
        </div>
        <ol className="tk-phone-list" ref={listRef} aria-live="polite">
          {ordered.length === 0 && <li className="tk-phone-empty">No messages yet.</li>}
          {ordered.map((m) => {
            const mine = m.direction === "in";
            const sub = subtitleFor(m, translations);
            const via = SOURCE_LABEL[m.source] ?? m.source;
            return (
              <li key={m.id} className={`tk-bubble ${mine ? "tk-bubble-me" : "tk-bubble-them"}`}>
                <span className="tk-bubble-text" lang={isHindi(m) ? "hi" : "en"}>
                  {m.text}
                </span>
                {sub && (
                  <span className="tk-bubble-sub" lang="en">
                    {sub}
                  </span>
                )}
                <span className="tk-bubble-meta">
                  {fmtTime(m.ts)}
                  {via ? ` · ${via}` : ""}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
