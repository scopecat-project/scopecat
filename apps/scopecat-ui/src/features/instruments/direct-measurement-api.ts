import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
export type MeasurementSave = components["schemas"]["DirectMeasurementSave"];
export type SavedMeasurement = components["schemas"]["DirectMeasurement"];
export function listDirectMeasurements(before?: number) {
  return apiData(apiClient.GET("/api/v1/direct-measurements", { params: { query: { before } } }));
}
export function saveDirectMeasurement(selection: MeasurementSave) {
  return apiData(apiClient.POST("/api/v1/direct-measurements", { body: selection }));
}
