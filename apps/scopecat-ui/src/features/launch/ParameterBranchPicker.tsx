import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { ScientificSelection } from "./scientific-selection";

type Configuration = ScientificSelection["configuration"];

export function ParameterBranchPicker({
  value,
  projectId,
  disabled,
  onChange,
}: {
  value: Configuration;
  projectId?: string;
  disabled?: boolean;
  onChange: (choice: Configuration) => void;
}) {
  const [browse, setBrowse] = useState(false);
  const [name, setName] = useState("");
  const heads = useInfiniteQuery({
    queryKey: ["parameter-branches", projectId],
    enabled: browse,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/parameters/branches", {
          params: { query: { limit: 100, after: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = heads.data?.pages.flatMap((page) => page.items) ?? [];
  const branch = items.find((item) => item.name === name);
  return (
    <div className="space-y-2">
      <p>
        Parameters:{" "}
        {value.kind === "parameters"
          ? value.ref.revision_id
          : value.kind === "active"
            ? "Lab default"
            : "From selected working point or saved evidence"}
      </p>
      <button
        type="button"
        disabled={disabled}
        aria-expanded={browse}
        onClick={() => setBrowse(!browse)}
      >
        {browse ? "Hide parameter branches" : "Choose parameter branch"}
      </button>
      {browse && (
        <>
          <label className="flex flex-col gap-1">
            Parameter branch
            <select
              aria-label="Parameter branch"
              value={name}
              disabled={disabled || heads.isPending}
              className="border rounded p-2"
              onChange={(event) => setName(event.target.value)}
            >
              <option value="">Choose a branch</option>
              {items.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name} · generation {item.generation}
                </option>
              ))}
            </select>
          </label>
          {branch && (
            <p>
              {branch.name} · generation {branch.generation} · {branch.actor}
              {branch.note && ` · ${branch.note}`}
              <br />
              Version: {branch.revision.revision_id}
            </p>
          )}
          <button
            type="button"
            disabled={disabled || !branch || heads.isFetching || heads.isError}
            onClick={() =>
              branch && onChange({ kind: "parameters", ref: branch.revision, overrides: [] })
            }
          >
            Use this parameter version
          </button>
          <button
            type="button"
            disabled={disabled || heads.isFetching}
            onClick={() => void heads.refetch()}
          >
            Refresh parameter branches
          </button>
          {heads.hasNextPage && (
            <button
              type="button"
              disabled={disabled || heads.isFetching}
              onClick={() => void heads.fetchNextPage()}
            >
              Load more parameter branches
            </button>
          )}
          {heads.error && <p role="alert">{heads.error.message}</p>}
          {heads.isSuccess && items.length === 0 && (
            <p>No parameter branches yet. Create one in your author session.</p>
          )}
          {value.kind !== "active" && (
            <button type="button" disabled={disabled} onClick={() => onChange({ kind: "active" })}>
              Use lab parameter default
            </button>
          )}
          <p className="text-sm">
            Choosing a version preserves this page's subject and batch. Branch saves and list
            refreshes do not replace the selected version. Preview checks compatibility; branch
            names do not establish calibration validity.
          </p>
        </>
      )}
    </div>
  );
}
