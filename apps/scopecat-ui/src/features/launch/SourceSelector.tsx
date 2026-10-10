import { secondaryButton } from "../../ui/styles";
import type { useAuthorWorkspaces } from "./source-api";

export function SourceSelector({
  catalog,
  workspaceId,
  onSelect,
}: {
  catalog: ReturnType<typeof useAuthorWorkspaces>;
  workspaceId: string;
  onSelect: (id: string) => void;
}) {
  const sources = catalog.data?.items ?? [];
  const selected = sources.find((source) => source.id === workspaceId);
  return (
    <div className="min-w-0 space-y-2">
      <label className="block">
        Code workspace{" "}
        <select
          aria-label="Code workspace"
          className="mt-1 block w-full min-w-0 border rounded p-2"
          value={workspaceId}
          onChange={(event) => onSelect(event.target.value)}
          disabled={catalog.isPending}
        >
          {!selected && (
            <option value={workspaceId}>
              {workspaceId
                ? `${workspaceId} ${catalog.isPending ? "(loading)" : "(unavailable)"}`
                : "Choose code"}
            </option>
          )}
          {sources.map((source) => (
            <option key={source.id} value={source.id} disabled={!source.available}>
              {source.name} · {source.id}
              {source.available ? "" : " (unavailable)"}
            </option>
          ))}
        </select>
      </label>
      <button
        className={secondaryButton}
        type="button"
        disabled={catalog.isFetching}
        onClick={() => void catalog.refetch()}
      >
        Refresh workspace list
      </button>
      {catalog.error && <p role="alert">Cannot read code workspaces: {catalog.error.message}</p>}
      {catalog.isSuccess && workspaceId && !selected && (
        <p role="alert">
          The selected code workspace {workspaceId} is not registered on this application. Select an
          available workspace explicitly.
        </p>
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
        <p role="alert">
          {selected.name} is unavailable:{" "}
          {selected.unavailable_reason ?? "No executable local source is bound."}
        </p>
      )}
      <p>
        Code selection belongs to this page. Switching workspaces retains the measurement context
        and clears old experiment inputs and previews.
      </p>
    </div>
  );
}
