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
  candidate: InstallationStatus | null;
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
  requalify(): Promise<void>;
  exit(background: boolean): Promise<void>;
  prepare_update(directory: string): Promise<InstallationStatus>;
  apply_update(): Promise<void>;
  register_source(directory: string): Promise<string>;
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
  useEffect(() => {
    window.scopecatRequestExit = () => setOpen(true);
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
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !busy && setOpen(next)}>
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup className={`${dialogPopup} grid gap-4 p-5`}>
            <Dialog.Title className={dialogTitle}>Close Scopecat</Dialog.Title>
            <Dialog.Description>
              Keep the application running to finish work in the background, or stop it and release
              its devices. Stopping interrupts active work. Saved records and source files are
              retained. Close your Python sessions before stopping.
            </Dialog.Description>
            {error && <p role="alert">{error}</p>}
            <div className="flex flex-wrap gap-2">
              <button className={primaryButton} disabled={busy} onClick={() => void exit(false)}>
                Stop and close
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
