import { withDraftContext } from "./draft-location";
import { useOperatorInput } from "../application/use-operator-input";
import * as Controls from "../../ui/Controls";
import { useRecordCollectionNames } from "../context/scope-catalog";
import { ReturnToPreparation } from "./ReturnToPreparation";
import { secondaryButton } from "../../ui/styles";
import { navigateLink } from "../../lib/navigation";
import { PlanOrigin } from "./PlanOrigin";
import { ClearData } from "../history/DataCleanup";
import { ProcedureWorkerLog } from "./ProcedureWorkerLog";
import { ComparisonLink } from "../analyses/ComparisonLink";
import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { ProcedureChildRun } from "./ProcedureChildRun";
import { outputHref, procedurePhase, stateLabel } from "./procedure-operator";

export function ProcedureProgress({
  procedureId,
  projectId,
}: {
  procedureId: string;
  projectId?: string;
}) {
  const { actor: cancelActor, setActor: setCancelActor } = useOperatorInput();
  const [cancelReason, setCancelReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const progress = useInfiniteQuery({
    queryKey: ["procedure-operator", procedureId],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/procedures/{procedure_run_id}/operator", {
          params: { path: { procedure_run_id: procedureId }, query: { cursor: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.steps.next_cursor ?? undefined,
    refetchInterval: 1000,
  });
  const view = progress.data?.pages[0];
  const run = view?.procedure;
  const workspaceId = view?.workspace_id;
  const steps = progress.data?.pages.flatMap((page) => page.steps.items) ?? [];
  const children = new Map(
    progress.data?.pages.flatMap((page) =>
      page.child_runs.map((child) => [child.step_key, child] as const),
    ),
  );
  const collectionNames = useRecordCollectionNames(
    projectId,
    Boolean(
      projectId &&
      (view?.current_child?.run.address ||
        [...children.values()].some((child) => child.run.address)),
    ),
  );
  async function cancel() {
    if (!run) return;
    setError("");
    setPending(true);
    try {
      await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/cancel", {
          params: { path: { procedure_run_id: procedureId } },
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
          body: {
            procedure_run_id: procedureId,
            expected_run_revision: run.revision,
            actor: cancelActor,
            reason: cancelReason,
          },
        }),
      );
      await progress.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  async function dispatch() {
    setError("");
    setPending(true);
    try {
      const receipt = await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/dispatch", {
          params: { path: { procedure_run_id: procedureId } },
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
        }),
      );
      if (receipt.dispatch_error) setError(receipt.dispatch_error);
      await progress.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  const queued = Boolean(
    run &&
    !run.closure &&
    (run.resource_wait || (run.state === "ready" && !view?.dispatch.worker_running)),
  );
  return (
    <section className="border border-accent/40 bg-panel-soft rounded-md p-4 space-y-3 break-words">
      <h3 className="font-semibold">Execution progress</h3>
      <p className="text-xs text-text-dim">
        Monitoring this submitted task. Editing another draft does not change its inputs. Closing
        this view leaves background work running.
      </p>
      <ReturnToPreparation />
      <PlanOrigin reference={run?.plan_ref} />
      <p>{run?.definition.id ?? procedureId}</p>
      <a
        onClick={navigateLink}
        className="underline"
        href={withDraftContext(`?procedure=${encodeURIComponent(procedureId)}#launch`)}
      >
        Reopen this procedure
      </a>
      {progress.isPending && <p role="status">Loading retained procedure state…</p>}
      {progress.error && (
        <p role="alert">
          Live task state unavailable; displayed state may be stale. {progress.error.message}
        </p>
      )}
      {view && run && (
        <>
          <p role="status" className="font-semibold">
            {procedurePhase(view)}
          </p>
          {!run.closure && run.state === "waiting_for_input" && (
            <p>Record your decision, then return here to continue.</p>
          )}
          {view.dispatch.failure && (
            <div role="alert" className="space-y-1">
              <p>Worker diagnostic: {view.dispatch.failure.message}</p>
            </div>
          )}
          {run.recovery && (
            <p>
              Recovery from{" "}
              <a
                onClick={navigateLink}
                className="underline"
                href={withDraftContext(
                  `?procedure=${encodeURIComponent(run.recovery.procedure_run_id)}#launch`,
                )}
              >
                failed procedure {run.recovery.procedure_run_id}
              </a>{" "}
              using{" "}
              <a
                onClick={navigateLink}
                className="underline"
                href={withDraftContext(
                  `?run=${encodeURIComponent(run.recovery.retained_run.run_id)}#runs`,
                )}
              >
                retained run {run.recovery.retained_run.run_id}
              </a>
              . The original failure history is unchanged.
            </p>
          )}
          {(run.attention_reason || run.closure?.reason) && (
            <p>{run.attention_reason ?? run.closure?.reason}</p>
          )}
          {run.state === "attention_required" && (
            <p>
              Inspect retained evidence and reconcile external state with the project workflow.
              Unknown hardware effects cannot be retried here.
            </p>
          )}
          {run.cancellation && !run.closure && (
            <p>
              {run.cancellation.actor} requested cancellation: {run.cancellation.reason}. This is
              not a completed stop. An already-started configuration acceptance step may still
              complete.
            </p>
          )}
          {run.resource_wait && (
            <a
              onClick={navigateLink}
              className="underline"
              href={withDraftContext(
                `?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(run.resource_wait.run_id)}#runs`,
              )}
            >
              Inspect waiting child run: {run.resource_wait.run_id}
            </a>
          )}
          {run.resource_wait && !run.closure && (
            <p>
              Cancelling this procedure stops only its waiting child. The resource owner keeps
              running.
            </p>
          )}
          <ComparisonLink />
          {run.state === "waiting_for_input" && (
            <a
              onClick={navigateLink}
              className="underline"
              href={withDraftContext(`?procedure=${encodeURIComponent(procedureId)}#decisions`)}
            >
              Review results in Decisions
            </a>
          )}
          {view.dispatch_blocked_reason === null && !progress.isError && (
            <button
              className={secondaryButton}
              type="button"
              disabled={pending}
              onClick={() => {
                void dispatch();
              }}
            >
              Continue task
            </button>
          )}
          {view.dispatch_blocked_reason && run.state === "ready" && (
            <p>{view.dispatch_blocked_reason}</p>
          )}
          {!run.cancellation && ["ready", "waiting_for_input", "leased"].includes(run.state) && (
            <details>
              <summary>{queued ? "Cancel queued task" : "Cancel remaining procedure"}</summary>
              <p>
                Retains completed results. An already-started step, including configuration
                acceptance, settles before cancellation completes. This is not an emergency stop.
              </p>
              <Controls.Field>
                Cancellation actor
                <Controls.Input
                  value={cancelActor}
                  onChange={(event) => setCancelActor(event.target.value)}
                />
              </Controls.Field>
              <Controls.Field>
                Cancellation reason
                <Controls.Input
                  value={cancelReason}
                  onChange={(event) => setCancelReason(event.target.value)}
                />
              </Controls.Field>
              <button
                className={secondaryButton}
                type="button"
                disabled={
                  pending || progress.isError || !cancelActor.trim() || !cancelReason.trim()
                }
                onClick={() => {
                  void cancel();
                }}
              >
                {queued
                  ? "Cancel queued task"
                  : run.state === "leased"
                    ? "Stop after current step"
                    : "Cancel procedure"}
              </button>
            </details>
          )}
          {run.closure?.actor && <p>Closed by {run.closure.actor}</p>}
          {view.current_step && (
            <p>
              Current step: {view.current_step.step_key} · {stateLabel(view.current_step.state)}
            </p>
          )}
          {view.current_child && (
            <ProcedureChildRun
              child={view.current_child}
              collectionName={
                view.current_child.run.address
                  ? collectionNames.get(view.current_child.run.address.collection_id)
                  : undefined
              }
              procedureId={procedureId}
              current
            />
          )}
          <ul className="space-y-3">
            {steps.map((step) => {
              if (
                !run.closure &&
                !run.resource_wait &&
                step.step_key === view.current_step?.step_key &&
                step.attempt === view.current_step.attempt &&
                !step.output &&
                !step.failure_reason &&
                !step.attention_reason
              )
                return null;
              const child = children.get(step.step_key);
              const href = step.output && outputHref(step.output, procedureId);
              return (
                <li key={`${step.step_key}:${step.attempt}`} className="border-t pt-2 space-y-1">
                  <p>
                    {step.step_key}:{" "}
                    {run.resource_wait?.step_key === step.step_key
                      ? run.closure?.status === "cancelled"
                        ? "Cancelled before acquisition"
                        : "Waiting for resources"
                      : run.closure?.status === "cancelled" && step.state === "waiting_for_input"
                        ? "Review cancelled"
                        : stateLabel(step.state)}
                  </p>
                  {(step.failure_reason || step.attention_reason) && (
                    <p>{step.failure_reason ?? step.attention_reason}</p>
                  )}
                  {child && child.step_key !== view.current_child?.step_key && (
                    <ProcedureChildRun
                      child={child}
                      collectionName={
                        child.run.address
                          ? collectionNames.get(child.run.address.collection_id)
                          : undefined
                      }
                      procedureId={procedureId}
                    />
                  )}
                  {step.output?.kind === "interpretation" && (
                    <details>
                      <summary>Recorded review</summary>
                      <pre className="overflow-auto text-xs">
                        {JSON.stringify(step.output.response, null, 2)}
                      </pre>
                    </details>
                  )}
                  {href && (!child || step.output?.kind !== "run") && (
                    <a onClick={navigateLink} className="underline" href={withDraftContext(href)}>
                      Open{" "}
                      {step.output?.kind === "analysis"
                        ? "analysis"
                        : step.output?.kind === "run"
                          ? "run"
                          : "retained result"}
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
          {progress.hasNextPage && (
            <button
              className={secondaryButton}
              type="button"
              disabled={progress.isFetchingNextPage}
              onClick={() => {
                void progress.fetchNextPage();
              }}
            >
              Load earlier steps
            </button>
          )}
          <details className="border border-line rounded-md p-3">
            <summary className="cursor-pointer text-sm font-semibold">
              Execution diagnostics
            </summary>
            <p>
              {view.dispatch.worker_running
                ? "Worker process active"
                : `Worker management: ${view.dispatch.management}`}
            </p>
            {view.dispatch.failure && <p>Observed at {view.dispatch.failure.observed_at}</p>}
            {view.dispatch.log_path && (
              <p className="break-all">
                Worker log: <code>{view.dispatch.log_path}</code>
              </p>
            )}
            <ProcedureWorkerLog procedureId={procedureId} />
            {run.state === "closed" && <ClearData key={procedureId} procedures={[procedureId]} />}
          </details>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
