import { calibrationStatusLabel, calibrationReasonLabel } from "./calibration-labels";
import type { components } from "../../api-schema";

export function CalibrationAssessments({
  assessments,
}: {
  assessments: components["schemas"]["CheckAssessment"][] | undefined;
}) {
  if (!assessments?.length) return null;
  return (
    <details>
      <summary>保留检查为何适用或需要重查</summary>
      {assessments.map((assessment) => (
        <div key={assessment.run_id} className="border rounded p-2">
          <a href={`?run=${encodeURIComponent(assessment.run_id)}#runs`} className="underline">
            {assessment.run_id}
          </a>
          <p>
            {calibrationStatusLabel(assessment.status)}:{" "}
            {assessment.reasons.map(calibrationReasonLabel).join(" ")}
          </p>
          {assessment.dependencies && (
            <>
              <p>
                依赖覆盖： {calibrationStatusLabel(assessment.dependencies.status)}.{" "}
                {assessment.dependencies.reasons.map(calibrationReasonLabel).join(" ")}
              </p>
              <p>已比较参数： {assessment.dependencies.compared_parameters?.join(", ") || "无"}</p>
              <p>已改变参数： {assessment.dependencies.changed_parameters?.join(", ") || "无"}</p>
            </>
          )}
        </div>
      ))}
    </details>
  );
}
