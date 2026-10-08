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
    <details>
      <summary>Recover experiment input or an original submission</summary>
      <p>
        Recovery never starts acquisition. Input versions and conflicting copies remain in
        application data.
      </p>
      <button
        type="button"
        onClick={() => {
          retryAttempts();
          void drafts.refetch();
          void attempts.refetch();
        }}
      >
        Refresh recovery history
      </button>
      {(drafts.error || attempts.error) && (
        <p role="alert">Cannot load recovery history. Retry when the application reconnects.</p>
      )}
      {!attemptsReady && (
        <p>
          Loading original submission records. Use Refresh recovery history to retry if unavailable.
        </p>
      )}
      <ul>
        {drafts.data?.pages
          .flatMap((page) => page.items)
          .map((record) => (
            <li key={record.revision}>
              {record.target.experiment} · {record.target.workspace_id} · {record.state} ·{" "}
              {record.created_at}
              <button type="button" onClick={() => recover(record)}>
                Review input revision {record.revision}
              </button>
            </li>
          ))}
      </ul>
      {drafts.hasNextPage && (
        <button
          type="button"
          onClick={() => {
            void drafts.fetchNextPage();
          }}
        >
          Older input versions
        </button>
      )}
      <ul>
        {attempts.data?.pages
          .flatMap((page) => page.items)
          .map((record) => (
            <li key={record.sequence}>
              {record.request.experiment} · {record.request.request_key} · {record.created_at}
              <button type="button" onClick={() => recoverAttempt(record)}>
                Recover original submission {record.sequence}
              </button>
            </li>
          ))}
      </ul>
      {attempts.hasNextPage && (
        <button
          type="button"
          onClick={() => {
            void attempts.fetchNextPage();
          }}
        >
          Older submission receipts
        </button>
      )}
    </details>
  );
}
