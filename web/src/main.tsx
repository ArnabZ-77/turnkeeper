import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./theme/tokens.css";
import "./styles.css";
import { applyTheme, readPreference, resolveTheme } from "./theme/useTheme";

// Stamp the theme before the first paint (index.html already set data-theme from storage; this
// also injects the matching Bryntum theme variables so the board never flashes the wrong theme).
const pref = readPreference();
applyTheme(resolveTheme(pref), pref);

const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
