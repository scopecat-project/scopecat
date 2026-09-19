import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function useAuthorWorkspaces(projectId: string | undefined) {
  return useQuery({
    queryKey: ["author-workspaces", projectId],
    enabled: Boolean(projectId),
    queryFn: ({ signal }) => apiData(apiClient.GET("/api/v1/author-workspaces", { signal })),
  });
}
