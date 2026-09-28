import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
export type ParameterRevision = components["schemas"]["ParameterRevision"];
export function getParameterRevisions(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/parameters/revisions", { signal }));
}
export function saveParameterRevision(body: components["schemas"]["ParameterSaveCommand"]) {
  return apiData(apiClient.POST("/api/v1/parameters/revisions", { body }));
}
export function commitParameterBranch(body: components["schemas"]["ParameterBranchCommitCommand"]) {
  return apiData(apiClient.POST("/api/v1/parameters/branch-commits", { body }));
}
