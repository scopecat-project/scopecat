import type { AnalysisExecutionOutputReference } from "../../api-contract";
import { titleCase } from "../../lib/presentation";
import type { AnalysisPublication } from "../../types";
import {
  AnalysisMetadataView,
  AnalysisOutputView,
  type AnalysisArtifactDownloader,
} from "../runs/AnalysisOutputView";

export function AnalysisPublicationView({
  analysis,
  getArtifactDownload,
  onOpenRun,
}: {
  analysis: AnalysisPublication;
  getArtifactDownload: AnalysisArtifactDownloader;
  onOpenRun?: (runId: string) => void;
}) {
  return (
    <div className="grid gap-2">
      {analysis.inputs.length > 0 ? (
        <section className="rounded-[7px] border border-line bg-panel p-[9px]">
          <h4 className="mt-0 mb-2 text-[0.58rem] font-extrabold tracking-[0.06em] text-text-dim uppercase">
            Inputs
          </h4>
          <ul className="m-0 grid list-none gap-2 p-0">
            {analysis.inputs.map((input) => {
              const runId =
                input.kind === "measurement_dataset" || input.kind === "configuration_snapshot"
                  ? input.run_id
                  : input.kind !== "interpretation" && input.source.subject.kind === "run"
                    ? input.source.subject.run_id
                    : undefined;
              return (
                <li className="min-w-0 text-[0.61rem] text-text-soft" key={input.id}>
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                    <strong>{input.title ?? input.target}</strong>
                    <span className="text-text-dim">
                      {input.role} · {titleCase(input.kind)}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    {onOpenRun && runId ? (
                      <button
                        className="cursor-pointer border-0 bg-transparent p-0 font-mono text-[0.59rem] text-blue hover:text-text hover:underline"
                        onClick={() => onOpenRun(runId)}
                        type="button"
                      >
                        {runId}
                      </button>
                    ) : (
                      <code className="text-text-dim">
                        {runId ??
                          (input.kind === "interpretation"
                            ? "Procedure decision"
                            : "Project analysis")}
                      </code>
                    )}
                    <code
                      className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-text-dim"
                      title={input.target}
                    >
                      {input.target}
                    </code>
                  </div>
                  {input.kind === "interpretation" ? (
                    <code title={input.source.response_hash}>
                      {input.source.procedure_run_id}:{input.source.step_key}
                    </code>
                  ) : input.kind !== "measurement_dataset" &&
                    input.kind !== "configuration_snapshot" ? (
                    <code
                      className="mt-1 block overflow-hidden text-ellipsis whitespace-nowrap text-text-dim"
                      title={`${input.source.analysis_record_id}:${input.source.output_id}`}
                    >
                      {input.source.analysis_record_id}:{input.source.output_id}
                    </code>
                  ) : null}
                  <code
                    className="mt-1 block overflow-hidden text-ellipsis whitespace-nowrap text-[0.55rem] text-text-dim"
                    title={input.content_hash}
                  >
                    {input.content_hash}
                  </code>
                  <AnalysisMetadataView metadata={input.metadata ?? {}} />
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}

      {analysis.executions.length > 0 ? (
        <section className="rounded-[7px] border border-line bg-panel p-[9px]">
          <h4 className="mt-0 mb-2 text-[0.58rem] font-extrabold tracking-[0.06em] text-text-dim uppercase">
            Execution evidence
          </h4>
          <ul className="m-0 grid list-none gap-2 p-0">
            {analysis.executions.map((execution) => (
              <li className="min-w-0 text-[0.61rem] text-text-soft" key={execution.id}>
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <strong>{execution.id}</strong>
                  <span className="text-text-dim">
                    {titleCase(execution.access)} · {execution.outputs.length} result
                    {execution.outputs.length === 1 ? "" : "s"}
                  </span>
                </div>
                <code
                  className="mt-1 block overflow-hidden text-ellipsis whitespace-nowrap text-text-dim"
                  title={execution.implementation}
                >
                  {execution.implementation}
                </code>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {analysis.outputs.map((output) => (
        <section
          className="overflow-hidden rounded-[7px] border border-line bg-panel"
          key={output.id}
        >
          <header className="flex items-center justify-between gap-2.5 border-b border-line px-[9px] py-[7px]">
            <span className="grid min-w-0 gap-0.5">
              <strong className="text-[0.65rem]">{output.title}</strong>
              {output.producedBy ? (
                <small className="text-[0.55rem] text-text-dim">
                  Produced by {formatExecutionOutput(output.producedBy)}
                </small>
              ) : output.derivedFrom ? (
                <small className="text-[0.55rem] text-text-dim">
                  Derived from {formatExecutionOutput(output.derivedFrom.source)} via{" "}
                  {output.derivedFrom.adapter}
                </small>
              ) : null}
            </span>
            <span className="text-[0.56rem] font-[750] text-text-dim uppercase">
              {titleCase(output.kind)}
            </span>
          </header>
          <AnalysisOutputView output={output} getArtifactDownload={getArtifactDownload} />
        </section>
      ))}
    </div>
  );
}

function formatExecutionOutput(source: AnalysisExecutionOutputReference): string {
  return `${source.execution_id}:${source.output_name}`;
}
