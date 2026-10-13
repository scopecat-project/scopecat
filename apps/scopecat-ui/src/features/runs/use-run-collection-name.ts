import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

// Only the opened detail may resolve an off-page name; rows share the paged catalog.
export function useRunCollectionName(
  projectId: string | undefined,
  collectionId: string | undefined,
  names: Map<string, string>,
) {
  const known = collectionId ? names.get(collectionId) : undefined;
  const collection = useQuery({
    queryKey: ["record-collection", projectId, collectionId],
    enabled: Boolean(projectId && collectionId && !known),
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/record-collections/{collection_id}", {
          params: { path: { collection_id: collectionId! } },
          signal,
        }),
      ),
  });
  return { name: known ?? collection.data?.name, error: known ? null : collection.error };
}
