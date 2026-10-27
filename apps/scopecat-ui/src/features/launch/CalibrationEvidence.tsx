import { calibrationStatusLabel, calibrationReasonLabel } from "./calibration-labels";
import * as Controls from "../../ui/Controls";
import { useState } from "react";
import type { MethodResponse } from "openapi-fetch";
import { apiClient, apiData } from "../../api-client";
import { CalibrationProfiles } from "./CalibrationProfiles";
import { CalibrationAssessments } from "./CalibrationAssessments";

type TaskView = MethodResponse<typeof apiClient, "get", "/api/v1/calibration-tasks/{task_id}">;
type Stage = TaskView["task"]["specification"]["plan"]["stages"][number];
type Report = MethodResponse<typeof apiClient, "post", "/api/v1/calibration-checks/report">;

type EvidenceProps = {
  stage: Stage;
  onProcedure: (id: string) => void;
};

export function CalibrationEvidence(props: EvidenceProps) {
  // A stage ID survives repair/verification attempts; its resolved check does not.
  // Replacing the state owner also prevents a late old request from updating the new report.
  return (
    <CalibrationEvidenceContext
      key={JSON.stringify([props.stage.id, props.stage.check])}
      {...props}
    />
  );
}

function CalibrationEvidenceContext({ stage, onProcedure }: EvidenceProps) {
  const [hours, setHours] = useState("");
  const [limit, setLimit] = useState("50");
  const [report, setReport] = useState<Report>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const seconds = Number(hours) * 3600;
  const historyLimit = Number(limit);
  const valid =
    Number.isFinite(seconds) &&
    seconds > 0 &&
    Number.isInteger(historyLimit) &&
    historyLimit >= 1 &&
    historyLimit <= 200;
  async function inspect() {
    if (!valid) return;
    setPending(true);
    setReport(undefined);
    setError("");
    try {
      setReport(
        await apiData(
          apiClient.POST("/api/v1/calibration-checks/report", {
            body: {
              context: stage.check.context,
              history_limit: historyLimit,
              requirements: [
                {
                  id: stage.id,
                  scope: stage.check.scope,
                  max_age: `PT${seconds}S`,
                  depends_on: [],
                  dependencies: stage.check.dependencies,
                },
              ],
            },
          }),
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  const result = report?.items[0];
  const evidence = result?.selection.evidence;
  return (
    <details className="space-y-2">
      <summary>检查适用证据： {stage.id}</summary>
      <p>
        使用此阶段固定的参数、对象、配置与范围，跨已保留任务读取证据。不评估当前分支头或样品整体就绪状态。
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void inspect();
        }}
      >
        <fieldset disabled={pending} className="flex flex-wrap gap-3 items-end">
          <Controls.Field>
            证据最大有效时长（小时）
            <Controls.Input
              className="block"
              type="number"
              step="any"
              required
              value={hours}
              onChange={(event) => {
                setHours(event.target.value);
                setReport(undefined);
                setError("");
              }}
            />
          </Controls.Field>
          <Controls.Field>
            历史检查上限
            <Controls.Input
              className="block"
              type="number"
              min="1"
              max="200"
              step="1"
              required
              value={limit}
              onChange={(event) => {
                setLimit(event.target.value);
                setReport(undefined);
                setError("");
              }}
            />
          </Controls.Field>
          <Controls.Button type="submit" disabled={!valid || pending}>
            检查证据： {stage.id}
          </Controls.Button>
        </fieldset>
      </form>
      {pending && <p role="status">正在读取已保留证据…</p>}
      {error && <p role="alert">{error}</p>}
      {report && result && (
        <section aria-label={`证据报告： ${stage.id}`} className="space-y-1">
          <p className="font-semibold">{calibrationStatusLabel(result.selection.status)}</p>
          <p>
            评估时间： {report.observed_at}. 已读取 {result.scanned}{" "}
            条检查。重新检查可刷新；此结果不会自动更新。
          </p>
          <ul>
            {[
              result.selection.reason,
              ...(result.selection.assessment?.reasons ?? []),
              ...result.incomplete_reasons,
            ].map((reason) => (
              <li key={reason}>{calibrationReasonLabel(reason)}</li>
            ))}
          </ul>
          <CalibrationAssessments assessments={result.assessments} />
          {evidence && (
            <>
              <p>测量创建时间： {evidence.measurement.created_at}.</p>
              <a
                className="underline"
                href={`?run=${encodeURIComponent(evidence.measurement.run_id)}#runs`}
              >
                打开所选测量
              </a>
              {evidence.analysis_record_id && (
                <a
                  className="underline block"
                  href={`?run=${encodeURIComponent(evidence.measurement.run_id)}&run-analysis=${encodeURIComponent(evidence.analysis_record_id)}#runs`}
                >
                  打开所选分析
                </a>
              )}
            </>
          )}
          {result.unresolved_procedures.map((id) => (
            <button
              type="button"
              className="underline block"
              key={id}
              onClick={() => onProcedure(id)}
            >
              查看未解决的执行： {id}
            </button>
          ))}
        </section>
      )}
      <CalibrationProfiles context={stage.check.context} onProcedure={onProcedure} />
    </details>
  );
}
