import * as Controls from "../../ui/Controls";
import { useState } from "react";
import { contextParameterLabel } from "../launch/context-parameters";
import { useQuery } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { ProjectRun } from "../../types";
import { CalibrationProfiles } from "../launch/CalibrationProfiles";

export function SampleCapabilities({
  sampleId,
  revision,
  runs,
}: {
  sampleId: string;
  revision: number;
  runs: Pick<ProjectRun, "runId" | "displayName" | "experimentId" | "createdAt" | "samples">[];
}) {
  const [runId, setRunId] = useState("");
  const choices = runs.filter((run) =>
    run.samples.some((sample) => sample.sample_id === sampleId && sample.revision === revision),
  );
  const query = useQuery({
    queryKey: ["sample-capability-context", sampleId, revision, runId],
    enabled: Boolean(runId),
    queryFn: ({ signal }) =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}", {
          params: { path: { run_id: runId } },
          signal,
        }),
      ),
  });
  const snapshot = query.isError ? undefined : query.data?.snapshot;
  const source = snapshot?.config_source;
  const belongs = snapshot?.samples.some(
    (sample) => sample.sample_id === sampleId && sample.revision === revision,
  );
  const context =
    snapshot &&
    belongs &&
    (source?.kind === "analysis_candidate" ||
      (source?.kind === "parameter_revision" && source.overrides.length === 0))
      ? {
          parameters: source.kind === "analysis_candidate" ? source : source.parameters,
          subject: snapshot.scientific_binding.subject,
          target_binding: snapshot.scientific_binding.target_binding ?? null,
          setup_content_hash: snapshot.scientific_binding.setup_content_hash,
          scenario: snapshot.scientific_binding.scenario ?? null,
        }
      : undefined;
  return (
    <details className="border rounded p-3 space-y-3">
      <summary>此样品版本的能力证据</summary>
      <p>
        选择测量以复用其精确保存的参数、setup、测量对象和场景。此处检查历史上下文，不代表当前参数分支或样品整体就绪情况。联合测量保留完整测量对象，包括其他样品。
      </p>
      <Controls.Field>
        测量上下文
        <Controls.Select value={runId} onChange={(event) => setRunId(event.target.value)}>
          <option value="">选择测量</option>
          {choices.map((run) => (
            <option key={run.runId} value={run.runId}>
              {run.displayName || run.experimentId} · {run.createdAt ?? "时间不可用"} · {run.runId}
            </option>
          ))}
        </Controls.Select>
      </Controls.Field>
      {!choices.length && <p>尚未加载此样品版本的测量。如有更早运行，可在下方加载。</p>}
      {runId && query.isPending && <p role="status">正在读取测量上下文…</p>}
      {query.error && (
        <>
          <p role="alert">{query.error.message}</p>
          <Controls.Button
            type="button"
            disabled={query.isFetching}
            onClick={() => {
              void query.refetch();
            }}
          >
            重试读取上下文
          </Controls.Button>
        </>
      )}
      {snapshot && !context && (
        <p role="alert">
          此测量未提供当前样品版本的精确保存参数上下文。含参数覆盖或未保存配置的测量不能用于此处。
        </p>
      )}
      {context && (
        <section key={runId} aria-label="选中的能力上下文">
          <p>
            参数： {contextParameterLabel(context.parameters)} · setup：{" "}
            {context.setup_content_hash}
          </p>
          <p>
            场景：{" "}
            {context.scenario
              ? `${context.scenario.id} (${context.scenario.model_id}, ${context.scenario.model_version})`
              : "physical"}
          </p>
          <details>
            <summary>完整测量对象</summary>
            <pre>{JSON.stringify(context.subject, null, 2)}</pre>
          </details>
          <CalibrationProfiles
            context={context}
            contextDescription={`使用测量 ${runId} 的固定上下文。`}
          />
        </section>
      )}
    </details>
  );
}
