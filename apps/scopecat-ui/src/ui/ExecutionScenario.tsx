import type { SoftwareExecutionScenario } from "../api-contract";

export function ExecutionScenario({
  scenario,
  label,
}: {
  scenario?: SoftwareExecutionScenario | null;
  label: string;
}) {
  return (
    <section aria-label={label} className="grid gap-2 rounded border border-line bg-panel p-3">
      <h4>{label}</h4>
      {scenario ? (
        <>
          <p>
            <strong>{scenario.label}</strong> · Software scenario
          </p>
          <p>
            Model: <code>{scenario.model_id}</code> · {scenario.model_version}
          </p>
          <p>
            Scenario: <code>{scenario.id}</code>
            {scenario.seed != null ? ` · Seed: ${scenario.seed}` : ""}
          </p>
          <div>
            <strong>Declared coverage</strong>
            {scenario.capabilities.length ? (
              <ul>
                {scenario.capabilities.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            ) : (
              <p>No coverage declared.</p>
            )}
          </div>
          <div>
            <strong>Limitations</strong>
            {scenario.limitations.length ? (
              <ul>
                {scenario.limitations.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            ) : (
              <p>No limitations declared.</p>
            )}
          </div>
        </>
      ) : (
        <p>No execution scenario declared.</p>
      )}
    </section>
  );
}
