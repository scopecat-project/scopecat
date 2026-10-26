import * as Controls from "../../ui/Controls";
import { useState } from "react";
import type { MethodResponse } from "openapi-fetch";
import { apiClient, apiData } from "../../api-client";
import { CalibrationAssessments } from "./CalibrationAssessments";

type Page = MethodResponse<typeof apiClient, "get", "/api/v1/calibration-profiles">;
type Report = MethodResponse<typeof apiClient, "post", "/api/v1/calibration-checks/report">;

export function CalibrationProfiles({
  context,
  onProcedure,
  contextDescription = "使用此阶段固定的测量上下文。",
}: {
  context: Report["context"];
  onProcedure?: (id: string) => void;
  contextDescription?: string;
}) {
  const [profiles, setProfiles] = useState<Page["items"]>([]);
  const [cursor, setCursor] = useState<number | null>();
  const [selected, setSelected] = useState("");
  const [requirementIds, setRequirementIds] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<Report>();
  const profile = profiles.find((record) => record.profile.id === selected)?.profile;
  async function load(reset = false) {
    setPending(true);
    setError("");
    if (reset) {
      setSelected("");
      setRequirementIds([]);
      setReport(undefined);
    }
    try {
      const page = await apiData(
        apiClient.GET("/api/v1/calibration-profiles", {
          params: { query: { limit: 20, cursor: reset ? undefined : (cursor ?? undefined) } },
        }),
      );
      setProfiles((previous) => (reset ? page.items : [...previous, ...page.items]));
      setCursor(page.next_cursor ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }
  async function inspect() {
    if (!profile) return;
    setPending(true);
    setError("");
    setReport(undefined);
    try {
      setReport(
        await apiData(
          apiClient.POST("/api/v1/calibration-profiles/{profile_id}/report", {
            params: { path: { profile_id: profile.id } },
            body: {
              context,
              history_limit: 50,
              ...(requirementIds.length ? { requirement_ids: requirementIds } : {}),
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
  return (
    <details className="space-y-2 border-t pt-2">
      <summary>检查已保存的能力 profile</summary>
      <p>{contextDescription} 请选择目标和条件适用于此处的 profile。任务顺序不作为能力策略。</p>
      {cursor !== null && (
        <Controls.Button
          type="button"
          disabled={pending}
          onClick={() => {
            void load();
          }}
        >
          {cursor === undefined ? "加载已保存的 profile" : "加载更早的 profile"}
        </Controls.Button>
      )}
      {cursor !== undefined && (
        <Controls.Button
          type="button"
          disabled={pending}
          onClick={() => {
            void load(true);
          }}
        >
          重新加载 profile
        </Controls.Button>
      )}
      {cursor === null && profiles.length === 0 && (
        <p>
          没有已保存的 profile。请在作者代码中用 lab.calibration_checks.save_profile() 保存要求。
        </p>
      )}
      {profiles.length > 0 && (
        <Controls.Field>
          能力 profile
          <Controls.Select
            value={selected}
            disabled={pending}
            onChange={(event) => {
              setSelected(event.target.value);
              setRequirementIds([]);
              setReport(undefined);
              setError("");
            }}
          >
            <option value="">选择 profile</option>
            {profiles.map(({ profile: item }) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
      )}
      {profile && (
        <>
          <p>{profile.description}</p>
          <p>
            勾选能力可聚焦报告；全部不选时检查完整 profile。自动包含前置条件，每份报告最多包含 32
            项能力。
          </p>
          <ul>
            {profile.requirements.map((item) => (
              <li key={item.id}>
                <input
                  type="checkbox"
                  aria-label={`包含 ${item.id}`}
                  disabled={pending}
                  checked={requirementIds.includes(item.id)}
                  onChange={(event) => {
                    setRequirementIds((previous) =>
                      event.target.checked
                        ? [...previous, item.id]
                        : previous.filter((id) => id !== item.id),
                    );
                    setReport(undefined);
                    setError("");
                  }}
                />
                {item.id}: {item.scope.capability} · {item.scope.targets.join(", ")} · 条件{" "}
                {item.scope.conditions}
                {" · 策略"}
                {item.scope.policy_version} · 最大有效时长 {item.max_age}
                {" · 依赖"}
                {item.depends_on.join(", ") || "none"}
              </li>
            ))}
          </ul>
          <Controls.Button
            type="button"
            disabled={pending}
            onClick={() => {
              void inspect();
            }}
          >
            检查已保存的 profile
          </Controls.Button>
        </>
      )}
      {pending && <p role="status">正在读取保存的能力策略…</p>}
      {error && <p role="alert">{error}</p>}
      {report && (
        <section aria-label="已保存的能力报告" className="space-y-2">
          <p>
            {report.profile_id} · 评估时间 {report.observed_at}
            。仅为快照，重新检查可刷新。每项要求最多检查 50 条记录，不代表样品整体就绪。
          </p>
          {report.items.map((item) => (
            <article key={item.requirement.id} className="border rounded p-2">
              <h4>{item.requirement.id}</h4>
              <p>
                自身检查： {item.selection.status} · 可用性： {item.availability.status}
              </p>
              <p>阻塞项： {item.availability.blocked_by.join(", ") || "none"}</p>
              <p>
                已检查 {item.scanned} 条记录。{" "}
                {[
                  item.selection.reason,
                  ...(item.selection.assessment?.reasons ?? []),
                  ...item.incomplete_reasons,
                ].join(", ")}
              </p>
              <CalibrationAssessments assessments={item.assessments} />
              {item.selection.evidence && (
                <a
                  className="underline"
                  href={`?run=${encodeURIComponent(item.selection.evidence.measurement.run_id)}#runs`}
                >
                  打开测量： {item.requirement.id}
                </a>
              )}
              {item.selection.evidence?.analysis_record_id && (
                <a
                  className="underline block"
                  href={`?run=${encodeURIComponent(item.selection.evidence.measurement.run_id)}&run-analysis=${encodeURIComponent(item.selection.evidence.analysis_record_id)}#runs`}
                >
                  打开分析： {item.requirement.id}
                </a>
              )}
              {item.unresolved_procedures.map((id) =>
                onProcedure ? (
                  <button
                    type="button"
                    key={id}
                    className="underline block"
                    onClick={() => onProcedure(id)}
                  >
                    检查未解决的执行： {id}
                  </button>
                ) : (
                  <a
                    key={id}
                    className="underline block"
                    href={`?procedure=${encodeURIComponent(id)}#launch`}
                  >
                    检查未解决的执行： {id}
                  </a>
                ),
              )}
            </article>
          ))}
        </section>
      )}
    </details>
  );
}
