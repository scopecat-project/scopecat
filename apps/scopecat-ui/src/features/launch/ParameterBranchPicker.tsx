import { Field, Select, Notice } from "../../ui/Controls";
import { secondaryButton } from "../../ui/styles";
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
        参数：{" "}
        {value.kind === "parameters"
          ? value.ref.revision_id
          : value.kind === "unselected"
            ? "未选择"
            : "来自选中工作点或保留证据"}
      </p>
      <button
        className={`${secondaryButton} mr-2`}
        type="button"
        disabled={disabled}
        aria-expanded={browse}
        onClick={() => setBrowse(!browse)}
      >
        {browse ? "收起参数分支" : "选择参数分支"}
      </button>
      {browse && (
        <>
          <Field className="flex-col">
            参数分支
            <Select
              aria-label="参数分支"
              value={name}
              disabled={disabled || heads.isPending}
              onChange={(event) => setName(event.target.value)}
            >
              <option value="">选择分支</option>
              {items.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name} · generation {item.generation}
                </option>
              ))}
            </Select>
          </Field>
          {branch && (
            <p>
              {branch.name} · generation {branch.generation} · {branch.actor}
              {branch.note && ` · ${branch.note}`}
              <br />
              Version: {branch.revision.revision_id}
            </p>
          )}
          <button
            className={`${secondaryButton} mr-2`}
            type="button"
            disabled={disabled || !branch || heads.isFetching || heads.isError}
            onClick={() =>
              branch &&
              onChange({
                kind: "parameters",
                ref: branch.revision,
                overrides: [],
                ...(value.kind === "parameters" && value.setup ? { setup: value.setup } : {}),
              })
            }
          >
            使用此参数版本
          </button>
          <button
            className={`${secondaryButton} mr-2`}
            type="button"
            disabled={disabled || heads.isFetching}
            onClick={() => void heads.refetch()}
          >
            刷新参数分支
          </button>
          {heads.hasNextPage && (
            <button
              className={`${secondaryButton} mr-2`}
              type="button"
              disabled={disabled || heads.isFetching}
              onClick={() => void heads.fetchNextPage()}
            >
              加载更多参数分支
            </button>
          )}
          {heads.error && <Notice role="alert">{heads.error.message}</Notice>}
          {heads.isSuccess && items.length === 0 && <p>暂无参数分支，请在作者会话中创建。</p>}
          {value.kind !== "unselected" && (
            <button
              className={`${secondaryButton} mr-2`}
              type="button"
              disabled={disabled}
              onClick={() => onChange({ kind: "unselected" })}
            >
              清除参数选择
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
