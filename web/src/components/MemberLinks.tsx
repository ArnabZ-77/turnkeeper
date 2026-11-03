import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { PassbookLink } from "../types";

/**
 * Organizer's per-member passbook links (flag TK_PASSBOOK). Renders nothing when the flag is off (404).
 * "Send" opens WhatsApp with the link as text; no number is stored.
 */
export function MemberLinks({ circleId }: { circleId: string | null }) {
  const [links, setLinks] = useState<PassbookLink[] | null>(null);
  const [off, setOff] = useState(false);
  useEffect(() => {
    setLinks(null);
    if (!circleId) return;
    let live = true;
    api.passbook
      .links(circleId)
      .then((r) => live && setLinks(r.links))
      .catch((e) => live && e instanceof ApiError && e.status === 404 && setOff(true));
    return () => {
      live = false;
    };
  }, [circleId]);
  if (off || !links) return null;
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  return (
    <details className="tk-card tk-advanced">
      <summary className="tk-advanced-summary">
        <span className="tk-judge-label">Member passbooks</span>
        <span className="tk-muted tk-small">each member's own view, read-only, link valid for a day</span>
      </summary>
      <ul className="tk-member-links">
        {links.map((l) => (
          <li key={l.member}>
            <a href={l.path} target="_blank" rel="noreferrer">
              {l.name}
            </a>{" "}
            <a
              className="tk-btn tk-btn-ghost tk-small"
              href={`https://wa.me/?text=${encodeURIComponent(`${l.name.split(" ")[0]}, your circle passbook: ${origin}${l.path}`)}`}
              target="_blank"
              rel="noreferrer"
            >
              Send
            </a>
          </li>
        ))}
      </ul>
    </details>
  );
}
