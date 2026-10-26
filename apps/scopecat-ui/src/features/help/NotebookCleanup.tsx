import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Notice } from "../../ui/Controls";
import { errorMessage } from "../../lib/presentation";
import type { LessonTopic, NotebookJourney } from "../application/DesktopSession";

export function NotebookCleanup({
  topic,
  journey,
  disabled,
  onBusyChange,
}: {
  topic: LessonTopic;
  journey: NotebookJourney;
  disabled: boolean;
  onBusyChange: (busy: boolean) => void;
}) {
  const api = window.pywebview?.api;
  const cache = useQueryClient();
  const [discard, setDiscard] = useState(journey.cleanup_files === "discard");
  const preview = useMutation({ mutationFn: () => api!.preview_notebook_cleanup!(topic) });
  const fixedFiles = preview.data?.files ?? journey.cleanup_files;
  const deleting = fixedFiles ? fixedFiles === "discard" : discard;
  const clear = useMutation({
    mutationFn: () =>
      api!.clear_notebook_course!(
        topic,
        JSON.stringify({
          preview: preview.data!.preview,
          files: fixedFiles ?? (discard ? "discard" : "preserve"),
        }),
      ),
    onMutate: () => onBusyChange(true),
    onSettled: async () => {
      onBusyChange(false);
      await cache.invalidateQueries({ queryKey: ["notebook-journey", topic] });
    },
  });
  if (journey.cleared) return <Notice>课程数据已清理。重新准备即可创建新的课程目录。</Notice>;
  if (!journey.scope_id)
    return <Notice>旧课程没有可靠的归属记录。请在数据页逐项核对；清理不会按名称推断归属。</Notice>;
  if (!api?.preview_notebook_cleanup || !api.clear_notebook_course) return null;
  const result = preview.data;
  return (
    <details className="grid gap-2">
      <summary>清理此课程</summary>
      <p>
        请先预览所有归属数据。使用常规控件完成或取消未完成任务。除非明确选择删除，否则保留已编辑代码。
      </p>
      <Button
        disabled={disabled || preview.isPending || clear.isPending}
        onClick={() => preview.mutate()}
      >
        预览课程清理
      </Button>
      {(preview.error || clear.error) && (
        <Notice role="alert">{errorMessage(preview.error ?? clear.error)}</Notice>
      )}
      {result && (
        <div className="grid gap-3 mt-3">
          <p>{result.preview.record_count} 条归属记录已选中。</p>
          <details>
            <summary>核对每条选中记录</summary>
            {Object.entries(result.preview.selection)
              .filter(([, ids]) => ids.length > 0)
              .map(([kind, ids]) => (
                <div key={kind}>
                  <strong>{kind}</strong>
                  <ul>
                    {ids.map((id) => (
                      <li key={id} className="break-all">
                        {id}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
          </details>
          {result.preview.blockers.map((blocker) => (
            <Notice key={blocker.owner} role="alert">
              {blocker.owner}: {blocker.reason}
            </Notice>
          ))}
          <label className="flex gap-2 items-start">
            <input
              type="checkbox"
              checked={deleting}
              disabled={!!fixedFiles || !!result.files_error || clear.isPending}
              onChange={(event) => setDiscard(event.target.checked)}
            />
            同时删除此课程的已编辑代码和本地 Python 目录： {journey.directory}
          </label>
          {result.files_error && <Notice>{result.files_error} 仍可选择保留代码。</Notice>}
          <Button
            variant="danger"
            disabled={
              disabled ||
              clear.isPending ||
              result.preview.blockers.length > 0 ||
              (deleting && !!result.files_error)
            }
            onClick={() => clear.mutate()}
          >
            {clear.isPending ? "正在清理…" : "确认课程清理"}
          </Button>
        </div>
      )}
    </details>
  );
}
