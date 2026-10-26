import { Button, Notice, Field, Select } from "../../ui/Controls";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

type Scope = components["schemas"]["PracticeScope"];

export function PracticePanel({ reachable }: { reachable: boolean }) {
  const catalog = useQuery({
    queryKey: ["practice"],
    queryFn: ({ signal }) => apiData(apiClient.GET("/api/v1/practice", { signal })),
    enabled: reachable,
  });
  const practices = catalog.data?.items.filter((scope) => scope.kind !== "notebook");
  if (!catalog.error && !practices?.length) return null;
  return (
    <section className="grid gap-3 rounded-lg border border-line bg-panel p-4">
      <h3 className="font-semibold">早期峰值练习</h3>
      <p>继续或清理已有练习。新学习请从上方 Notebook 课程开始。</p>
      {catalog.error && <Notice role="alert">{catalog.error.message}</Notice>}
      {practices?.map((scope) => (
        <PracticeCard key={scope.id} scope={scope} refresh={() => catalog.refetch()} />
      ))}
    </section>
  );
}

function PracticeCard({ scope, refresh }: { scope: Scope; refresh: () => Promise<unknown> }) {
  const [choice, setChoice] = useState<"preserve" | "discard">("preserve");
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function clear() {
    setPending(true);
    setError("");
    try {
      await apiData(
        apiClient.POST("/api/v1/practice/{scope_id}/clear", {
          params: { path: { scope_id: scope.id } },
          body: { files: scope.file_disposition ?? choice },
        }),
      );
      setConfirming(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      await refresh();
      setPending(false);
    }
  }
  return (
    <article className="grid gap-2 border-t border-line pt-3">
      <h4 className="font-semibold">{scope.title}</h4>
      <p>
        {scope.state === "cleared"
          ? "练习已清理"
          : scope.state === "cleaning"
            ? "清理未完成 — 已阻止新任务"
            : "可以继续"}
      </p>
      {scope.state === "active" && scope.procedure_id && (
        <>
          <a
            className="underline"
            href={`?procedure=${encodeURIComponent(scope.procedure_id)}#launch`}
          >
            打开练习任务
          </a>
          <a
            className="underline"
            href={`?procedure=${encodeURIComponent(scope.procedure_id)}#decisions`}
          >
            核对曲线并输入频率
          </a>
          <p>可关闭此页以后继续。重新打开任务不会重复扫描。</p>
        </>
      )}
      {scope.file_disposition !== "discard" && (
        <>
          <p>
            {scope.state === "cleared" ? "已保留文件：" : "可选的 VS Code 笔记："}
            <code>{scope.directory}</code>
          </p>
          <a className="underline" href={`/api/v1/practice/${encodeURIComponent(scope.id)}/files`}>
            导出练习文件
          </a>
        </>
      )}
      {(error || scope.cleanup_error) && (
        <Notice role="alert">{error || scope.cleanup_error}</Notice>
      )}
      {scope.state === "cleaning" ? (
        <Button type="button" disabled={pending} onClick={() => void clear()}>
          {pending ? "正在清理…" : "重试清理"}
        </Button>
      ) : (
        scope.state === "active" &&
        (confirming ? (
          <>
            <p>停止此练习并移除其扫描、分析和决策。其他工作仍保留。</p>
            <Field>
              练习文件{" "}
              <Select
                value={choice}
                onChange={(event) => setChoice(event.target.value as "preserve" | "discard")}
                disabled={pending}
              >
                <option value="preserve">保留笔记与已编辑文件</option>
                <option value="discard">删除此练习目录中的全部文件</option>
              </Select>
            </Field>
            <Button type="button" disabled={pending} onClick={() => void clear()}>
              {pending ? "正在清理…" : "清理此练习"}
            </Button>
            <Button type="button" disabled={pending} onClick={() => setConfirming(false)}>
              取消
            </Button>
          </>
        ) : (
          <Button type="button" onClick={() => setConfirming(true)}>
            清理练习…
          </Button>
        ))
      )}
    </article>
  );
}
