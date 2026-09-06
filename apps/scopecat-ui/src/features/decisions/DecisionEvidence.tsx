import { useQuery } from "@tanstack/react-query";
import type { ProcedureStepAttempt } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import {
  getRunParameterProposals,
  getOlderRunParameterProposals,
} from "../../data/parameter-proposals/api";
import { AnalysisPublicationView } from "../analyses/AnalysisPublicationView";
import { getProjectAnalysis, getProjectAnalysisArtifactDownload } from "../analyses/analysis-api";
import { getRunAnalysis, getRunArtifactDownload } from "../runs/run-api";
import { getSampleAnalysis, getSampleAnalysisArtifactDownload } from "../samples/sample-api";
import { parameterChanges, changeValue } from "./parameter-changes";
import { isRecord } from "./DecisionFields";

type Evidence = Extract<ProcedureStepAttempt["inputs"][number], { kind: "analysis" }>;

export function DecisionEvidence({ input }: { input: Evidence }) {
  const { subject, analysis_record_id: id } = input;
  const analysis = useQuery({
    queryKey: ["decision-evidence", subject, id],
    queryFn: ({ signal }) =>
      subject.kind === "run"
        ? getRunAnalysis(subject.run_id, id, signal)
        : subject.kind === "sample"
          ? getSampleAnalysis(subject.sample_id, id, signal)
          : getProjectAnalysis(id, signal),
  });
  const download = (selector: string) =>
    subject.kind === "run"
      ? getRunArtifactDownload(subject.run_id, selector)
      : subject.kind === "sample"
        ? getSampleAnalysisArtifactDownload(subject.sample_id, id, selector)
        : getProjectAnalysisArtifactDownload(id, selector);
  return (
    <section className="min-w-0 rounded-md border border-line p-3">
      <h3 className="text-sm">{analysis.data?.title ?? id}</h3>
      {analysis.isPending && <p>Loading evidence…</p>}
      {analysis.isError && <p role="alert">Evidence unavailable: {errorMessage(analysis.error)}</p>}
      {analysis.data && (
        <>
          {analysis.data.outputs
            .filter((output) => output.kind === "fact")
            .map((output) => (
              <section key={output.id} className="mb-3">
                <h4 className="text-sm">{output.title}</h4>
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
                  {Object.entries(
                    isRecord(output.content.value)
                      ? output.content.value
                      : { value: output.content.value },
                  ).map(([name, value]) => (
                    <div key={name} className="contents">
                      <dt>{name.replaceAll("_", " ")}</dt>
                      <dd className="m-0">
                        {typeof value === "boolean"
                          ? value
                            ? "Yes"
                            : "No"
                          : value == null
                            ? "Not available"
                            : typeof value === "object"
                              ? JSON.stringify(value)
                              : typeof value === "string"
                                ? value
                                : JSON.stringify(value)}
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            ))}
          {subject.kind === "run" &&
            analysis.data.outputs.some((output) => output.kind === "parameter_change_proposal") && (
              <DecisionProposals runId={subject.run_id} analysisId={id} />
            )}
          <AnalysisPublicationView
            analysis={{
              ...analysis.data,
              outputs: analysis.data.outputs.filter((output) => output.kind !== "fact"),
            }}
            getArtifactDownload={download}
          />
        </>
      )}
    </section>
  );
}

function DecisionProposals({ runId, analysisId }: { runId: string; analysisId: string }) {
  const proposals = useQuery({
    queryKey: ["decision-proposals", runId, analysisId],
    queryFn: async ({ signal }) => {
      let page = await getRunParameterProposals(runId, signal);
      const items = [...page.items];
      while (page.nextCursor !== undefined) {
        page = await getOlderRunParameterProposals(runId, page.nextCursor, signal);
        items.push(...page.items);
      }
      return items.filter((item) => item.analysisRecordId === analysisId);
    },
  });
  if (proposals.isError)
    return <p role="alert">Parameter changes unavailable: {errorMessage(proposals.error)}</p>;
  if (proposals.isPending) return <p>Loading parameter changes…</p>;
  return (
    <>
      {proposals.data.map((proposal) => (
        <div key={proposal.id} className="mb-3 max-h-72 overflow-auto">
          <p className="text-sm">{proposal.reason}</p>
          <table
            className="w-full text-left text-xs [overflow-wrap:anywhere]"
            aria-label="Proposed parameter changes"
          >
            <thead>
              <tr>
                <th>Parameter</th>
                <th>Before</th>
                <th>Candidate</th>
              </tr>
            </thead>
            <tbody>
              {proposal.deltas
                .flatMap((delta) => parameterChanges(delta.parameterId, delta.before, delta.after))
                .map((delta) => (
                  <tr key={delta.path}>
                    <td>{delta.path}</td>
                    <td>{changeValue(delta.before)}</td>
                    <td>{changeValue(delta.after)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}
