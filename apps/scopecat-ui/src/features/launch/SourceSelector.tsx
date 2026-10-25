import { Field, Select, Button, Notice } from "../../ui/Controls";
import type { useAuthorWorkspaces } from "./source-api";

export function SourceSelector({
  context = "acquisition",
  catalog,
  workspaceId,
  onSelect,
}: {
  context?: "acquisition" | "analysis";
  catalog: ReturnType<typeof useAuthorWorkspaces>;
  workspaceId: string;
  onSelect: (id: string) => void;
}) {
  const sources = catalog.data?.items ?? [];
  const selected = sources.find((source) => source.id === workspaceId);
  return (
    <div className="min-w-0 space-y-2">
      <Field>
        代码工作区{" "}
        <Select
          aria-label="代码工作区"
          className="mt-1 w-full min-w-0"
          value={workspaceId}
          onChange={(event) => onSelect(event.target.value)}
          disabled={catalog.isPending}
        >
          {!selected && (
            <option value={workspaceId}>
              {workspaceId
                ? `${workspaceId} ${catalog.isPending ? "（加载中）" : "（不可用）"}`
                : "选择代码"}
            </option>
          )}
          {sources.map((source) => (
            <option key={source.id} value={source.id} disabled={!source.available}>
              {source.name} · {source.id}
              {source.available ? "" : " （不可用）"}
            </option>
          ))}
        </Select>
      </Field>
      <Button type="button" disabled={catalog.isFetching} onClick={() => void catalog.refetch()}>
        刷新工作区列表
      </Button>
      {catalog.error && <Notice role="alert">无法读取代码工作区： {catalog.error.message}</Notice>}
      {catalog.isSuccess && workspaceId && !selected && (
        <Notice role="alert">
          The selected code workspace {workspaceId} is not registered on this application. Select an
          available workspace explicitly.
        </Notice>
      )}
      {catalog.isSuccess && sources.length === 0 && (
        <p>
          Create an example folder or add existing code in{" "}
          <a className="underline" href="#settings">
            Application settings
          </a>
          , then open it in VS Code. Retained results remain available.
        </p>
      )}
      {selected && !selected.available && (
        <Notice role="alert">
          {selected.name} is unavailable:{" "}
          {selected.unavailable_reason ?? "No executable local source is bound."}
        </Notice>
      )}
      {context === "analysis" && <p>切换代码工作区会清除比较预览，拟合前需重新检查数据。</p>}
    </div>
  );
}
