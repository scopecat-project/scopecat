import { Field, Input, Button, Select, Notice } from "../../ui/Controls";
import { selectedBatch } from "./scientific-selection";
import { useId, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { getSamples } from "../samples/sample-api";
import { ScopeCatalog } from "../context/ScopeCatalog";
import { TargetPicker } from "./TargetPicker";
import { ParameterBranchPicker } from "./ParameterBranchPicker";
import { DeviceContextPicker } from "./DeviceContextPicker";
import type { LaunchDraft } from "./LaunchDraft";

export type MeasurementContextChange = Partial<Pick<LaunchDraft, "actor" | "collection">> & {
  selection?: Partial<LaunchDraft["selection"]>;
};

export function MeasurementContext({
  draft,
  projectId,
  onChange,
}: {
  draft: LaunchDraft;
  projectId?: string;
  onChange: (changes: MeasurementContextChange) => void;
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
  const configuration = draft.selection.configuration;
  const selectedSample = subject.kind === "sample" ? subject.sample_id : "";
  const batch = selectedBatch(draft.selection);
  const subjectLocked = subject.kind === "registered_target" && !choosingSubject;
  function changeSample(sample_id: string) {
    onChange({
      selection: {
        subject: sample_id ? { kind: "sample", sample_id } : { kind: "unbound" },
      },
    });
  }
  return (
    <fieldset
      disabled={draft.pending === "submit"}
      className="border border-line rounded p-3 space-y-3"
    >
      <legend className="font-semibold">Measurement context · this page</legend>
      <ParameterBranchPicker
        value={configuration}
        projectId={projectId}
        disabled={draft.pending === "submit"}
        onChange={(choice) => onChange({ selection: { configuration: choice } })}
      />
      {configuration.kind === "parameters" && (
        <DeviceContextPicker
          value={configuration}
          projectId={projectId}
          disabled={draft.pending === "submit"}
          onChange={(choice) => onChange({ selection: { configuration: choice } })}
        />
      )}
      <div className="flex flex-wrap gap-3">
        <Field>
          Sample ID{" "}
          <Input
            aria-label="Sample ID"
            list={listId}
            disabled={subjectLocked}
            value={selectedSample}
            onChange={(event) => changeSample(event.target.value)}
          />
        </Field>
        <datalist id={listId}>
          {samples.data?.pages
            .flatMap((page) => page.items)
            .map((sample) => (
              <option key={sample.record.id} value={sample.record.id}>
                {sample.revision.content.display_name}
              </option>
            ))}
        </datalist>
        <Field>
          Operator{" "}
          <Input
            aria-label="Operator"
            value={draft.actor}
            onChange={(event) => onChange({ actor: event.target.value })}
          />
        </Field>
      </div>
      <ScopeCatalog
        kind="collection"
        owner={projectId}
        value={draft.collection}
        onChange={(collection) => onChange({ collection })}
      />
      <p className="text-sm text-text-dim">
        New runs keep this collection. Named collections number independently from 1; default
        collection numbers may have gaps.
      </p>
      <TargetPicker
        value={subject.kind === "registered_target" ? subject.ref : undefined}
        projectId={projectId}
        browse={browse}
        disabled={draft.pending === "submit"}
        onChange={(ref) => {
          setChoosingSubject(false);
          onChange({
            selection: { subject: { kind: "registered_target", ref } },
          });
        }}
      />
      {subject.kind === "registered_target" && !choosingSubject && (
        <Button
          type="button"
          onClick={() => {
            setChoosingSubject(true);
            setBrowse(true);
          }}
        >
          Choose another subject
        </Button>
      )}
      <Button type="button" aria-expanded={browse} onClick={() => setBrowse(!browse)}>
        {browse ? "Hide context choices" : "Browse samples, batches and collections"}
      </Button>
      {browse && (
        <>
          <Field className="flex-col">
            Registered sample
            <Select
              aria-label="Registered sample"
              disabled={subjectLocked}
              value={selectedSample}
              onChange={(event) => changeSample(event.target.value)}
            >
              <option value="">No sample selected</option>
              {selectedSample &&
                !samples.data?.pages.some((page) =>
                  page.items.some((sample) => sample.record.id === selectedSample),
                ) && <option value={selectedSample}>{selectedSample}</option>}
              {samples.data?.pages
                .flatMap((page) => page.items)
                .map((sample) => (
                  <option key={sample.record.id} value={sample.record.id}>
                    {sample.revision.content.display_name} · {sample.record.id}
                  </option>
                ))}
            </Select>
          </Field>
          {samples.error && (
            <Notice role="alert">
              {samples.error.message}{" "}
              <Button type="button" onClick={() => void samples.refetch()}>
                Retry sample list
              </Button>
            </Notice>
          )}
          {samples.hasNextPage && (
            <Button
              type="button"
              disabled={samples.isFetchingNextPage}
              onClick={() => void samples.fetchNextPage()}
            >
              Load more samples
            </Button>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <ScopeCatalog
              kind="batch"
              owner={projectId}
              value={batch}
              onChange={(id) =>
                onChange({
                  selection: {
                    batch: id ? { kind: "declared", id } : { kind: "unscoped" },
                  },
                })
              }
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
