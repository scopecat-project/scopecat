import type { components } from "../../api-schema";

export function CalibrationAssessments({
  assessments,
}: {
  assessments: components["schemas"]["CheckAssessment"][] | undefined;
}) {
  if (!assessments?.length) return null;
  return (
    <details>
      <summary>Why retained checks apply or need rechecking</summary>
      {assessments.map((assessment) => (
        <div key={assessment.run_id} className="border rounded p-2">
          <a href={`?run=${encodeURIComponent(assessment.run_id)}#runs`} className="underline">
            {assessment.run_id}
          </a>
          <p>
            {assessment.status}: {assessment.reasons.join(", ").replaceAll("_", " ")}
          </p>
          {assessment.dependencies && (
            <>
              <p>
                Dependency coverage: {assessment.dependencies.status}.{" "}
                {assessment.dependencies.reasons.join(", ").replaceAll("_", " ")}
              </p>
              <p>
                Compared parameters:{" "}
                {assessment.dependencies.compared_parameters?.join(", ") || "none"}
              </p>
              <p>
                Changed parameters:{" "}
                {assessment.dependencies.changed_parameters?.join(", ") || "none"}
              </p>
            </>
          )}
        </div>
      ))}
    </details>
  );
}
