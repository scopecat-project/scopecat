import type { ProjectRun } from "../../types";

export function RunCancellationNotice({
  run,
}: {
  run: Pick<ProjectRun, "status" | "cancellationRequestedAt">;
}) {
  if (
    !run.cancellationRequestedAt ||
    ["succeeded", "failed", "cancelled", "attention_required", "closed"].includes(run.status)
  )
    return null;
  return (
    <p role="status">
      Run cancellation requested — waiting for acquisition and cleanup to settle. Collected data
      remains available; cancellation is not complete yet.
    </p>
  );
}
