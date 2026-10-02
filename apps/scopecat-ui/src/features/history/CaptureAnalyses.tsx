import type { components } from "../../api-schema";
import { apiClient, apiData } from "../../api-client";
import { AnalysisPublicationView } from "../analyses/AnalysisPublicationView";
import { analysisOutput } from "../analyses/analysis-model";

export function CaptureAnalyses({
  contentHash,
  analyses,
  onOpenRun,
}: {
  contentHash: string;
  analyses: components["schemas"]["AnalysisEvidence"][];
  onOpenRun: (runId: string) => void;
}) {
  return (
    <section className="space-y-3" aria-label="Retained analyses">
      <h4 className="font-semibold">Retained analyses</h4>
      {analyses.length === 0 && <p>No analysis publications in this file.</p>}
      {analyses.map(({ record, entry, published_at }) => (
        <details key={entry.content_hash}>
          <summary>
            {record.title} · revision {record.revision}
          </summary>
          <p className="text-sm text-text-dim">
            {record.subject.kind === "run" ? `Run: ${record.subject.run_id}` : record.subject.kind}
          </p>
          <AnalysisPublicationView
            analysis={{
              id: entry.id,
              title: record.title,
              key: record.key ?? undefined,
              stepId: record.step_id ?? undefined,
              revision: record.revision,
              publicationHash: record.publication_hash,
              publishedAt: published_at,
              subject: record.subject.kind,
              inputs: record.inputs ?? [],
              executions: record.executions ?? [],
              outputs: record.outputs.map(analysisOutput),
            }}
            onOpenRun={onOpenRun}
            getArtifactDownload={async (artifactId) => {
              const blob = await apiData(
                apiClient.GET(
                  "/api/v1/data/captures/{content_hash}/analyses/{analysis_hash}/artifacts/{artifact_id}",
                  {
                    params: {
                      path: {
                        content_hash: contentHash,
                        analysis_hash: entry.content_hash,
                        artifact_id: artifactId,
                      },
                    },
                    parseAs: "blob",
                  },
                ),
              );
              const output = record.outputs.find(
                (item) => item.kind === "artifact" && item.content.artifact_id === artifactId,
              );
              return {
                blob,
                filename:
                  output?.kind === "artifact"
                    ? (output.content.filename ?? artifactId)
                    : artifactId,
              };
            }}
          />
        </details>
      ))}
    </section>
  );
}
