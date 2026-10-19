import { useState } from "react";
import { AlertDialog } from "@base-ui/react/alert-dialog";
import { AlertTriangle } from "lucide-react";
import {
  classes,
  dialogBackdrop,
  dialogDescription,
  dialogPopup,
  dialogTitle,
  dialogViewport,
  primaryButton,
  dangerButton,
  secondaryButton,
} from "./styles";

export interface ConfirmationRequest {
  title: string;
  description: string;
  confirmLabel: string;
  intent?: "default" | "danger";
  onConfirm: () => void;
}

export function useConfirmationDialog() {
  const [request, setRequest] = useState<ConfirmationRequest>();

  return {
    requestConfirmation: setRequest,
    confirmationDialog: request ? (
      <ConfirmationDialog request={request} onClose={() => setRequest(undefined)} />
    ) : null,
  };
}

function ConfirmationDialog({
  request,
  onClose,
}: {
  request: ConfirmationRequest;
  onClose: () => void;
}) {
  const confirm = () => {
    onClose();
    request.onConfirm();
  };

  return (
    <AlertDialog.Root open onOpenChange={(open) => !open && onClose()}>
      <AlertDialog.Portal>
        <AlertDialog.Backdrop className={dialogBackdrop} />
        <AlertDialog.Viewport className={dialogViewport}>
          <AlertDialog.Popup className={classes(dialogPopup, "w-[min(440px,100%)]")}>
            <header className="grid grid-cols-[34px_minmax(0,1fr)] items-start gap-[11px] p-[18px]">
              <span
                className={classes(
                  "grid size-8 place-items-center rounded-md",
                  request.intent === "danger"
                    ? "bg-red-soft text-red"
                    : "bg-accent-soft text-accent",
                )}
                aria-hidden="true"
              >
                <AlertTriangle size={18} />
              </span>
              <div>
                <AlertDialog.Title className={dialogTitle}>{request.title}</AlertDialog.Title>
                <AlertDialog.Description className={dialogDescription}>
                  {request.description}
                </AlertDialog.Description>
              </div>
            </header>
            <footer className="flex flex-wrap justify-end gap-2 border-t border-line bg-panel-soft px-[18px] py-3">
              <AlertDialog.Close className={secondaryButton}>Cancel</AlertDialog.Close>
              <AlertDialog.Close
                className={request.intent === "danger" ? dangerButton : primaryButton}
                onClick={confirm}
              >
                {request.confirmLabel}
              </AlertDialog.Close>
            </footer>
          </AlertDialog.Popup>
        </AlertDialog.Viewport>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}
