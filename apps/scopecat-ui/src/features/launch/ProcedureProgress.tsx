import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { ProcedureChildRun } from "./ProcedureChildRun";
import { outputHref, procedurePhase, stateLabel } from "./procedure-operator";

export function ProcedureProgress({ procedureId }: { procedureId: string }) {
  const [cancelActor, setCancelActor] = useState("");
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
  const steps = progress.data?.pages.flatMap((page) => page.steps.items) ?? [];
  const children = new Map(
    progress.data?.pages.flatMap((page) =>
      page.child_runs.map((child) => [child.step_key, child] as const),
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
  return (
    <section className="border rounded p-4 space-y-3">
      <h3 className="font-semibold">Procedure progress</h3>
      <p>{run?.definition.id ?? procedureId}</p>
      <a className="underline" href={`?procedure=${encodeURIComponent(procedureId)}#launch`}>
        Reopen this procedure
      </a>
      {progress.isPending && <p role="status">Loading retained procedure state…</p>}
      {progress.error && <p role="alert">{progress.error.message}</p>}
      {view && run && (
        <>
          <p role="status" className="font-semibold">
            {procedurePhase(view)}
          </p>
          <p>
            {view.dispatch.worker_running
              ? "Worker process active"
              : view.dispatch.management === "paused"
                ? "Worker dispatch is paused after a start or process failure. The admitted procedure and its results are retained."
                : view.dispatch.management === "active"
                  ? "Managed by the console; ready work can continue when a worker is available."
                  : "No console worker is assigned to this admitted procedure."}
          </p>
          {run.recovery && (
            <p>
              Recovery from{" "}
              <a
                className="underline"
                href={`?procedure=${encodeURIComponent(run.recovery.procedure_run_id)}#launch`}
              >
                failed procedure {run.recovery.procedure_run_id}
              </a>{" "}
              using{" "}
              <a
                className="underline"
                href={`?run=${encodeURIComponent(run.recovery.retained_run.run_id)}#runs`}
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
              className="underline"
              href={`?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(run.resource_wait.run_id)}#runs`}
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
          {run.state === "waiting_for_input" && (
            <a
              className="underline"
              href={`?procedure=${encodeURIComponent(procedureId)}#decisions`}
            >
              Review results in Decisions
            </a>
          )}
          {view.dispatch_blocked_reason === null && !progress.isError && (
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                void dispatch();
              }}
              className="border rounded px-3 py-1"
            >
              Dispatch existing procedure
            </button>
          )}
          {view.dispatch_blocked_reason && run.state === "ready" && (
            <p>{view.dispatch_blocked_reason}</p>
          )}
          {!run.cancellation && ["ready", "waiting_for_input", "leased"].includes(run.state) && (
            <details>
              <summary>Cancel remaining procedure</summary>
              <p>
                Retains completed results. An already-started step, including configuration
                acceptance, settles before cancellation completes. This is not an emergency stop.
              </p>
              <label>
                Cancellation actor
                <input
                  value={cancelActor}
                  onChange={(event) => setCancelActor(event.target.value)}
                />
              </label>
              <label>
                Cancellation reason
                <input
                  value={cancelReason}
                  onChange={(event) => setCancelReason(event.target.value)}
                />
              </label>
              <button
                type="button"
                disabled={pending || !cancelActor.trim() || !cancelReason.trim()}
                onClick={() => {
                  void cancel();
                }}
              >
                {run.state === "leased" ? "Stop after current step" : "Cancel procedure"}
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
            <ProcedureChildRun child={view.current_child} procedureId={procedureId} current />
          )}
          <ul className="space-y-3">
            {steps.map((step) => {
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
                    <ProcedureChildRun child={child} procedureId={procedureId} />
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
                    <a className="underline" href={href}>
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
              type="button"
              disabled={progress.isFetchingNextPage}
              onClick={() => {
                void progress.fetchNextPage();
              }}
            >
              Load earlier steps
            </button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
