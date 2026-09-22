import { selectedBatch, subjectSample } from "./scientific-selection";
import { useId, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { getSamples } from "../samples/sample-api";
import { ScopeCatalog } from "../context/ScopeCatalog";
import type { ConfigContextResolution } from "../config/config-api";
import { TargetPicker } from "./TargetPicker";
import { ParameterBranchPicker } from "./ParameterBranchPicker";
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
  onChange: (changes: Partial<Pick<LaunchDraft, "selection" | "actor" | "collection">>) => void;
}) {
  const [browse, setBrowse] = useState(Boolean(selectedBatch(draft.selection) || draft.collection));
  const [choosingSubject, setChoosingSubject] = useState(false);
  const listId = useId();
  const samples = useInfiniteQuery({
    queryKey: ["context-samples", projectId],
    enabled: browse,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => getSamples(pageParam, signal),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const subject = draft.selection.subject;
  const savedSelection = draft.plan?.definition.selection;
  const configuration = draft.selection.configuration;
  const matchingContext =
    configuration.kind === "working_point" &&
    selectedContext?.config_source.context.entry_id === configuration.ref.entry_id &&
    selectedContext.config_source.context.content_hash === configuration.ref.content_hash;
  const sameWorkingPoint =
    configuration.kind === "working_point" &&
    savedSelection?.configuration?.kind === "working_point" &&
    configuration.ref.entry_id === savedSelection.configuration.ref.entry_id &&
    configuration.ref.content_hash === savedSelection.configuration.ref.content_hash;
  const binding = matchingContext
    ? selectedContext.config_source.sample
    : sameWorkingPoint
      ? subjectSample(draft.plan?.definition.scientific_binding)
      : undefined;
  const selectedSample = subject.kind === "sample" ? subject.sample_id : "";
  const batch = selectedBatch(draft.selection);
  const subjectLocked =
    Boolean(binding) || (subject.kind === "registered_target" && !choosingSubject);
  function changeSample(sample_id: string) {
    onChange({
      selection: {
        ...draft.selection,
        subject: sample_id ? { kind: "sample", sample_id } : { kind: "unbound" },
      },
    });
  }
  return (
    <fieldset disabled={draft.pending} className="border border-line rounded p-3 space-y-3">
      <legend className="font-semibold">Measurement context · this page</legend>
      <ParameterBranchPicker
        value={configuration}
        projectId={projectId}
        disabled={draft.pending}
        onChange={(choice) =>
          onChange({ selection: { ...draft.selection, configuration: choice } })
        }
      />
      <div className="flex flex-wrap gap-3">
        <label>
          Sample ID{" "}
          <input
            aria-label="Sample ID"
            list={listId}
            disabled={subjectLocked}
            value={binding?.sample_id ?? selectedSample}
            onChange={(event) => changeSample(event.target.value)}
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
      <TargetPicker
        value={subject.kind === "registered_target" ? subject.ref : undefined}
        projectId={projectId}
        browse={browse}
        disabled={draft.pending}
        onChange={(ref) => {
          setChoosingSubject(false);
          onChange({
            selection: { ...draft.selection, subject: { kind: "registered_target", ref } },
          });
        }}
      />
      {subject.kind === "registered_target" && !binding && !choosingSubject && (
        <button
          type="button"
          onClick={() => {
            setChoosingSubject(true);
            setBrowse(true);
          }}
        >
          Choose another subject
        </button>
      )}
      {binding && (
        <p>
          {binding.display_name} · revision {binding.revision}. Sample and batch are bound to this
          working point.
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
              disabled={subjectLocked}
              value={binding?.sample_id ?? selectedSample}
              onChange={(event) => changeSample(event.target.value)}
              className="border rounded p-2"
            >
              <option value="">No sample selected</option>
              {(binding?.sample_id ?? selectedSample) &&
                !samples.data?.pages.some((page) =>
                  page.items.some(
                    (sample) => sample.record.id === (binding?.sample_id ?? selectedSample),
                  ),
                ) && (
                  <option value={binding?.sample_id ?? selectedSample}>
                    {binding?.display_name ?? selectedSample}
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
              value={batch}
              disabled={Boolean(binding)}
              onChange={(id) =>
                onChange({
                  selection: {
                    ...draft.selection,
                    batch: id ? { kind: "declared", id } : { kind: "unscoped" },
                  },
                })
              }
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
          {batch ? `Batch: ${batch}` : "Batch unspecified"} ·{" "}
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
