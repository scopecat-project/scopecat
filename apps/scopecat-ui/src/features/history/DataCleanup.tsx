import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { secondaryButton } from "../../ui/styles";

type Preview = components["schemas"]["DataCleanupPreview"];
type Operation = components["schemas"]["DataCleanupOperation"];
type Selection = components["schemas"]["DataCleanupSelection"];

function blockerReason(reason: string): string {
  const prefix = "Retained scientific content refers to: ";
  if (reason.startsWith(prefix)) return `保留的科学记录仍引用：${reason.slice(prefix.length)}`;
  const labels: Record<string, string> = {
    "Wait for source preparation to finish before clearing the course":
      "请等待源码准备结束，再清理课程。",
    "Clear this practice from Help to settle its tasks and choose whether to keep its edited files":
      "请从帮助页清理此练习，以处理其任务并选择是否保留已编辑文件。",
    "Cleanup request key already belongs to a different selection":
      "此清理请求已绑定其他选择，请核对原操作。",
    "Data or retaining dependencies changed; preview this cleanup again":
      "数据或保留依赖已变化，请重新预览本次清理。",
    "Resolve retaining dependencies and active tasks before clearing data":
      "清理前请先处理保留依赖与活动任务。",
    "Select records to clear": "请选择要清理的记录。",
    "Record no longer exists; refresh your selection": "记录已不存在，请刷新选择。",
    "This analysis belongs to a measurement; select that measurement for cleanup":
      "此分析属于一条测量记录，请选择该测量记录进行清理。",
    "Finish or cancel this task through its normal controls before clearing data":
      "清理数据前，请通过任务的正常入口完成或取消任务。",
    "Finish or cancel this task through its normal task controls before clearing data":
      "清理数据前，请通过任务的正常入口完成或取消任务。",
    "Stop the live analysis follow before clearing its input data":
      "清理输入数据前，请先停止实时跟随分析。",
  };
  return labels[reason] ?? reason;
}

function including(selection: Selection, owner: string): Selection | undefined {
  const separator = owner.indexOf(":");
  const kind = owner.slice(0, separator);
  const identity = owner.slice(separator + 1);
  const field =
    kind === "run"
      ? "runs"
      : kind === "procedure"
        ? "procedures"
        : kind === "analysis"
          ? "analyses"
          : undefined;
  if (!field || selection[field].includes(identity)) return undefined;
  return { ...selection, [field]: [...selection[field], identity] };
}

