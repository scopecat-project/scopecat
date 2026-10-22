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
    <section
      aria-label="Independent experiment drafts"
      className="space-y-2 border-b border-line pb-3"
    >
      <ActionRow>
        <Field>
          Editable draft
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
          disabled={!recovery.ready || recovery.writing}
          onClick={() => {
            void copyDraft().then(() => drafts.refetch());
          }}
        >
          Copy to new draft
        </Button>
        <Button disabled={!recovery.ready} onClick={() => setRenaming(!renaming)}>
          Rename draft
        </Button>
        {drafts.hasNextPage && (
          <Button
            onClick={() => {
              void drafts.fetchNextPage();
            }}
          >
            More drafts
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
              Draft name{" "}
              <Input
                name="name"
                disabled={!recovery.ready}
                defaultValue={draft.draftName ?? "Draft"}
                maxLength={200}
                required
              />
            </Field>
            <Button type="submit" disabled={!recovery.ready}>
              Save name
            </Button>
          </ActionRow>
        </form>
      )}
      <p className="text-xs text-text-dim">
        Edits belong to this draft. Submitted inputs stay frozen; copying never starts acquisition.
      </p>
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
