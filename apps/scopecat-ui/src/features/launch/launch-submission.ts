import type { ScientificBinding } from "./scientific-selection";
import { apiClient, apiData, ApiError } from "../../api-client";
import type { components } from "../../api-schema";

export type SubmissionRequest = components["schemas"]["LaunchRequest-Input"];
export interface SubmissionAttempt {
  request: SubmissionRequest;
  definition: string;
  status: "pending" | "unknown" | "rejected" | "confirmed";
  error: string;
  checking?: boolean;
  procedureId?: string;
}
export function matchesSubmissionIntent(
  intent: Record<string, unknown>,
  request: SubmissionRequest,
  scientificBinding: ScientificBinding | null | undefined,
): boolean {
  return (
    typeof intent.request_hash === "string" &&
    intent.request_hash === request.expected_request_hash &&
    request.reviewed != null &&
    canonical(intent.config_source) === canonical(request.reviewed.config_source) &&
    canonical(scientificBinding) === canonical(request.reviewed.binding)
  );
}
export const isKnownRejection = (error: unknown) =>
  error instanceof ApiError &&
  error.status !== undefined &&
  error.status >= 400 &&
  error.status < 500;
export async function findSubmittedProcedure(request: SubmissionRequest): Promise<string> {
  const page = await apiData(
    apiClient.GET("/api/v1/procedures", {
      params: { query: { request_key: request.request_key, limit: 2 } },
    }),
  );
  if (!page.items.length)
    throw new Error(
      "No retained procedure found yet. The original submission remains unconfirmed.",
    );
  if (page.items.length !== 1 || page.next_cursor != null)
    throw new Error(
      "Multiple procedures share this request key. Their identity is unconfirmed; inspect retained procedures.",
    );
  const procedure = page.items[0]!;
  if (!matchesSubmissionIntent(procedure.intent, request, procedure.scientific_binding))
    throw new Error(
      "The retained procedure does not confirm the original launch hash and configuration binding. The submission remains unconfirmed.",
    );
  return procedure.procedure_run_id;
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object" && value !== null)
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  return JSON.stringify(value) ?? "null";
}
