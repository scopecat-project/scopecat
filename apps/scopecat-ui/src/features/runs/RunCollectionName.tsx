import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function RunCollectionName({
  runId,
  projectId,
  collectionId,
}: {
  runId: string;
  projectId?: string;
  collectionId: string;
}) {
  const collection = useQuery({
    queryKey: ["record-collection", projectId, runId, collectionId],
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/record-collections/{collection_id}", {
          params: { path: { collection_id: collectionId } },
          signal,
        }),
      ),
  });
  return (
    <p className="my-1 text-sm text-text-soft">
      {collection.data?.name ??
        (collection.isPending ? "Loading collection name…" : "Collection name unavailable")}
      {collection.error && <span role="alert"> · {collection.error.message}</span>}
    </p>
  );
}
