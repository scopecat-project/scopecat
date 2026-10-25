import type { components } from "../../api-schema";

import type { ProcedureOperatorView } from "../../api-contract";
export type { ProcedureOperatorView } from "../../api-contract";
export type ProcedureOutput = components["schemas"]["ProcedureStepAttempt"]["output"];

export function procedurePhase(view: ProcedureOperatorView): string {
  const run = view.procedure;
  if (run.closure) return stateLabel(run.closure.status);
  if (run.cancellation) return "已请求取消，正在完成当前步骤";
  if (
    run.state === "attention_required" ||
    view.current_child?.run.control.state === "attention_required" ||
    (view.current_child?.run.snapshot.outcome &&
      view.current_child.run.snapshot.outcome.certainty !== "known")
  )
    return "需要处理";
  if (run.resource_wait) return "等待资源";
  if (run.state === "waiting_for_input") return "等待人工判断";
  if (run.state === "leased") return "执行中";
  if (view.dispatch.management === "paused") return "调度已暂停";
  return view.dispatch.management === "active" ? "等待 Worker" : "已接纳，尚未调度";
}

export function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    ready: "就绪",
    accepted: "已接纳",
    completed: "已完成",
    interrupted: "已中断",
    leased: "执行中",
    waiting_for_input: "等待人工判断",
    attention_required: "需要处理",
    succeeded: "已完成",
    failed: "失败",
    closed: "已结束",
    cancelled: "已取消",
    running: "执行中",
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
