import { SubmissionComparison } from "./SubmissionComparison";
import { useEffect, useRef } from "react";
import { secondaryButton } from "../../ui/styles";
import { subjectLabel } from "./scientific-selection";
import { useLaunchDraft } from "./LaunchDraft";

export function OriginalSubmission({
  onOpen,
  onPrepare,
  catalogReady: _catalogReady,
}: {
  onOpen: (id: string) => void;
  onPrepare?: () => void;
  catalogReady: boolean;
}) {
  const { attempt, draft, checkSubmission, rerun } = useLaunchDraft();
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
      className={`scroll-mt-24 border-b py-2 space-y-2 break-words ${attempt.status === "confirmed" ? "border-line" : "border-accent/40 bg-panel-soft p-3"}`}
      aria-label="Original launch submission"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">
          {attempt.status === "confirmed"
            ? "Submission confirmed"
            : attempt.status === "rejected"
              ? "Submission rejected"
              : "Original submission awaiting confirmation"}
        </h3>
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
            onClick={() => {
              rerun();
              onPrepare?.();
            }}
          >
            Prepare a new run (separate acquisition)
          </button>
        </div>
      </div>
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
      <details className="text-sm">
        <summary className="cursor-pointer text-text-dim">
          Submission source and request details
        </summary>
        <div className="space-y-2 py-2 select-text [overflow-wrap:anywhere]">
          <p>
            Workspace {attempt.request.workspace_id} · {attempt.request.experiment} · version{" "}
            {attempt.request.version} · {attempt.request.actor} ·
            {subjectLabel(attempt.request.selection)}
          </p>
          <p>
            Request key: <code>{attempt.request.request_key}</code>
          </p>
        </div>
      </details>
      <SubmissionComparison request={attempt.request} draft={draft} />
    </section>
  );
}
