import type { components } from "../../api-schema";

export type ProcedureOperatorView = components["schemas"]["ProcedureOperatorView"];
export type ProcedureOutput = components["schemas"]["ProcedureStepAttempt"]["output"];

export function procedurePhase(view: ProcedureOperatorView): string {
  const run = view.procedure;
  if (run.closure) return stateLabel(run.closure.status);
  if (run.cancellation) return "Cancellation requested — finishing current step";
  if (
    run.state === "attention_required" ||
    view.current_child?.run.control.state === "attention_required" ||
    (view.current_child?.run.snapshot.outcome &&
      view.current_child.run.snapshot.outcome.certainty !== "known")
  )
    return "Needs attention";
  if (run.resource_wait) return "Waiting for resources";
  if (run.state === "waiting_for_input") return "Waiting for review";
  if (run.state === "leased") return "Running";
  if (view.dispatch.management === "paused") return "Dispatch paused";
  return view.dispatch.management === "active"
    ? "Queued for a worker"
    : "Admitted — not dispatched";
}

export function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    ready: "Ready",
    leased: "Running",
    waiting_for_input: "Waiting for review",
    attention_required: "Needs attention",
    succeeded: "Completed",
    failed: "Failed",
    closed: "Finished",
    cancelled: "Cancelled",
    running: "Running",
  };
  return labels[state] ?? state.replaceAll("_", " ");
}

export function outputHref(
  output: NonNullable<ProcedureOutput>,
  procedureId: string,
): string | undefined {
  const query = new URLSearchParams({ procedure: procedureId });
  if (output.kind === "run") {
    query.set("run", output.run_id);
    return `?${query}#runs`;
  }
  if (output.kind === "analysis") {
    if (output.subject.kind === "run") {
      query.set("run-analysis", output.analysis_record_id);
      query.set("run", output.subject.run_id);
      return `?${query}#runs`;
    }
    if (output.subject.kind === "sample") {
      query.set("sample-analysis", output.analysis_record_id);
      query.set("sample", output.subject.sample_id);
      return `?${query}#samples`;
    }
    query.set("analysis", output.analysis_record_id);
    return `?${query}#analyses`;
  }
  return undefined;
}
