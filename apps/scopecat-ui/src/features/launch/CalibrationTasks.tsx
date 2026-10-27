import { calibrationStatusLabel } from "./calibration-labels";
import { useOperatorInput } from "../application/use-operator-input";
import * as Controls from "../../ui/Controls";
import { useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import type { MethodResponse } from "openapi-fetch";
import { CalibrationEvidence } from "./CalibrationEvidence";
import { contextParameterLabel } from "./context-parameters";

type TaskControl = components["schemas"]["CalibrationTaskControl"];
type TaskView = MethodResponse<typeof apiClient, "get", "/api/v1/calibration-tasks/{task_id}">;

export function CalibrationTasks({
  projectId,
  workspaceId,
  onProcedure,
}: {
  projectId: string;
  workspaceId?: string;
  onProcedure: (id: string) => void;
}) {
  const [selected, setSelected] = useState(
    () => new URLSearchParams(window.location.search).get("task") ?? "",
  );
  const [expanded, setExpanded] = useState(Boolean(selected));
  const history = useInfiniteQuery({
    queryKey: ["calibration-tasks", projectId, workspaceId],
    enabled: expanded,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/calibration-tasks", {
          params: { query: { limit: 20, cursor: pageParam } },
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: 3000,
  });
  function select(id: string) {
    setSelected(id);
    const url = new URL(window.location.href);
    url.searchParams.set("task", id);
    window.history.replaceState(window.history.state, "", url);
  }
  return (
    <details
      open={expanded}
      className="border border-line rounded p-4 space-y-3"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary className="font-semibold">校准任务</summary>
      {expanded && (
        <>
          <p>查看已保留的检查计划，并控制其后台推进。</p>
          {history.isPending && <p role="status">正在加载校准任务…</p>}
          {history.error && <p role="alert">{history.error.message}</p>}
          {history.data?.pages[0]?.items.length === 0 && (
            <p>尚无校准任务。请在作者代码中用 lab.calibration_tasks.create() 创建计划。</p>
          )}
          <ul className="space-y-1">
            {history.data?.pages
              .flatMap((page) => page.items)
              .map((task) => (
                <li key={task.specification.task_id}>
                  <button
                    type="button"
                    className="underline text-left"
                    aria-current={selected === task.specification.task_id ? "true" : undefined}
                    onClick={() => select(task.specification.task_id)}
                  >
                    {task.specification.task_id} · {calibrationStatusLabel(task.mode)} ·{" "}
                    {task.created_at}
                  </button>
                </li>
              ))}
          </ul>
          {history.hasNextPage && (
            <Controls.Button
              type="button"
              disabled={history.isFetchingNextPage}
              onClick={() => {
                void history.fetchNextPage();
              }}
            >
              加载更早的任务
            </Controls.Button>
          )}
          {selected && (
            <TaskDetail
              key={`${projectId}:${workspaceId}:${selected}`}
              workspaceId={workspaceId}
              taskId={selected}
              projectId={projectId}
              onProcedure={onProcedure}
            />
          )}
        </>
      )}
    </details>
  );
}

function TaskDetail({
  taskId,
  projectId,
  workspaceId,
  onProcedure,
}: {
  taskId: string;
  projectId: string;
  workspaceId?: string;
  onProcedure: (id: string) => void;
}) {
  const client = useQueryClient();
  const { actor, setActor } = useOperatorInput();
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const queryKey = ["calibration-task", projectId, workspaceId, taskId];
  const detail = useQuery({
    queryKey,
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/calibration-tasks/{task_id}", {
          params: { path: { task_id: taskId } },
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
          signal,
        }),
      ),
    refetchInterval: 1000,
  });
  async function control(action: TaskControl["action"]) {
    if (!detail.data) return;
    setPending(true);
    setError("");
    try {
      const result = await apiData(
        apiClient.POST("/api/v1/calibration-tasks/control", {
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
          body: {
            task_id: taskId,
            expected_revision: detail.data.task.control_revision,
            action,
            actor: actor.trim(),
            reason: reason.trim(),
          },
        }),
      );
      client.setQueryData(queryKey, result);
      await client.invalidateQueries({ queryKey: ["calibration-tasks", projectId] });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      await detail.refetch();
    } finally {
      setPending(false);
    }
  }
  const view = detail.data;
  const terminal = view?.task.mode === "cancelled" || view?.task.mode === "finished";
  const disabled = pending || !actor.trim() || !reason.trim() || detail.isError;
  return (
    <section className="space-y-3" aria-label="校准任务详情">
      <h3 className="font-semibold">{taskId}</h3>
      <a className="underline" href={`?task=${encodeURIComponent(taskId)}#launch`}>
        重新打开此任务
      </a>
      {detail.isPending && <p role="status">正在加载任务进度…</p>}
      {detail.error && <p role="alert">{detail.error.message}</p>}
      {error && <p role="alert">{error}</p>}
      {view && (
        <>
          <p>
            推进状态： {calibrationStatusLabel(view.task.mode)}.{" "}
            {view.progress.complete
              ? view.progress.successful
                ? "全部计划检查已通过。"
                : "计划已结束，仅保留部分结果。"
              : "计划尚未完成。"}
          </p>
          <p>暂停或取消会停止接纳新阶段。已接纳的测量可能继续；请打开对应执行，查看或取消。</p>
          {!terminal && (
            <fieldset disabled={pending} className="flex flex-wrap gap-3 items-end">
              <Controls.Field>
                任务操作者
                <Controls.Input
                  className="block"
                  value={actor}
                  onChange={(event) => setActor(event.target.value)}
                />
              </Controls.Field>
              <Controls.Field>
                任务控制原因
                <Controls.Input
                  className="block"
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </Controls.Field>
              <Controls.Button
                type="button"
                disabled={
                  disabled ||
                  (view.task.mode === "running" &&
                    Object.keys(view.task.dispatch_errors ?? {}).length === 0 &&
                    !view.task.finalization_error)
                }
                onClick={() => {
                  void control("start");
                }}
              >
                {view.task.mode === "manual" ? "开始任务" : "恢复 / 重试接纳"}
              </Controls.Button>
              <Controls.Button
                type="button"
                disabled={disabled || view.task.mode === "paused"}
                onClick={() => {
                  void control("pause");
                }}
              >
                暂停任务
              </Controls.Button>
              <Controls.Button
                type="button"
                disabled={disabled}
                onClick={() => {
                  void control("cancel");
                }}
              >
                取消剩余阶段
              </Controls.Button>
            </fieldset>
          )}
          {view.task.last_control && (
            <p>
              上次控制： {calibrationStatusLabel(view.task.last_control.action)}，操作者{" "}
              {view.task.last_control.actor} — {view.task.last_control.reason}
            </p>
          )}
          {view.task.specification.repair_budget && (
            <p>
              修复预算：{" "}
              {
                Object.values(view.task.attempts ?? {})
                  .flat()
                  .filter((a) => a.phase === "repair").length
              }
              {" / "}
              {view.task.specification.repair_budget.max_repairs}.
              每个阶段最多修复一次；候选参数须通过原检查，才会执行后续阶段。
            </p>
          )}
          {view.admission_deadline && (
            <p>
              新工作接纳截止时间： {new Date(view.admission_deadline).toLocaleString()}.
              已接纳的工作可以在截止时间之后完成。
            </p>
          )}
          {view.task.stop_reason && (
            <p role="status">
              已停止接纳： {calibrationStatusLabel(view.task.stop_reason)}.
              已保留的工作不会作为成功子集发布。
            </p>
          )}
          {view.task.specification.finalization && (
            <section aria-label="任务最终处理" className="border border-line rounded p-3 space-y-1">
              <h4 className="font-semibold">最终验证与发布</h4>
              <p>
                {view.finalization
                  ? `最终流程： ${calibrationStatusLabel(view.finalization.closure?.status ?? view.finalization.state)}.`
                  : view.progress.complete && !view.progress.successful
                    ? "未接纳：计划检查未全部通过。"
                    : "等待全部计划检查通过，并等待任务推进。"}
              </p>
              <p>检查通过本身并不验证或发布合并后的参数。</p>
              {view.task.finalization_error && (
                <p role="alert">最终处理已停止接纳： {view.task.finalization_error}</p>
              )}
              {view.finalization && (
                <button
                  type="button"
                  className="underline"
                  onClick={() => onProcedure(view.finalization!.procedure_run_id)}
                >
                  打开最终验证与发布
                </button>
              )}
            </section>
          )}
          <ol className="space-y-3">
            {view.progress.stages.map((stage) => {
              const specification = view.task.specification.plan.stages.find(
                (item) => item.id === stage.id,
              )!;
              const attempts = view.task.attempts?.[stage.id] ?? [];
              const resolved = attempts.at(-1)?.check;
              const pendingBinding = Boolean(specification.candidate_from && !resolved);
              const planned = { ...specification, check: resolved ?? specification.check };
              const admissionError = view.task.dispatch_errors?.[stage.id];
              return (
                <li key={stage.id} className="border border-line rounded p-3 space-y-1">
                  <h4 className="font-semibold">
                    {stage.id} · {calibrationStatusLabel(stage.state)}
                  </h4>
                  {attempts.length > 0 && (
                    <p>当前阶段： {calibrationStatusLabel(attempts.at(-1)!.phase)}</p>
                  )}
                  {attempts.length > 1 && (
                    <details>
                      <summary>已保留的检查与修复历史</summary>
                      {attempts.map((attempt) => (
                        <button
                          type="button"
                          className="underline block"
                          key={attempt.procedure_run_id}
                          onClick={() => onProcedure(attempt.procedure_run_id)}
                        >
                          打开{calibrationStatusLabel(attempt.phase)}：{stage.id}
                        </button>
                      ))}
                    </details>
                  )}
                  <p>
                    {planned.check.scope.capability} · {planned.check.scope.targets.join(", ")} ·{" "}
                    {planned.check.scope.conditions}
                  </p>
                  {!!stage.blocked_by?.length && <p>前置阶段： {stage.blocked_by.join(", ")}</p>}
                  {admissionError && (
                    <p role="alert">
                      已停止接纳： {admissionError}. 纠正原因后恢复任务，以重试接纳。
                    </p>
                  )}
                  {stage.state === "queued" && (
                    <p>等待执行。打开对应执行可查看 Worker 与资源状态。</p>
                  )}
                  {(stage.state === "attention_required" ||
                    stage.state === "waiting_for_input") && (
                    <p>打开对应执行，查看已保留证据并处理所需操作。</p>
                  )}
                  {stage.procedure_run_id && (
                    <button
                      className="underline"
                      type="button"
                      onClick={() => onProcedure(stage.procedure_run_id!)}
                    >
                      打开执行： {stage.id}
                    </button>
                  )}
                  {pendingBinding ? (
                    <p>
                      等待候选 {specification.candidate_from!.proposal_id} 来自阶段{" "}
                      {specification.candidate_from!.stage_id}. 参数输入尚未绑定。
                    </p>
                  ) : (
                    <>
                      <details>
                        <summary>固定测量上下文</summary>
                        <dl className="text-sm space-y-1 break-all">
                          <dt>参数</dt>
                          <dd>{contextParameterLabel(planned.check.context.parameters)}</dd>
                          <dt>测量对象</dt>
                          <dd>{subjectName(planned.check.context.subject)}</dd>
                          <dt>执行场景</dt>
                          <dd>{planned.check.context.scenario?.label ?? "物理设备"}</dd>
                          <dt>配置指纹</dt>
                          <dd>{planned.check.context.setup_content_hash}</dd>
                          <dt>参数指纹</dt>
                          <dd>{planned.check.context.parameters.content_hash}</dd>
                        </dl>
                      </details>
                      <CalibrationEvidence stage={planned} onProcedure={onProcedure} />
                    </>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
}

function subjectName(
  subject: TaskView["task"]["specification"]["plan"]["stages"][number]["check"]["context"]["subject"],
): string {
  if (subject.kind === "unbound") return "未选择样品";
  if (subject.kind === "registered_target")
    return `${subject.ref.target_id} · ${subject.sample.display_name}`;
  return subject.samples.map((sample) => `${sample.role}: ${sample.display_name}`).join(", ");
}
