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
  if (!attempt) return null;
  return (
    <section className="border rounded p-3 space-y-2" aria-label="Original launch submission">
      <h3>
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
            type="button"
            disabled={attempt.checking}
            onClick={() => {
              void checkSubmission();
            }}
            className="border rounded px-3 py-1"
          >
            Check original submission
          </button>
        </>
      )}
      {attempt.status === "pending" && (
        <p role="status">Waiting for the original submission response…</p>
      )}
      {attempt.procedureId && (
        <button type="button" onClick={() => onOpen(attempt.procedureId!)} className="underline">
          Open submitted procedure
        </button>
      )}
      <button type="button" disabled={attempt.status === "pending"} onClick={rerun}>
        Prepare a new run (separate acquisition)
      </button>
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
