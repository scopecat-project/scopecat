import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import type { RunAnalysis } from "../../types";
import { errorMessage } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";

type Request = components["schemas"]["AuthorAnalysisRequest"];

export function RepeatRunAnalysis({ runId, analysis }: { runId: string; analysis: RunAnalysis }) {
  const client = useQueryClient();
  const settings = analysis.outputs.find(
    (output) => output.id === "author_analysis_request" && output.kind === "artifact",
  );
  const repeat = useMutation({
    mutationFn: async () => {
      if (settings?.kind !== "artifact") throw new Error("Saved analysis settings unavailable.");
      const saved = await apiData(
        apiClient.GET("/api/v1/runs/{run_id}/artifacts/{selector}/text", {
          params: {
            path: { run_id: runId, selector: settings.content.artifact_id },
            query: { expected_kind: "analysis_artifact" },
          },
        }),
      );
      if (saved.artifact.content_hash !== settings.content.content_hash)
        throw new Error("Saved analysis settings do not match this publication.");
      const request = repeatRequest(saved.content, runId, analysis);
      return apiData(apiClient.POST("/api/v1/author-revisions/analyze", { body: request }));
    },
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["analyses", "related"] }),
        client.invalidateQueries({ queryKey: ["analyses", "run", runId] }),
        client.invalidateQueries({ queryKey: ["run-contents", runId] }),
      ]);
    },
    retry: false,
  });
  if (!settings) return null;
  return (
    <section
      className="mb-3 rounded border border-line bg-panel-soft p-3 text-sm"
      aria-label="Repeat saved analysis"
    >
      <p className="mb-2 text-text-soft">
        Analyze this run again with the saved settings and author code version. Existing results
        stay available; identical results may reuse the saved publication.
      </p>
      <button
        className={secondaryButton}
        disabled={repeat.isPending}
        onClick={() => repeat.mutate()}
        type="button"
      >
        {repeat.isPending ? "Analyzing…" : "Repeat with saved settings"}
      </button>
      {repeat.data && (
        <p className="mt-2" role="status">
          Analysis finished.{" "}
          <a
            className="text-accent underline"
            href={`?run=${encodeURIComponent(runId)}&run-analysis=${encodeURIComponent(repeat.data.analysis_id)}#runs`}
          >
            View saved result
          </a>
        </p>
      )}
      {repeat.isError && (
        <p className="mt-2 text-red" role="alert">
          {errorMessage(repeat.error)} Check analysis history before trying again if the response
          was lost. No automatic retry was submitted.
        </p>
      )}
    </section>
  );
}

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function repeatRequest(text: string, runId: string, analysis: RunAnalysis): Request {
  const request: unknown = JSON.parse(text);
  const fact = (id: string) => {
    const output = analysis.outputs.find((item) => item.id === id);
    return output?.kind === "fact" ? output.content.value : undefined;
  };
  if (
    !object(request) ||
    request.run_id !== runId ||
    typeof request.analysis !== "string" ||
    !request.analysis.includes(":") ||
    typeof request.workspace_id !== "string" ||
    request.workspace_id !== fact("author_workspace") ||
    !object(request.code_revision) ||
    typeof request.code_revision.content_hash !== "string" ||
    request.code_revision.content_hash !== fact("author_code_revision") ||
    !object(request.arguments) ||
    request.grouping != null ||
    request.measurement_slice != null ||
    (request.key != null && typeof request.key !== "string")
  ) {
    throw new Error(
      "This publication does not contain matching settings for a whole-run analysis.",
    );
  }
  return {
    run_id: runId,
    workspace_id: request.workspace_id,
    code_revision: { content_hash: request.code_revision.content_hash },
    analysis: request.analysis,
    arguments: request.arguments,
    key: request.key,
  };
}
