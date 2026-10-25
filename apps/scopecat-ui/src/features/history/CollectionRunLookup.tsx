import { Field, Input, Button, Notice } from "../../ui/Controls";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";

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
        <Field className="flex-col">
          记录集内 run 编号{" "}
          <Input
            aria-label="记录集内 run 编号"
            className="w-28"
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
        </Field>
        <Button disabled={!collection || unavailable || !input || result.isFetching}>
          查找 run
        </Button>
      </form>
      <p className="text-xs text-text-dim">仅在此记录集中查找，不受下方筛选影响。</p>
      {number && !unavailable && result.isPending && (
        <Notice role="status">正在查找记录集内 run #{number}…</Notice>
      )}
      {number && result.error && (
        <Notice role="alert">
          无法找到记录集内 run #{number}: {result.error.message}
        </Notice>
      )}
      {!unavailable && !result.error && result.data && (
        <Button onClick={() => onOpenRun(result.data.run_id)}>
          打开记录集内 run #{result.data.number}
        </Button>
      )}
    </div>
  );
}
