import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

export type ObjectRef = components["schemas"]["ApparatusObjectRef"];
export type ObjectRevision = components["schemas"]["ApparatusObjectRevision"];
export type Observation = components["schemas"]["ApparatusObservation"];
export const pageSize = 20;

export function findObjects(query: string, before?: number, signal?: AbortSignal) {
  return apiData(
    apiClient.GET("/api/v1/apparatus-objects", {
      params: { query: { query, limit: pageSize, before } },
      signal,
    }),
  );
}

// POST resolves an immutable description; it does not create or revise records.
export function resolveObject(ref: ObjectRef, signal?: AbortSignal) {
  return apiData(apiClient.POST("/api/v1/apparatus-objects/resolve", { body: ref, signal }));
}

export function objectHistory(objectId: string, before?: number, signal?: AbortSignal) {
  return apiData(
    apiClient.GET("/api/v1/apparatus-objects/{object_id}/observations", {
      params: { path: { object_id: objectId }, query: { limit: pageSize, before } },
      signal,
    }),
  );
}

export function getObservation(observationId: string, signal?: AbortSignal) {
  return apiData(
    apiClient.GET("/api/v1/apparatus-observations/{observation_id}", {
      params: { path: { observation_id: observationId } },
      signal,
    }),
  );
}

export function attachmentUrl(observationId: string, hash: string) {
  return `/api/v1/apparatus-observations/${encodeURIComponent(observationId)}/attachments/${encodeURIComponent(hash)}`;
}
