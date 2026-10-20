import { Button, Notice, Field, Select } from "../../ui/Controls";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

type Scope = components["schemas"]["PracticeScope"];

export function PracticePanel({ reachable }: { reachable: boolean }) {
  const catalog = useQuery({
    queryKey: ["practice"],
    queryFn: ({ signal }) => apiData(apiClient.GET("/api/v1/practice", { signal })),
    enabled: reachable,
  });
  const practices = catalog.data?.items.filter((scope) => scope.kind !== "notebook");
  if (!catalog.error && !practices?.length) return null;
  return (
    <section className="grid gap-3 rounded-lg border border-line bg-panel p-4">
      <h3 className="font-semibold">Earlier peak practices</h3>
      <p>
        Continue or clean up an existing practice. Start new learning from the Notebook courses
        above.
      </p>
      {catalog.error && <Notice role="alert">{catalog.error.message}</Notice>}
      {practices?.map((scope) => (
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
      {(error || scope.cleanup_error) && (
        <Notice role="alert">{error || scope.cleanup_error}</Notice>
      )}
      {scope.state === "cleaning" ? (
        <Button type="button" disabled={pending} onClick={() => void clear()}>
          {pending ? "Clearing…" : "Retry cleanup"}
        </Button>
      ) : (
        scope.state === "active" &&
        (confirming ? (
          <>
            <p>
              Stop this practice and remove its scans, analysis and decisions. Your other work stays
              available.
            </p>
            <Field>
              Practice files{" "}
              <Select
                value={choice}
                onChange={(event) => setChoice(event.target.value as "preserve" | "discard")}
                disabled={pending}
              >
                <option value="preserve">Keep notes and edited files</option>
                <option value="discard">Delete all files in this practice folder</option>
              </Select>
            </Field>
            <Button type="button" disabled={pending} onClick={() => void clear()}>
              {pending ? "Clearing…" : "Clear this practice"}
            </Button>
            <Button type="button" disabled={pending} onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button type="button" onClick={() => setConfirming(true)}>
            Clear practice…
          </Button>
        ))
      )}
    </article>
  );
}
