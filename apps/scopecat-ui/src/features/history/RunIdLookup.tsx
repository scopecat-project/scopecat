import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../../api-client";
import { secondaryButton } from "../../ui/styles";
import { getRun } from "../runs/run-api";
import { RunAddressLabel } from "../runs/RunAddressLabel";
import { useRunCollectionName } from "../runs/use-run-collection-name";

// Matches the server's safe storage-reference grammar, including retained legacy
// IDs. A syntactically valid string is not evidence that a record exists.
export const validRunReference = (value: string) => /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value);

export function RunIdLookup({
  projectId,
  unavailable,
  names,
  onOpenRun,
}: {
  projectId: string;
  unavailable: boolean;
  names: Map<string, string>;
  onOpenRun: (id: string) => void;
}) {
  const [input, setInput] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [invalid, setInvalid] = useState(false);
  const result = useQuery({
    queryKey: ["run-id-lookup", projectId, submitted],
    enabled: Boolean(submitted && !unavailable),
    retry: false,
    queryFn: ({ signal }) => getRun(submitted, signal),
  });
  const collection = useRunCollectionName(
    projectId,
    unavailable ? undefined : result.data?.address?.collection_id,
    names,
  );
  const error = result.error;
  return (
    <section aria-label="Find by full run ID" className="min-w-0 rounded border border-line p-3">
      <details>
        <summary className="cursor-pointer text-sm">Find by full run ID</summary>
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const value = input.trim();
            setInvalid(!validRunReference(value));
            if (!validRunReference(value)) {
              setSubmitted("");
              return;
            }
            if (value === submitted) void result.refetch();
            else setSubmitted(value);
          }}
        >
          <label className="min-w-0 flex-1 basis-72">
            Full run ID
            <input
              aria-label="Full run ID"
              className="mt-1 block w-full min-w-0 rounded border border-line p-2"
              value={input}
              onChange={(event) => {
                setInput(event.target.value);
                setSubmitted("");
                setInvalid(false);
              }}
              placeholder="Paste the complete ID"
            />
          </label>
          <button className={secondaryButton} disabled={unavailable || result.isFetching}>
            Find exact run
          </button>
        </form>
        <p className="mt-1 text-xs text-text-dim">
          Current data space only · ignores collection and list filters.
        </p>
        {invalid && (
          <p role="alert">Invalid run ID format. Paste a complete ID, not a URL or path.</p>
        )}
        {unavailable ? (
          <p role="status">Run lookup unavailable: reconnect to this data space.</p>
        ) : (
          submitted && (
            <>
              {(result.isPending || result.isFetching) && <p role="status">Finding run…</p>}
              {error && !result.isFetching && (
                <p role="alert">
                  {error instanceof ApiError && error.status === 404
                    ? "Run not found in this data space. Check the complete ID and data space."
                    : error instanceof ApiError && (error.status === 400 || error.status === 422)
                      ? `Invalid run reference: ${error.message}`
                      : `Run lookup unavailable: ${error.message}`}
                </p>
              )}
              {result.data && !error && !result.isFetching && (
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <RunAddressLabel
                    address={result.data.address}
                    sequence={result.data.sequence}
                    collectionName={collection.name}
                  />
                  <button className={secondaryButton} onClick={() => onOpenRun(result.data.runId)}>
                    Open found run
                  </button>
                </div>
              )}
            </>
          )
        )}
      </details>
    </section>
  );
}
