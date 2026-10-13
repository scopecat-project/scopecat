import { primaryButton, secondaryButton } from "../../ui/styles";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

export function RenameCollection({
  collection,
  disabled,
}: {
  collection: { id: string; name: string; description: string; revision: number };
  disabled: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(collection.name);
  const [revision, setRevision] = useState(collection.revision);
  const cache = useQueryClient();
  const save = useMutation({
    mutationFn: () =>
      apiData(
        apiClient.PUT("/api/v1/record-collections/{collection_id}", {
          params: { path: { collection_id: collection.id } },
          body: {
            name: name.trim(),
            description: collection.description,
            expected_revision: revision,
          },
        }),
      ),
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ["context-catalog"] });
      await cache.invalidateQueries({ queryKey: ["record-collection"] });
      setEditing(false);
    },
  });
  return (
    <div className="space-y-2">
      <details className="text-xs text-text-dim">
        <summary className="cursor-pointer">Collection identity</summary>
        <code className="break-all">{collection.id}</code>
      </details>
      {editing ? (
        <div className="flex flex-wrap gap-2">
          <label>
            New collection name{" "}
            <input
              aria-label="New collection name"
              maxLength={200}
              className="border rounded p-2"
              value={name}
              disabled={save.isPending || disabled}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <button
            className={primaryButton}
            type="button"
            disabled={disabled || save.isPending || !name.trim()}
            onClick={() => save.mutate()}
          >
            Save collection name
          </button>
          <button
            className={secondaryButton}
            type="button"
            disabled={save.isPending}
            onClick={() => {
              setEditing(false);
              save.reset();
            }}
          >
            Cancel rename
          </button>
          <p className="text-sm">Renaming keeps the collection ID and all admitted run numbers.</p>
        </div>
      ) : (
        <button
          className={secondaryButton}
          type="button"
          disabled={disabled}
          onClick={() => {
            setName(collection.name);
            setRevision(collection.revision);
            save.reset();
            setEditing(true);
          }}
        >
          Rename collection
        </button>
      )}
      {save.error && (
        <p role="alert">
          {save.error.message} Cancel this rename, refresh the collection, and reopen Rename
          collection before retrying a conflicting edit.
        </p>
      )}
    </div>
  );
}
