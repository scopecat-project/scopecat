import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import { secondaryButton } from "../../ui/styles";

export function CollectionRunLookup({
  collection,
  collectionName,
  projectId,
  unavailable,
  onOpenRun,
}: {
  collection?: string;
  collectionName?: string;
  projectId: string;
  unavailable: boolean;
  onOpenRun: (runId: string) => void;
}) {
  const [input, setInput] = useState("");
  const [number, setNumber] = useState<number>();
  const result = useQuery({
    queryKey: ["collection-run", projectId, collection, number],
    enabled: Boolean(collection && number && !unavailable),
    retry: false,
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/record-collections/{collection_id}/runs/{number}", {
          params: { path: { collection_id: collection!, number: number! } },
          signal,
        }),
      ),
  });
  return (
    <div
      className="space-y-2"
      title={`Find a number in ${collectionName ?? "the selected collection"}, independently of other filters.`}
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const value = Number(input);
          if (collection && /^[1-9]\d*$/.test(input) && Number.isSafeInteger(value)) {
            if (value === number) void result.refetch();
            else setNumber(value);
          }
        }}
      >
        <label className="flex flex-col gap-1">
          Collection run number{" "}
          <input
            aria-label="Collection run number"
            className="w-28 rounded border border-line p-2"
            value={input}
            type="text"
            inputMode="numeric"
            pattern="[1-9][0-9]*"
            required
            disabled={!collection || unavailable}
            onChange={(event) => {
              setInput(event.target.value);
              setNumber(undefined);
            }}
          />
        </label>
        <button
          className={secondaryButton}
          disabled={!collection || unavailable || !input || result.isFetching}
        >
          Find run
        </button>
      </form>
      <p className="text-xs text-text-dim">Collection only · ignores the filters below.</p>
      {number && !unavailable && result.isPending && (
        <p role="status">Finding collection run #{number}…</p>
      )}
      {number && result.error && (
        <p role="alert">
          Could not find collection run #{number}: {result.error.message}
        </p>
      )}
      {!unavailable && !result.error && result.data && (
        <button className={secondaryButton} onClick={() => onOpenRun(result.data.run_id)}>
          Open collection run #{result.data.number}
        </button>
      )}
    </div>
  );
}
