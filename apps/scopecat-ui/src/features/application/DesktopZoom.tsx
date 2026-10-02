import { useEffect, useState } from "react";
import { useDesktopAvailable } from "./DesktopSession";

const levels = [50, 67, 80, 90, 100, 110, 125, 150, 175, 200];
const actualSize = levels.indexOf(100);

/** Zoom belongs to a window, not the shared application or a data record. */
export function DesktopZoom() {
  const desktop = useDesktopAvailable();
  const [level, setLevel] = useState(actualSize);

  useEffect(() => {
    if (!desktop) return;
    const root = document.documentElement;
    const previous = root.style.zoom;
    root.style.zoom = `${levels[level]}%`;
    return () => {
      root.style.zoom = previous;
    };
  }, [desktop, level]);

  useEffect(() => {
    if (!desktop) return;
    const increase = () => setLevel((value) => Math.min(value + 1, levels.length - 1));
    const decrease = () => setLevel((value) => Math.max(value - 1, 0));
    const reset = () => setLevel(actualSize);
    const keydown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      const command =
        event.key === "+" || event.key === "="
          ? increase
          : event.key === "-"
            ? decrease
            : event.key === "0" && !event.shiftKey
              ? reset
              : undefined;
      if (command) {
        event.preventDefault();
        command();
      }
    };
    window.addEventListener("scopecat:zoom-in", increase);
    window.addEventListener("scopecat:zoom-out", decrease);
    window.addEventListener("scopecat:zoom-reset", reset);
    window.addEventListener("keydown", keydown);
    return () => {
      window.removeEventListener("scopecat:zoom-in", increase);
      window.removeEventListener("scopecat:zoom-out", decrease);
      window.removeEventListener("scopecat:zoom-reset", reset);
      window.removeEventListener("keydown", keydown);
    };
  }, [desktop]);

  return desktop ? (
    <span className="sr-only" role="status">
      Zoom {levels[level]}%
    </span>
  ) : null;
}
