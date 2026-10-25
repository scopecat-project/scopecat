import { clearDraftLocation } from "./draft-location";
import { readDraft } from "./launch-recovery";
import {
  pageTitle,
  sectionTitle,
  pageContent,
  classes,
  detailCard,
  secondaryButton,
  workbenchTabs,
  workbenchTab,
} from "../../ui/styles";
import { DraftPicker } from "./DraftPicker";
import { LaunchRecoveryPanel } from "./LaunchRecoveryPanel";
import type { ComparisonHandoff } from "../analyses/RunComparison";
import { useEffect, useRef, useState } from "react";
import { navigate, useLocationUrl, type NavigationOptions } from "../../lib/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { definitionKey, invalidateDraft, useLaunchDraft } from "./LaunchDraft";
import { SourceSelector } from "./SourceSelector";
import { useAuthorWorkspaces } from "./source-api";
import { AuthorRefresh } from "./AuthorRefresh";
import { LineScans } from "./LineScans";
import { PlanLibrary } from "./PlanLibrary";
import { ExperimentPicker } from "./ExperimentPicker";
import { LaunchForm } from "./LaunchForm";
import { OriginalSubmission } from "./OriginalSubmission";
import { SubmittedWork } from "./SubmittedWork";
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
    selectCurrentSource,
    authorRefreshed,
    draft,
    select,
    update,
    importHandoff,
    sourceObserved,
    switchDraft,
  } = useLaunchDraft();
  const location = useLocationUrl();
  const linkedId = location.searchParams.get("draft");
  const linkedWorkspace = location.searchParams.get("workspace");
  const linkedExperiment = location.searchParams.get("experiment");
  const linked = linkedId !== null && !handoff;
  const linkedMatches =
    linked &&
    draft?.workspaceId === linkedWorkspace &&
    draft.experiment === linkedExperiment &&
    (draft.draftId ?? "default") === linkedId;
  const queryClient = useQueryClient();
  useEffect(() => {
    void queryClient.invalidateQueries({ queryKey: ["config", "launch-context", projectId] });
  }, [projectId, queryClient]);
  const workspaceId = handoff
    ? handoff.request.workspace_id
    : linked
      ? (linkedWorkspace ?? "")
      : selectedWorkspaceId;
  const sources = useAuthorWorkspaces(projectId);
  useEffect(() => {
    const available = sources.data?.items.filter((source) => source.available) ?? [];
    if (!linked && !workspaceId && available.length === 1 && available[0])
      selectWorkspace(available[0].id);
  }, [workspaceId, sources.data, selectWorkspace, linked]);
  const sourceAvailable =
    sources.data?.items.some((source) => source.id === workspaceId && source.available) ?? false;
  const linkedRead = useQuery({
    queryKey: ["linked-launch-draft", projectId, linkedWorkspace, linkedExperiment, linkedId],
    enabled:
      linked &&
      !linkedMatches &&
      sourceAvailable &&
      Boolean(linkedExperiment && linkedId && linkedWorkspace),
    retry: false,
    queryFn: async () => {
      const { head } = await readDraft(linkedWorkspace!, linkedExperiment!, linkedId!);
      if (
        !head ||
        head.state !== "saved" ||
        head.target.workspace_id !== linkedWorkspace ||
        head.target.experiment !== linkedExperiment ||
        (head.target.draft_id ?? "default") !== linkedId
      )
        throw new Error("This saved draft is unavailable. No other draft was opened.");
      if (head.input.pinned_source && !head.input.code_revision)
        throw new Error(
          "This draft's pinned code revision is unavailable. Current code was not substituted.",
        );
      return head;
    },
  });
  const codeRevision = handoff
    ? handoff.request.code_revision
    : !linked || linkedMatches
      ? draft?.codeRevision
      : linkedRead.data?.input.pinned_source
        ? (linkedRead.data.input.code_revision ?? undefined)
        : undefined;
  const catalog = useQuery({
    queryKey: ["experiment-launcher", projectId, workspaceId, codeRevision?.content_hash],
    enabled: Boolean(projectId && sourceAvailable && (!linked || linkedMatches || linkedRead.data)),
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
    if (catalog.data && !handoff && (!linked || linkedMatches))
      sourceObserved(workspaceId, catalog.data.code_revision ?? undefined);
  }, [catalog.data, workspaceId, sourceObserved, handoff, linked, linkedMatches]);
  const linkedEntry = catalog.data?.entries.find((item) => item.id === linkedExperiment);
  const [linkBlocked, setLinkBlocked] = useState(false);
  const [linkRetry, setLinkRetry] = useState(0);
  const linkedActions = useRef(switchDraft);
  useEffect(() => {
    linkedActions.current = switchDraft;
  }, [switchDraft]);
  useEffect(() => {
    if (
      !linked ||
      !linkedRead.data ||
      linkedMatches ||
      !sourceAvailable ||
      !linkedEntry ||
      !catalog.isSuccess
    )
      return;
    let active = true;
    void linkedActions
      .current(linkedRead.data, () => active)
      .then((opened) => {
        if (active) setLinkBlocked(!opened);
      });
    return () => {
      active = false;
    };
  }, [
    linked,
    linkedRead.data,
    linkedMatches,
    linkRetry,
    sourceAvailable,
    linkedEntry,
    catalog.isSuccess,
  ]);
  const linkError =
    linked && !linkedMatches
      ? !linkedId || !linkedWorkspace || !linkedExperiment
        ? "The draft link is incomplete. No other draft was opened."
        : sources.error
          ? `Cannot check the linked draft's workspace: ${sources.error.message}`
          : catalog.error
            ? `Cannot check the linked draft's experiment: ${catalog.error.message}`
            : sources.isSuccess && !sourceAvailable
              ? "This draft's code workspace is unavailable. No other draft was opened."
              : catalog.isSuccess && !linkedEntry
                ? "This draft's experiment is unavailable. No other draft was opened."
                : (linkedRead.error?.message ??
                  (linkBlocked
                    ? "Save or resolve the current input before opening the linked draft."
                    : undefined))
      : undefined;
  const procedureId = location.searchParams.get("procedure") ?? draft?.admittedProcedureId ?? "";
  const execution = useRef<HTMLElement>(null);
  const preparation = useRef<HTMLElement>(null);
  const preparing = location.searchParams.has("preparation");
  const view =
    location.searchParams.get("workbench") === "submitted"
      ? "submitted"
      : preparing || !procedureId
        ? "editor"
        : "monitor";
  const lastFocusedRoute = useRef<string | undefined>(undefined);
  useEffect(() => {
    const route = JSON.stringify([procedureId, preparing, view]);
    if (lastFocusedRoute.current === route) return;
    lastFocusedRoute.current = route;
    if (view === "editor" && preparing) {
      preparation.current?.focus({ preventScroll: true });
      preparation.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    } else if (view === "monitor" && procedureId) {
      execution.current?.focus({ preventScroll: true });
      execution.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }, [procedureId, preparing, view]);
  const entry = draft?.experiment
    ? catalog.data?.entries.find((item) => item.id === draft.experiment)
    : catalog.data?.entries[0];
  const unavailable =
    !handoff &&
    (!linked || linkedMatches) &&
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
    if (entry && !handoff && sourceAvailable && (!linked || linkedMatches))
      void select(entry, false, workspaceId);
  }, [entry, select, handoff, sourceAvailable, workspaceId, linked, linkedMatches]);
  const handoffTarget = handoff
    ? catalog.data?.entries.find((item) => item.id === handoff.request.experiment)
    : undefined;
  const handoffUnavailable = handoff && catalog.isSuccess && !handoffTarget;
  const importer = useRef({ importHandoff, onHandoffImported });
  useEffect(() => {
    importer.current = { importHandoff, onHandoffImported };
  }, [importHandoff, onHandoffImported]);
  const [handoffRetry, setHandoffRetry] = useState(0);
  const [handoffFailed, setHandoffFailed] = useState(false);
  useEffect(() => {
    if (!handoff || !handoffTarget) return;
    let active = true;
    void importer.current.importHandoff(handoffTarget, handoff).then((accepted) => {
      if (!active) return;
      if (accepted) {
        setHandoffFailed(false);
        importer.current.onHandoffImported?.();
      } else setHandoffFailed(true);
    });
    return () => {
      active = false;
    };
  }, [handoff, handoffTarget, handoffRetry]);
  return (
    <section className={classes(pageContent, "experiment-workspace")}>
      {handoffFailed && (
        <button
          type="button"
          onClick={() => {
            setHandoffFailed(false);
            setHandoffRetry((value) => value + 1);
          }}
        >
          Retry importing inputs
        </button>
      )}
      <header>
        <h2 className={pageTitle}>Experiments</h2>
      </header>
      <nav aria-label="Experiment workspaces" className={workbenchTabs}>
        <button
          type="button"
          className={workbenchTab}
          aria-current={view === "editor" ? "page" : undefined}
          onClick={() => showView("editor")}
        >
          Editor
        </button>
        <button
          type="button"
          className={workbenchTab}
          disabled={!procedureId}
          aria-current={view === "monitor" ? "page" : undefined}
          onClick={() => showView("monitor")}
        >
          Monitor
        </button>
        <button
          type="button"
          className={workbenchTab}
          aria-current={view === "submitted" ? "page" : undefined}
          onClick={() => showView("submitted")}
        >
          Submitted work
        </button>
      </nav>
      <div>
        <OriginalSubmission
          onOpen={openProcedure}
          onPrepare={() => showView("editor")}
          catalogReady={sourceAvailable && !handoff && Boolean(entry) && catalog.isSuccess}
        />
      </div>
      {view === "monitor" && procedureId && (
        <section
          ref={execution}
          tabIndex={-1}
          aria-label="Selected execution"
          className="scroll-mt-24 rounded-md focus-visible:outline-accent"
        >
          <ProcedureProgress key={procedureId} procedureId={procedureId} projectId={projectId} />
        </section>
      )}
      {linked && !linkedMatches && (
        <section aria-label="Open saved draft" className={detailCard}>
          <p role={linkError ? "alert" : "status"}>
            {linkError ?? "Opening the exact saved draft…"}
          </p>
          {linkError && (
            <button
              type="button"
              className={secondaryButton}
              onClick={() => {
                setLinkBlocked(false);
                setLinkRetry((value) => value + 1);
                void sources.refetch();
                if (sourceAvailable) void catalog.refetch();
                if (sourceAvailable && linkedWorkspace && linkedExperiment && linkedId)
                  void linkedRead.refetch();
              }}
            >
              Retry opening draft
            </button>
          )}
          <button
            type="button"
            className={secondaryButton}
            onClick={() => clearDraftLocation(selectedWorkspaceId)}
          >
            Return to editor
          </button>
        </section>
      )}
      <section
        hidden={view !== "editor" || (linked && !linkedMatches)}
        ref={preparation}
        tabIndex={-1}
        aria-labelledby="experiment-preparation"
        className={`${detailCard} experiment-preparation space-y-3 scroll-mt-24`}
      >
        <div>
          <h3 id="experiment-preparation" className="sr-only">
            Prepare an experiment
          </h3>
          {preparing && (
            <details className="text-xs text-text-dim">
              <summary className="cursor-pointer">Current draft</summary>
              <p>
                Showing your current editable inputs. Returning from a run does not load its
                historical inputs.
              </p>
            </details>
          )}
        </div>
        <div className="experiment-selectors grid items-start gap-3 lg:grid-cols-3 border-b border-line pb-2">
          <SourceSelector
            catalog={sources}
            workspaceId={workspaceId}
            onSelect={(id) => {
              onHandoffImported?.();
              selectWorkspace(id);
            }}
          />
          {(entry || draft) && (
            <>
              <DraftPicker />
              <ExperimentPicker
                key={workspaceId}
                entries={catalog.data?.entries ?? []}
                selectedId={draft?.experiment ?? entry?.id ?? ""}
                disabled={!sourceAvailable || Boolean(handoff)}
                onSelect={(selected) => select(selected, false, workspaceId)}
              />
            </>
          )}
        </div>
        <AuthorRefresh
          compact
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
              className={secondaryButton}
              disabled={!sourceAvailable}
              onClick={() => {
                onHandoffImported?.();
                selectCurrentSource(workspaceId);
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
            · {draft.handoff.source_hash}. Starting will retain this exact decision source, together
            with the inputs you submit. You can edit the suggestion first.
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
            {entry &&
              sourceAvailable &&
              !handoff &&
              draft?.workspaceId === workspaceId &&
              draft.definition === definitionKey(entry) && (
                <LaunchForm
                  key={`${workspaceId}:${codeRevision?.content_hash ?? "current"}:${draft.definition}`}
                  entry={entry}
                  active={view === "editor" && (!linked || linkedMatches)}
                  onAdmitted={admitted}
                  catalogReady={sourceAvailable && catalog.isSuccess}
                />
              )}
          </>
        )}
      </section>
      <section
        hidden={view !== "editor" || (linked && !linkedMatches)}
        aria-labelledby="experiment-history"
        className="space-y-3"
      >
        <h3 id="experiment-history" className={sectionTitle}>
          Reuse and recovery
        </h3>
        <LineScans key={`lines:${projectId}`} />
        <PlanLibrary
          key={`plans:${projectId}`}
          initializing={catalog.isPending && draft === undefined}
        />
        <LaunchRecoveryPanel />
      </section>
      {view === "submitted" && (
        <SubmittedWork projectId={projectId} onOpenMonitor={openProcedure} />
      )}
      {view === "editor" && (!linked || linkedMatches) && projectId && (
        <CalibrationTasks
          key={`calibration:${projectId}:${workspaceId}`}
          projectId={projectId}
          workspaceId={workspaceId}
          onProcedure={admitted}
        />
      )}
    </section>
  );
}

function openProcedure(id: string, options?: NavigationOptions) {
  const url = new URL(window.location.href);
  url.searchParams.delete("preparation");
  url.searchParams.delete("workbench");
  url.searchParams.set("procedure", id);
  navigate(url, options);
}
function admitted(id: string) {
  // A selected monitor is a fixed object. A new submission is available through
  // its receipt, but must not silently replace the monitor already selected.
  const selected = new URL(window.location.href).searchParams.get("procedure");
  if (!selected || selected === id) openProcedure(id, { replace: true });
}

function showView(next: "editor" | "monitor" | "submitted") {
  const url = new URL(window.location.href);
  url.searchParams.delete("workbench");
  url.searchParams.delete("preparation");
  if (next === "editor") url.searchParams.set("preparation", "1");
  if (next === "submitted") url.searchParams.set("workbench", "submitted");
  navigate(url);
}
