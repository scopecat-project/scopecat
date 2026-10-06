import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
export type ParameterDraftView = components["schemas"]["ParameterDraftView"];
export type ParameterDraftInput = components["schemas"]["ParameterDraftInput"];
export type ParameterDraftAtom = components["schemas"]["ParameterDraftAtom"];
export type ParameterDraftValue = components["schemas"]["ParameterDraftValue"];
export const startParameterDraft = (body: components["schemas"]["ParameterDraftStart"]) =>
  apiData(apiClient.POST("/api/v1/parameter-drafts/start", { body }));
export const readParameterDraft = (draft_id: string) =>
  apiData(apiClient.GET("/api/v1/parameter-drafts/{draft_id}", { params: { path: { draft_id } } }));
export const saveParameterDraft = (
  draft_id: string,
  body: components["schemas"]["ParameterDraftSave"],
) =>
  apiData(
    apiClient.POST("/api/v1/parameter-drafts/{draft_id}/save", {
      params: { path: { draft_id } },
      body,
    }),
  );
export const commitParameterDraft = (draft_id: string, expected_revision: number) =>
  apiData(
    apiClient.POST("/api/v1/parameter-drafts/{draft_id}/commit", {
      params: { path: { draft_id } },
      body: { expected_revision },
    }),
  );
export const parameterDraftHistory = (base_id: string, before?: number) =>
  apiData(
    apiClient.GET("/api/v1/parameter-drafts", {
      params: { query: { base_id, before, limit: 20 } },
    }),
  );
export type WorkingInput = { draft_id: string; revision: number };
export const freezeParameterDraft = (draft_id: string, expected_revision: number) =>
  apiData(
    apiClient.POST("/api/v1/parameter-drafts/{draft_id}/freeze", {
      params: { path: { draft_id } },
      body: { expected_revision },
    }),
  );
