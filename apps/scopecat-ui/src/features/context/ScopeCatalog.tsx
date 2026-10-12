import { secondaryButton } from "../../ui/styles";
import { useScopeCatalog } from "./scope-catalog";
import { RenameCollection } from "./RenameCollection";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

// Catalog state is shared; the selection is always owned by the calling page.
export function ScopeCatalog({
  kind,
  owner,
  value,
  onChange,
  disabled = false,
  emptyLabel,
  selectLabel,
  available = true,
}: {
  kind: "batch" | "collection";
  owner?: string;
  value?: string;
  onChange: (id: string | undefined) => void;
  disabled?: boolean;
  emptyLabel?: string;
  selectLabel?: string;
  available?: boolean;
}) {
  const label = kind === "batch" ? "Experimental batch" : "Record collection";
  const empty =
    emptyLabel ?? (kind === "batch" ? "Unspecified batch" : "Default record collection");
  const key = ["context-catalog", owner, kind];
  const cache = useQueryClient();
  const pages = useScopeCatalog(kind, owner, available);
  const items = pages.data?.pages.flatMap((page) => page.items) ?? [];
  const selected = useQuery({
    queryKey: [...key, value],
    enabled: available && Boolean(value && !items.some((item) => item.id === value)),
    queryFn: ({ signal }) =>
      kind === "batch"
        ? apiData(
            apiClient.GET("/api/v1/experimental-batches/{batch_id}", {
              params: { path: { batch_id: value! } },
              signal,
            }),
          )
        : apiData(
            apiClient.GET("/api/v1/record-collections/{collection_id}", {
              params: { path: { collection_id: value! } },
              signal,
            }),
          ),
  });
  const current = items.find((item) => item.id === value) ?? selected.data;
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const operation = useRef<{ name: string; id: string } | undefined>(undefined);
  async function create() {
    const title = name.trim();
    if (!title || pending) return;
    // Keep the same ID after a lost response so retry cannot create a duplicate.
    if (operation.current?.name !== title)
      operation.current = { name: title, id: crypto.randomUUID() };
    const id = operation.current.id;
    setPending(true);
    setError("");
    try {
      const body = { name: title, description: "", expected_revision: 0 };
      const saved =
        kind === "batch"
          ? await apiData(
              apiClient.PUT("/api/v1/experimental-batches/{batch_id}", {
                params: { path: { batch_id: id } },
                body,
              }),
            )
          : await apiData(
              apiClient.PUT("/api/v1/record-collections/{collection_id}", {
                params: { path: { collection_id: id } },
                body,
              }),
            );
      cache.setQueryData([...key, saved.id], saved);
      await cache.invalidateQueries({ queryKey: key });
      // Creation only adds metadata. Selection is a separate, explicit action.
      setCreating(false);
      setName("");
      operation.current = undefined;
      setCreated(saved);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  const [created, setCreated] = useState<{ id: string; name: string }>();
  return (
    <div className="space-y-2 min-w-0">
      <label className="flex flex-col gap-1">
        {label}
        <select
          aria-label={selectLabel ?? label}
          title={value}
          value={value ?? ""}
          disabled={disabled || pending}
          onChange={(event) => onChange(event.target.value || undefined)}
          className="border rounded p-2"
        >
          <option value="">{empty}</option>
          {value && !items.some((item) => item.id === value) && (
            <option value={value}>{selected.data?.name ?? value}</option>
          )}
          {items.map((item) => (
            <option key={item.id} value={item.id} title={item.id}>
              {item.name}
              {items.some((other) => other.id !== item.id && other.name === item.name)
                ? ` · ${item.id}`
                : ""}
            </option>
          ))}
        </select>
      </label>
      {(error || pages.error || selected.error) && (
        <p role="alert">{error || pages.error?.message || selected.error?.message}</p>
      )}
      {pages.isPending && <p role="status">Loading {label.toLowerCase()} choices…</p>}
      {pages.hasNextPage && (
        <button
          className={secondaryButton}
          type="button"
          disabled={pages.isFetchingNextPage}
          onClick={() => void pages.fetchNextPage()}
        >
          Load more {kind === "batch" ? "batches" : "collections"}
        </button>
      )}
      {pages.isError && (
        <button className={secondaryButton} type="button" onClick={() => void pages.refetch()}>
          Retry {label.toLowerCase()} list
        </button>
      )}
      {kind === "collection" && current && (
        <RenameCollection key={current.id} collection={current} disabled={disabled || pending} />
      )}
      {created && (
        <p role="status">
          Created {created.name}.{" "}
          <button
            className={secondaryButton}
            type="button"
            disabled={disabled}
            onClick={() => {
              onChange(created.id);
              setCreated(undefined);
            }}
          >
            Use {created.name}
          </button>
        </p>
      )}
      {!disabled &&
        (creating ? (
          <div className="flex flex-wrap gap-2">
            <label>
              {label} name{" "}
              <input
                aria-label={`${label} name`}
                value={name}
                maxLength={200}
                disabled={pending}
                onChange={(event) => setName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void create();
                  }
                }}
                className="border rounded p-2"
              />
            </label>
            <button
              className={secondaryButton}
              type="button"
              disabled={pending || !name.trim()}
              onClick={() => void create()}
            >
              Create {kind === "batch" ? "batch" : "collection"}
            </button>
            <button
              className={secondaryButton}
              type="button"
              disabled={pending}
              onClick={() => setCreating(false)}
            >
              Cancel new {kind}
            </button>
          </div>
        ) : (
          <button className={secondaryButton} type="button" onClick={() => setCreating(true)}>
            New {kind === "batch" ? "batch" : "collection"}
          </button>
        ))}
    </div>
  );
}
