import { secondaryButton } from "../../ui/styles";
import { subjectLabel } from "./scientific-selection";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { useLaunchDraft } from "./LaunchDraft";
import { planDifferences, type PlanRevision } from "./experiment-plans";

export function PlanLibrary({ initializing = false }: { initializing?: boolean } = {}) {
  const { projectId, workspaceId, draft, openPlan, isCurrent } = useLaunchDraft();
  const cache = useQueryClient();
  const [history, setHistory] = useState<string>();
  const [selected, setSelected] = useState<PlanRevision>();
  const [error, setError] = useState("");
  const alive = useRef(true);
  const opening = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const plans = useQuery({
    queryKey: ["experiment-plans", projectId, workspaceId, history],
    enabled: Boolean(projectId),
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/experiment-plans", {
          params: { query: { plan_id: history } },
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
        }),
      ),
  });
  async function open(plan: PlanRevision) {
    const token = ++opening.current;
    const revision = draft?.revision;
    const current = () => alive.current && opening.current === token && isCurrent(revision);
    setError("");
    try {
      const catalog = await apiData(
        apiClient.GET("/api/v1/experiment-launcher", {
          params: {
            query: {
              code_revision: plan.definition.code_revision?.content_hash,
            },
            header: { "X-Scopecat-Workspace": plan.definition.workspace_id },
          },
        }),
      );
      const entry = catalog.entries.find(
        (item) =>
          item.id === plan.definition.experiment && item.version === plan.definition.version,
      );
      if (!entry)
        throw new Error(
          "已保存的定义不可用。计划和历史仍可读取；请先恢复其受支持的作者源码修订，再重新打开。",
        );
      if (current()) await openPlan(plan, entry);
    } catch (caught) {
      if (current()) setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const planId = params.get("plan");
    const revision = Number(params.get("plan_revision"));
    const hash = params.get("plan_hash");
    if (!planId || !revision || !hash) return;
    void apiData(
      apiClient.POST("/api/v1/experiment-plans/read", {
        body: { plan_id: planId, revision, content_hash: hash },
      }),
    )
      .then((plan) => {
        if (!alive.current) return;
        setSelected(plan);
        setHistory(plan.ref.plan_id);
        // A durable link reveals the revision; the author explicitly opens its draft.
      })
      .catch((caught) => {
        if (alive.current) setError(String(caught));
      });
  }, []);
  async function hide(plan: PlanRevision) {
    try {
      await apiData(
        apiClient.POST("/api/v1/experiment-plans/hide", {
          body: plan.ref,
          headers: workspaceId ? { "X-Scopecat-Workspace": workspaceId } : undefined,
        }),
      );
      await cache.invalidateQueries({ queryKey: ["experiment-plans", projectId] });
    } catch (caught) {
      if (alive.current) setError(caught instanceof Error ? caught.message : String(caught));
    }
  }
  return (
    <section aria-label="已保存的实验计划" className="border border-line rounded p-3 space-y-2">
      <h3 className="font-semibold">已保存计划</h3>
      <p>保存和打开计划不会启动实验，也不会改变实验室默认值。每次运行都需要重新预览。</p>
      {initializing && <p role="status">正在加载实验，完成后可打开已保存计划…</p>}
      {history && (
        <button className={secondaryButton} type="button" onClick={() => setHistory(undefined)}>
          返回命名计划目录
        </button>
      )}
      {(error || plans.error) && <p role="alert">{error || plans.error?.message}</p>}
      {plans.data?.items.length === 0 && <p>暂无已保存计划。请先预览输入，再保存命名计划。</p>}
      {plans.data?.items
        .filter((plan) => !plan.definition.manual_lines)
        .map((plan) => (
          <div
            key={`${plan.ref.plan_id}:${plan.ref.revision}`}
            className="flex flex-wrap gap-2 items-center"
          >
            <span>
              {plan.name} · 修订 {plan.ref.revision} · 保存人：{plan.saved_by}
            </span>
            <button
              className={secondaryButton}
              type="button"
              disabled={initializing}
              onClick={() => void open(plan)}
            >
              打开 {plan.name} r{plan.ref.revision}
            </button>
            <button
              className={secondaryButton}
              type="button"
              onClick={() => {
                setSelected(plan);
                setHistory(plan.ref.plan_id);
              }}
            >
              历史与对比
            </button>
            {!history && (
              <button className={secondaryButton} type="button" onClick={() => void hide(plan)}>
                隐藏 {plan.name}
              </button>
            )}
            {history && (
              <button className={secondaryButton} type="button" onClick={() => setSelected(plan)}>
                对比修订 {plan.ref.revision}
              </button>
            )}
          </div>
        ))}
      {selected && (
        <details open>
          <summary>
            {selected.name}，修订 {selected.ref.revision}
          </summary>
          <p>
            实验：{selected.definition.experiment}。样品/目标：{" "}
            {subjectLabel(selected.definition.selection)}.
          </p>
          {selected.definition.source && (
            <a
              className="underline"
              href={`?compare=${encodeURIComponent(selected.definition.source.run_id)}&comparison-analysis=${encodeURIComponent(selected.definition.source.analysis_id)}#analyses`}
            >
              来源分析
            </a>
          )}
          {draft?.plan && (
            <ul aria-label="计划修订差异">
              {planDifferences(draft.plan, selected).map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          <details>
            <summary>精确引用与输入</summary>
            <pre className="overflow-auto text-xs">{JSON.stringify(selected, null, 2)}</pre>
          </details>
        </details>
      )}
      <p className="text-sm">
        隐藏仅将命名计划的当前版本移出目录；已有运行链接和精确历史修订仍可读取。
      </p>
    </section>
  );
}
