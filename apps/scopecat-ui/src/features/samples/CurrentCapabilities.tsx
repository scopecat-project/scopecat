import { useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import type { MethodResponse } from "openapi-fetch";
import { apiClient, apiData } from "../../api-client";
import { CalibrationProfiles } from "../launch/CalibrationProfiles";

type Resolution = MethodResponse<typeof apiClient, "post", "/api/v1/measurement-context/resolve">;

export function CurrentCapabilities({
  sampleId,
  revision,
}: {
  sampleId: string;
  revision: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [branch, setBranch] = useState("");
  const [parameterId, setParameterId] = useState("");
  const [source, setSource] = useState<"branch" | "revision">("branch");
  const [setupId, setSetupId] = useState("");
  const [targetId, setTargetId] = useState("");
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
  const targets = useInfiniteQuery({
    queryKey: ["capability-target-choices", sampleId, revision],
    enabled: expanded,
    initialPageParam: undefined as number | undefined,
    queryFn: ({ signal, pageParam }) =>
      apiData(
        apiClient.GET("/api/v1/measurement-targets", {
          params: { query: { limit: 50, before: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const choices =
    targets.data?.pages
      .flatMap((page) => page.items)
      .filter(
        (item) =>
          item.content.members.length === 1 &&
          item.content.connections.length === 0 &&
          item.content.members[0]?.sample_id === sampleId &&
          item.content.members[0]?.revision === revision,
      ) ?? [];
  const selectedTarget = choices.find(
    (item) => `${item.ref.target_id}:${item.ref.revision}` === targetId,
  );
  async function resolve() {
    setAttempt((value) => value + 1);
    setPending(true);
    setError("");
    setResolution(undefined);
    try {
      const parameters =
        source === "revision"
          ? await apiData(
              apiClient.GET("/api/v1/parameters/revisions/{revision_id}", {
                params: { path: { revision_id: parameterId.trim() } },
              }),
            )
          : undefined;
      setResolution(
        await apiData(
          apiClient.POST("/api/v1/measurement-context/resolve", {
            body: {
              ...(parameters
                ? {
                    parameters: {
                      revision_id: parameters.id,
                      content_hash: parameters.content_hash,
                    },
                  }
                : { branch: branch.trim() }),
              setup: selectedSetup
                ? { revision_id: selectedSetup.id, content_hash: selectedSetup.content_hash }
                : null,
              samples: selectedTarget ? [] : [{ sample_id: sampleId, revision, role: "subject" }],
              target: selectedTarget?.ref ?? null,
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
      <summary>Capability evidence from saved parameters</summary>
      <p>
        Choose a parameter branch or saved revision and setup for this exact sample revision.
        Resolving captures their current versions without running an experiment or changing
        defaults. This entry selects one inline sample in the subject role or a registered
        single-member target. Connected targets are not supported by the current target projection.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void resolve();
        }}
      >
        <fieldset disabled={pending} className="space-y-2">
          <label>
            Measurement subject
            <select
              value={targetId}
              onChange={(event) => {
                setTargetId(event.target.value);
                setResolution(undefined);
                setError("");
              }}
            >
              <option value="">Inline sample · subject role</option>
              {choices.map((item) => (
                <option
                  key={`${item.ref.target_id}:${item.ref.revision}`}
                  value={`${item.ref.target_id}:${item.ref.revision}`}
                >
                  {item.name} · {item.ref.target_id} · r{item.ref.revision}
                </option>
              ))}
            </select>
          </label>
          {targets.hasNextPage && (
            <button
              type="button"
              disabled={targets.isFetchingNextPage}
              onClick={() => {
                void targets.fetchNextPage();
              }}
            >
              Load more target choices
            </button>
          )}
          <label>
            Parameter source
            <select
              value={source}
              onChange={(event) => {
                setSource(event.target.value === "revision" ? "revision" : "branch");
                setResolution(undefined);
                setError("");
              }}
            >
              <option value="branch">Parameter branch</option>
              <option value="revision">Exact saved revision</option>
            </select>
          </label>
          <label>
            {source === "branch" ? "Parameter branch" : "Saved parameter revision"}
            <input
              required
              value={source === "branch" ? branch : parameterId}
              onChange={(event) => {
                if (source === "branch") setBranch(event.target.value);
                else setParameterId(event.target.value);
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
            disabled={
              !(source === "branch" ? branch.trim() : parameterId.trim()) ||
              pending ||
              Boolean(setupId && !selectedSetup) ||
              Boolean(targetId && !selectedTarget)
            }
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
      {targets.error && (
        <p role="alert">Could not load registered targets: {targets.error.message}.</p>
      )}
      {pending && <p role="status">Resolving measurement context…</p>}
      {error && <p role="alert">{error}</p>}
      {resolution && (
        <section key={attempt} aria-label="Resolved current capability context">
          <p>
            {resolution.branch
              ? `Branch ${resolution.branch.name} · generation ${resolution.branch.generation} · `
              : "Exact saved "}
            parameters {resolution.context.parameters.revision_id} · setup{" "}
            {resolution.setup.revision_id}
          </p>
          <p>
            These versions are now frozen for this report. Resolve again to capture changes to a
            selected branch or the active setup.
          </p>
          <p>Scenario: {resolution.context.scenario?.id ?? "physical"}</p>
          <details>
            <summary>Resolved measurement subject</summary>
            <pre>{JSON.stringify(resolution.context.subject, null, 2)}</pre>
          </details>
          <CalibrationProfiles
            context={resolution.context}
            contextDescription="Uses the explicitly resolved parameter, setup and sample versions."
          />
        </section>
      )}
    </details>
  );
}
