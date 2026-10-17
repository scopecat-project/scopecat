import { Notice, Button, Field, Select } from "../../ui/Controls";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { errorMessage } from "../../lib/presentation";
import {
  getTargetHeads,
  resolveTarget,
  targetRefKey,
  type TargetRevisionRef,
  type TargetRevision,
} from "./target-api";

function targetLabel(target: TargetRevision): string {
  return `${target.name} (${target.ref.target_id}) · revision ${target.ref.revision} · catalog ${target.ref.catalog_id}`;
}
function memberSummary(target: TargetRevision): string {
  return target.content.members
    .map((member) => `${member.id}: sample ${member.sample_id}, revision ${member.revision}`)
    .join("; ");
}

function unsupportedTarget(target: TargetRevision): string | undefined {
  if (target.content.members.length !== 1)
    return "Execution currently supports exactly one member; choose a single-member target.";
  if (target.content.connections.length !== 0)
    return "Execution does not yet support target connections; choose a target without connections.";
  return undefined;
}

export function TargetPicker({
  value,
  projectId,
  browse,
  disabled,
  onChange,
}: {
  value?: TargetRevisionRef;
  projectId?: string;
  browse: boolean;
  disabled?: boolean;
  onChange: (ref: TargetRevisionRef) => void;
}) {
  const heads = useInfiniteQuery({
    queryKey: ["target-catalog", projectId],
    enabled: browse,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => getTargetHeads(pageParam, signal),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const selected = useQuery({
    queryKey: ["target-revision", projectId, value],
    enabled: Boolean(value),
    queryFn: ({ signal }) => resolveTarget(value!, signal),
  });
  const items = heads.data?.pages.flatMap((page) => page.items) ?? [];
  const selectedKey = value ? targetRefKey(value) : "";
  const selectedIsHead = items.some((target) => targetRefKey(target.ref) === selectedKey);
  return (
    <div className="space-y-2">
      {value && (
        <div aria-label="Selected registered target" className="border border-line rounded p-2">
          <p>
            Registered target {value.target_id}, revision {value.revision} · catalog{" "}
            {value.catalog_id} · exact registered target retained.
          </p>
          {selected.data && (
            <p>
              {selected.data.name} · {memberSummary(selected.data)}
            </p>
          )}
          {selected.isPending && <p>Resolving the selected exact target revision…</p>}
          {selected.error && (
            <Notice role="alert">
              Cannot resolve this exact registered target: {errorMessage(selected.error)}{" "}
              <Button type="button" disabled={disabled} onClick={() => void selected.refetch()}>
                Retry target resolution
              </Button>
            </Notice>
          )}
          {selected.data && unsupportedTarget(selected.data) && (
            <Notice role="alert">{unsupportedTarget(selected.data)}</Notice>
          )}
        </div>
      )}
      {browse && (
        <>
          <Field className="flex-col">
            Registered target
            <Select
              aria-label="Registered target"
              disabled={disabled || heads.isPending}
              value={selectedKey}
              onChange={(event) => {
                const target = items.find((item) => targetRefKey(item.ref) === event.target.value);
                if (target && !unsupportedTarget(target)) onChange(target.ref);
              }}
            >
              <option value="" disabled>
                Choose an exact target revision
              </option>
              {value && !selectedIsHead && (
                <option value={selectedKey}>
                  {selected.data
                    ? targetLabel(selected.data)
                    : `${value.target_id} · revision ${value.revision} · catalog ${value.catalog_id}`}{" "}
                  (selected exact revision)
                </option>
              )}
              {items.map((target) => (
                <option
                  key={targetRefKey(target.ref)}
                  value={targetRefKey(target.ref)}
                  disabled={Boolean(unsupportedTarget(target))}
                >
                  {targetLabel(target)} · {memberSummary(target)}
                  {unsupportedTarget(target) ? ` · unsupported: ${unsupportedTarget(target)}` : ""}
                </option>
              ))}
            </Select>
          </Field>
          <Button
            type="button"
            disabled={disabled || heads.isFetching}
            onClick={() => void heads.refetch()}
          >
            Refresh target list
          </Button>
          {heads.hasNextPage && (
            <Button
              type="button"
              disabled={disabled || heads.isFetchingNextPage}
              onClick={() => void heads.fetchNextPage()}
            >
              Load more targets
            </Button>
          )}
          {heads.error && (
            <Notice role="alert">Cannot read target catalog: {errorMessage(heads.error)}</Notice>
          )}
          {heads.isSuccess && items.length === 0 && <p>No registered targets in this catalog.</p>}
          <p>
            Refreshing the catalog keeps the selected exact revision. Choosing another revision
            changes only this page's measurement subject; preview checks working point
            compatibility.
          </p>
        </>
      )}
    </div>
  );
}
