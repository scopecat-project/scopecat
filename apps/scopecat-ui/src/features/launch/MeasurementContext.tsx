import { useId, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { getSamples } from "../samples/sample-api";
import { ScopeCatalog } from "../context/ScopeCatalog";
import type { ConfigContextResolution } from "../config/config-api";
import type { LaunchDraft } from "./LaunchDraft";

export function MeasurementContext({
  draft,
  selectedContext,
  projectId,
  onChange,
}: {
  draft: LaunchDraft;
  selectedContext?: ConfigContextResolution;
  projectId?: string;
  onChange: (
    changes: Partial<Pick<LaunchDraft, "sample" | "actor" | "batch" | "collection">>,
  ) => void;
}) {
  const [browse, setBrowse] = useState(Boolean(draft.batch || draft.collection));
  const listId = useId();
  const samples = useInfiniteQuery({
    queryKey: ["context-samples", projectId],
    enabled: browse,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => getSamples(pageParam, signal),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const binding = selectedContext?.config_source.sample ?? draft.sampleBinding;
  return (
    <fieldset disabled={draft.pending} className="border border-line rounded p-3 space-y-3">
      <legend className="font-semibold">Measurement context · this page</legend>
      <div className="flex flex-wrap gap-3">
        <label>
          Sample ID{" "}
          <input
            aria-label="Sample ID"
            list={listId}
            disabled={Boolean(binding)}
            value={binding?.sample_id ?? draft.sample}
            onChange={(event) => onChange({ sample: event.target.value })}
            className="border rounded p-2"
          />
        </label>
        <datalist id={listId}>
          {samples.data?.pages
            .flatMap((page) => page.items)
            .map((sample) => (
              <option key={sample.record.id} value={sample.record.id}>
                {sample.revision.content.display_name}
              </option>
            ))}
        </datalist>
        <label>
          Operator{" "}
          <input
            aria-label="Operator"
            value={draft.actor}
            onChange={(event) => onChange({ actor: event.target.value })}
            className="border rounded p-2"
          />
        </label>
      </div>
      {binding && (
        <p>
          {binding.display_name} · revision {binding.revision}. Sample and batch are bound to this
          working point or saved plan.
        </p>
      )}
      <button type="button" aria-expanded={browse} onClick={() => setBrowse(!browse)}>
        {browse ? "Hide context choices" : "Browse samples, batches and collections"}
      </button>
      {browse && (
        <>
          <label className="flex flex-col gap-1">
            Registered sample
            <select
              aria-label="Registered sample"
              disabled={Boolean(binding)}
              value={binding?.sample_id ?? draft.sample}
              onChange={(event) => onChange({ sample: event.target.value })}
              className="border rounded p-2"
            >
              <option value="">No sample selected</option>
              {(binding?.sample_id ?? draft.sample) &&
                !samples.data?.pages.some((page) =>
                  page.items.some(
                    (sample) => sample.record.id === (binding?.sample_id ?? draft.sample),
                  ),
                ) && (
                  <option value={binding?.sample_id ?? draft.sample}>
                    {binding?.display_name ?? draft.sample}
                  </option>
                )}
              {samples.data?.pages
                .flatMap((page) => page.items)
                .map((sample) => (
                  <option key={sample.record.id} value={sample.record.id}>
                    {sample.revision.content.display_name} · {sample.record.id}
                  </option>
                ))}
            </select>
          </label>
          {samples.error && (
            <p role="alert">
              {samples.error.message}{" "}
              <button type="button" onClick={() => void samples.refetch()}>
                Retry sample list
              </button>
            </p>
          )}
          {samples.hasNextPage && (
            <button
              type="button"
              disabled={samples.isFetchingNextPage}
              onClick={() => void samples.fetchNextPage()}
            >
              Load more samples
            </button>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <ScopeCatalog
              kind="batch"
              owner={projectId}
              value={draft.batch}
              disabled={Boolean(binding)}
              onChange={(batch) => onChange({ batch })}
            />
            <ScopeCatalog
              kind="collection"
              owner={projectId}
              value={draft.collection}
              onChange={(collection) => onChange({ collection })}
            />
          </div>
        </>
      )}
      {!browse && (
        <p>
          {draft.batch ? `Batch: ${draft.batch}` : "Batch unspecified"} ·{" "}
          {draft.collection ? `Collection: ${draft.collection}` : "Default record collection"}
        </p>
      )}
      <p className="text-sm">
        Kept when switching experiments in this page. Preview validates the selection; other pages
        and admitted measurements keep their own context.
      </p>
    </fieldset>
  );
}
