import { useEffect } from "react";
import { useDesktopAvailable } from "./DesktopSession";

/** The active data view owns both browser and native window identification. */
export function WindowTitle({ title }: { title: string }) {
  const desktop = useDesktopAvailable();
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    if (desktop) void window.pywebview!.api.set_window_title(title);
    return () => {
      document.title = previous;
      if (desktop) void window.pywebview!.api.set_window_title("Scopecat");
    };
  }, [desktop, title]);
  return null;
}
