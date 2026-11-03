/**
 * Tiny client-side router (no dependency): the FastAPI app serves the SPA shell for every path that is
 * not an API route, so `/compile` and `/join/{invite}` land here. `/join/{invite}/return` and `/cancel`
 * are server routes (PayPal redirects there; the server answers 303 to `/join/{invite}?status=...`).
 */

export type Route =
  | { page: "board" }
  | { page: "compile" }
  | { page: "join"; invite: string; status: string | null }
  | { page: "passbook" };

function safeDecode(s: string): string | null {
  try {
    return decodeURIComponent(s);
  } catch {
    return null;
  }
}

/** Map a location to a page. Unknown paths fall back to the board (never a blank page). */
export function parseRoute(pathname: string, search = ""): Route {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/compile") return { page: "compile" };
  if (path === "/m") return { page: "passbook" }; // the capability is in the fragment (#tkc1...), never sent
  const m = /^\/join\/([^/]+)$/.exec(path);
  if (m) {
    const invite = safeDecode(m[1]);
    if (invite) {
      const status = new URLSearchParams(search).get("status");
      return { page: "join", invite, status: status ? status.slice(0, 32) : null };
    }
  }
  return { page: "board" };
}

/** Same-origin path for a page. */
export function routePath(route: Route): string {
  switch (route.page) {
    case "compile":
      return "/compile";
    case "join":
      return `/join/${encodeURIComponent(route.invite)}${route.status ? `?status=${encodeURIComponent(route.status)}` : ""}`;
    case "passbook":
      return "/m";
    default:
      return "/";
  }
}

export const NAV_EVENT = "tk:navigate";

/** Push a same-origin path and tell the root to re-render. */
export function navigate(path: string): void {
  if (typeof window === "undefined") return;
  if (!path.startsWith("/") || path.startsWith("//")) return; // same-origin paths only
  window.history.pushState(null, "", path);
  window.dispatchEvent(new Event(NAV_EVENT));
  window.scrollTo?.(0, 0);
}
