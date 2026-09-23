import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { MethodResponse } from "openapi-fetch";
import { apiClient, apiData } from "../../api-client";
import { CalibrationProfiles } from "../launch/CalibrationProfiles";

type Resolution = MethodResponse<typeof apiClient, "post", "/api/v1/calibration-checks/context">;

export function CurrentCapabilities({
  sampleId,
  revision,
}: {
  sampleId: string;
  revision: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [branch, setBranch] = useState("");
  const [setupId, setSetupId] = useState("");
  const [resolution, setResolution] = useState<Resolution>();
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const setups = useQuery({
    queryKey: ["capability-setup-choices"],
    enabled: expanded,
    queryFn: ({ signal }) => apiData(apiClient.GET("/api/v1/setup/revisions", { signal })),
  });
  const selectedSetup = setups.data?.items.find((item) => item.id === setupId);
  async function resolve() {
    setAttempt((value) => value + 1);
    setPending(true);
    setError("");
    setResolution(undefined);
    try {
      setResolution(
        await apiData(
          apiClient.POST("/api/v1/calibration-checks/context", {
            body: {
              branch: branch.trim(),
              setup: selectedSetup
                ? { revision_id: selectedSetup.id, content_hash: selectedSetup.content_hash }
                : null,
              samples: [{ sample_id: sampleId, revision, role: "subject" }],
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
    <details
      className="border rounded p-3 space-y-3"
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>Capability evidence from a parameter branch</summary>
      <p>
        Choose a parameter branch and setup for this exact sample revision. Resolving captures their
        current versions without running an experiment or changing defaults. This entry selects one
        inline sample in the subject role; it does not select a registered or joint target.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void resolve();
        }}
      >
        <fieldset disabled={pending} className="space-y-2">
          <label>
            Parameter branch
            <input
              required
              value={branch}
              onChange={(event) => {
                setBranch(event.target.value);
                setResolution(undefined);
                setError("");
              }}
            />
          </label>
          <label>
            Setup for capability context
            <select
              value={setupId}
              onChange={(event) => {
                setSetupId(event.target.value);
                setResolution(undefined);
                setError("");
              }}
            >
              <option value="">Current active setup at resolution</option>
              {setups.data?.items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            disabled={!branch.trim() || pending || Boolean(setupId && !selectedSetup)}
          >
            Resolve capability context
          </button>
        </fieldset>
      </form>
      {setups.error && (
        <p role="alert">
          Could not load saved setups: {setups.error.message}. The current active setup can still be
          resolved.
        </p>
      )}
      {pending && <p role="status">Resolving current versions…</p>}
      {error && <p role="alert">{error}</p>}
      {resolution && (
        <section key={attempt} aria-label="Resolved current capability context">
          <p>
            Branch {resolution.branch.name} · generation {resolution.branch.generation} · parameters{" "}
            {resolution.context.parameters.revision_id} · setup {resolution.setup.revision_id}
          </p>
          <p>
            These versions are now frozen for this report. Resolve again to follow later branch or
            setup changes.
          </p>
          <p>Scenario: {resolution.context.scenario?.id ?? "physical"}</p>
          <CalibrationProfiles
            context={resolution.context}
            contextDescription="Uses the explicitly resolved branch, setup and sample versions."
          />
        </section>
      )}
    </details>
  );
}
