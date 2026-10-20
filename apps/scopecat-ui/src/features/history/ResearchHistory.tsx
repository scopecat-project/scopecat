import { ActionRow, Notice, Field, Select, Button, Input } from "../../ui/Controls";
import { pageTitle, sectionTitle, pageContent, detailCard, secondaryButton } from "../../ui/styles";
import { useRecordCollectionNames } from "../context/scope-catalog";
import { ScopeCatalog } from "../context/ScopeCatalog";
import { RunAddressLabel } from "../runs/RunAddressLabel";
import { RunIdLookup } from "./RunIdLookup";
import { CollectionRunLookup } from "./CollectionRunLookup";
import { useState } from "react";
import { OpenRunWindow } from "../runs/OpenRunWindow";
import { DataCleanupHistory } from "./DataCleanup";
import { ImportedCaptures } from "./ImportedCaptures";
import { CourseData } from "./CourseData";
import { useLocationUrl } from "../../lib/navigation";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

type Project = components["schemas"]["ResearchProject"];

export function ResearchHistory({
  onOpenRun,
  daemonUnavailable,
  projectId,
}: {
  onOpenRun: (runId: string) => void;
  daemonUnavailable: boolean;
  projectId: string;
}) {
  const cache = useQueryClient();
  const course = useLocationUrl().searchParams.get("course") ?? undefined;
  const [selected, setSelected] = useState<Project>();
  const [name, setName] = useState("");
  const [collection, setCollection] = useState<string>();
  const [sample, setSample] = useState("");
  const [workingPoint, setWorkingPoint] = useState("");
  const [bench, setBench] = useState("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");
  const [allRuns, setAllRuns] = useState(false);
  const [managing, setManaging] = useState(false);
  const projects = useInfiniteQuery({
    queryKey: ["research", "projects", projectId],
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
    queryKey: ["research", "samples", projectId],
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
    queryKey: ["research", "members", projectId, selected?.id],
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
  const collectionNames = useRecordCollectionNames(projectId, !daemonUnavailable);
  const filters = course
    ? { practice_scope: course }
    : {
        record_collection: collection,
        research_project: allRuns ? undefined : selected?.id,
        sample_id: sample || undefined,
        working_point: workingPoint || undefined,
        deployment_id: bench || undefined,
        created_after: after ? new Date(after).toISOString() : undefined,
        created_before: before ? new Date(before).toISOString() : undefined,
      };
  const runs = useInfiniteQuery({
    queryKey: ["research", "runs", projectId, filters],
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
    <section className={pageContent} aria-label="Data">
      <h2 className={pageTitle}>Data</h2>
      {course && <CourseData scopeId={course} unavailable={daemonUnavailable} />}

      {error && <Notice role="alert">{error.message}</Notice>}
      {!course && (
        <>
          <div className={`${detailCard} flex flex-wrap items-start gap-3`}>
            <div className="min-w-0 flex-1 basis-80">
              <ScopeCatalog
                compact
                kind="collection"
                owner={projectId}
                value={collection}
                onChange={setCollection}
                emptyLabel="All collections"
                selectLabel="Record collection filter"
                disabled={daemonUnavailable}
                available={!daemonUnavailable}
              />
            </div>
            {collection && (
              <CollectionRunLookup
                key={JSON.stringify([projectId, collection])}
                projectId={projectId}
                collection={collection}
                collectionName={collection ? collectionNames.get(collection) : undefined}
                unavailable={daemonUnavailable}
                onOpenRun={onOpenRun}
              />
            )}
            {!collection && (
              <span className="text-xs text-text-dim">
                Choose a collection to find a short number.
              </span>
            )}
          </div>
          <RunIdLookup
            key={projectId}
            projectId={projectId}
            unavailable={daemonUnavailable}
            names={collectionNames}
            onOpenRun={onOpenRun}
          />
          <ActionRow className={detailCard}>
            <Field>
              Research project{" "}
              <Select
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
              </Select>
            </Field>
            {projects.hasNextPage && (
              <Button onClick={() => void projects.fetchNextPage()}>More projects</Button>
            )}

            <Field>
              Sample{" "}
              <Select
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
              </Select>
            </Field>
            {samples.hasNextPage && (
              <Button onClick={() => void samples.fetchNextPage()}>More samples</Button>
            )}
            <Field>
              From (local time){" "}
              <Input
                type="datetime-local"
                value={after}
                onChange={(event) => setAfter(event.target.value)}
              />
            </Field>
            <Field>
              Before (local time){" "}
              <Input
                type="datetime-local"
                value={before}
                onChange={(event) => setBefore(event.target.value)}
              />
            </Field>
          </ActionRow>
          <details className={detailCard}>
            <summary className="cursor-pointer">
              Advanced source filters{workingPoint || bench ? " · active" : ""}
            </summary>
            <p className="text-sm text-text-dim">
              Exact working-point context ID and deployment ID; these do not describe scientific
              conditions.
            </p>
            <div className="mt-2 flex flex-wrap gap-3">
              <Field>
                Working point{" "}
                <Input
                  value={workingPoint}
                  onChange={(event) => setWorkingPoint(event.target.value)}
                />
              </Field>
              <Field>
                Bench deployment{" "}
                <Input value={bench} onChange={(event) => setBench(event.target.value)} />
              </Field>
            </div>
          </details>
        </>
      )}
      <div className={detailCard}>
        <h3 className={sectionTitle}>Retained runs</h3>
        {!course && allRuns && selected && (
          <Notice role="status">Showing runs across all projects for association.</Notice>
        )}
        {daemonUnavailable ? (
          <Notice role="status">Data unavailable while the application service is offline.</Notice>
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
                    {run.control.admission.display_name ||
                      run.control.admission.plan?.experiment_id ||
                      "Open run"}
                  </button>
                  <RunAddressLabel
                    address={run.address ?? undefined}
                    collectionName={
                      run.address ? collectionNames.get(run.address.collection_id) : undefined
                    }
                    sequence={run.control.sequence}
                  />
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
                  <span>
                    {run.snapshot.samples
                      ?.map(
                        (binding) =>
                          `${binding.display_name} / ${binding.context_id ?? "no working point"}`,
                      )
                      .join(", ")}
                  </span>
                  {managing && selected && (
                    <Button
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
                    </Button>
                  )}
                </li>
              ))}
          </ul>
        )}
        {runs.data?.pages[0]?.items.length === 0 && <p>No runs match these filters.</p>}
        {runs.hasNextPage && (
          <Button disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>
            Older runs
          </Button>
        )}
      </div>
      {!course && (
        <>
          <ImportedCaptures unavailable={daemonUnavailable} projectId={projectId} />
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
              Manage {selected?.name ?? "a research project"}.
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
              <Field>
                Project name{" "}
                <Input required value={name} onChange={(event) => setName(event.target.value)} />
              </Field>
              <Button variant="primary" disabled={pending || !name.trim()}>
                {selected ? "Rename project" : "Create project"}
              </Button>
            </form>
            {!course && selected && (
              <div className="mt-3 space-y-2">
                <p>Associated samples (membership does not automatically add their runs):</p>
                {members.data?.pages
                  .flatMap((page) => page.ids)
                  .map((id) => (
                    <div key={id} className="flex gap-2">
                      <Button onClick={() => setSample(id)}>{id}</Button>
                      <Button
                        disabled={pending}
                        onClick={() =>
                          associate.mutate({ kind: "samples", identity: id, present: false })
                        }
                      >
                        Remove sample association
                      </Button>
                    </div>
                  ))}
                {members.hasNextPage && (
                  <Button onClick={() => void members.fetchNextPage()}>
                    More associated samples
                  </Button>
                )}
              </div>
            )}
            {selected && sample && (
              <Button
                disabled={pending}
                onClick={() =>
                  associate.mutate({ kind: "samples", identity: sample, present: true })
                }
              >
                Associate sample
              </Button>
            )}
            {!course && selected && (
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
            <summary className="cursor-pointer font-semibold">Data cleanup</summary>
            <div className="mt-3 space-y-4">
              <DataCleanupHistory />
            </div>
          </details>
        </>
      )}
    </section>
  );
}
