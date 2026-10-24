/** Small formatting helpers shared by the components (pure, unit-tested through the board mapping). */

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})/;

/**
 * Parse a calendar date. A `YYYY-MM-DD...` string is read as that calendar day in local time so
 * a due date never slips into the previous month in negative-offset time zones.
 */
export function parseCalendarDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const m = DATE_ONLY.exec(value);
  if (m) {
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    return Number.isNaN(d.getTime()) ? null : d;
  }
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Parse a full timestamp (ISO-8601 UTC on the wire). */
export function parseDateTime(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export interface MoneyOptions {
  /** false: whole units ("$1,155"), for chips where space is tight; the exact cents go in the tooltip. */
  cents?: boolean;
}

/** "$1,154.93" (thousands separators, two decimals). Non-numeric input is shown as given. */
export function fmtMoney(amount: string | number | null | undefined, currency = "USD", opts: MoneyOptions = {}): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const n = typeof amount === "number" ? amount : Number(String(amount).trim());
  const cents = opts.cents !== false;
  let s: string;
  if (Number.isFinite(n)) {
    s = new Intl.NumberFormat("en-US", {
      minimumFractionDigits: cents ? 2 : 0,
      maximumFractionDigits: cents ? 2 : 0,
    }).format(cents ? n : Math.round(n));
  } else {
    s = String(amount);
  }
  if (currency !== "USD") return `${s} ${currency}`;
  return s.startsWith("-") ? `-$${s.slice(1)}` : `$${s}`;
}

export function fmtDate(value: string | null | undefined): string {
  const d = parseCalendarDate(value);
  if (!d) return "—";
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

export function fmtDateTime(value: string | null | undefined): string {
  const d = parseDateTime(value);
  if (!d) return "—";
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function fmtTime(value: string | null | undefined): string {
  const d = parseDateTime(value);
  if (!d) return "";
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** Shorten long PayPal ids for chips: first `n` characters plus an ellipsis. */
export function shortId(id: string | null | undefined, n = 12): string {
  if (!id) return "—";
  return id.length > n + 1 ? `${id.slice(0, n)}…` : id;
}

export function titleCase(s: string): string {
  const lower = s.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/** Best-effort clipboard copy; never throws (clipboard may be unavailable in insecure contexts). */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  return false;
}
