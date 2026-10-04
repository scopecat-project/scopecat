import { Dialog } from "@base-ui/react/dialog";
import { useEffect, useRef, useState } from "react";
import type { components } from "../../api-schema";
import {
  dialogBackdrop,
  dialogPopup,
  dialogTitle,
  dialogViewport,
  primaryButton,
  secondaryButton,
} from "../../ui/styles";

export interface ApplicationStatus {
  home: string;
  state: string;
  detail: string | null;
  installation: InstallationStatus;
  sources: { directory: string; python: string | null; execution_python: string }[];
}

export interface InstallationStatus {
  python: string;
  static_dir: string;
  environment: Record<string, string>;
  adapter_identity: string | null;
}

interface DesktopAPI {
  set_window_title(title: string): Promise<void>;
  open_capture(): Promise<components["schemas"]["CaptureImportReceipt"] | null>;
  save_configuration(document: string): Promise<string | null>;
  save_configuration_source(contentHash: string): Promise<string | null>;
  save_capture(contentHash: string): Promise<string | null>;
  export_run(runId: string): Promise<string | null>;
  save_captured_artifact(
    contentHash: string,
    analysisHash: string,
    artifactId: string,
    filename: string,
  ): Promise<string | null>;
  status(): Promise<ApplicationStatus>;
  retry(): Promise<void>;
  restart(): Promise<void>;
  exit(background: boolean): Promise<void>;
  request_exit(): Promise<ApplicationActivity | null>;
  wait_for_idle(wait: boolean): Promise<void>;
  register_source(directory: string, interpreter: string): Promise<string>;
  select_source_environment(directory: string, interpreter: string): Promise<string>;
  choose_directory(): Promise<string | null>;
  create_source(parent: string, name: string): Promise<string>;
  prepare_author_environment(directory: string): Promise<string>;
  create_author_environment(directory: string, rebuild?: boolean): Promise<string>;
}

interface ApplicationActivity {
  file_operations?: number;
  runs: number;
  procedures: number;
  instrument_sessions: number;
  requests: number;
  calibration_tasks: number;
  scheduled_workflows: number;
}

function describeWork(work: ApplicationActivity): string {
  const labels: [keyof ApplicationActivity, string][] = [
    ["file_operations", "file operation"],
    ["runs", "experiment"],
    ["procedures", "workflow"],
    ["instrument_sessions", "device session"],
    ["requests", "change"],
    ["calibration_tasks", "calibration task"],
    ["scheduled_workflows", "scheduled workflow"],
  ];
  return labels
    .filter(([key]) => (work[key] ?? 0) > 0)
    .map(([key, label]) => `${work[key]} ${label}${work[key] === 1 ? "" : "s"}`)
    .join(", ");
}

declare global {
  interface Window {
    pywebview?: { api: DesktopAPI };
    scopecatRequestExit?: () => void;
  }
}

export function useDesktopAvailable() {
  const [available, setAvailable] = useState(!!window.pywebview);
  useEffect(() => {
    const ready = () => setAvailable(true);
    window.addEventListener("pywebviewready", ready);
    return () => window.removeEventListener("pywebviewready", ready);
  }, []);
  return available;
}

export function DesktopSession() {
  const [open, setOpen] = useState(false);
  const [progress, setProgress] = useState<string>();
  const pending = useRef(false);
  const busy = progress !== undefined;
  const [error, setError] = useState<string>();
  const [activity, setActivity] = useState<ApplicationActivity>();
  const [waiting, setWaiting] = useState(false);
  useEffect(() => {
    window.scopecatRequestExit = () => {
      if (!window.pywebview || pending.current) return;
      pending.current = true;
      setOpen(true);
      setActivity(undefined);
      setProgress("Checking unfinished work and closing Scopecat…");
      setError(undefined);
      void window.pywebview?.api
        .request_exit()
        .then((work) => {
          if (work) {
            pending.current = false;
            setProgress(undefined);
            setActivity(work);
          } else {
            setProgress("Closing Scopecat…");
          }
        })
        .catch((failure) => {
          setError(failure instanceof Error ? failure.message : String(failure));
          pending.current = false;
          setProgress(undefined);
        });
    };
    const keydown = (event: KeyboardEvent) => {
      if (
        !window.pywebview ||
        !event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.shiftKey ||
        event.key.toLowerCase() !== "q"
      )
        return;
      event.preventDefault();
      if (!event.repeat) window.scopecatRequestExit?.();
    };
    window.addEventListener("keydown", keydown);
    return () => {
      window.removeEventListener("keydown", keydown);
      delete window.scopecatRequestExit;
    };
  }, []);

  const exit = async (background: boolean) => {
    if (!window.pywebview || pending.current) return;
    pending.current = true;
    setProgress(background ? "Hiding the window…" : "Stopping work and releasing devices…");
    setError(undefined);
    try {
      await window.pywebview.api.exit(background);
      if (background) {
        setOpen(false);
        setWaiting(false);
        pending.current = false;
        setProgress(undefined);
      } else {
        setProgress("Closing Scopecat…");
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
      pending.current = false;
      setProgress(undefined);
    }
  };

  const changeWaiting = async (wait: boolean) => {
    if (!window.pywebview || pending.current) return;
    pending.current = true;
    setProgress(wait ? "Setting automatic quit…" : "Cancelling automatic quit…");
    setError(undefined);
    try {
      await window.pywebview.api.wait_for_idle(wait);
      setWaiting(wait);
      if (!wait) setOpen(false);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      pending.current = false;
      setProgress(undefined);
    }
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!busy) {
          if (!next && waiting) void changeWaiting(false);
          else setOpen(next);
        }
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup className={`${dialogPopup} grid gap-4 p-5`}>
            <Dialog.Title className={dialogTitle}>Quit Scopecat</Dialog.Title>
            <Dialog.Description>
              {activity && `Unfinished work: ${describeWork(activity)}. `}
              Quitting stops this work and releases devices. Background mode keeps Scopecat
              available from the menu bar or system tray. Saved records and code are retained.
            </Dialog.Description>
            {progress && <p role="status">{progress}</p>}
            {waiting && (
              <p role="status">
                Waiting for work to finish. Close this dialog to cancel automatic quit.
              </p>
            )}
            {error && <p role="alert">{error}</p>}
            <div className="flex flex-wrap gap-2">
              <button className={primaryButton} disabled={busy} onClick={() => void exit(false)}>
                Stop and close
              </button>
              <button
                className={secondaryButton}
                disabled={busy || waiting}
                onClick={() => void changeWaiting(true)}
              >
                Quit when work finishes
              </button>
              <button className={secondaryButton} disabled={busy} onClick={() => void exit(true)}>
                Keep running in background
              </button>
              <Dialog.Close className={secondaryButton} disabled={busy}>
                Cancel
              </Dialog.Close>
            </div>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
