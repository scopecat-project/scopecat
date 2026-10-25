import { withDraftContext } from "./draft-location";
import { useOperatorInput } from "../application/use-operator-input";
import * as Controls from "../../ui/Controls";
import { useRecordCollectionNames } from "../context/scope-catalog";
import { ReturnToPreparation } from "./ReturnToPreparation";
import { secondaryButton } from "../../ui/styles";
import { navigateLink } from "../../lib/navigation";
import { PlanOrigin } from "./PlanOrigin";
import { ClearData } from "../history/DataCleanup";
import { ProcedureWorkerLog } from "./ProcedureWorkerLog";
import { ComparisonLink } from "../analyses/ComparisonLink";
import { useState } from "react";
import { useProcedureOperator } from "./use-procedure-operator";
import { apiClient, apiData } from "../../api-client";
import { ProcedureChildRun } from "./ProcedureChildRun";
import { outputHref, procedurePhase, stateLabel } from "./procedure-operator";

export function ProcedureProgress({
  procedureId,
  projectId,
}: {
  procedureId: string;
  projectId?: string;
}) {
  const { actor: cancelActor, setActor: setCancelActor } = useOperatorInput();
  const [cancelReason, setCancelReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const progress = useProcedureOperator(procedureId);
  const view = progress.data?.pages[0];
  const run = view?.procedure;
  const workspaceId = view?.workspace_id;
  const steps = progress.data?.pages.flatMap((page) => page.steps.items) ?? [];
  const children = new Map(
    progress.data?.pages.flatMap((page) =>
      page.child_runs.map((child) => [child.step_key, child] as const),
    ),
  );
  const collectionNames = useRecordCollectionNames(
    projectId,
    Boolean(
      projectId &&
      (view?.current_child?.run.address ||
        [...children.values()].some((child) => child.run.address)),
    ),
  );
  async function cancel() {
    if (!run) return;
    setError("");
    setPending(true);
    try {
      await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/cancel", {
          params: { path: { procedure_run_id: procedureId } },
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
          body: {
            procedure_run_id: procedureId,
            expected_run_revision: run.revision,
            actor: cancelActor,
            reason: cancelReason,
          },
        }),
      );
      await progress.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  async function dispatch() {
    setError("");
    setPending(true);
    try {
      const receipt = await apiData(
        apiClient.POST("/api/v1/procedures/{procedure_run_id}/dispatch", {
          params: { path: { procedure_run_id: procedureId } },
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
        }),
      );
      if (receipt.dispatch_error) setError(receipt.dispatch_error);
      await progress.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  const queued = Boolean(
    run &&
    !run.closure &&
    (run.resource_wait || (run.state === "ready" && !view?.dispatch.worker_running)),
  );
  return (
    <section className="border border-accent/40 bg-panel-soft rounded-md p-3 space-y-2 break-words">
      <h3 className="font-semibold">执行进度</h3>
      <p className="text-xs text-text-dim">关闭此视图不会停止后台执行。</p>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <p>{run?.definition.id ?? procedureId}</p>
        <ReturnToPreparation />
        <a
          onClick={navigateLink}
          className="underline"
          href={withDraftContext(`?procedure=${encodeURIComponent(procedureId)}#launch`)}
        >
          重新打开此任务
        </a>
      </div>
      <PlanOrigin reference={run?.plan_ref} />
      {progress.isPending && <p role="status">正在读取任务状态…</p>}
      {progress.error && (
        <p role="alert">无法更新任务状态；当前显示可能已过时。 {progress.error.message}</p>
      )}
      {view && run && (
        <>
          <p role="status" className="font-semibold">
            {procedurePhase(view)}
          </p>
          {!run.closure && run.state === "waiting_for_input" && (
            <p>记录人工判断后，返回此处继续。</p>
          )}
          {view.dispatch.failure && (
            <div role="alert" className="space-y-1">
              <p>Worker 诊断： {view.dispatch.failure.message}</p>
            </div>
          )}
          {run.recovery && (
            <p>
              恢复来源：{" "}
              <a
                onClick={navigateLink}
                className="underline"
                href={withDraftContext(
                  `?procedure=${encodeURIComponent(run.recovery.procedure_run_id)}#launch`,
                )}
              >
                失败任务 {run.recovery.procedure_run_id}
              </a>{" "}
              ，使用{" "}
              <a
                onClick={navigateLink}
                className="underline"
                href={withDraftContext(
                  `?run=${encodeURIComponent(run.recovery.retained_run.run_id)}#runs`,
                )}
              >
                保留 run {run.recovery.retained_run.run_id}
              </a>
              。原始失败历史保持不变。
            </p>
          )}
          {(run.attention_reason || run.closure?.reason) && (
            <p>{run.attention_reason ?? run.closure?.reason}</p>
          )}
          {run.state === "attention_required" && (
            <p>请检查保留证据，并按项目流程核实外部状态。此处不能重试硬件影响尚不明确的操作。</p>
          )}
          {run.cancellation && !run.closure && (
            <p>
              {run.cancellation.actor} 已请求取消：{run.cancellation.reason}。
              停止尚未完成；已开始的配置接纳步骤仍可能完成。
            </p>
          )}
          {run.resource_wait && (
            <a
              onClick={navigateLink}
              className="underline"
              href={withDraftContext(
                `?procedure=${encodeURIComponent(procedureId)}&run=${encodeURIComponent(run.resource_wait.run_id)}#runs`,
              )}
            >
              查看等待中的子 run： {run.resource_wait.run_id}
            </a>
          )}
          {run.resource_wait && !run.closure && (
            <p>取消此任务只会停止其等待中的子 run，资源占用者继续执行。</p>
          )}
          <ComparisonLink />
          {run.state === "waiting_for_input" && (
            <a
              onClick={navigateLink}
              className="underline"
              href={withDraftContext(`?procedure=${encodeURIComponent(procedureId)}#decisions`)}
            >
              查看结果并作出人工判断
            </a>
          )}
          {view.dispatch_blocked_reason === null && !progress.isError && (
            <button
              className={secondaryButton}
              type="button"
              disabled={pending}
              onClick={() => {
                void dispatch();
              }}
            >
              继续任务
            </button>
          )}
          {view.dispatch_blocked_reason && run.state === "ready" && (
            <p>{view.dispatch_blocked_reason}</p>
          )}
          {!run.cancellation && ["ready", "waiting_for_input", "leased"].includes(run.state) && (
            <details>
              <summary>{queued ? "取消等待任务" : "取消后续步骤"}</summary>
              <p>
                已完成结果会保留。正在执行的步骤（包括配置接纳）结束后，取消才会完成。这不是紧急停止。
              </p>
              <Controls.Field>
                取消操作人
                <Controls.Input
                  value={cancelActor}
                  onChange={(event) => setCancelActor(event.target.value)}
                />
              </Controls.Field>
              <Controls.Field>
                取消原因
                <Controls.Input
                  value={cancelReason}
                  onChange={(event) => setCancelReason(event.target.value)}
                />
              </Controls.Field>
              <button
                className={secondaryButton}
                type="button"
                disabled={
                  pending || progress.isError || !cancelActor.trim() || !cancelReason.trim()
                }
                onClick={() => {
                  void cancel();
                }}
              >
                {queued
                  ? "取消等待任务"
                  : run.state === "leased"
                    ? "当前步骤结束后停止"
                    : "取消任务"}
              </button>
            </details>
          )}
          {run.closure?.actor && <p>结束操作人：{run.closure.actor}</p>}
          {view.current_step && (
            <p>
              当前步骤： {view.current_step.step_key} · {stateLabel(view.current_step.state)}
            </p>
          )}
          {view.current_child && (
            <ProcedureChildRun
              child={view.current_child}
              collectionName={
                view.current_child.run.address
                  ? collectionNames.get(view.current_child.run.address.collection_id)
                  : undefined
              }
              procedureId={procedureId}
              current
            />
          )}
          <ul className="space-y-3">
            {steps.map((step) => {
              if (
                !run.closure &&
                !run.resource_wait &&
                step.step_key === view.current_step?.step_key &&
                step.attempt === view.current_step.attempt &&
                !step.output &&
                !step.failure_reason &&
                !step.attention_reason
              )
                return null;
              const child = children.get(step.step_key);
              const href = step.output && outputHref(step.output, procedureId);
              return (
                <li key={`${step.step_key}:${step.attempt}`} className="border-t pt-2 space-y-1">
                  <p>
                    {step.step_key}:{" "}
                    {run.resource_wait?.step_key === step.step_key
                      ? run.closure?.status === "cancelled"
                        ? "采集前已取消"
                        : "等待资源"
                      : run.closure?.status === "cancelled" && step.state === "waiting_for_input"
                        ? "人工判断已取消"
                        : stateLabel(step.state)}
                  </p>
                  {(step.failure_reason || step.attention_reason) && (
                    <p>{step.failure_reason ?? step.attention_reason}</p>
                  )}
                  {child && child.step_key !== view.current_child?.step_key && (
                    <ProcedureChildRun
                      child={child}
                      collectionName={
                        child.run.address
                          ? collectionNames.get(child.run.address.collection_id)
                          : undefined
                      }
                      procedureId={procedureId}
                    />
                  )}
                  {step.output?.kind === "interpretation" && (
                    <details>
                      <summary>已记录的人工判断</summary>
                      <pre className="overflow-auto text-xs">
                        {JSON.stringify(step.output.response, null, 2)}
                      </pre>
                    </details>
                  )}
                  {href && (!child || step.output?.kind !== "run") && (
                    <a onClick={navigateLink} className="underline" href={withDraftContext(href)}>
                      打开{" "}
                      {step.output?.kind === "analysis"
                        ? "analysis"
                        : step.output?.kind === "run"
                          ? "run"
                          : "保留结果"}
                    </a>
                  )}
                </li>
              );
            })}
          </ul>
          {progress.hasNextPage && (
            <button
              className={secondaryButton}
              type="button"
              disabled={progress.isFetchingNextPage}
              onClick={() => {
                void progress.fetchNextPage();
              }}
            >
              加载更早步骤
            </button>
          )}
          <details className="border border-line rounded-md p-3">
            <summary className="cursor-pointer text-sm font-semibold">执行诊断</summary>
            <p>
              {view.dispatch.worker_running
                ? "Worker 进程运行中"
                : `Worker 管理状态： ${view.dispatch.management}`}
            </p>
            {view.dispatch.failure && <p>记录时间：{view.dispatch.failure.observed_at}</p>}
            {view.dispatch.log_path && (
              <p className="break-all">
                Worker 日志： <code>{view.dispatch.log_path}</code>
              </p>
            )}
            <ProcedureWorkerLog procedureId={procedureId} />
            {run.state === "closed" && <ClearData key={procedureId} procedures={[procedureId]} />}
          </details>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
