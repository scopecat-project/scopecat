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
      <legend className="font-semibold">本页测量上下文</legend>
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
          样品 ID{" "}
          <Input
            aria-label="样品 ID"
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
          操作人{" "}
          <Input
            aria-label="操作人"
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
        新 run 保留此记录集。命名记录集各自从 1 编号；默认记录集编号可能不连续。
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
          选择其他对象
        </Button>
      )}
      <Button type="button" aria-expanded={browse} onClick={() => setBrowse(!browse)}>
        {browse ? "收起上下文选择" : "浏览样品、批次与记录集"}
      </Button>
      {browse && (
        <>
          <Field className="flex-col">
            已注册样品
            <Select
              aria-label="已注册样品"
              disabled={subjectLocked}
              value={selectedSample}
              onChange={(event) => changeSample(event.target.value)}
            >
              <option value="">未选择样品</option>
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
                重试加载样品
              </Button>
            </Notice>
          )}
          {samples.hasNextPage && (
            <Button
              type="button"
              disabled={samples.isFetchingNextPage}
              onClick={() => void samples.fetchNextPage()}
            >
              加载更多样品
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
          {batch ? `Batch: ${batch}` : "未指定批次"} ·{" "}
          {draft.collection ? `Collection: ${draft.collection}` : "默认记录集"}
        </p>
      )}
      <p className="text-sm">
        在本页切换实验时保留上下文。预览会验证选择；其他页面与已接纳测量保留各自上下文。
      </p>
    </fieldset>
  );
}
