import { useState } from "react";
import { OpenRunWindow } from "../runs/OpenRunWindow";
import { DataCleanupHistory } from "./DataCleanup";
import { ImportedCaptures } from "./ImportedCaptures";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { detailCard, primaryButton, secondaryButton } from "../../ui/styles";

type Project = components["schemas"]["ResearchProject"];
const inputClass = "max-w-full min-w-0 rounded border border-line bg-bg px-2 py-1 text-sm";

export function ResearchHistory({
  onOpenRun,
  daemonUnavailable,
}: {
  onOpenRun: (runId: string) => void;
  daemonUnavailable: boolean;
}) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState<Project>();
  const [name, setName] = useState("");
  const [sample, setSample] = useState("");
  const [workingPoint, setWorkingPoint] = useState("");
  const [bench, setBench] = useState("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [allRuns, setAllRuns] = useState(false);
  const [managing, setManaging] = useState(false);
  const projects = useInfiniteQuery({
    queryKey: ["research", "projects"],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/research-projects", {
          params: { query: { limit: 100, before: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !daemonUnavailable,
  });
  const samples = useInfiniteQuery({
    queryKey: ["research", "samples"],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/samples", {
          params: { query: { limit: 100, before: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !daemonUnavailable,
  });
  const members = useInfiniteQuery({
    queryKey: ["research", "members", selected?.id],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/research-projects/{project_id}/members/{kind}", {
          params: {
            path: { project_id: selected!.id, kind: "samples" },
            query: { limit: 100, after: pageParam },
          },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !!selected && !daemonUnavailable,
  });
  const filters = {
    research_project: allRuns ? undefined : selected?.id,
    sample_id: sample || undefined,
    working_point: workingPoint || undefined,
    deployment_id: bench || undefined,
    created_after: after ? new Date(after).toISOString() : undefined,
    created_before: before ? new Date(before).toISOString() : undefined,
  };
  const runs = useInfiniteQuery({
    queryKey: ["research", "runs", filters],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      apiData(
        apiClient.GET("/api/v1/runs", {
          params: { query: { ...filters, limit: 50, before: pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !daemonUnavailable,
  });
  const save = useMutation({
    mutationFn: ({ id, expected }: { id: string; expected: number }) =>
      apiData(
        apiClient.PUT("/api/v1/research-projects/{project_id}", {
          params: { path: { project_id: id } },
          body: { name, description: selected?.description ?? "", expected_revision: expected },
        }),
      ),
    onSuccess: (project) => {
      setSelected(project);
      void cache.invalidateQueries({ queryKey: ["research"] });
    },
  });
  const associate = useMutation({
    mutationFn: async ({
      kind,
      identity,
      present,
    }: {
      kind: "samples" | "runs";
      identity: string;
      present: boolean;
    }) => {
      const options = { params: { path: { project_id: selected!.id, kind, identity } } };
      return present
        ? apiData(
            apiClient.PUT(
              "/api/v1/research-projects/{project_id}/members/{kind}/{identity}",
              options,
            ),
          )
        : apiData(
            apiClient.DELETE(
              "/api/v1/research-projects/{project_id}/members/{kind}/{identity}",
              options,
            ),
          );
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["research"] });
    },
  });
  const error =
    projects.error ?? samples.error ?? members.error ?? runs.error ?? save.error ?? associate.error;
  const pending = daemonUnavailable || save.isPending || associate.isPending;
  return (
    <section className="space-y-4 p-4" aria-label="Data">
      <h2 className="text-lg font-semibold">Data</h2>
      <p className="text-sm text-text-dim">
        Find retained runs by project, sample or time. Open a run for its measurements and saved
        analyses, or open it independently alongside your current acquisition. Imported captures are
        available under Import and manage data.
      </p>
      {error && <p role="alert">{error.message}</p>}
      <div className={`${detailCard} flex flex-wrap items-end gap-3`}>
        <label>
          Research project{" "}
          <select
            className={inputClass}
            aria-label="Research project"
            value={selected?.id ?? ""}
            onChange={(event) => {
              const project = projects.data?.pages
                .flatMap((page) => page.items)
                .find((item) => item.id === event.target.value);
              setSelected(project);
              setName(project?.name ?? "");
            }}
          >
            <option value="">All projects</option>
            {projects.data?.pages
              .flatMap((page) => page.items)
              .map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
          </select>
        </label>
        {projects.hasNextPage && (
          <button className={secondaryButton} onClick={() => void projects.fetchNextPage()}>
            More projects
          </button>
        )}

        <label>
          Sample{" "}
          <select
            className={inputClass}
            aria-label="Sample"
            value={sample}
            onChange={(event) => setSample(event.target.value)}
          >
            <option value="">All samples</option>
            {samples.data?.pages
              .flatMap((page) => page.items)
              .map((item) => (
                <option key={item.record.id} value={item.record.id}>
                  {item.revision.content.display_name} ({item.record.id})
                </option>
              ))}
          </select>
        </label>
        {samples.hasNextPage && (
          <button className={secondaryButton} onClick={() => void samples.fetchNextPage()}>
            More samples
          </button>
        )}
        <label>
          Working point{" "}
          <input
            className={inputClass}
            value={workingPoint}
            onChange={(event) => setWorkingPoint(event.target.value)}
          />
        </label>
        <label>
          Bench deployment{" "}
          <input
            className={inputClass}
            value={bench}
            onChange={(event) => setBench(event.target.value)}
          />
        </label>
        <label>
          From (local time){" "}
          <input
            className={inputClass}
            type="datetime-local"
            value={after}
            onChange={(event) => setAfter(event.target.value)}
          />
        </label>
        <label>
          Before (local time){" "}
          <input
            className={inputClass}
            type="datetime-local"
            value={before}
            onChange={(event) => setBefore(event.target.value)}
          />
        </label>
      </div>
      <div className={detailCard}>
        <h3 className="mt-0 text-base font-semibold">Retained runs</h3>
        {allRuns && selected && (
          <p role="status">Showing runs across all projects for association.</p>
        )}
        {daemonUnavailable ? (
          <p role="status">Data unavailable while the application service is offline.</p>
        ) : runs.isPending ? (
          <p>Loading history…</p>
        ) : (
          <ul className="space-y-3">
            {runs.data?.pages
              .flatMap((page) => page.items)
              .map((run) => (
                <li
                  key={run.snapshot.run_id}
                  className="flex min-w-0 flex-wrap items-center gap-3 border-b border-line pb-3 break-words"
                >
                  <button
                    className={`${secondaryButton} max-w-full whitespace-normal break-all`}
                    onClick={() => onOpenRun(run.snapshot.run_id)}
                  >
                    {run.control.admission.display_name || run.snapshot.run_id}
                  </button>
                  <code className="max-w-full break-all text-xs text-text-dim">
                    {run.snapshot.run_id}
                  </code>
                  <OpenRunWindow runId={run.snapshot.run_id} />
                  <span>
                    {run.snapshot.created_at
                      ? new Date(run.snapshot.created_at).toLocaleString()
                      : "Unknown time"}
                  </span>
                  <span>{run.snapshot.outcome?.result ?? run.control.state}</span>
                  {run.snapshot.outcome?.certainty === "indeterminate" && (
                    <span>Outcome indeterminate</span>
                  )}
                  {run.control.attention_reason && <span>{run.control.attention_reason}</span>}
                  <span>Bench: {run.deployment_id ?? "unknown"}</span>
                  <span>
                    {run.snapshot.samples
                      ?.map(
                        (binding) =>
                          `${binding.display_name} / ${binding.context_id ?? "no working point"}`,
                      )
                      .join(", ")}
                  </span>
                  {managing && selected && (
                    <button
                      className={secondaryButton}
                      disabled={pending}
                      onClick={() =>
                        associate.mutate({
                          kind: "runs",
                          identity: run.snapshot.run_id,
                          present: allRuns,
                        })
                      }
                    >
                      {allRuns ? "Associate run" : "Remove run association"}
                    </button>
                  )}
                </li>
              ))}
          </ul>
        )}
        {runs.data?.pages[0]?.items.length === 0 && <p>No runs match these filters.</p>}
        {runs.hasNextPage && (
          <button
            className={secondaryButton}
            disabled={runs.isFetchingNextPage}
            onClick={() => void runs.fetchNextPage()}
          >
            Older runs
          </button>
        )}
      </div>
      <details
        className={detailCard}
        open={managing}
        onToggle={(event) => {
          const open = event.currentTarget.open;
          setManaging(open);
        }}
      >
        <summary className="cursor-pointer font-semibold">Research project management</summary>
        <p className="text-sm text-text-dim">
          Manage the project selected above. Associations do not move data or change parameter
          selections.
        </p>
        <form
          className="mt-3 flex flex-wrap gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate({
              id: selected?.id ?? crypto.randomUUID(),
              expected: selected?.revision ?? 0,
            });
          }}
        >
          <label>
            Project name{" "}
            <input
              required
              className={inputClass}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <button className={primaryButton} disabled={pending || !name.trim()}>
            {selected ? "Rename project" : "Create project"}
          </button>
        </form>
        {selected && (
          <div className="mt-3 space-y-2">
            <p>Associated samples (membership does not automatically add their runs):</p>
            {members.data?.pages
              .flatMap((page) => page.ids)
              .map((id) => (
                <div key={id} className="flex gap-2">
                  <button className={secondaryButton} onClick={() => setSample(id)}>
                    {id}
                  </button>
                  <button
                    className={secondaryButton}
                    disabled={pending}
                    onClick={() =>
                      associate.mutate({ kind: "samples", identity: id, present: false })
                    }
                  >
                    Remove sample association
                  </button>
                </div>
              ))}
            {members.hasNextPage && (
              <button className={secondaryButton} onClick={() => void members.fetchNextPage()}>
                More associated samples
              </button>
            )}
          </div>
        )}
        {selected && sample && (
          <button
            className={secondaryButton}
            disabled={pending}
            onClick={() => associate.mutate({ kind: "samples", identity: sample, present: true })}
          >
            Associate sample
          </button>
        )}
        {selected && (
          <label>
            <input
              type="checkbox"
              checked={allRuns}
              onChange={(event) => setAllRuns(event.target.checked)}
            />{" "}
            Show runs outside this project to associate
          </label>
        )}
      </details>
      <details className={detailCard}>
        <summary className="cursor-pointer font-semibold">Import and manage data</summary>
        <div className="mt-3 space-y-4">
          <ImportedCaptures unavailable={daemonUnavailable} />
          <DataCleanupHistory />
        </div>
      </details>
    </section>
  );
}
