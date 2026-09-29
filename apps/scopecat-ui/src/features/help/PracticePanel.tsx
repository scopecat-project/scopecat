import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

type Scope = components["schemas"]["PracticeScope"];

export function PracticePanel({ reachable }: { reachable: boolean }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const catalog = useQuery({
    queryKey: ["practice"],
    queryFn: ({ signal }) => apiData(apiClient.GET("/api/v1/practice", { signal })),
    enabled: reachable,
  });
  async function start() {
    setPending(true);
    setError("");
    try {
      await apiData(
        apiClient.POST("/api/v1/practice", {
          body: { request_key: requestKey, lesson: "manual-peaks" },
        }),
      );
      setRequestKey(crypto.randomUUID());
      await catalog.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  return (
    <section className="grid gap-3 rounded-lg border border-line bg-panel p-4">
      <h3 className="font-semibold">Practice without devices</h3>
      <p>
        Scan a synthetic response, read its curve and enter a peak frequency. Use the same task and
        Decisions pages as an experiment. No device connection or separate application is needed.
      </p>
      <button type="button" disabled={!reachable || pending} onClick={() => void start()}>
        {pending ? "Starting…" : "Start peak practice"}
      </button>
      {(error || catalog.error) && <p role="alert">{error || catalog.error?.message}</p>}
      {catalog.data?.items.map((scope) => (
        <PracticeCard key={scope.id} scope={scope} refresh={() => catalog.refetch()} />
      ))}
    </section>
  );
}

function PracticeCard({ scope, refresh }: { scope: Scope; refresh: () => Promise<unknown> }) {
  const [choice, setChoice] = useState<"preserve" | "discard">("preserve");
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function clear() {
    setPending(true);
    setError("");
    try {
      await apiData(
        apiClient.POST("/api/v1/practice/{scope_id}/clear", {
          params: { path: { scope_id: scope.id } },
          body: { files: scope.file_disposition ?? choice },
        }),
      );
      setConfirming(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      await refresh();
      setPending(false);
    }
  }
  return (
    <article className="grid gap-2 border-t border-line pt-3">
      <h4 className="font-semibold">{scope.title}</h4>
      <p>
        {scope.state === "cleared"
          ? "Practice cleared"
          : scope.state === "cleaning"
            ? "Cleanup unfinished — new work is blocked"
            : "Ready to continue"}
      </p>
      {scope.state === "active" && scope.procedure_id && (
        <>
          <a
            className="underline"
            href={`?procedure=${encodeURIComponent(scope.procedure_id)}#launch`}
          >
            Open practice task
          </a>
          <a
            className="underline"
            href={`?procedure=${encodeURIComponent(scope.procedure_id)}#decisions`}
          >
            Review curve and enter frequency
          </a>
          <p>
            You can close this page and continue later. Reopening the task does not repeat the scan.
          </p>
        </>
      )}
      {scope.file_disposition !== "discard" && (
        <>
          <p>
            {scope.state === "cleared" ? "Files kept: " : "Optional notes in VS Code: "}
            <code>{scope.directory}</code>
          </p>
          <a className="underline" href={`/api/v1/practice/${encodeURIComponent(scope.id)}/files`}>
            Export practice files
          </a>
        </>
      )}
      {(error || scope.cleanup_error) && <p role="alert">{error || scope.cleanup_error}</p>}
      {scope.state === "cleaning" ? (
        <button type="button" disabled={pending} onClick={() => void clear()}>
          {pending ? "Clearing…" : "Retry cleanup"}
        </button>
      ) : (
        scope.state === "active" &&
        (confirming ? (
          <>
            <p>
              Stop this practice and remove its scans, analysis and decisions. Your other work stays
              available.
            </p>
            <label>
              Practice files{" "}
              <select
                value={choice}
                onChange={(event) => setChoice(event.target.value as "preserve" | "discard")}
                disabled={pending}
              >
                <option value="preserve">Keep notes and edited files</option>
                <option value="discard">Delete all files in this practice folder</option>
              </select>
            </label>
            <button type="button" disabled={pending} onClick={() => void clear()}>
              {pending ? "Clearing…" : "Clear this practice"}
            </button>
            <button type="button" disabled={pending} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </>
        ) : (
          <button type="button" onClick={() => setConfirming(true)}>
            Clear practice…
          </button>
        ))
      )}
    </article>
  );
}
