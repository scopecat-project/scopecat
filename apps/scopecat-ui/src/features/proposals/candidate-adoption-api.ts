import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
export type Branch = components["schemas"]["ParameterBranch"];
export type Publication = components["schemas"]["ParameterBranchPublishCommand"];
export const branches = (after?: string) =>
  apiData(
    apiClient.GET("/api/v1/parameters/branches", { params: { query: { after, limit: 100 } } }),
  );
export const history = (name: string) =>
  apiData(
    apiClient.GET("/api/v1/parameters/branch-history/{name}", { params: { path: { name } } }),
  );
export const revision = (revision_id: string) =>
  apiData(
    apiClient.GET("/api/v1/parameters/revisions/{revision_id}", {
      params: { path: { revision_id } },
    }),
  );
export const sourceRun = (run_id: string) =>
  apiData(apiClient.GET("/api/v1/runs/{run_id}", { params: { path: { run_id } } }));
export const workingTable = (name: string) =>
  apiData(
    apiClient.GET("/api/v1/parameter-drafts/working-branch/{name}", { params: { path: { name } } }),
  );
export const verifications = (run_id: string, proposal_id: string, before?: number) =>
  apiData(
    apiClient.GET("/api/v1/runs/{run_id}/parameter-proposals/{proposal_id}/verifications", {
      params: { path: { run_id, proposal_id }, query: { before, limit: 20 } },
    }),
  );
export const publish = (body: Publication) =>
  apiData(apiClient.POST("/api/v1/parameters/branch-publications", { body }));
