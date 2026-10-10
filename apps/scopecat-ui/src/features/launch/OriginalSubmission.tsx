import { useEffect, useRef } from "react";
import { secondaryButton } from "../../ui/styles";
import { subjectLabel } from "./scientific-selection";
import { useLaunchDraft } from "./LaunchDraft";

export function OriginalSubmission({
  onOpen,
  catalogReady: _catalogReady,
}: {
  onOpen: (id: string) => void;
  catalogReady: boolean;
}) {
  const { attempt, checkSubmission, rerun } = useLaunchDraft();
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (attempt?.status === "unknown" || attempt?.status === "rejected") {
      panel.current?.focus({ preventScroll: true });
      panel.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }, [attempt?.request.request_key, attempt?.status]);
  if (!attempt) return null;
  return (
    <section
      ref={panel}
      tabIndex={-1}
      className="scroll-mt-24 border border-accent/40 bg-panel-soft rounded-md p-4 space-y-3 break-words"
      aria-label="Original launch submission"
    >
      <h3 className="font-semibold">
        {attempt.status === "confirmed"
          ? "Submission confirmed"
          : attempt.status === "rejected"
            ? "Submission rejected"
            : "Original submission awaiting confirmation"}
      </h3>
      <p>
        Workspace {attempt.request.workspace_id} · {attempt.request.experiment} · version{" "}
        {attempt.request.version} · {attempt.request.actor} ·
        {subjectLabel(attempt.request.selection)}
      </p>
      <p>
        Request key: <code>{attempt.request.request_key}</code>
      </p>
      {attempt.error && <p role="alert">{attempt.error}</p>}
      {attempt.status === "unknown" && (
        <>
          <p>
            Keep this original request separate from your editable draft. Check whether it was
            already admitted before starting another acquisition.
          </p>
          <button
            className={secondaryButton}
            type="button"
            disabled={attempt.checking}
            onClick={() => {
              void checkSubmission();
            }}
          >
            Check original submission
          </button>
        </>
      )}
      {attempt.status === "pending" && (
        <p role="status">Waiting for the original submission response…</p>
      )}
      <div className="flex flex-wrap gap-2">
        {attempt.procedureId && (
          <button
            className={secondaryButton}
            type="button"
            onClick={() => onOpen(attempt.procedureId!)}
          >
            Open submitted procedure
          </button>
        )}
        <button
          className={secondaryButton}
          type="button"
          disabled={attempt.status === "pending"}
          onClick={rerun}
        >
          Prepare a new run (separate acquisition)
        </button>
      </div>
      <details>
        <summary>Original launch inputs</summary>
        <pre className="overflow-auto">
          {JSON.stringify(
            { inputs: attempt.request.inputs, control_edits: attempt.request.control_edits },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}
