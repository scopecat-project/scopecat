import { apiClient, apiData } from "../../api-client";
import type { ManualLineReconfirmation } from "../../api-contract";

export async function reconfirmManualLine(
  runId: string,
  fingerprint: string,
  confirmation: ManualLineReconfirmation,
) {
  await apiData(
    apiClient.POST("/api/v1/runs/{run_id}/attention", {
      params: { path: { run_id: runId } },
      body: {
        disposition: "continue",
        run_contract_fingerprint: fingerprint,
        manual_line_reconfirmation: confirmation,
      },
    }),
  );
  const current = await apiData(
    apiClient.GET("/api/v1/runs/{run_id}", {
      params: { path: { run_id: runId } },
    }),
  );
  if (current.control.attention_reason === "manual_line_confirmation_expired") {
    return "expired" as const;
  }
  if (current.control.state !== "queued") return "recorded" as const;
  const procedureId = confirmation.original.procedure_run_id;
  const view = await apiData(
    apiClient.GET("/api/v1/procedures/{procedure_run_id}/operator", {
      params: { path: { procedure_run_id: procedureId } },
    }),
  );
  const step = view.current_step;
  if (view.dispatch.worker_running && view.procedure.state === "leased") return "recorded" as const;
  if (
    view.current_child?.run.control.admission.run_id !== runId ||
    view.dispatch.worker_running ||
    view.procedure.cancellation ||
    !["attention_required", "ready"].includes(view.procedure.state) ||
    !step ||
    step.state !== "attention_required" ||
    step.operation !== "run" ||
    step.attention_reason !== `confirm current wiring before continuing run ${runId}`
  ) {
    throw new Error(
      "Wiring confirmation was recorded. The task changed or its worker is still settling; inspect it before continuing.",
    );
  }
  if (view.procedure.state === "attention_required")
    await apiData(
      apiClient.POST(
        "/api/v1/procedures/{procedure_run_id}/steps/{step_key}/attempts/{attempt}/retry",
        {
          params: {
            path: { procedure_run_id: procedureId, step_key: step.step_key, attempt: step.attempt },
          },
          body: {
            procedure_run_id: procedureId,
            step_key: step.step_key,
            attempt: step.attempt,
            expected_run_revision: view.procedure.revision,
            expected_step_revision: step.revision,
          },
        },
      ),
    );
  await apiData(
    apiClient.POST("/api/v1/procedures/{procedure_run_id}/dispatch", {
      params: { path: { procedure_run_id: procedureId } },
    }),
  );
  return "recorded" as const;
}
