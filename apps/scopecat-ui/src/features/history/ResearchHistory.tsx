import { stateLabel } from "../launch/procedure-operator";
import { ActionRow, Notice, Field, Select, Button, Input } from "../../ui/Controls";
import { pageTitle, sectionTitle, pageContent, detailCard, secondaryButton } from "../../ui/styles";
import { useRecordCollectionNames } from "../context/scope-catalog";
import { ScopeCatalog } from "../context/ScopeCatalog";
import { RunAddressLabel } from "../runs/RunAddressLabel";
import { RunIdLookup } from "./RunIdLookup";
import { CollectionRunLookup } from "./CollectionRunLookup";
import { Activity, useState } from "react";
import { ApparatusHistory } from "../apparatus/ApparatusHistory";
import { OpenRunWindow } from "../runs/OpenRunWindow";
import { DataCleanupHistory } from "./DataCleanup";
import { ImportedCaptures } from "./ImportedCaptures";
import { CourseData } from "./CourseData";
import { navigate, useLocationUrl } from "../../lib/navigation";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

type Project = components["schemas"]["ResearchProject"];

export function ResearchHistory(props: {
  onOpenRun: (runId: string) => void;
  daemonUnavailable: boolean;
  projectId: string;
}) {
  const url = useLocationUrl();
  const apparatus = url.searchParams.get("data") === "apparatus" && !url.searchParams.has("course");
  return (
    <>
      <nav aria-label="数据浏览" className="mb-4 flex gap-3">
        {[
          [false, "运行与记录"],
          [true, "实验装置历史"],
        ].map(([active, label]) => (
          <Button
            key={String(label)}
            aria-pressed={apparatus === active}
            onClick={() => {
              const target = new URL(window.location.href);
              target.hash = "history";
              target.searchParams.delete("course");
              if (active) target.searchParams.set("data", "apparatus");
              else target.searchParams.delete("data");
              navigate(target);
            }}
          >
            {label}
          </Button>
        ))}
      </nav>
      <Activity mode={apparatus ? "visible" : "hidden"}>
        <ApparatusHistory {...props} />
      </Activity>
      <Activity mode={apparatus ? "hidden" : "visible"}>
        <ResearchRecords {...props} />
      </Activity>
    </>
  );
}

function ResearchRecords({
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
    <section className={pageContent} aria-label="数据">
      <h2 className={pageTitle}>数据</h2>
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
                emptyLabel="所有记录集"
                selectLabel="记录集筛选"
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
              <span className="text-xs text-text-dim">选择记录集后可按短编号查找。</span>
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
              研究项目{" "}
              <Select
                aria-label="研究项目"
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
              起始时间（本地）{" "}
              <Input
                type="datetime-local"
                value={after}
                onChange={(event) => setAfter(event.target.value)}
              />
            </Field>
            <Field>
              截止时间（本地）{" "}
              <Input
                type="datetime-local"
                value={before}
                onChange={(event) => setBefore(event.target.value)}
              />
            </Field>
          </ActionRow>
          <details className={detailCard}>
            <summary className="cursor-pointer">
              高级来源筛选{workingPoint || bench ? " · active" : ""}
            </summary>
            <p className="text-sm text-text-dim">
              Exact working-point context ID and deployment ID; these do not describe scientific
              conditions.
            </p>
            <div className="mt-2 flex flex-wrap gap-3">
              <Field>
                工作点{" "}
                <Input
                  value={workingPoint}
                  onChange={(event) => setWorkingPoint(event.target.value)}
                />
              </Field>
              <Field>
                实验台部署{" "}
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
                      "打开 run"}
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
                      : "时间未知"}
                  </span>
                  <span>{stateLabel(run.snapshot.outcome?.result ?? run.control.state)}</span>
                  {run.snapshot.outcome?.certainty === "indeterminate" && <span>结果尚不确定</span>}
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
                      {allRuns ? "关联 run" : "移除 run 关联"}
                    </Button>
                  )}
                </li>
              ))}
          </ul>
        )}
        {runs.data?.pages[0]?.items.length === 0 && <p>没有符合筛选条件的 run。</p>}
        {runs.hasNextPage && (
          <Button disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>
            更早 run
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
            <summary className="cursor-pointer font-semibold">研究项目管理</summary>
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
                项目名称{" "}
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
                        移除样品关联
                      </Button>
                    </div>
                  ))}
                {members.hasNextPage && (
                  <Button onClick={() => void members.fetchNextPage()}>更多关联样品</Button>
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
                关联样品
              </Button>
            )}
            {!course && selected && (
              <label>
                <input
                  type="checkbox"
                  checked={allRuns}
                  onChange={(event) => setAllRuns(event.target.checked)}
                />{" "}
                显示项目外的 run 以供关联
              </label>
            )}
          </details>
          <details className={detailCard}>
            <summary className="cursor-pointer font-semibold">数据清理</summary>
            <div className="mt-3 space-y-4">
              <DataCleanupHistory />
            </div>
          </details>
        </>
      )}
    </section>
  );
}
