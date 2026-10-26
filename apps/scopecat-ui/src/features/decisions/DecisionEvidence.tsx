import { useState } from "react";
import {
  bindingProblem,
  boundPointValue,
  type PointBinding,
  type FigurePoint,
} from "./point-binding";
import { useQuery } from "@tanstack/react-query";
import type { ProcedureStepAttempt } from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import {
  getRunParameterProposals,
  getOlderRunParameterProposals,
} from "../../data/parameter-proposals/api";
import { AnalysisOutputView } from "../runs/AnalysisOutputView";
import { getProjectAnalysis, getProjectAnalysisArtifactDownload } from "../analyses/analysis-api";
import { getRunAnalysis, getRunArtifactDownload } from "../runs/run-api";
import { getSampleAnalysis, getSampleAnalysisArtifactDownload } from "../samples/sample-api";
import { proposalChanges, changeValue } from "./parameter-changes";
import { scientificChangeLabel } from "../config/quantity-change";
import { isRecord } from "./DecisionFields";

type Evidence = Extract<ProcedureStepAttempt["inputs"][number], { kind: "analysis" }>;

export function DecisionEvidence({
  input,
  binding,
  disabled = false,
  onPick,
}: {
  input: Evidence;
  binding?: PointBinding;
  disabled?: boolean;
  onPick?: (value: number) => boolean;
}) {
  const [selected, setSelected] = useState<FigurePoint>();
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
      {analysis.isPending && <p>正在加载判断依据…</p>}
      {analysis.isError && <p role="alert">无法读取判断依据： {errorMessage(analysis.error)}</p>}
      {analysis.data && analysis.data.id !== id && (
        <p role="alert">判断依据标识已变化。请重新加载此请求后再选点。</p>
      )}
      {binding &&
        analysis.data &&
        !analysis.data.outputs.some(
          (output) => output.id === binding.output && output.kind === "figure",
        ) && <p role="alert">声明的图表不可用。</p>}
      {analysis.data && analysis.data.id === id && (
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
                            ? "是"
                            : "否"
                          : value == null
                            ? "不可用"
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
          {analysis.data.outputs
            .filter((output) => output.kind === "figure" || output.kind === "table")
            .map((output) => (
              <section key={output.id} className="min-w-0">
                <h4 className="text-sm font-semibold">{output.title}</h4>
                {binding?.output === output.id && bindingProblem(binding, output) && (
                  <p role="alert">{bindingProblem(binding, output)}</p>
                )}
                {binding?.output === output.id && !bindingProblem(binding, output) && (
                  <p className="text-xs text-text-dim">
                    {disabled
                      ? "请先选择适用的判断，再从图中选点。"
                      : `点击图中的一个点填写 ${binding.field}${binding.unit ? ` (${binding.unit})` : ""}，核对调整后提交。`}
                  </p>
                )}
                <AnalysisOutputView
                  output={output}
                  getArtifactDownload={download}
                  showMetadata={false}
                  selectedPoint={!disabled && binding?.output === output.id ? selected : undefined}
                  onPoint={
                    !disabled && binding?.output === output.id && !bindingProblem(binding, output)
                      ? (point) => {
                          const value = boundPointValue(binding, output, point);
                          if (value !== undefined && onPick?.(value)) setSelected(point);
                        }
                      : undefined
                  }
                />
              </section>
            ))}
          {subject.kind === "run" &&
            analysis.data.outputs.some((output) => output.kind === "parameter_change_proposal") && (
              <DecisionProposals runId={subject.run_id} analysisId={id} />
            )}
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
    return <p role="alert">无法读取参数变更： {errorMessage(proposals.error)}</p>;
  if (proposals.isPending) return <p>正在加载参数变更…</p>;
  return (
    <>
      {proposals.data.map((proposal) => (
        <div key={proposal.id} className="mb-3 max-h-72 overflow-auto">
          <p className="text-sm">{proposal.reason}</p>
          <table
            className="w-full text-left text-xs [overflow-wrap:anywhere]"
            aria-label="候选参数变更"
          >
            <thead>
              <tr>
                <th>参数</th>
                <th>变更前</th>
                <th>候选值</th>
              </tr>
            </thead>
            <tbody>
              {proposal.deltas.flatMap(proposalChanges).map((delta) => (
                <tr key={delta.path}>
                  <td>
                    {delta.path}
                    <small className="block">
                      {delta.changeKind === "added" || delta.changeKind === "removed"
                        ? delta.changeKind === "added"
                          ? "新增"
                          : "移除"
                        : scientificChangeLabel(delta.changeKind ?? "physical")}
                    </small>
                  </td>
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
