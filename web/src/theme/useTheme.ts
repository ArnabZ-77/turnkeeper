/**
 * Light/dark theming.
 *
 * The app's own colours are CSS tokens (theme/tokens.css) that respond to `prefers-color-scheme`
 * and to an explicit `data-theme` on <html>. Bryntum's Stockholm theme ships as two files of
 * `:root` variables (light and dark); both are bundled as strings and the active one is injected
 * into a <style> element, so a toggle re-themes the scheduler without any network request.
 */

import { useCallback, useEffect, useState } from "react";
import bryntumDark from "@bryntum/scheduler/stockholm-dark.css?inline";
import bryntumLight from "@bryntum/scheduler/stockholm-light.css?inline";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "tk-theme";
const STYLE_ID = "tk-bryntum-theme";
export const DEFAULT_PREFERENCE: ThemePreference = "light";

export function readPreference(): ThemePreference {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage unavailable */
  }
  // Light is the default (screen recordings, projectors); dark is one toggle away and remembered.
  return DEFAULT_PREFERENCE;
}

function systemTheme(): ResolvedTheme {
  try {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function resolveTheme(pref: ThemePreference): ResolvedTheme {
  return pref === "system" ? systemTheme() : pref;
}

/** Stamp <html data-theme> and swap the Bryntum theme variables. Safe to call before React mounts. */
export function applyTheme(resolved: ResolvedTheme, pref: ThemePreference): void {
  const root = document.documentElement;
  if (pref === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
  root.style.colorScheme = resolved;

  let style = document.getElementById(STYLE_ID) as HTMLStyleElement | null;
  if (!style) {
    style = document.createElement("style");
    style.id = STYLE_ID;
    document.head.appendChild(style);
  }
  const css = resolved === "dark" ? bryntumDark : bryntumLight;
  if (style.textContent !== css) style.textContent = css;
}

export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => readPreference());
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolveTheme(preference));

  useEffect(() => {
    const apply = () => {
      const next = resolveTheme(preference);
      setResolved(next);
      applyTheme(next, preference);
    };
    apply();
    if (preference !== "system") return;
    try {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      mq.addEventListener("change", apply);
      return () => mq.removeEventListener("change", apply);
    } catch {
      return;
    }
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      if (next === "system") localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* storage unavailable */
    }
  }, []);

  const toggle = useCallback(() => {
    setPreference(resolved === "dark" ? "light" : "dark");
  }, [resolved, setPreference]);

  return { preference, resolved, setPreference, toggle };
}
