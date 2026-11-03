import { lazy, StrictMode, Suspense, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { CompilePage } from "./pages/CompilePage";
import { JoinPage } from "./pages/JoinPage";
import { PassbookPage } from "./pages/PassbookPage";
import { NAV_EVENT, parseRoute } from "./routes";
import "./theme/tokens.css";
import "./styles.css";
import "./recovery.css";
import "./pages.css";
import "./mandates.css";
import { applyTheme, readPreference, resolveTheme } from "./theme/useTheme";

// Stamp the theme before the first paint (index.html already set data-theme from storage; this
// also injects the matching Bryntum theme variables so the board never flashes the wrong theme).
const pref = readPreference();
applyTheme(resolveTheme(pref), pref);

// The board (and, through it, the Bryntum scheduler chunk) loads only on the board route, so the
// member pages (/m, /join, /compile) stay light on a phone.
const App = lazy(() => import("./App"));

/** Pick the page from the URL: `/` board, `/compile` charter compiler, `/join/{invite}` member join. */
function Root() {
  const [route, setRoute] = useState(() => parseRoute(window.location.pathname, window.location.search));
  useEffect(() => {
    const sync = () => setRoute(parseRoute(window.location.pathname, window.location.search));
    window.addEventListener("popstate", sync);
    window.addEventListener(NAV_EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(NAV_EVENT, sync);
    };
  }, []);
  if (route.page === "join") return <JoinPage key={route.invite} invite={route.invite} status={route.status} />;
  if (route.page === "compile") return <CompilePage />;
  if (route.page === "passbook") return <PassbookPage />;
  return (
    <Suspense fallback={<p className="tk-route-loading">Loading the board…</p>}>
      <App />
    </Suspense>
  );
}

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
