import type { ComparisonHandoff } from "../analyses/RunComparison";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { definitionKey, invalidateDraft, useLaunchDraft } from "./LaunchDraft";
import { AuthorRefresh } from "./AuthorRefresh";
import { LaunchForm } from "./LaunchForm";
import { OriginalSubmission } from "./OriginalSubmission";
import { ProcedureHistory } from "./ProcedureHistory";
import { ProcedureProgress } from "./ProcedureProgress";

export function LaunchWorkspace({
  handoff,
  onHandoffImported,
}: { handoff?: ComparisonHandoff; onHandoffImported?: () => void } = {}) {
  const { projectId, draft, select, update, importHandoff } = useLaunchDraft();
  const queryClient = useQueryClient();
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ["config", "launch-context", projectId] });
  }, [projectId, queryClient]);
  const catalog = useQuery({
    queryKey: ["experiment-launcher", projectId],
    enabled: Boolean(projectId),
    queryFn: async () => {
      const result = await apiData(apiClient.GET("/api/v1/experiment-launcher"));
      return result.entries;
    },
  });
  const [procedureId, setProcedureId] = useState(
    () =>
      draft?.admittedProcedureId ??
      new URLSearchParams(window.location.search).get("procedure") ??
      "",
  );
  function admitted(id: string) {
    setProcedureId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("procedure", id);
    window.history.replaceState(null, "", url);
  }
  const entry = draft
    ? catalog.data?.find((item) => item.id === draft.experiment)
    : catalog.data?.[0];
  const unavailable = catalog.isSuccess && !catalog.isFetching && draft !== undefined && !entry;
  useEffect(() => {
    if (unavailable && (draft.preview || draft.pending || draft.requestKey))
      update((current) =>
        invalidateDraft(
          current,
          "The selected experiment is unavailable. Preview again when its declaration returns.",
        ),
      );
  }, [unavailable, draft, update]);
  useEffect(() => {
    if (entry) select(entry);
  }, [entry, select]);
  const handoffTarget = handoff
    ? catalog.data?.find((item) => item.id === handoff.request.experiment)
    : undefined;
  const handoffUnavailable = handoff && catalog.isSuccess && !handoffTarget;
  useEffect(() => {
    if (!handoff || !handoffTarget) return;
    importHandoff(handoffTarget, handoff);
    onHandoffImported?.();
  }, [handoff, handoffTarget, importHandoff, onHandoffImported]);
  return (
    <section className="p-6 space-y-4">
      <h2 className="text-lg font-semibold">Experiments</h2>
      <AuthorRefresh projectId={projectId} />
      {handoffUnavailable && (
        <p role="alert">
          The suggested experiment is unavailable. The source analysis is retained.
        </p>
      )}
      {draft?.handoff && (
        <p>
          Suggested by{" "}
          <a
            className="underline"
            href={`?compare=${encodeURIComponent(draft.handoff.source_run)}&comparison-analysis=${encodeURIComponent(draft.handoff.source_analysis)}#analyses`}
          >
            {draft.handoff.source_analysis}
          </a>{" "}
          · {draft.handoff.source_hash}. This source is retained only in the current draft, not yet
          as destination-run provenance.
        </p>
      )}
      <p>Select a maintained experiment and preview its configured parameters.</p>
      {catalog.isPending && <p role="status">Loading experiments…</p>}
      {catalog.error && <p role="alert">{catalog.error.message}</p>}
      {catalog.data?.length === 0 && <p>This project has no registered experiments.</p>}
      {unavailable && (
        <p role="alert">
          The selected experiment ({draft.experiment}) is unavailable. Its inputs are retained until
          it returns or you explicitly choose another experiment.
        </p>
      )}
      {(entry || draft) && (
        <>
          <label className="block">
            Experiment{" "}
            <select
              aria-label="Experiment"
              value={draft?.experiment ?? entry?.id}
              onChange={(event) => {
                const selected = catalog.data?.find((item) => item.id === event.target.value);
                if (selected) select(selected);
              }}
              className="border rounded p-2 ml-2"
            >
              {!entry && draft && (
                <option value={draft.experiment}>{draft.experiment} (unavailable)</option>
              )}
              {catalog.data?.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          {entry && draft?.definition === definitionKey(entry) && (
            <LaunchForm
              key={draft.definition}
              entry={entry}
              onAdmitted={admitted}
              catalogReady={catalog.isSuccess && !catalog.isFetching}
            />
          )}
        </>
      )}
      <OriginalSubmission
        onOpen={admitted}
        catalogReady={Boolean(entry) && catalog.isSuccess && !catalog.isFetching}
      />
      <ProcedureHistory selectedId={procedureId} onSelect={admitted} />
      {procedureId && <ProcedureProgress key={procedureId} procedureId={procedureId} />}
    </section>
  );
}
