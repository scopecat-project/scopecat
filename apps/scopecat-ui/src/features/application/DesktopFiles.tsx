import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { secondaryButton } from "../../ui/styles";

const openFileEvent = "scopecat:open-file";

export function requestOpenFile() {
  window.dispatchEvent(new Event(openFileEvent));
}

export function selectCapture(contentHash: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("capture", contentHash);
  url.hash = "history";
  window.history.pushState(null, "", url);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

export function selectedCaptureFromLocation() {
  return new URLSearchParams(window.location.search).get("capture") ?? undefined;
}

/** One Open command per window, shared by the native menu and page button. */
export function DesktopFiles() {
  const cache = useQueryClient();
  const pending = useRef(false);
  const [opening, setOpening] = useState(false);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();

  useEffect(() => {
    const open = async () => {
      if (!window.pywebview || pending.current) return;
      pending.current = true;
      setOpening(true);
      setMessage(undefined);
      setError(undefined);
      try {
        const receipt = await window.pywebview.api.open_capture();
        if (receipt) {
          void cache.invalidateQueries({ queryKey: ["data", "captures"] });
          selectCapture(receipt.capture.content_hash);
          setMessage(receipt.created ? "File imported." : "This data is already available.");
        }
      } catch (failure) {
        setError(failure instanceof Error ? failure.message : String(failure));
      } finally {
        pending.current = false;
        setOpening(false);
      }
    };
    const request = () => void open();
    const keydown = (event: KeyboardEvent) => {
      if (
        window.pywebview &&
        (event.metaKey || event.ctrlKey) &&
        !event.altKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === "o"
      ) {
        event.preventDefault();
        request();
      }
    };
    window.addEventListener(openFileEvent, request);
    window.addEventListener("keydown", keydown);
    return () => {
      window.removeEventListener(openFileEvent, request);
      window.removeEventListener("keydown", keydown);
    };
  }, [cache]);

  if (!opening && !message && !error) return null;
  return (
    <aside className="fixed bottom-4 right-4 z-50 max-w-lg rounded-lg border border-line bg-panel p-4 text-text shadow-lg">
      {opening && <p role="status">Opening and checking the selected file…</p>}
      {message && <p role="status">{message}</p>}
      {error && <p role="alert">{error}</p>}
      {!opening && (
        <button
          className={secondaryButton}
          onClick={() => {
            setMessage(undefined);
            setError(undefined);
          }}
        >
          Dismiss
        </button>
      )}
    </aside>
  );
}
