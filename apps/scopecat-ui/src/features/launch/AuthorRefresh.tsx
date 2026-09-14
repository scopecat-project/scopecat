import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
type Preparation = components["schemas"]["AuthorPreparation"];
const terminal = (status: Preparation["status"]) =>
  ["succeeded", "failed", "cancelled", "interrupted"].includes(status);
type Request = { operation_id: string; expected_generation: number };

type Props = {
  projectId: string | undefined;
  onRefreshed?: () => void | Promise<void>;
};

export function AuthorRefresh(props: Props) {
  return <AuthorRefreshPanel key={props.projectId} {...props} />;
}

function AuthorRefreshPanel({ projectId, onRefreshed }: Props) {
  const queryClient = useQueryClient();
  const [request, setRequest] = useState<Request>();
  const notified = useRef<string | undefined>(undefined);
  const state = useQuery({
    queryKey: ["author-revisions", projectId],
    enabled: Boolean(projectId),
    queryFn: () => apiData(apiClient.GET("/api/v1/author-revisions")),
    refetchInterval: (query) =>
      query.state.data?.enabled && !query.state.data.active ? 1000 : false,
  });
  const history = useQuery({
    queryKey: ["author-preparations", projectId],
    enabled: Boolean(projectId && state.data?.enabled),
    queryFn: () => apiData(apiClient.GET("/api/v1/author-preparations")),
    refetchInterval: (query) =>
      !state.data?.active || query.state.data?.some((item) => !terminal(item.status))
        ? 1000
        : false,
  });
  const identity =
    request?.operation_id ?? state.data?.preparation_id ?? history.data?.[0]?.operation_id;
  const operation = useQuery({
    queryKey: ["author-preparation", projectId, identity],
    enabled: Boolean(identity && projectId),
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/author-preparations/{operation_id}", {
          params: { path: { operation_id: identity! } },
        }),
      ),
    retry: false,
    refetchInterval: (query) =>
      query.state.data && terminal(query.state.data.status) ? false : 1000,
  });
  const refresh = useMutation({
    mutationFn: (body: Request) => apiData(apiClient.POST("/api/v1/author-preparations", { body })),
    retry: false,
    onError: (error) => {
      if (error instanceof ApiError && (error.status === 409 || error.status === 422)) {
        setRequest(undefined);
        void queryClient.invalidateQueries({ queryKey: ["author-revisions", projectId] });
      }
    },
    onSuccess: (next) => {
      queryClient.setQueryData(["author-preparation", projectId, next.operation_id], next);
      void queryClient.invalidateQueries({ queryKey: ["author-preparations", projectId] });
    },
  });
  const cancel = useMutation({
    mutationFn: () =>
      apiData(
        apiClient.POST("/api/v1/author-preparations/{operation_id}/cancel", {
          params: { path: { operation_id: identity! } },
        }),
      ),
    onSuccess: (next) =>
      queryClient.setQueryData(["author-preparation", projectId, next.operation_id], next),
  });
  useEffect(() => {
    const next = operation.data;
    if (next?.status !== "succeeded" || notified.current === next.operation_id) return;
    notified.current = next.operation_id;
    void queryClient.invalidateQueries({ queryKey: ["author-revisions", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["experiment-launcher"] });
    void onRefreshed?.();
  }, [operation.data, projectId, queryClient, onRefreshed]);
  if (state.data && !state.data.enabled) return null;
  const busy = identity && (!operation.data || !terminal(operation.data.status));
  return (
    <div className="space-y-2">
      <button
        type="button"
        className="border rounded px-3 py-2"
        disabled={Boolean(busy) || refresh.isPending || state.isPending}
        onClick={() => {
          const body = {
            operation_id: crypto.randomUUID(),
            expected_generation: state.data?.generation ?? 0,
          };
          setRequest(body);
          refresh.mutate(body);
        }}
      >
        {busy ? "Preparing author code…" : "Refresh author code"}
      </button>
      {operation.data && (
        <p role="status">
          {operation.data.status}: {operation.data.phase}
          {!terminal(operation.data.status) &&
            ` · ${Math.max(0, Math.floor((operation.dataUpdatedAt - Date.parse(operation.data.created_at)) / 1000))}s elapsed`}
          {operation.data.status === "succeeded" &&
            " · Preview the updated experiment before starting."}
        </p>
      )}
      {identity && <p className="text-xs break-all">Preparation: {identity}</p>}
      {busy && (
        <button
          type="button"
          disabled={cancel.isPending || operation.data?.status === "cancelling"}
          onClick={() => cancel.mutate()}
        >
          Cancel preparation
        </button>
      )}
      {refresh.error && request && (
        <button type="button" disabled={refresh.isPending} onClick={() => refresh.mutate(request)}>
          Retry same submission
        </button>
      )}
      {state.data?.active && (
        <p className="text-xs break-all">Author revision: {state.data.active.content_hash}</p>
      )}
      {(operation.data?.error ||
        refresh.error ||
        operation.error ||
        cancel.error ||
        state.error) && (
        <pre role="alert" className="whitespace-pre-wrap text-sm">
          {operation.data?.error ??
            (refresh.error || operation.error || cancel.error || state.error)?.message}
        </pre>
      )}
    </div>
  );
}
