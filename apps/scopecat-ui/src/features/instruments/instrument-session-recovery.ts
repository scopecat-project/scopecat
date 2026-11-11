import { apiClient, apiData } from "../../api-client";

/** Observe the original session without renewing its lease or issuing device I/O. */
export function getInstrumentSessionRecovery(sessionId: string) {
  return apiData(
    apiClient.GET("/api/v1/instrument-sessions/{session_id}/recovery", {
      params: { path: { session_id: sessionId } },
    }),
  );
}
