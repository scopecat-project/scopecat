import { apiClient, apiData, ApiError } from "../../api-client";
import type { components } from "../../api-schema";

export type SubmissionRequest = components["schemas"]["LaunchRequest"];
export interface SubmissionAttempt {
  request: SubmissionRequest;
  definition: string;
  status: "pending" | "unknown" | "rejected" | "confirmed";
  error: string;
  checking?: boolean;
  procedureId?: string;
}
function configBinding(value: unknown): string | undefined {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return undefined;
  const source = value as Record<string, unknown>;
  if (source.kind === "parameter_context") {
    if (
      typeof source.content_hash !== "string" ||
      typeof source.lab_generation !== "number" ||
      typeof source.context !== "object" ||
      source.context === null ||
      typeof source.sample !== "object" ||
      source.sample === null ||
      !Array.isArray(source.overrides)
    )
      return undefined;
    return canonical({
      kind: source.kind,
      context: source.context,
      content_hash: source.content_hash,
      lab_generation: source.lab_generation,
      sample: source.sample,
      overrides: source.overrides,
    });
  }
  if (
    source.kind !== "config_registry" ||
    typeof source.selector !== "string" ||
    typeof source.entry_id !== "string" ||
    typeof source.config_ref !== "string" ||
    typeof source.content_hash !== "string" ||
    typeof source.registry_generation !== "number"
  )
    return undefined;
  return JSON.stringify([
    source.kind,
    source.selector,
    source.entry_id,
    source.config_ref,
    source.content_hash,
    source.registry_generation,
  ]);
}
export function matchesSubmissionIntent(
  intent: Record<string, unknown>,
  request: SubmissionRequest,
): boolean {
  const binding = configBinding(request.config_source);
  return (
    typeof intent.request_hash === "string" &&
    intent.request_hash === request.expected_request_hash &&
    binding !== undefined &&
    configBinding(intent.config_source) === binding
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
  if (!matchesSubmissionIntent(procedure.intent, request))
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
