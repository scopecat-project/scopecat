import { problemLocationLabel } from "../../lib/problem-location";
import type { LaunchRejection } from "../../api-client";
import { ExecutionScenario } from "../../ui/ExecutionScenario";

export function LaunchRejectionDetails({ rejection }: { rejection: LaunchRejection }) {
  return (
    <section
      role="alert"
      aria-label="Preview rejection"
      className="grid gap-2 rounded border border-line p-3"
    >
      <p>{rejection.message}</p>
      <ul>
        {rejection.problems.map((problem, index) => (
          <li key={index}>
            <p>
              <code>{problem.code}</code>: {problem.message}
            </p>
            {typeof problem.details === "object" &&
              problem.details !== null &&
              "dimension" in problem.details &&
              problem.details.dimension === "capability" && <p>Declared capability limit</p>}
            {problem.location && (
              <p>
                Location: <code>{problemLocationLabel(problem.location)}</code>
              </p>
            )}
          </li>
        ))}
      </ul>
      <ExecutionScenario scenario={rejection.scenario} label="Rejected preview scenario" />
    </section>
  );
}
