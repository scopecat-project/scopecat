import { detailCard, eyebrow, secondaryButton } from "../../ui/styles";
import { LaunchRecoveryPanel } from "./LaunchRecoveryPanel";
import type { ComparisonHandoff } from "../analyses/RunComparison";
import { useEffect, useRef } from "react";
import { navigate, useLocationUrl, type NavigationOptions } from "../../lib/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { definitionKey, invalidateDraft, useLaunchDraft } from "./LaunchDraft";
import { SourceSelector } from "./SourceSelector";
import { useAuthorWorkspaces } from "./source-api";
import { AuthorRefresh } from "./AuthorRefresh";
import { PlanLibrary } from "./PlanLibrary";
import { ExperimentPicker } from "./ExperimentPicker";
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
    sourceObserved,
  } = useLaunchDraft();
  const queryClient = useQueryClient();
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ["config", "launch-context", projectId] });
  }, [projectId, queryClient]);
  const workspaceId = handoff ? handoff.request.workspace_id : selectedWorkspaceId;
  const sources = useAuthorWorkspaces(projectId);
  useEffect(() => {
    const available = sources.data?.items.filter((source) => source.available) ?? [];
    if (!workspaceId && available.length === 1 && available[0]) selectWorkspace(available[0].id);
  }, [workspaceId, sources.data, selectWorkspace]);
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
      return result;
    },
  });
  useEffect(() => {
    if (catalog.data) sourceObserved(workspaceId, catalog.data.code_revision ?? undefined);
  }, [catalog.data, workspaceId, sourceObserved]);
  const location = useLocationUrl();
  const procedureId = location.searchParams.get("procedure") ?? draft?.admittedProcedureId ?? "";
  const execution = useRef<HTMLElement>(null);
  useEffect(() => {
    if (procedureId) {
      execution.current?.focus({ preventScroll: true });
      execution.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }, [procedureId]);
  const entry = draft?.experiment
    ? catalog.data?.entries.find((item) => item.id === draft.experiment)
    : catalog.data?.entries[0];
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
    ? catalog.data?.entries.find((item) => item.id === handoff.request.experiment)
    : undefined;
  const handoffUnavailable = handoff && catalog.isSuccess && !handoffTarget;
  useEffect(() => {
    if (!handoff || !handoffTarget) return;
    importHandoff(handoffTarget, handoff);
    onHandoffImported?.();
  }, [handoff, handoffTarget, importHandoff, onHandoffImported]);
  return (
    <section className="p-6 space-y-5 max-w-6xl mx-auto">
      <header>
        <p className={eyebrow}>Experiment workbench</p>
        <h2 className="text-xl font-semibold">Experiments</h2>
        <p className="mt-2 text-sm text-text-dim">
          Prepare an experiment, check its inputs, then start acquisition.
        </p>
      </header>
      <OriginalSubmission
        onOpen={openProcedure}
        catalogReady={sourceAvailable && !handoff && Boolean(entry) && catalog.isSuccess}
      />
      {procedureId && (
        <section
          ref={execution}
          tabIndex={-1}
          aria-label="Selected execution"
          className="scroll-mt-24 rounded-md focus-visible:outline-accent"
        >
          <ProcedureProgress key={procedureId} procedureId={procedureId} />
        </section>
      )}
      <section aria-labelledby="experiment-preparation" className={`${detailCard} space-y-4`}>
        <div>
          <h3 id="experiment-preparation" className="font-semibold">
            Prepare an experiment
          </h3>
          <p className="text-sm text-text-dim">
            Your editable inputs stay here while submitted work runs.
          </p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 border-b border-line pb-4">
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
        </div>
        {codeRevision && (
          <div className="space-y-2">
            <p>
              This draft is pinned to author revision {codeRevision.content_hash}. Refresh prepares
              the workspace's current code without changing this pinned plan.
            </p>
            <button
              type="button"
              className={secondaryButton}
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
            · {draft.handoff.source_hash}. This source is retained only in the current draft, not
            yet as destination-run provenance.
          </p>
        )}
        {sourceAvailable && catalog.isPending && <p role="status">Loading experiments…</p>}
        {catalog.error && <p role="alert">{catalog.error.message}</p>}
        {catalog.data?.entries.length === 0 && <p>This project has no registered experiments.</p>}
        {unavailable && draft && (
          <p role="alert">
            The selected experiment ({draft.experiment}) is unavailable. Its inputs are retained
            until it returns or you explicitly choose another experiment.
          </p>
        )}
        {(entry || draft) && (
          <>
            <ExperimentPicker
              key={workspaceId}
              entries={catalog.data?.entries ?? []}
              selectedId={draft?.experiment ?? entry?.id ?? ""}
              disabled={!sourceAvailable || Boolean(handoff)}
              onSelect={(selected) => select(selected, false, workspaceId)}
            />
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
      </section>
      <section aria-labelledby="experiment-history" className="space-y-3">
        <h3 id="experiment-history" className="font-semibold">
          Reuse and recovery
        </h3>
        <PlanLibrary
          key={`plans:${projectId}`}
          initializing={catalog.isPending && draft === undefined}
        />
        <LaunchRecoveryPanel />
        <ProcedureHistory selectedId={procedureId} onSelect={openProcedure} />
      </section>
      {projectId && (
        <CalibrationTasks
          key={`calibration:${projectId}`}
          projectId={projectId}
          onProcedure={admitted}
        />
      )}
    </section>
  );
}

function openProcedure(id: string, options?: NavigationOptions) {
  const url = new URL(window.location.href);
  url.searchParams.set("procedure", id);
  navigate(url, options);
}
function admitted(id: string) {
  openProcedure(id, { replace: true });
}
