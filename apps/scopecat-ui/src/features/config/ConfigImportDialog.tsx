import { useRef } from "react";
import { Dialog } from "@base-ui/react/dialog";
import { FileUp, LoaderCircle, X } from "lucide-react";
import {
  classes,
  dialogBackdrop,
  dialogDescription,
  dialogPopup,
  dialogTitle,
  dialogViewport,
  iconButton,
  primaryButton,
  secondaryButton,
} from "../../ui/styles";
import { ActionNote } from "./ConfigUi";
import { safeConfigEntryId } from "./config-utils";
import type { ImportDraft } from "./useConfigMutationWorkflow";

export function ConfigImportDialog({
  draft,
  note,
  pending,
  disabled,
  onChange,
  onNoteChange,
  onCancel,
  onSubmit,
}: {
  draft: ImportDraft;
  note: string;
  pending: boolean;
  disabled: boolean;
  onChange: (draft: ImportDraft) => void;
  onNoteChange: (note: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const validEntryId =
    draft.entryId.length > 0 && safeConfigEntryId(draft.entryId) === draft.entryId;
  const entryIdInput = useRef<HTMLInputElement>(null);
  return (
    <Dialog.Root open onOpenChange={(open) => !open && !pending && onCancel()}>
      <Dialog.Portal>
        <Dialog.Backdrop className={dialogBackdrop} />
        <Dialog.Viewport className={dialogViewport}>
          <Dialog.Popup className={dialogPopup} initialFocus={entryIdInput}>
            <header className="grid grid-cols-[38px_minmax(0,1fr)_auto] items-center gap-2.5 border-b border-line px-[18px] py-4">
              <span
                className="grid size-9 place-items-center rounded-[9px] bg-accent-soft text-accent"
                aria-hidden="true"
              >
                <FileUp size={19} />
              </span>
              <div>
                <Dialog.Title className={dialogTitle}>Import config snapshot</Dialog.Title>
                <Dialog.Description
                  className={classes(
                    dialogDescription,
                    "mt-[3px] overflow-hidden text-[0.59rem] text-ellipsis whitespace-nowrap",
                  )}
                >
                  Advanced raw import · {draft.fileName}
                </Dialog.Description>
              </div>
              <Dialog.Close
                className={iconButton}
                aria-label="Close import dialog"
                disabled={pending}
              >
                <X size={16} />
              </Dialog.Close>
            </header>
            <div className="grid gap-3.5 p-[18px] [&>label]:grid [&>label]:gap-1.5 [&_label>span]:text-[0.54rem] [&_label>span]:font-extrabold [&_label>span]:tracking-[0.07em] [&_label>span]:text-text-dim [&_label>span]:uppercase [&_label>input]:min-h-[38px] [&_label>input]:w-full [&_label>input]:rounded-[8px] [&_label>input]:border [&_label>input]:border-line [&_label>input]:bg-bg [&_label>input]:px-2.5 [&_label>input]:text-[0.68rem] [&_label>input]:text-text [&_label>input]:outline-0 [&_label>input:focus]:border-[rgb(128_163_207_/_45%)] [&_label>small]:text-[0.57rem] [&_label>small]:leading-[1.4] [&_label>small]:text-red">
              <p>
                Import selects parameter defaults only. A snapshot with a different executable setup
                must first have its setup saved and explicitly selected in Executable setup.
              </p>
              <label>
                <span>Registry entry id</span>
                <input
                  ref={entryIdInput}
                  value={draft.entryId}
                  onChange={(event) => onChange({ ...draft, entryId: event.target.value })}
                  aria-invalid={!validEntryId}
                />
                {!validEntryId && (
                  <small>
                    Use letters, numbers, underscores, and hyphens; start with a letter or number.
                  </small>
                )}
              </label>
              <ActionNote value={note} onChange={onNoteChange} />
            </div>
            <footer className="flex justify-end gap-2 border-t border-line bg-panel-soft px-[18px] py-[13px]">
              <Dialog.Close className={secondaryButton} disabled={pending}>
                Cancel
              </Dialog.Close>
              <button
                className={primaryButton}
                type="button"
                disabled={disabled || !validEntryId}
                onClick={onSubmit}
              >
                {pending ? (
                  <LoaderCircle className="animate-spin" size={15} />
                ) : (
                  <FileUp size={15} />
                )}
                Import raw snapshot
              </button>
            </footer>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
