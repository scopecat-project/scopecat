import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function AuthorRefresh({
  projectId,
  onRefreshed,
}: {
  projectId: string | undefined;
  onRefreshed?: () => void | Promise<void>;
}) {
  const queryClient = useQueryClient();
  const state = useQuery({
    queryKey: ["author-revisions", projectId],
    enabled: Boolean(projectId),
    queryFn: () => apiData(apiClient.GET("/api/v1/author-revisions")),
  });
  const refresh = useMutation({
    mutationFn: () =>
      apiData(
        apiClient.POST("/api/v1/author-revisions/refresh", {
          body: { expected_generation: state.data?.generation ?? 0 },
        }),
      ),
    onError: async () => {
      await queryClient.invalidateQueries({ queryKey: ["author-revisions", projectId] });
    },
    onSuccess: async (next) => {
      queryClient.setQueryData(["author-revisions", projectId], next);
      await queryClient.invalidateQueries({ queryKey: ["experiment-launcher"] });
      await onRefreshed?.();
    },
  });
  if (state.data && !state.data.enabled) return null;
  return (
    <div className="space-y-2">
      <button
        type="button"
        className="border rounded px-3 py-2"
        disabled={refresh.isPending || state.isPending}
        onClick={() => refresh.mutate()}
      >
        {refresh.isPending ? "Validating author changes…" : "Refresh author code"}
      </button>
      {state.data?.active && (
        <p className="text-xs break-all">Author revision: {state.data.active.content_hash}</p>
      )}
      {refresh.isSuccess && (
        <p role="status">Author code refreshed. Preview the updated experiment before starting.</p>
      )}
      {(refresh.error || state.error) && (
        <pre role="alert" className="whitespace-pre-wrap text-sm">
          {(refresh.error || state.error)?.message}
        </pre>
      )}
    </div>
  );
}
