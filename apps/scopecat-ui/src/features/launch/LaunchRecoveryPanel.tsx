import { secondaryButton } from "../../ui/styles";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useLaunchDraft } from "./LaunchDraft";
import { draftHistory, attemptHistory } from "./launch-recovery";

export function LaunchRecoveryPanel() {
  const { projectId, recover, recoverAttempt, attemptsReady, retryAttempts } = useLaunchDraft();
  const drafts = useInfiniteQuery({
    queryKey: ["launch-draft-history", projectId],
    enabled: Boolean(projectId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => draftHistory(pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchOnWindowFocus: true,
  });
  const attempts = useInfiniteQuery({
    queryKey: ["launch-attempt-history", projectId],
    enabled: Boolean(projectId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => attemptHistory(pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchOnWindowFocus: true,
  });
  return (
    <details className="rounded-md border border-line bg-panel-soft p-4 space-y-3">
      <summary>恢复实验输入或原始提交</summary>
      <p>恢复不会开始采集。输入版本与冲突副本仍保留在应用数据中。</p>
      <button
        className={secondaryButton}
        type="button"
        onClick={() => {
          retryAttempts();
          void drafts.refetch();
          void attempts.refetch();
        }}
      >
        刷新恢复历史
      </button>
      {(drafts.error || attempts.error) && (
        <p role="alert">无法加载恢复历史。请在应用重新连接后重试。</p>
      )}
      {!attemptsReady && <p>正在加载原始提交记录。如暂不可用，请点击“刷新恢复历史”重试。</p>}
      <ul>
        {drafts.data?.pages
          .flatMap((page) => page.items)
          .map((record) => (
            <li key={record.revision}>
              {record.input.draft_name ?? "草稿"} · {record.target.draft_id ?? "default"} ·{" "}
              {record.target.experiment} · {record.target.workspace_id} ·{" "}
              {record.state === "saved"
                ? "已保存"
                : record.state === "conflict"
                  ? "冲突副本"
                  : record.state}{" "}
              · {record.created_at}
              <button className={secondaryButton} type="button" onClick={() => recover(record)}>
                审阅输入修订 {record.revision}
              </button>
            </li>
          ))}
      </ul>
      {drafts.hasNextPage && (
        <button
          className={secondaryButton}
          type="button"
          onClick={() => {
            void drafts.fetchNextPage();
          }}
        >
          更早的输入版本
        </button>
      )}
      <ul>
        {attempts.data?.pages
          .flatMap((page) => page.items)
          .map((record) => (
            <li key={record.sequence}>
              {record.request.experiment} · {record.request.request_key} · {record.created_at}
              <button
                className={secondaryButton}
                type="button"
                onClick={() => recoverAttempt(record)}
              >
                恢复原始提交 {record.sequence}
              </button>
            </li>
          ))}
      </ul>
      {attempts.hasNextPage && (
        <button
          className={secondaryButton}
          type="button"
          onClick={() => {
            void attempts.fetchNextPage();
          }}
        >
          更早的提交收据
        </button>
      )}
    </details>
  );
}
