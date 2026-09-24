import type { ComparisonHandoff } from "../analyses/RunComparison";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { definitionKey, invalidateDraft, useLaunchDraft } from "./LaunchDraft";
import { SourceSelector } from "./SourceSelector";
import { useAuthorWorkspaces } from "./source-api";
import { AuthorRefresh } from "./AuthorRefresh";
import { PlanLibrary } from "./PlanLibrary";
import { LaunchForm } from "./LaunchForm";
import { OriginalSubmission } from "./OriginalSubmission";
import { ProcedureHistory } from "./ProcedureHistory";
import { ProcedureProgress } from "./ProcedureProgress";
import { CalibrationTasks } from "./CalibrationTasks";

export function LaunchWorkspace({
  handoff,
  onHandoffImported,
}: { handoff?: ComparisonHandoff; onHandoffImported?: () => void } = {}) {
  const {
    projectId,
    workspaceId: selectedWorkspaceId,
    selectWorkspace,
    useCurrentSource,
    authorRefreshed,
    draft,
    select,
    update,
    importHandoff,
  } = useLaunchDraft();
  const queryClient = useQueryClient();
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ["config", "launch-context", projectId] });
  }, [projectId, queryClient]);
  const workspaceId = handoff ? (handoff.request.workspace_id ?? "legacy") : selectedWorkspaceId;
  const sources = useAuthorWorkspaces(projectId);
  const sourceAvailable =
    sources.data?.items.some((source) => source.id === workspaceId && source.available) ?? false;
  const codeRevision = handoff ? handoff.request.code_revision : draft?.codeRevision;
  const catalog = useQuery({
    queryKey: ["experiment-launcher", projectId, workspaceId, codeRevision?.content_hash],
    enabled: Boolean(projectId && sourceAvailable),
    queryFn: async ({ signal }) => {
      const result = await apiData(
        apiClient.GET("/api/v1/experiment-launcher", {
          params: {
            query: { code_revision: codeRevision?.content_hash },
            header: { "X-Scopecat-Workspace": workspaceId },
          },
          signal,
        }),
      );
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
  const entry = draft?.experiment
    ? catalog.data?.find((item) => item.id === draft.experiment)
    : catalog.data?.[0];
  const unavailable =
    !handoff &&
    sourceAvailable &&
    catalog.isSuccess &&
    !catalog.isFetching &&
    Boolean(draft?.experiment) &&
    !entry;
  useEffect(() => {
    if (unavailable && draft && (draft.preview || draft.pending || draft.requestKey))
      update((current) =>
        invalidateDraft(
          current,
          "The selected experiment is unavailable. Preview again when its declaration returns.",
        ),
      );
  }, [unavailable, draft, update]);
  useEffect(() => {
    if (entry && !handoff && sourceAvailable) select(entry, false, workspaceId);
  }, [entry, select, handoff, sourceAvailable, workspaceId]);
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
      <SourceSelector
        catalog={sources}
        workspaceId={workspaceId}
        onSelect={(id) => {
          onHandoffImported?.();
          selectWorkspace(id);
        }}
      />
      <AuthorRefresh
        projectId={projectId}
        workspaceId={workspaceId}
        disabled={!sourceAvailable}
        onRefreshed={() => authorRefreshed(workspaceId)}
      />
      {codeRevision && (
        <div className="space-y-2">
          <p>
            This draft is pinned to author revision {codeRevision.content_hash}. Refresh prepares
            the workspace's current code without changing this pinned plan.
          </p>
          <button
            type="button"
            disabled={!sourceAvailable}
            onClick={() => {
              onHandoffImported?.();
              useCurrentSource(workspaceId);
            }}
          >
            Use current source
          </button>
        </div>
      )}
      <PlanLibrary
        key={`plans:${projectId}`}
        initializing={catalog.isPending && draft === undefined}
      />
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
          {draft.handoff.request.selection?.configuration?.kind !== "working_point" && (
            <>
              {" "}
              Only suggested inputs were imported. Previous sample and working-point selections were
              cleared. Review the current default configuration or explicitly select a
              sample/context in Configuration before preview.
            </>
          )}
        </p>
      )}
      <p>Select a maintained experiment and preview its configured parameters.</p>
      {sourceAvailable && catalog.isPending && <p role="status">Loading experiments…</p>}
      {catalog.error && <p role="alert">{catalog.error.message}</p>}
      {catalog.data?.length === 0 && <p>This project has no registered experiments.</p>}
      {unavailable && draft && (
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
              disabled={!sourceAvailable || Boolean(handoff)}
              value={draft?.experiment ?? entry?.id}
              onChange={(event) => {
                const selected = catalog.data?.find((item) => item.id === event.target.value);
                if (selected) select(selected, false, workspaceId);
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
          {entry &&
            sourceAvailable &&
            !handoff &&
            draft?.workspaceId === workspaceId &&
            draft.definition === definitionKey(entry) && (
              <LaunchForm
                key={`${workspaceId}:${codeRevision?.content_hash ?? "current"}:${draft.definition}`}
                entry={entry}
                onAdmitted={admitted}
                catalogReady={sourceAvailable && catalog.isSuccess}
              />
            )}
        </>
      )}
      <OriginalSubmission
        onOpen={admitted}
        catalogReady={sourceAvailable && !handoff && Boolean(entry) && catalog.isSuccess}
      />
      <ProcedureHistory selectedId={procedureId} onSelect={admitted} />
      {projectId && (
        <CalibrationTasks
          key={`calibration:${projectId}`}
          projectId={projectId}
          onProcedure={admitted}
        />
      )}
      {procedureId && <ProcedureProgress key={procedureId} procedureId={procedureId} />}
    </section>
  );
}
