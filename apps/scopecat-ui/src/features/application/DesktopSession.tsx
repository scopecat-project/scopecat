import { Dialog } from "@base-ui/react/dialog";
import { useEffect, useState } from "react";
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
}

export interface InstallationStatus {
  python: string;
  static_dir: string;
  environment: Record<string, string>;
  adapter_identity: string | null;
}

interface DesktopAPI {
  status(): Promise<ApplicationStatus>;
  retry(): Promise<void>;
  restart(): Promise<void>;
  exit(background: boolean): Promise<void>;
  request_exit(): Promise<ApplicationActivity | null>;
  wait_for_idle(wait: boolean): Promise<void>;
  register_source(directory: string): Promise<string>;
  prepare_author_environment(directory: string): Promise<string>;
  create_author_environment(directory: string, rebuild?: boolean): Promise<string>;
}

interface ApplicationActivity {
  runs: number;
  procedures: number;
  instrument_sessions: number;
  requests: number;
  calibration_tasks: number;
  scheduled_workflows: number;
}

function describeWork(work: ApplicationActivity): string {
  const labels: [keyof ApplicationActivity, string][] = [
    ["runs", "experiment"],
    ["procedures", "workflow"],
    ["instrument_sessions", "device session"],
    ["requests", "change"],
    ["calibration_tasks", "calibration task"],
    ["scheduled_workflows", "scheduled workflow"],
  ];
  return labels
    .filter(([key]) => work[key] > 0)
    .map(([key, label]) => `${work[key]} ${label}${work[key] === 1 ? "" : "s"}`)
    .join(", ");
}

declare global {
  interface Window {
    pywebview?: { api: DesktopAPI };
    scopecatRequestExit?: () => void;
  }
}

export function DesktopSession() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [activity, setActivity] = useState<ApplicationActivity>();
  const [waiting, setWaiting] = useState(false);
  useEffect(() => {
    window.scopecatRequestExit = () => {
      setError(undefined);
      void window.pywebview?.api
        .request_exit()
        .then((work) => {
          if (work) {
            setActivity(work);
            setOpen(true);
          }
        })
        .catch((failure) => {
          setError(failure instanceof Error ? failure.message : String(failure));
          setOpen(true);
        });
    };
    return () => {
      delete window.scopecatRequestExit;
    };
  }, []);

  const exit = async (background: boolean) => {
    if (!window.pywebview) return;
    setBusy(true);
    setError(undefined);
    try {
      await window.pywebview.api.exit(background);
      if (background) {
        setOpen(false);
        setWaiting(false);
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!busy) {
          setOpen(next);
          if (!next) {
            setWaiting(false);
            void window.pywebview?.api.wait_for_idle(false);
          }
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
                onClick={() => {
                  void window.pywebview?.api.wait_for_idle(true).then(() => setWaiting(true));
                }}
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
