/* oxlint-disable jsx-a11y/no-noninteractive-tabindex -- Named scroll regions need keyboard access. */
import type { components } from "../../api-schema";

type Stage = Pick<
  components["schemas"]["PreflightStage"],
  "planned_settings" | "planned_setting_limit" | "planned_settings_truncated" | "selected_points"
>;
type StateValue = components["schemas"]["StateValue"];

function displayValue(value: StateValue): string {
  if (typeof value !== "object") return String(value);
  if ("unit" in value) return `${value.value} ${value.unit}`;
  return `载荷引用： ${value.payload_id}`;
}

export function PlannedSettings({ stage }: { stage: Stage }) {
  const settings = stage.planned_settings;
  return (
    <section
      aria-label="计划仪器设置"
      className="min-w-0 space-y-2 text-sm [overflow-wrap:anywhere]"
    >
      <h5 className="font-medium">计划仪器设置</h5>
      <p>
        以下设置来自选中点的冻结计划。此次预览不执行设备操作或回读；这些值不是已观测或已确认的状态。
      </p>
      {stage.selected_points === 0 ? (
        <p>未检查任何点；计划仪器设置未知。</p>
      ) : settings.length === 0 ? (
        <p>选中点没有仪器设置。</p>
      ) : (
        <div
          role="region"
          aria-label="计划仪器设置表格"
          tabIndex={0}
          className="min-w-0 max-w-full overflow-x-auto [overflow-wrap:normal]"
        >
          <table className="w-full min-w-96 text-left [&_th]:whitespace-nowrap [&_td]:whitespace-nowrap [&_th]:px-2 [&_td]:px-2">
            <thead>
              <tr>
                <th>点 / 顺序</th>
                <th>仪器 / 成员</th>
                <th>计划值</th>
              </tr>
            </thead>
            <tbody>
              {settings.map((assignment) => {
                const target = assignment.setting.target;
                return (
                  <tr key={`${assignment.operation_index}:${assignment.assignment_index}`}>
                    <td title={assignment.proposal_fingerprint}>
                      {assignment.point_index === null
                        ? "选中候选点"
                        : `点 ${assignment.point_index}`}
                      {" · "}
                      {assignment.operation_index + 1}.{assignment.assignment_index + 1}
                    </td>
                    <td>
                      {assignment.instrument_id} / {target.component_path?.join("/") || "根组件"}
                      {" / "}
                      {target.property_id}
                      <span className="block text-xs text-text-dim">
                        {target.kind === "interface" ? target.interface_id : target.schema_id}
                      </span>
                    </td>
                    <td>{displayValue(assignment.setting.value)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {stage.planned_settings_truncated && (
        <p role="status">仅显示前 {stage.planned_setting_limit} 项计划设置；其余设置已省略。</p>
      )}
    </section>
  );
}
