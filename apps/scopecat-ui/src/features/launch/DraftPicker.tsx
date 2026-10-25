import { draftLocation } from "./draft-location";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLaunchDraft } from "./LaunchDraft";
import { listDrafts } from "./launch-recovery";
import { Button, Input, Select, Field, ActionRow } from "../../ui/Controls";

export function DraftPicker() {
  const { projectId, draft, recovery, copyDraft, switchDraft, renameDraft } = useLaunchDraft();
  const [renaming, setRenaming] = useState(false);
  const drafts = useInfiniteQuery({
    queryKey: ["launch-draft-heads", projectId, draft?.workspaceId, draft?.experiment],
    enabled: Boolean(draft?.experiment && draft.workspaceId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) => listDrafts(draft!.workspaceId, draft!.experiment, pageParam),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchOnWindowFocus: true,
  });
  if (!draft?.experiment) return null;
  const records = drafts.data?.pages.flatMap((page) => page.items) ?? [];
  const id = draft.draftId ?? "default";
  return (
    <section aria-label="独立实验草稿" className="min-w-0 space-y-1">
      <ActionRow>
        <Field>
          可编辑草稿
          <Select
            value={id}
            disabled={!recovery.ready || recovery.writing}
            onFocus={() => {
              void drafts.refetch();
            }}
            onChange={(event) => {
              const record = records.find(
                (item) => (item.target.draft_id ?? "default") === event.target.value,
              );
              if (record) {
                setRenaming(false);
                void switchDraft(record);
              }
            }}
          >
            <option value={id}>{draft.draftName ?? "Draft"}</option>
            {records
              .filter((record) => (record.target.draft_id ?? "default") !== id)
              .map((record) => (
                <option
                  key={record.target.draft_id ?? "default"}
                  value={record.target.draft_id ?? "default"}
                >
                  {record.input.draft_name ?? "Draft"} · {record.target.draft_id ?? "default"}
                </option>
              ))}
          </Select>
        </Field>
        <Button
          aria-label="复制为新草稿"
          disabled={!recovery.ready || recovery.writing}
          onClick={() => {
            void copyDraft().then(() => drafts.refetch());
          }}
        >
          复制
        </Button>
        <Button
          aria-label="重命名草稿"
          disabled={!recovery.ready}
          onClick={() => setRenaming(!renaming)}
        >
          重命名
        </Button>
        {drafts.hasNextPage && (
          <Button
            onClick={() => {
              void drafts.fetchNextPage();
            }}
          >
            更多草稿
          </Button>
        )}
      </ActionRow>
      {renaming && (
        <form
          key={`${draft.workspaceId}:${draft.experiment}:${id}`}
          onSubmit={(event) => {
            event.preventDefault();
            if (!recovery.ready) return;
            const name = new FormData(event.currentTarget).get("name");
            if (typeof name === "string" && name.trim()) {
              renameDraft(name);
              setRenaming(false);
            }
          }}
        >
          <ActionRow>
            <Field>
              草稿名称{" "}
              <Input
                name="name"
                disabled={!recovery.ready}
                defaultValue={draft.draftName ?? "Draft"}
                maxLength={200}
                required
              />
            </Field>
            <Button type="submit" disabled={!recovery.ready}>
              保存名称
            </Button>
          </ActionRow>
        </form>
      )}
      {recovery.ready && !recovery.writing && (
        <a
          className="text-xs underline"
          href={(() => {
            const url = draftLocation(draft);
            url.hash = "launch";
            url.searchParams.set("preparation", "1");
            url.searchParams.delete("workbench");
            return url.pathname + url.search + url.hash;
          })()}
        >
          Reopen this saved draft
        </a>
      )}
      <details className="text-xs text-text-dim">
        <summary className="cursor-pointer">Draft details</summary>
        <p>Draft edits stay separate from frozen submissions; copies never start acquisition.</p>
      </details>
      {drafts.error && (
        <p role="alert">
          Saved drafts unavailable. Your current input remains here.{" "}
          <Button
            onClick={() => {
              void drafts.refetch();
            }}
          >
            Retry draft list
          </Button>
        </p>
      )}
    </section>
  );
}
