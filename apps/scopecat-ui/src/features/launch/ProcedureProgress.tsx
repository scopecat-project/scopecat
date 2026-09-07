import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function ProcedureProgress({ procedureId }: { procedureId: string }) {
  const [cancelActor, setCancelActor] = useState("");
  const [cancelReason, setCancelReason] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState("");
  const status = useQuery({
    queryKey: ["launch-procedure", procedureId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/procedures/{procedure_run_id}", {
          params: { path: { procedure_run_id: procedureId } },
        }),
      ),
    refetchInterval: 1000,
  });
  const steps = useQuery({
    queryKey: ["launch-procedure-steps", procedureId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/procedures/{procedure_run_id}/steps", {
          params: { path: { procedure_run_id: procedureId }, query: { limit: 50 } },
        }),
      ),
    refetchInterval: 1000,
  });
  async function cancel() {
    if (!status.data) return;
    setError("");
    setCancelling(true);
    try {
      await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/cancel", {
          params: { path: { procedure_run_id: procedureId } },
          body: {
            procedure_run_id: procedureId,
            expected_run_revision: status.data.revision,
            actor: cancelActor,
            reason: cancelReason,
          },
        }),
      );
      await status.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setCancelling(false);
    }
  }
  async function resume() {
    setError("");
    try {
      const receipt = await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/dispatch", {
          params: { path: { procedure_run_id: procedureId } },
        }),
      );
      if (receipt.dispatch_error) setError(receipt.dispatch_error);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  return (
    <section className="border rounded p-4 space-y-2">
      <h3>Procedure progress</h3>
      <p>{procedureId}</p>
      {status.error && <p role="alert">{status.error.message}</p>}
      <p>
        {status.data?.state === "leased" && status.data.cancellation
          ? "Cancellation requested — finishing current step"
          : status.data?.state === "ready" && status.data.resource_wait
            ? "Waiting for resources"
            : status.data && statusLabel(status.data.closure?.status ?? status.data.state)}
      </p>
      {status.data?.resource_wait && (
        <a href={`?run=${encodeURIComponent(status.data.resource_wait.run_id)}#runs`}>
          Inspect waiting child run: {status.data.resource_wait.run_id}
        </a>
      )}
      {(status.data?.attention_reason || status.data?.closure?.reason) && (
        <p>{status.data.attention_reason ?? status.data.closure?.reason}</p>
      )}
      {status.data?.state === "waiting_for_input" && (
        <a href="#decisions">Review results in Decisions</a>
      )}
      {status.data && ["ready", "waiting_for_input"].includes(status.data.state) && (
        <button
          type="button"
          onClick={() => {
            void resume();
          }}
          className="border rounded px-3 py-1"
        >
          Resume execution
        </button>
      )}
      {status.data &&
        !status.data.cancellation &&
        ["ready", "waiting_for_input", "leased"].includes(status.data.state) && (
          <details>
            <summary>Cancel remaining procedure</summary>
            <p>
              Retains completed results. A running step completes and settles before the procedure
              stops.
            </p>
            <label>
              Cancellation actor
              <input value={cancelActor} onChange={(event) => setCancelActor(event.target.value)} />
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
              disabled={cancelling || !cancelActor.trim() || !cancelReason.trim()}
              onClick={() => {
                void cancel();
              }}
            >
              {status.data.state === "leased" ? "Stop after current step" : "Cancel procedure"}
            </button>
          </details>
        )}
      {status.data?.closure?.actor && <p>Closed by {status.data.closure.actor}</p>}
      {error && <p role="alert">{error}</p>}
      <ul>
        {steps.data?.items.map((step) => (
          <li key={`${step.step_key}:${step.attempt}`}>
            {step.step_key}:{" "}
            {status.data?.resource_wait?.step_key === step.step_key &&
            status.data.closure?.status === "cancelled"
              ? "Cancelled before acquisition"
              : status.data?.resource_wait?.step_key === step.step_key &&
                  status.data.state === "ready"
                ? "Waiting for resources"
                : status.data?.closure?.status === "cancelled" && step.state === "waiting_for_input"
                  ? "Review cancelled"
                  : statusLabel(step.state)}{" "}
            {step.failure_reason}
            {step.output?.kind === "run" && (
              <a
                className="ml-2 underline"
                href={`?run=${encodeURIComponent(step.output.run_id)}#runs`}
              >
                Open run
              </a>
            )}
            {step.output?.kind === "analysis" && (
              <a
                className="ml-2 underline"
                href={
                  step.output.subject.kind === "run"
                    ? `?run=${encodeURIComponent(step.output.subject.run_id)}#runs`
                    : step.output.subject.kind === "sample"
                      ? `?sample=${encodeURIComponent(step.output.subject.sample_id)}#samples`
                      : `?analysis=${encodeURIComponent(step.output.analysis_record_id)}#analyses`
                }
              >
                Open analysis
              </a>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function statusLabel(state: string): string {
  const labels: Record<string, string> = {
    ready: "Queued",
    leased: "Running",
    waiting_for_input: "Waiting for review",
    attention_required: "Needs attention",
    succeeded: "Completed",
    failed: "Failed",
    closed: "Finished",
    cancelled: "Cancelled",
    running: "Running",
  };
  return labels[state] ?? state.replaceAll("_", " ");
}
