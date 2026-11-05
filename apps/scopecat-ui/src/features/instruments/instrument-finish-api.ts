import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
export function getInstrumentFinishPlan(sessionId: string) {
  return apiData(
    apiClient.GET("/api/v1/instrument-sessions/{session_id}/finish-plan", {
      params: { path: { session_id: sessionId } },
    }),
  );
}
export function finishInstrumentSession(
  sessionId: string,
  command: components["schemas"]["InstrumentFinishCommand"],
) {
  return apiData(
    apiClient.POST("/api/v1/instrument-sessions/{session_id}/finish", {
      params: { path: { session_id: sessionId } },
      body: command,
    }),
  );
}
