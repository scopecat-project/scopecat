import type {
  ProcedureRunPage,
  ProcedureInputContinueReceipt,
  ProcedureStepAttemptPage,
  ProcedureStepInputSubmitCommand,
} from "../../api-contract";
import { apiClient, apiData } from "../../api-client";

export async function getWaitingProcedures(signal?: AbortSignal): Promise<ProcedureRunPage> {
  return apiData(
    apiClient.GET("/api/v1/procedures", {
      params: { query: { limit: 50, state: "waiting_for_input" } },
      signal,
    }),
  );
}

export async function getProcedureSteps(
  procedureRunId: string,
  signal?: AbortSignal,
): Promise<ProcedureStepAttemptPage> {
  return apiData(
    apiClient.GET("/api/v1/procedures/{procedure_run_id}/steps", {
      params: {
        path: { procedure_run_id: procedureRunId },
        query: { limit: 200 },
      },
      signal,
    }),
  );
}

export async function submitProcedureInput(
  command: ProcedureStepInputSubmitCommand,
): Promise<ProcedureInputContinueReceipt> {
  // Course ownership can exist without an authored ProcedureSource. The
  // retained operator view is authoritative and survives continuation retries.
  const operator = await getDecisionProgress(command.procedure_run_id);
  const ownerWorkspace = operator.workspace_id;
  return apiData(
    apiClient.POST(
      "/api/v1/procedures/{procedure_run_id}/steps/{step_key}/attempts/{attempt}/input/continue",
      {
        params: {
          path: {
            procedure_run_id: command.procedure_run_id,
            step_key: command.step_key,
            attempt: command.attempt,
          },
        },
        body: command,
        headers: ownerWorkspace ? { "X-Scopecat-Workspace": ownerWorkspace } : undefined,
      },
    ),
  );
}

export type DecisionDraftInput =
  import("../../api-schema").components["schemas"]["DecisionDraftInput"];
export type DecisionDraftTarget =
  import("../../api-schema").components["schemas"]["DecisionDraftTarget"];
export type DecisionDraftBaseline =
  import("../../api-schema").components["schemas"]["DecisionDraftBaseline"];
export type DecisionDraftView =
  import("../../api-schema").components["schemas"]["DecisionDraftView"];
export type DecisionDraftSave =
  import("../../api-schema").components["schemas"]["DecisionDraftSave"];

export function readDecisionDraft(target: DecisionDraftTarget) {
  return apiData(apiClient.POST("/api/v1/decision-drafts/read", { body: target }));
}
export function saveDecisionDraft(command: DecisionDraftSave) {
  return apiData(apiClient.POST("/api/v1/decision-drafts/save", { body: command }));
}
export function getDecisionDraftHistory(before?: number) {
  return apiData(
    apiClient.GET("/api/v1/decision-drafts", { params: { query: { before, limit: 25 } } }),
  );
}

export type { ProcedureInputContinueReceipt } from "../../api-contract";

export async function getDecisionProgress(procedureRunId: string, signal?: AbortSignal) {
  return apiData(
    apiClient.GET("/api/v1/procedures/{procedure_run_id}/operator", {
      params: { path: { procedure_run_id: procedureRunId } },
      signal,
    }),
  );
}
