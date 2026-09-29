import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { secondaryButton } from "../../ui/styles";

type Preview = components["schemas"]["DataCleanupPreview"];
type Operation = components["schemas"]["DataCleanupOperation"];
type Selection = components["schemas"]["DataCleanupSelection"];

function including(selection: Selection, owner: string): Selection | undefined {
  const separator = owner.indexOf(":");
  const kind = owner.slice(0, separator);
  const identity = owner.slice(separator + 1);
  const field =
    kind === "run"
      ? "runs"
      : kind === "procedure"
        ? "procedures"
        : kind === "analysis"
          ? "analyses"
          : undefined;
  if (!field || selection[field].includes(identity)) return undefined;
  return { ...selection, [field]: [...selection[field], identity] };
}

export function ClearData({
  runs = [],
  procedures = [],
  analyses = [],
}: {
  runs?: string[];
  procedures?: string[];
  analyses?: string[];
}) {
  const cache = useQueryClient();
  const [preview, setPreview] = useState<Preview>();
  const [operation, setOperation] = useState<Operation>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [requestKey] = useState(() => crypto.randomUUID());
  async function inspect(
    selection: Selection = {
      runs,
      procedures,
      analyses,
      setups: [],
      setup_definitions: [],
      parameters: [],
    },
  ) {
    setPending(true);
    setError("");
    try {
      setPreview(
        await apiData(apiClient.POST("/api/v1/data-cleanup/preview", { body: selection })),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  async function clear() {
    if (!preview) return;
    setPending(true);
    setError("");
    try {
      setOperation(
        await apiData(
          apiClient.POST("/api/v1/data-cleanup", { body: { request_key: requestKey, preview } }),
        ),
      );
      await cache.invalidateQueries({ queryKey: ["data-cleanup"] });
      await Promise.all([
        cache.invalidateQueries({ queryKey: ["runs"] }),
        cache.invalidateQueries({ queryKey: ["research", "runs"] }),
        cache.invalidateQueries({ queryKey: ["analyses", "project"], exact: true }),
      ]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  if (operation) return <CleanupResult operation={operation} update={setOperation} />;
  return (
    <section className="grid gap-2">
      {!preview ? (
        <button
          className={secondaryButton}
          disabled={pending || !(runs.length || procedures.length || analyses.length)}
          onClick={() => void inspect()}
        >
          Review data cleanup…
        </button>
      ) : (
        <>
          <h3 className="font-semibold">Clear selected data</h3>
          <p>
            {preview.record_count} selected records and their stored files will be removed. This
            does not delete your code or device settings. Export or back up anything you want to
            retain before continuing.
          </p>
          <p>
            Files to reclaim:{" "}
            {(preview.bytes_to_reclaim / 1024).toLocaleString(undefined, {
              maximumFractionDigits: 1,
            })}{" "}
            KiB.
          </p>
          <ul>
            {[
              ...preview.selection.runs.map((id) => `Measurement: ${id}`),
              ...preview.selection.procedures.map((id) => `Task: ${id}`),
              ...preview.selection.analyses.map((id) => `Analysis: ${id}`),
            ].map((label) => (
              <li key={label}>
                <code>{label}</code>
              </li>
            ))}
          </ul>
          {preview.blockers.length > 0 && (
            <ul>
              {preview.blockers.map((item, index) => (
                <li key={index}>
                  <strong>{item.owner}</strong>: {item.reason}
                  {including(preview.selection, item.owner) && (
                    <button
                      className={secondaryButton}
                      disabled={pending}
                      onClick={() => {
                        const expanded = including(preview.selection, item.owner);
                        if (expanded) void inspect(expanded);
                      }}
                    >
                      Include this retaining record and review again
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <button
            className={secondaryButton}
            disabled={pending || !!preview.blockers.length}
            onClick={() => void clear()}
          >
            Delete selected records and files
          </button>
          <button
            className={secondaryButton}
            disabled={pending}
            onClick={() => setPreview(undefined)}
          >
            Cancel
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

function CleanupResult({
  operation,
  update,
}: {
  operation: Operation;
  update: (value: Operation) => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function resume() {
    setPending(true);
    try {
      update(
        await apiData(
          apiClient.POST("/api/v1/data-cleanup/{operation_id}/resume", {
            params: { path: { operation_id: operation.id } },
          }),
        ),
      );
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="grid gap-2">
      <p role="status">
        {operation.state === "complete"
          ? "Records and files cleared."
          : operation.state === "records_removed"
            ? "Records removed; file cleanup needs retry."
            : "Cleanup has not removed the selected records."}
      </p>
      {(error || operation.error) && <p role="alert">{error || operation.error}</p>}
      {operation.state !== "complete" && (
        <button className={secondaryButton} disabled={pending} onClick={() => void resume()}>
          {pending ? "Retrying…" : "Retry cleanup"}
        </button>
      )}
    </section>
  );
}

export function DataCleanupHistory() {
  const [open, setOpen] = useState(false);
  const history = useQuery({
    queryKey: ["data-cleanup"],
    queryFn: () => apiData(apiClient.GET("/api/v1/data-cleanup")),
    enabled: open,
  });
  return (
    <section className="grid gap-3 rounded border border-line p-3">
      <button className={secondaryButton} onClick={() => setOpen(!open)}>
        Data cleanup history
      </button>
      {open && (
        <>
          {history.error && <p role="alert">{history.error.message}</p>}
          {history.data?.length === 0 && (
            <p>No cleanup operations yet. Select a measurement and choose Review data cleanup.</p>
          )}
          {history.data?.map((operation) => (
            <article className="border-t border-line pt-2" key={operation.id}>
              <p>
                {operation.created_at ? new Date(operation.created_at).toLocaleString() : "Cleanup"}
              </p>
              <CleanupResult operation={operation} update={() => void history.refetch()} />
            </article>
          ))}
        </>
      )}
    </section>
  );
}
