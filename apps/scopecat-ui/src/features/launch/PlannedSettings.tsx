import type { components } from "../../api-schema";

type Stage = Pick<
  components["schemas"]["PreflightStage"],
  "planned_settings" | "planned_setting_limit" | "planned_settings_truncated" | "selected_points"
>;
type StateValue = components["schemas"]["StateValue"];

function displayValue(value: StateValue): string {
  if (typeof value !== "object") return String(value);
  if ("unit" in value) return `${value.value} ${value.unit}`;
  return `Payload reference: ${value.payload_id}`;
}

export function PlannedSettings({ stage }: { stage: Stage }) {
  const settings = stage.planned_settings;
  return (
    <section aria-label="Planned instrument settings" className="space-y-2 text-sm">
      <h5 className="font-medium">Planned instrument settings</h5>
      <p>
        Settings from the selected point's frozen plan. No device operation or readback is performed
        by this preview; these values are not observed or confirmed state.
      </p>
      {stage.selected_points === 0 ? (
        <p>No point was inspected; planned instrument settings are unknown.</p>
      ) : settings.length === 0 ? (
        <p>No instrument settings in the selected point.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr>
                <th>Point / order</th>
                <th>Instrument / member</th>
                <th>Planned value</th>
              </tr>
            </thead>
            <tbody>
              {settings.map((assignment) => {
                const target = assignment.setting.target;
                return (
                  <tr key={`${assignment.operation_index}:${assignment.assignment_index}`}>
                    <td title={assignment.proposal_fingerprint}>
                      {assignment.point_index === null
                        ? "Selected candidate"
                        : `Point ${assignment.point_index}`}
                      {" · "}
                      {assignment.operation_index + 1}.{assignment.assignment_index + 1}
                    </td>
                    <td>
                      {assignment.instrument_id} / {target.component_path?.join("/") || "root"}
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
        <p role="status">
          Showing the first {stage.planned_setting_limit} planned settings; additional settings are
          omitted.
        </p>
      )}
    </section>
  );
}
