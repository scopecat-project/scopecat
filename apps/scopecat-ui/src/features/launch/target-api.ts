import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

export type TargetRevisionRef = components["schemas"]["TargetRevisionRef"];
export type TargetRevision = Awaited<ReturnType<typeof resolveTarget>>;

export function getTargetHeads(before?: number, signal?: AbortSignal) {
  return apiData(
    apiClient.GET("/api/v1/measurement-targets", {
      params: { query: { limit: 100, before } },
      signal,
    }),
  );
}
export function resolveTarget(ref: TargetRevisionRef, signal?: AbortSignal) {
  return apiData(apiClient.POST("/api/v1/measurement-targets/resolve", { body: ref, signal }));
}
export function targetRefKey(ref: TargetRevisionRef): string {
  return JSON.stringify([ref.catalog_id, ref.target_id, ref.revision, ref.content_hash]);
}
