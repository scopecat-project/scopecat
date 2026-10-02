import { useCallback, useEffect, useRef, useState } from "react";
import { useDesktopAvailable } from "./DesktopSession";
import { secondaryButton } from "../../ui/styles";

type SearchableWindow = Window & {
  find?: (text: string, caseSensitive: boolean, backwards: boolean, wrap: boolean) => boolean;
};

export function DesktopFind() {
  const desktop = useDesktopAvailable();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [message, setMessage] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  const search = useCallback(
    (backwards: boolean) => {
      if (!query) return;
      const engine = window as SearchableWindow;
      if (!engine.find) {
        setMessage("Text search is unavailable in this window.");
        return;
      }
      setMessage(engine.find(query, false, backwards, true) ? "Match found" : "No matches");
      input.current?.focus({ preventScroll: true });
    },
    [query],
  );

  const close = useCallback(() => {
    setOpen(false);
    setMessage("");
    if (previousFocus.current?.isConnected) previousFocus.current.focus({ preventScroll: true });
  }, []);

  useEffect(() => {
    if (!desktop) return;
    const show = () => {
      if (!open && document.activeElement instanceof HTMLElement) {
        previousFocus.current = document.activeElement;
      }
      setOpen(true);
      input.current?.select();
    };
    const keydown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "f") {
        event.preventDefault();
        show();
      } else if (open && event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (
        open &&
        (event.key === "F3" ||
          ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "g"))
      ) {
        event.preventDefault();
        search(event.shiftKey);
      }
    };
    window.addEventListener("scopecat:find", show);
    window.addEventListener("keydown", keydown);
    return () => {
      window.removeEventListener("scopecat:find", show);
      window.removeEventListener("keydown", keydown);
    };
  }, [desktop, open, search, close]);

  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);

  if (!desktop || !open) return null;
  return (
    <form
      role="search"
      aria-label="Find in this view"
      className="fixed top-4 right-4 z-50 max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-panel p-3 text-text shadow-lg"
      onSubmit={(event) => {
        event.preventDefault();
        search(false);
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="desktop-find">Find in this view</label>
        <input
          id="desktop-find"
          ref={input}
          type="search"
          value={query}
          className="min-w-0 rounded border border-line bg-panel-soft px-2 py-1"
          onChange={(event) => {
            setQuery(event.target.value);
            setMessage("");
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && event.shiftKey) {
              event.preventDefault();
              search(true);
            }
          }}
        />
        <button
          type="button"
          className={secondaryButton}
          disabled={!query}
          onClick={() => search(true)}
        >
          Previous
        </button>
        <button type="submit" className={secondaryButton} disabled={!query}>
          Next
        </button>
        <button type="button" className={secondaryButton} onClick={close}>
          Close find
        </button>
      </div>
      {message && (
        <p role="status" className="mt-2 text-sm">
          {message}
        </p>
      )}
    </form>
  );
}
