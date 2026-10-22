import {
  prepareLaunchClose,
  releaseLaunchClose,
  type LaunchCloseStatus,
} from "../launch/launch-close-barrier";
import { Dialog } from "@base-ui/react/dialog";
import { useEffect, useRef, useState } from "react";
import type { components } from "../../api-schema";
import {
  dialogBackdrop,
  dialogPopup,
  dialogTitle,
  dialogViewport,
  dangerButton,
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

export type LessonTopic =
  | "parameters"
  | "groups"
  | "refresh"
  | "compute"
  | "procedure"
  | "calibration"
  | "joint-calibration"
  | "task-calibration";

export interface NotebookJourney {
  directory: string;
  notebook: string;
  python: string;
  ready: boolean;
  scope_id?: string | null;
  cleared?: boolean;
  cleanup_files?: "preserve" | "discard" | null;
}

export interface NotebookJourneyStatus {
  state: "not_started" | "preparing" | "ready" | "retryable";
  journey: NotebookJourney | null;
}

interface DesktopAPI {
  complete_close_preflight?(requestId: string, status: LaunchCloseStatus): Promise<void>;
  preview_notebook_cleanup?(topic: LessonTopic): Promise<{
    preview: components["schemas"]["DataCleanupPreview"];
    files_error: string | null;
    files?: "preserve" | "discard" | null;
  }>;
  clear_notebook_course?(topic: LessonTopic, commandJson: string): Promise<NotebookJourney>;
  notebook_journey(topic?: LessonTopic): Promise<NotebookJourneyStatus>;
  prepare_notebook_journey(
    parent?: string,
    topic?: LessonTopic,
    repair?: boolean,
  ): Promise<NotebookJourney>;
  open_lesson_notebook(topic?: LessonTopic): Promise<void>;
  open_run_window(runId: string): Promise<void>;
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
  retry(afterReply?: boolean): Promise<void>;
  restart(): Promise<void>;
  backup_application(): Promise<void>;
  exit(background: boolean): Promise<void>;
  request_exit(): Promise<ApplicationActivity | null>;
  wait_for_idle(wait: boolean): Promise<void>;
  exit_status(): Promise<ExitReport>;
  register_source(directory: string, interpreter: string): Promise<string>;
  select_source_environment(directory: string, interpreter: string): Promise<string>;
  choose_directory(): Promise<string | null>;
  create_source(parent: string, name: string): Promise<string>;
  prepare_author_environment(directory: string): Promise<string>;
  create_author_environment(directory: string, rebuild?: boolean): Promise<string>;
}

interface ExitWork {
  kind: string;
  identity: string;
  reason: string;
}
interface ExitReport {
  draining: boolean;
  finishing: ExitWork[];
  deferred: ExitWork[];
  blockers: ExitWork[];
  file_operations: number;
  error: string | null;
}

interface ApplicationActivity {
  file_operations?: number;
  runs: number;
  procedures: number;
  instrument_sessions: number;
  requests: number;
  calibration_tasks: number;
  scheduled_workflows: number;
  background_operations: number;
  background_attention: number;
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
    ["background_operations", "active background round"],
    ["background_attention", "unresolved background issue"],
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
  const [report, setReport] = useState<ExitReport>();
  const [closeNotice, setCloseNotice] = useState<string>();
  useEffect(() => {
    const active = new Set<string>();
    const preflight = (event: Event) => {
      const detail: unknown = (event as CustomEvent).detail;
      if (
        !detail ||
        typeof detail !== "object" ||
        !("requestId" in detail) ||
        typeof detail.requestId !== "string" ||
        !detail.requestId
      )
        return;
      const requestId = detail.requestId;
      if (active.has(requestId)) return;
      active.add(requestId);
      setCloseNotice("Saving experiment input before closing…");
      void prepareLaunchClose(requestId).then(async (status) => {
        if (!active.has(requestId)) return;
        try {
          const api = window.pywebview?.api;
          if (!api?.complete_close_preflight)
            throw new Error("Native close acknowledgement is unavailable.");
          await api.complete_close_preflight(requestId, status);
          if (active.has(requestId) && status !== "saved")
            setCloseNotice(
              "Window kept open: experiment input is not confirmed saved. Resolve recovery, save errors or conflicts before closing again.",
            );
        } catch (failure) {
          // Only the host can release a close barrier. A failed reply is not permission to close.
          if (active.has(requestId))
            setCloseNotice(`Window close is unconfirmed: ${String(failure)}`);
        }
      });
    };
    const release = (event: Event) => {
      const detail: unknown = (event as CustomEvent).detail;
      if (
        !detail ||
        typeof detail !== "object" ||
        !("requestId" in detail) ||
        typeof detail.requestId !== "string"
      )
        return;
      active.delete(detail.requestId);
      releaseLaunchClose(detail.requestId);
      setCloseNotice(
        "message" in detail && typeof detail.message === "string" ? detail.message : undefined,
      );
    };
    window.addEventListener("scopecat:close-preflight", preflight);
    window.addEventListener("scopecat:close-release", release);
    return () => {
      active.clear();
      window.removeEventListener("scopecat:close-preflight", preflight);
      window.removeEventListener("scopecat:close-release", release);
    };
  }, []);
  useEffect(() => {
    if (!open || !window.pywebview) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await window.pywebview!.api.exit_status();
        if (!disposed) {
          setReport(next);
          if (next.error) setError(next.error);
        }
      } catch (failure) {
        if (!disposed) setError(failure instanceof Error ? failure.message : String(failure));
      } finally {
        if (!disposed) timer = setTimeout(() => void refresh(), 1000);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [open, waiting]);
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
    <>
      {closeNotice && (
        <p role="status" className="border-b border-line bg-panel px-4 py-2 text-sm">
          {closeNotice}
        </p>
      )}
      <Dialog.Root
        open={open}
        onOpenChange={(next) => {
          if (!busy) {
            if (!next) void changeWaiting(false);
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
              <p>
                Waiting stops new work and lets admitted work finish. Future plans and safe human
                checkpoints remain saved. Paused work, resource waits, faults and uncertain device
                outcomes still need attention. Cancel restores normal admission.
              </p>
              {report &&
                (
                  [
                    ["Finishing now", report.finishing],
                    ["Deferred and retained", report.deferred],
                    ["Needs attention", report.blockers],
                  ] as const
                ).map(
                  ([title, items]) =>
                    items.length > 0 && (
                      <section key={title} aria-label={title}>
                        <h3>{title}</h3>
                        <ul>
                          {items.map((item) => (
                            <li key={`${item.kind}:${item.identity}`}>
                              <strong>{item.identity}</strong>: {item.reason}
                            </li>
                          ))}
                        </ul>
                      </section>
                    ),
                )}
              {report && report.file_operations > 0 && (
                <p>Finishing {report.file_operations} file operations.</p>
              )}
              {progress && <p role="status">{progress}</p>}
              {waiting && (
                <p role="status">
                  Automatic quit is enabled. Waiting does not answer reviews, resolve faults, or
                  resume paused workflows. Close this dialog to cancel automatic quit and return to
                  your work.
                </p>
              )}
              {error && <p role="alert">{error}</p>}
              <div className="flex flex-wrap gap-2">
                <button className={dangerButton} disabled={busy} onClick={() => void exit(false)}>
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
    </>
  );
}
