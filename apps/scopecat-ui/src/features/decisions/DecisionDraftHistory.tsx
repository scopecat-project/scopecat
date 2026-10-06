import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDecisionDraftHistory } from "./decision-api";
import { errorMessage } from "../../lib/presentation";

export function DecisionDraftHistory() {
  const [open, setOpen] = useState(false);
  const [before, setBefore] = useState<number>();
  const history = useQuery({
    queryKey: ["decision-draft-history", before],
    queryFn: () => getDecisionDraftHistory(before),
    enabled: open,
    refetchInterval: 2000,
  });
  return (
    <section className="mb-3 rounded-md border border-line bg-panel p-3">
      <button type="button" onClick={() => setOpen(!open)}>
        Recover saved Decision drafts
      </button>
      {open && (
        <div className="grid gap-2 text-sm">
          <p>
            Retained editing history, including conflicts and discarded copies. These are not
            recorded scientific decisions. Completed or changed requests must be reviewed in the
            task; this history cannot submit a decision or continue a run. Copy text you want to
            reuse explicitly.
          </p>
          {history.isError && <p role="alert">{errorMessage(history.error)}</p>}
          {history.data?.items.map((draft) => (
            <details key={draft.revision}>
              <summary>
                {draft.target.procedure_run_id} · {draft.target.step_key} · attempt{" "}
                {draft.target.attempt} ·{draft.state} · revision {draft.revision}
              </summary>
              <p>
                Saved {draft.created_at}; original request {draft.baseline.request_hash}
              </p>
              <textarea
                aria-label={`Retained draft ${draft.revision}`}
                readOnly
                rows={8}
                className="w-full font-mono"
                value={JSON.stringify(draft.input, null, 2)}
              />
            </details>
          ))}
          {history.data?.next_cursor && (
            <button type="button" onClick={() => setBefore(history.data.next_cursor!)}>
              Older drafts
            </button>
          )}
          {before && (
            <button type="button" onClick={() => setBefore(undefined)}>
              Newest drafts
            </button>
          )}
        </div>
      )}
    </section>
  );
}
