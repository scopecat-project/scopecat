import { ApiError } from "../../api-client";
import type { components } from "../../api-schema";

export type SubmissionRequest = components["schemas"]["LaunchRequest-Input"];
export interface SubmissionAttempt {
  sequence?: number;
  request: SubmissionRequest;
  definition: string;
  status: "pending" | "unknown" | "rejected" | "confirmed";
  error: string;
  checking?: boolean;
  procedureId?: string;
}
export const isKnownRejection = (error: unknown) =>
  error instanceof ApiError &&
  error.status !== undefined &&
  error.status >= 400 &&
  error.status < 500;
