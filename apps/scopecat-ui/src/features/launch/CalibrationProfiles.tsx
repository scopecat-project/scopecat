import { useState } from "react";
import type { MethodResponse } from "openapi-fetch";
import { apiClient, apiData } from "../../api-client";

type Page = MethodResponse<typeof apiClient, "get", "/api/v1/calibration-profiles">;
type Report = MethodResponse<typeof apiClient, "post", "/api/v1/calibration-checks/report">;

export function CalibrationProfiles({
  context,
  onProcedure,
  contextDescription = "Uses this stage's frozen measurement context.",
}: {
  context: Report["context"];
  onProcedure?: (id: string) => void;
  contextDescription?: string;
}) {
  const [profiles, setProfiles] = useState<Page["items"]>([]);
  const [cursor, setCursor] = useState<number | null>();
  const [selected, setSelected] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [report, setReport] = useState<Report>();
  const profile = profiles.find((record) => record.profile.id === selected)?.profile;
  async function load(reset = false) {
    setPending(true);
    setError("");
    if (reset) {
      setSelected("");
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
            body: { context, history_limit: 50 },
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
      <summary>Inspect a saved capability profile</summary>
      <p>
        {contextDescription} Choose a profile whose targets and conditions apply here. Task ordering
        is not used as capability policy.
      </p>
      {cursor !== null && (
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            void load();
          }}
        >
          {cursor === undefined ? "Load saved profiles" : "Load earlier profiles"}
        </button>
      )}
      {cursor !== undefined && (
        <button
          type="button"
          disabled={pending}
          onClick={() => {
            void load(true);
          }}
        >
          Reload profiles
        </button>
      )}
      {cursor === null && profiles.length === 0 && (
        <p>
          No saved profiles. Save requirements with lab.calibration_checks.save_profile() in your
          author code.
        </p>
      )}
      {profiles.length > 0 && (
        <label>
          Capability profile
          <select
            value={selected}
            disabled={pending}
            onChange={(event) => {
              setSelected(event.target.value);
              setReport(undefined);
              setError("");
            }}
          >
            <option value="">Choose a profile</option>
            {profiles.map(({ profile: item }) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
      )}
      {profile && (
        <>
          <p>{profile.description}</p>
          <ul>
            {profile.requirements.map((item) => (
              <li key={item.id}>
                {item.id}: {item.scope.capability} · {item.scope.targets.join(", ")} · conditions{" "}
                {item.scope.conditions}
                {" · policy "}
                {item.scope.policy_version} · max age {item.max_age}
                {" · requires "}
                {item.depends_on.join(", ") || "none"}
              </li>
            ))}
          </ul>
          <button
            type="button"
            disabled={pending}
            onClick={() => {
              void inspect();
            }}
          >
            Check saved profile
          </button>
        </>
      )}
      {pending && <p role="status">Reading saved capability policy…</p>}
      {error && <p role="alert">{error}</p>}
      {report && (
        <section aria-label="Saved capability report" className="space-y-2">
          <p>
            {report.profile_id} · evaluated at {report.observed_at}. Snapshot only; check again to
            refresh. Inspects at most 50 checks per requirement. This does not establish overall
            sample readiness.
          </p>
          {report.items.map((item) => (
            <article key={item.requirement.id} className="border rounded p-2">
              <h4>{item.requirement.id}</h4>
              <p>
                Own check: {item.selection.status} · Availability: {item.availability.status}
              </p>
              <p>Blocked by: {item.availability.blocked_by.join(", ") || "none"}</p>
              <p>
                Inspected {item.scanned} checks.{" "}
                {[
                  item.selection.reason,
                  ...(item.selection.assessment?.reasons ?? []),
                  ...item.incomplete_reasons,
                ].join(", ")}
              </p>
              {item.selection.evidence && (
                <a
                  className="underline"
                  href={`?run=${encodeURIComponent(item.selection.evidence.measurement.run_id)}#runs`}
                >
                  Open measurement for {item.requirement.id}
                </a>
              )}
              {item.selection.evidence?.analysis_record_id && (
                <a
                  className="underline block"
                  href={`?run=${encodeURIComponent(item.selection.evidence.measurement.run_id)}&run-analysis=${encodeURIComponent(item.selection.evidence.analysis_record_id)}#runs`}
                >
                  Open analysis for {item.requirement.id}
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
                    Inspect unresolved execution {id}
                  </button>
                ) : (
                  <a
                    key={id}
                    className="underline block"
                    href={`?procedure=${encodeURIComponent(id)}#launch`}
                  >
                    Inspect unresolved execution {id}
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
