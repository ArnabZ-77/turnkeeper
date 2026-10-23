import type { AnchorHTMLAttributes, MouseEvent } from "react";
import { navigate } from "../routes";
import { useTheme } from "../theme/useTheme";
import { IconMoon, IconSun } from "./icons";

/** Same-origin SPA link: a normal <a> (middle-click, copy link work) that navigates without a reload. */
export function Link({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
  const go = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    navigate(href);
  };
  return <a href={href} onClick={go} {...rest} />;
}

/** Light/dark toggle chip (light is the default). */
export function ThemeToggle() {
  const { resolved, toggle } = useTheme();
  return (
    <button type="button" className="tk-chip" onClick={toggle} aria-label="Toggle light/dark theme">
      {resolved === "dark" ? <IconSun /> : <IconMoon />}
      {resolved === "dark" ? "light" : "dark"}
    </button>
  );
}