export function ClearData({
  runs = [],
  procedures = [],
  analyses = [],
  captures = [],
  onCleared,
}: {
  runs?: string[];
  procedures?: string[];
  analyses?: string[];
  captures?: string[];
  onCleared?: () => void;
}) {
  const cache = useQueryClient();
  const [preview, setPreview] = useState<Preview>();
  const [operation, setOperation] = useState<Operation>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [requestKey] = useState(() => crypto.randomUUID());
  async function inspect(
    selection: Selection = {
      runs,
      procedures,
      analyses,
      captures,
      setups: [],
      setup_definitions: [],
      parameters: [],
      branches: [],
      plans: [],
      schedules: [],
      calibration_tasks: [],
      calibration_profiles: [],
    },
  ) {
    setPending(true);
    setError("");
    try {
      setPreview(
        await apiData(apiClient.POST("/api/v1/data-cleanup/preview", { body: selection })),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  async function clear() {
    if (!preview) return;
    setPending(true);
    setError("");
    try {
      const result = await apiData(
        apiClient.POST("/api/v1/data-cleanup", { body: { request_key: requestKey, preview } }),
      );
      setOperation(result);
      if (result.state === "complete") onCleared?.();
      await cache.invalidateQueries({ queryKey: ["data-cleanup"] });
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["runs"] }),
        cache.invalidateQueries({ queryKey: ["data"] }),
        cache.invalidateQueries({ queryKey: ["research", "runs"] }),
        cache.invalidateQueries({ queryKey: ["analyses"] }),
      ]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  if (operation)
    return (
      <CleanupResult
        operation={operation}
        update={(result) => {
          setOperation(result);
          if (result.state === "complete") onCleared?.();
        }}
      />
    );
  return (
    <section className="grid gap-2">
      {!preview ? (
        <button
          className={secondaryButton}
          disabled={
            pending || !(runs.length || procedures.length || analyses.length || captures.length)
          }
          onClick={() => void inspect()}
        >
          预览数据清理…
        </button>
      ) : (
        <>
          <h3 className="font-semibold">清理选定数据</h3>
          <p>
            {preview.record_count} 条选定记录及其存储文件将被删除。此操作不删除你的代码或设备设置。
            继续前，请导出或备份需要保留的内容。
          </p>
          <p>
            预计回收文件空间：{" "}
            {(preview.bytes_to_reclaim / 1024).toLocaleString(undefined, {
              maximumFractionDigits: 1,
            })}{" "}
            KiB.
          </p>
          <ul>
            {[
              ...preview.selection.runs.map((id) => `测量：${id}`),
              ...preview.selection.procedures.map((id) => `任务：${id}`),
              ...preview.selection.analyses.map((id) => `分析：${id}`),
              ...preview.selection.captures.map((id) => `导入文件：${id}`),
            ].map((label) => (
              <li key={label}>
                <code>{label}</code>
              </li>
            ))}
          </ul>
          {preview.blockers.length > 0 && (
            <ul>
              {preview.blockers.map((item, index) => (
                <li key={index}>
                  <strong>{item.owner}</strong>: {blockerReason(item.reason)}
                  {including(preview.selection, item.owner) && (
                    <button
                      className={secondaryButton}
                      disabled={pending}
                      onClick={() => {
                        const expanded = including(preview.selection, item.owner);
                        if (expanded) void inspect(expanded);
                      }}
                    >
                      纳入此依赖记录并重新预览
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <button
            className={secondaryButton}
            disabled={pending || !!preview.blockers.length}
            onClick={() => void clear()}
          >
            删除选定记录与文件
          </button>
          <button
            className={secondaryButton}
            disabled={pending}
            onClick={() => setPreview(undefined)}
          >
            取消
          </button>
        </>
      )}
      {error && <p role="alert">{blockerReason(error)}</p>}
    </section>
  );
}

function CleanupResult({
  operation,
  update,
}: {
  operation: Operation;
  update: (value: Operation) => void;
}) {
  const cache = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function resume() {
    setPending(true);
    try {
      update(
        await apiData(
          apiClient.POST("/api/v1/data-cleanup/{operation_id}/resume", {
            params: { path: { operation_id: operation.id } },
          }),
        ),
      );
      setError("");
      await cache.invalidateQueries();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="grid gap-2">
      <p role="status">
        {operation.state === "complete"
          ? "记录与文件已清理。"
          : operation.state === "records_removed"
            ? "数据库记录已删除；物理文件清理尚未完成，需要重试。"
            : "清理尚未删除选定记录。"}
      </p>
      {(error || operation.error) && (
        <p role="alert">{blockerReason(error || operation.error || "")}</p>
      )}
      {operation.state !== "complete" && (
        <button className={secondaryButton} disabled={pending} onClick={() => void resume()}>
          {pending ? "正在重试…" : "重试清理"}
        </button>
      )}
    </section>
  );
}

export function DataCleanupHistory() {
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ["data-cleanup"],
    queryFn: () => apiData(apiClient.GET("/api/v1/data-cleanup")),
    enabled: open,
  });
  return (
    <section className="grid gap-3 rounded border border-line p-3">
      <button className={secondaryButton} onClick={() => setOpen(!open)}>
        数据清理历史
      </button>
      {open && (
        <>
          {history.error && <p role="alert">{history.error.message}</p>}
          {history.data?.length === 0 && (
            <p>暂无清理操作。请选择测量记录，再选择“预览数据清理”。</p>
          )}
          {history.data?.map((operation) => (
            <article className="border-t border-line pt-2" key={operation.id}>
              <p>
                {operation.created_at ? new Date(operation.created_at).toLocaleString() : "清理"}
              </p>
              <CleanupResult operation={operation} update={() => void history.refetch()} />
            </article>
          ))}
        </>
      )}
    </section>
  );
}
