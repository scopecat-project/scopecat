import { SampleArtifacts } from "./SampleArtifacts";
import { useMemo, useState, type ReactNode } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  Activity,
  Boxes,
  ChevronRight,
  CircleDot,
  FlaskConical,
  GitBranch,
  History,
  LoaderCircle,
  Map as MapIcon,
  Search,
  Unplug,
} from "lucide-react";
import type { SampleGeometry, SampleSummary, SampleView } from "../../api-contract";
import {
  errorMessage,
  formatDateTime,
  formatRelative,
  shorten,
  titleCase,
} from "../../lib/presentation";
import type { AnalysisPublication, ProjectRun } from "../../types";
import { classes, detailCard, eyebrow } from "../../ui/styles";
import { AnalysisPublicationView } from "../analyses/AnalysisPublicationView";
import {
  getSample,
  getSampleAnalyses,
  getSampleAnalysis,
  getSampleAnalysisArtifactDownload,
  getSampleRevision,
  getSampleRevisions,
  getSampleRuns,
  getSamples,
} from "./sample-api";

type StatusFilter = "all" | SampleSummary["revision"]["content"]["status"];

const STATUS_FILTERS: Array<{ key: StatusFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "available", label: "Available" },
  { key: "mounted", label: "Mounted" },
  { key: "received", label: "Received" },
  { key: "retired", label: "Retired" },
  { key: "damaged", label: "Damaged" },
];
const EMPTY_SAMPLES: SampleSummary[] = [];

export function SamplesWorkspace({
  selectedSampleId,
  selectedSampleRevision,
  onSelectSample,
  onOpenRun,
  daemonUnavailable,
}: {
  selectedSampleId?: string;
  selectedSampleRevision?: number;
  onSelectSample: (sampleId: string, revision?: number) => void;
  onOpenRun: (runId: string) => void;
  daemonUnavailable: boolean;
}) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const samplesQuery = useInfiniteQuery({
    queryKey: ["samples"],
    queryFn: ({ signal, pageParam }) => getSamples(pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const samples = samplesQuery.data?.pages.flatMap((page) => page.items) ?? EMPTY_SAMPLES;
  const filteredSamples = useMemo(
    () => filterSamples(samples, search, status),
    [samples, search, status],
  );
  const selectedSummary = samples.find((sample) => sample.record.id === selectedSampleId);
  const activeCount = samples.filter((sample) =>
    ["available", "mounted"].includes(sample.revision.content.status),
  ).length;
  const mappedCount = samples.filter(
    (sample) => (sample.revision.content.topology?.entities?.length ?? 0) > 0,
  ).length;

  return (
    <>
      <section
        className="mb-2.5 grid grid-cols-3 overflow-hidden rounded-lg border border-line bg-panel max-[560px]:grid-cols-1"
        aria-label="Sample registry status"
      >
        <RegistryMetric
          label="Samples"
          value={samplesQuery.isSuccess ? String(samples.length) : "—"}
          detail={
            samplesQuery.hasNextPage ? "Loaded physical identities" : "Stable physical identities"
          }
        />
        <RegistryMetric
          label="In service"
          value={samplesQuery.isSuccess ? String(activeCount) : "—"}
          detail="Available or mounted"
        />
        <RegistryMetric
          label="Mapped"
          value={samplesQuery.isSuccess ? String(mappedCount) : "—"}
          detail="With entity topology"
        />
      </section>

      <div className="grid min-h-[650px] grid-cols-[minmax(300px,350px)_minmax(0,1fr)] items-start overflow-hidden rounded-lg border border-line bg-panel max-[940px]:block max-[940px]:overflow-visible max-[940px]:bg-transparent">
        <aside
          className="sticky top-[60px] flex max-h-[calc(100vh-72px)] min-h-[650px] flex-col border-r border-line bg-panel-soft px-3 pt-3.5 pb-3 max-[940px]:static max-[940px]:mb-3 max-[940px]:max-h-none max-[940px]:min-h-0 max-[940px]:rounded-lg max-[940px]:border max-[940px]:border-line"
          aria-labelledby="samples-heading"
        >
          <div className="flex items-start justify-between px-1 pb-3">
            <div>
              <p className={eyebrow}>Physical registry</p>
              <h2 id="samples-heading" className="m-0 text-[1.08rem] font-[650] tracking-[-0.02em]">
                Chips &amp; samples
              </h2>
            </div>
            {samplesQuery.isFetching && (
              <LoaderCircle
                className="mt-2 animate-spin text-text-dim"
                size={17}
                aria-label="Refreshing samples"
              />
            )}
          </div>

          <label className="flex min-h-10 items-center gap-2.5 rounded-[9px] border border-line bg-bg px-3 text-text-dim focus-within:border-[rgb(128_163_207_/_55%)]">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">Search samples</span>
            <input
              className="w-full min-w-0 border-0 bg-transparent p-0 text-[0.8rem] text-text outline-none placeholder:text-[#5e6a77]"
              type="search"
              placeholder="Name, ID, alias, tag"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>

          <div className="my-2.5 flex gap-1 overflow-x-auto pb-0.5" aria-label="Filter samples">
            {STATUS_FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={classes(
                  "min-h-7 flex-none cursor-pointer rounded-[7px] border border-transparent bg-transparent px-2 text-[0.66rem] font-bold text-text-dim hover:text-text-soft",
                  status === item.key &&
                    "border-[rgb(128_163_207_/_17%)] bg-accent-soft text-accent",
                )}
                aria-pressed={status === item.key}
                onClick={() => setStatus(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto p-0.5 [scrollbar-color:#344252_transparent] [scrollbar-width:thin] max-[940px]:grid max-[940px]:grid-flow-col max-[940px]:auto-cols-[minmax(280px,60vw)] max-[940px]:overflow-x-auto">
            {samplesQuery.isPending && (
              <PanelMessage
                icon={<LoaderCircle className="animate-spin" />}
                title="Reading registry"
                detail="Loading stable sample identities."
              />
            )}
            {samplesQuery.isError && (
              <PanelMessage
                icon={<Unplug />}
                title="Registry unavailable"
                detail={errorMessage(samplesQuery.error)}
              />
            )}
            {samplesQuery.isSuccess && samples.length === 0 && (
              <PanelMessage
                icon={<Boxes />}
                title="No samples yet"
                detail="Register a chip or physical sample before binding it to runs."
              />
            )}
            {samplesQuery.isSuccess && samples.length > 0 && filteredSamples.length === 0 && (
              <PanelMessage
                icon={<Search />}
                title="No matching samples"
                detail="Try another lifecycle state or search term."
              />
            )}
            {filteredSamples.map((sample) => (
              <SampleListItem
                key={sample.record.id}
                sample={sample}
                selected={sample.record.id === selectedSampleId}
                onSelect={() => onSelectSample(sample.record.id)}
              />
            ))}
            {samplesQuery.hasNextPage && (
              <LoadMoreButton
                pending={samplesQuery.isFetchingNextPage}
                onClick={() => void samplesQuery.fetchNextPage()}
              >
                Load older samples
              </LoadMoreButton>
            )}
          </div>
        </aside>

        <section className="min-h-[650px] min-w-0 p-[clamp(18px,2vw,28px)] max-[940px]:rounded-lg max-[940px]:border max-[940px]:border-line max-[940px]:bg-panel">
          {daemonUnavailable ? (
            <DetailEmpty
              icon={<Unplug />}
              title="Connect to the daemon"
              detail="The sample registry is read directly from this project."
            />
          ) : selectedSampleId ? (
            <SampleDetail
              key={selectedSampleId}
              sampleId={selectedSampleId}
              selectedRevision={selectedSampleRevision}
              summary={selectedSummary}
              onOpenRun={onOpenRun}
              onSelectSample={onSelectSample}
            />
          ) : samplesQuery.isPending ? (
            <DetailEmpty
              icon={<LoaderCircle className="animate-spin" />}
              title="Loading samples"
              detail="Reading the project sample registry."
            />
          ) : (
            <DetailEmpty
              icon={<CircleDot />}
              title="No sample selected"
              detail="Choose a chip or sample to inspect its topology, run history, analyses, and revisions."
            />
          )}
        </section>
      </div>
    </>
  );
}

function SampleDetail({
  sampleId,
  selectedRevision,
  summary,
  onOpenRun,
  onSelectSample,
}: {
  sampleId: string;
  selectedRevision?: number;
  summary?: SampleSummary;
  onOpenRun: (runId: string) => void;
  onSelectSample: (sampleId: string, revision?: number) => void;
}) {
  const [selectedEntityId, setSelectedEntityId] = useState<string>();
  const [selectedAnalysisId, setSelectedAnalysisId] = useState<string | undefined>(
    () => new URLSearchParams(window.location.search).get("sample-analysis") ?? undefined,
  );
  const detailQuery = useQuery({
    queryKey: ["sample", sampleId],
    queryFn: ({ signal }) => getSample(sampleId, signal),
  });
  const runsQuery = useInfiniteQuery({
    queryKey: ["runs", "sample", sampleId],
    queryFn: ({ signal, pageParam }) => getSampleRuns(sampleId, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.nextCursor,
  });
  const revisionsQuery = useInfiniteQuery({
    queryKey: ["sample-revisions", sampleId],
    queryFn: ({ signal, pageParam }) => getSampleRevisions(sampleId, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const selectedRevisionQuery = useQuery({
    queryKey: ["sample-revision", sampleId, selectedRevision],
    queryFn: ({ signal }) => getSampleRevision(sampleId, selectedRevision!, signal),
    enabled: selectedRevision !== undefined,
  });
  const analysesQuery = useInfiniteQuery({
    queryKey: ["analyses", "sample", sampleId],
    queryFn: ({ signal, pageParam }) => getSampleAnalyses(sampleId, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const analysisQuery = useQuery({
    queryKey: ["analysis", "sample", sampleId, selectedAnalysisId],
    queryFn: ({ signal }) => getSampleAnalysis(sampleId, selectedAnalysisId!, signal),
    enabled: selectedAnalysisId !== undefined,
  });
  const sample = detailQuery.data;
  const effective = sample ?? summary;

  if (detailQuery.isPending && !effective) {
    return (
      <DetailEmpty
        icon={<LoaderCircle className="animate-spin" />}
        title="Loading sample"
        detail={`Reading ${sampleId}.`}
      />
    );
  }
  if (detailQuery.isError && !effective) {
    return (
      <DetailEmpty
        icon={<Unplug />}
        title="Sample unavailable"
        detail={errorMessage(detailQuery.error)}
      />
    );
  }
  if (!effective) return null;

  const revision = selectedRevisionQuery.data ?? effective.revision;
  const content = revision.content;
  const historical = revision.revision !== effective.record.active_revision;
  const displayedSample: SampleSummary = { ...effective, revision };
  const topology = content.topology;
  const entity = topology?.entities?.find((item) => item.id === selectedEntityId);
  const runs = runsQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const analyses = analysesQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const revisions = revisionsQuery.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <div className="grid gap-4">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-4">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <StatusBadge status={content.status} />
            <span className="text-[0.64rem] font-bold tracking-[0.08em] text-text-dim uppercase">
              {titleCase(effective.record.kind)}
            </span>
            <span className="text-[0.64rem] text-text-dim">Revision {revision.revision}</span>
          </div>
          <h2 className="m-0 text-[clamp(1.35rem,2.4vw,2rem)] font-[650] tracking-[-0.035em]">
            {content.display_name}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[0.7rem] text-text-dim">
            <code className="text-text-soft">{effective.record.id}</code>
            {content.design_ref && (
              <>
                <span>·</span>
                <span>{content.design_ref}</span>
              </>
            )}
          </div>
          {(content.tags.length > 0 || content.aliases.length > 0) && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {content.tags.map((tag) => (
                <Tag key={tag}>#{tag}</Tag>
              ))}
              {content.aliases.map((alias) => (
                <Tag key={alias}>{alias}</Tag>
              ))}
            </div>
          )}
        </div>
        <div className="grid grid-cols-3 overflow-hidden rounded-md border border-line bg-panel-soft">
          <MiniMetric label="Runs" value={String(effective.run_count)} />
          <MiniMetric
            label="Analyses"
            value={`${analyses.length}${analysesQuery.hasNextPage ? "+" : ""}`}
          />
          <MiniMetric label="Entities" value={String(topology?.entities?.length ?? 0)} />
        </div>
      </header>

      {selectedRevisionQuery.isError && (
        <section className="flex items-center justify-between gap-3 rounded-md border border-[rgb(255_140_136_/_27%)] bg-red-soft px-3 py-2 text-[0.68rem] text-[#efc3c0]">
          <span>{errorMessage(selectedRevisionQuery.error)}</span>
          <button
            type="button"
            className="cursor-pointer rounded-md border border-line-strong bg-panel px-2 py-1 text-text-soft"
            onClick={() => onSelectSample(sampleId)}
          >
            View active revision
          </button>
        </section>
      )}

      {historical && (
        <section className="flex items-center justify-between gap-3 rounded-md border border-[rgb(237_201_111_/_23%)] bg-yellow-soft px-3 py-2 text-[0.68rem] text-yellow">
          <span>
            Viewing historical revision {revision.revision}; active revision is{" "}
            {effective.record.active_revision}.
          </span>
          <button
            type="button"
            className="cursor-pointer rounded-md border border-line-strong bg-panel px-2 py-1 text-text-soft"
            onClick={() => onSelectSample(sampleId)}
          >
            View active revision
          </button>
        </section>
      )}

      {content.relations.length > 0 && (
        <section className="flex flex-wrap items-center gap-2" aria-label="Sample relations">
          <GitBranch size={14} className="text-text-dim" aria-hidden="true" />
          {content.relations.map((relation) => (
            <button
              type="button"
              key={`${relation.kind}:${relation.sample_id}`}
              className="cursor-pointer rounded-md border border-line bg-panel-soft px-2 py-1 text-[0.65rem] text-text-soft hover:border-line-strong"
              onClick={() => onSelectSample(relation.sample_id)}
            >
              <span className="text-text-dim">{titleCase(relation.kind)}</span>{" "}
              <code>{relation.sample_id}</code>
            </button>
          ))}
        </section>
      )}

      <div className="grid grid-cols-[minmax(0,1.45fr)_minmax(280px,0.8fr)] gap-4 max-[1120px]:grid-cols-1">
        <section
          className={classes(detailCard, "min-h-[360px]")}
          aria-labelledby="sample-map-heading"
        >
          <SectionHeading
            icon={<MapIcon />}
            eyebrowText="Physical projection"
            title="Sample map"
            id="sample-map-heading"
          />
          <SampleMap
            topology={topology}
            geometry={content.geometry ?? undefined}
            selectedEntityId={selectedEntityId}
            onSelectEntity={setSelectedEntityId}
          />
        </section>

        <div className="grid content-start gap-4">
          <section className={detailCard}>
            <SectionHeading
              icon={<Activity />}
              eyebrowText="Context"
              title={entity ? "Selected entity" : "Sample facts"}
            />
            {entity ? (
              <dl className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[0.7rem]">
                <Fact term="ID">
                  <code>{entity.id}</code>
                </Fact>
                <Fact term="Kind">{titleCase(entity.kind ?? "entity")}</Fact>
                <Fact term="Links">
                  {String(
                    topology?.connections?.filter((connection) =>
                      connection.endpoints.includes(entity.id),
                    ).length ?? 0,
                  )}
                </Fact>
              </dl>
            ) : (
              <dl className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[0.7rem]">
                <Fact term="Created">
                  {effective.record.created_at ? formatDateTime(effective.record.created_at) : "—"}
                </Fact>
                <Fact term="Last run">
                  {effective.last_run_at ? formatRelative(effective.last_run_at) : "No runs"}
                </Fact>
                <Fact term="Connections">{String(topology?.connections?.length ?? 0)}</Fact>
                <Fact term="Artifacts">{String(content.artifacts.length)}</Fact>
              </dl>
            )}
          </section>

          <PropertiesCard sample={displayedSample} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 max-[900px]:grid-cols-1">
        <section className={detailCard} aria-labelledby="sample-runs-heading">
          <SectionHeading
            icon={<FlaskConical />}
            eyebrowText="Provenance"
            title="Runs"
            id="sample-runs-heading"
            count={effective.run_count}
          />
          <HistoryList
            pending={runsQuery.isPending}
            error={runsQuery.error}
            empty="No runs are bound to this sample yet."
          >
            {runs.map((run) => (
              <RunRow
                key={run.runId}
                run={run}
                sampleId={sampleId}
                onOpen={() => onOpenRun(run.runId)}
              />
            ))}
          </HistoryList>
          {runsQuery.hasNextPage && (
            <LoadMoreButton
              pending={runsQuery.isFetchingNextPage}
              onClick={() => void runsQuery.fetchNextPage()}
            >
              Load older runs
            </LoadMoreButton>
          )}
        </section>

        <section className={detailCard} aria-labelledby="sample-analyses-heading">
          <SectionHeading
            icon={<Activity />}
            eyebrowText="Longitudinal"
            title="Analyses"
            id="sample-analyses-heading"
            count={`${analyses.length}${analysesQuery.hasNextPage ? "+" : ""}`}
          />
          <HistoryList
            pending={analysesQuery.isPending}
            error={analysesQuery.error}
            empty="No sample-level analyses have been published."
          >
            {analyses.map((analysis) => (
              <button
                type="button"
                key={analysis.entry.id}
                className={classes(
                  "grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] gap-2 rounded-md border border-transparent bg-transparent px-2.5 py-2 text-left hover:border-line hover:bg-panel",
                  selectedAnalysisId === analysis.entry.id && "border-line-strong bg-panel",
                )}
                onClick={() => setSelectedAnalysisId(analysis.entry.id)}
              >
                <span className="grid min-w-0 gap-1">
                  <strong className="overflow-hidden text-[0.72rem] text-ellipsis whitespace-nowrap">
                    {analysis.title}
                  </strong>
                  <span className="text-[0.63rem] text-text-dim">
                    {analysis.input_count} inputs · {analysis.output_count} outputs
                  </span>
                </span>
                <span className="text-[0.62rem] text-text-dim">r{analysis.revision}</span>
              </button>
            ))}
          </HistoryList>
          {analysesQuery.hasNextPage && (
            <LoadMoreButton
              pending={analysesQuery.isFetchingNextPage}
              onClick={() => void analysesQuery.fetchNextPage()}
            >
              Load older analyses
            </LoadMoreButton>
          )}
          {selectedAnalysisId && (
            <AnalysisPeek
              analysis={analysisQuery.data}
              pending={analysisQuery.isPending}
              error={analysisQuery.error}
              sampleId={sampleId}
              onOpenRun={onOpenRun}
            />
          )}
        </section>
      </div>

      {revisionsQuery.data && (
        <section className={detailCard} aria-labelledby="sample-revisions-heading">
          <SectionHeading
            icon={<History />}
            eyebrowText="Audit trail"
            title="Revision history"
            id="sample-revisions-heading"
            count={`${revisions.length}${revisionsQuery.hasNextPage ? "+" : ""}`}
          />
          <ol className="m-0 grid list-none gap-0 p-0">
            {revisions.map((item, index) => (
              <li
                key={item.revision}
                className="grid grid-cols-[22px_minmax(0,1fr)_auto] gap-2 border-b border-line py-2.5 last:border-0"
              >
                <button
                  type="button"
                  aria-label={`View revision ${item.revision}`}
                  aria-pressed={item.revision === revision.revision}
                  className={classes(
                    "grid size-[22px] cursor-pointer place-items-center rounded-full border border-line-strong bg-panel text-[0.6rem] font-bold text-accent",
                    item.revision === revision.revision && "bg-accent-soft",
                  )}
                  onClick={() => onSelectSample(sampleId, item.revision)}
                >
                  {item.revision}
                </button>
                <button
                  type="button"
                  className="grid cursor-pointer gap-1 border-0 bg-transparent p-0 text-left text-inherit"
                  onClick={() => onSelectSample(sampleId, item.revision)}
                >
                  <strong className="text-[0.7rem]">{item.content.display_name}</strong>
                  <span className="text-[0.63rem] text-text-dim">
                    {item.note ||
                      (index === revisions.length - 1 && !revisionsQuery.hasNextPage
                        ? "Initial registration"
                        : "Revision recorded")}{" "}
                    · {item.actor}
                  </span>
                </button>
                <time className="text-[0.62rem] text-text-dim" dateTime={item.recorded_at}>
                  {item.recorded_at ? formatRelative(item.recorded_at) : "—"}
                </time>
              </li>
            ))}
          </ol>
          {revisionsQuery.hasNextPage && (
            <LoadMoreButton
              pending={revisionsQuery.isFetchingNextPage}
              onClick={() => void revisionsQuery.fetchNextPage()}
            >
              Load older revisions
            </LoadMoreButton>
          )}
        </section>
      )}
    </div>
  );
}

function SampleMap({
  topology,
  geometry,
  selectedEntityId,
  onSelectEntity,
}: {
  topology: SampleView["revision"]["content"]["topology"];
  geometry?: SampleGeometry;
  selectedEntityId?: string;
  onSelectEntity: (entityId: string) => void;
}) {
  const entities = topology?.entities ?? [];
  const connections = topology?.connections ?? [];
  const points = layoutPoints(
    entities.map((entity) => entity.id),
    geometry,
  );
  if (entities.length === 0) {
    return (
      <div className="grid min-h-[285px] place-content-center justify-items-center text-center text-text-dim">
        <MapIcon size={28} className="mb-3" />
        <strong className="text-[0.75rem] text-text-soft">No entity map</strong>
        <span className="mt-1 text-[0.67rem]">
          Add topology and optional geometry to a sample revision.
        </span>
      </div>
    );
  }
  return (
    <div className="mt-3 overflow-hidden rounded-md border border-line bg-[radial-gradient(circle_at_center,rgb(128_163_207_/_6%),transparent_68%)]">
      <svg
        className="block aspect-[16/9] min-h-[280px] w-full"
        viewBox="0 0 1000 560"
        role="img"
        aria-label="Sample topology map"
      >
        <defs>
          <pattern id="sample-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path
              d="M 40 0 L 0 0 0 40"
              fill="none"
              stroke="rgb(255 255 255 / 0.035)"
              strokeWidth="1"
            />
          </pattern>
        </defs>
        <rect width="1000" height="560" fill="url(#sample-grid)" />
        {connections.map((connection) => {
          const [firstEntityId, secondEntityId] = connection.endpoints;
          if (!firstEntityId || !secondEntityId) return null;
          const first = points.get(firstEntityId);
          const second = points.get(secondEntityId);
          if (!first || !second) return null;
          return (
            <g key={connection.id}>
              <line
                x1={first.x}
                y1={first.y}
                x2={second.x}
                y2={second.y}
                stroke="rgb(128 163 207 / 0.34)"
                strokeWidth="2"
              />
              <text
                x={(first.x + second.x) / 2}
                y={(first.y + second.y) / 2 - 7}
                textAnchor="middle"
                fill="#718090"
                fontSize="13"
              >
                {connection.kind}
              </text>
            </g>
          );
        })}
        {entities.map((entity) => {
          const point = points.get(entity.id)!;
          const selected = entity.id === selectedEntityId;
          return (
            <g
              key={entity.id}
              role="button"
              tabIndex={0}
              className="cursor-pointer outline-none"
              onClick={() => onSelectEntity(entity.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") onSelectEntity(entity.id);
              }}
            >
              <circle
                cx={point.x}
                cy={point.y}
                r={selected ? 25 : 20}
                fill={selected ? "#7ce8de" : "#182733"}
                stroke={selected ? "#a5fff7" : "#66849e"}
                strokeWidth={selected ? 4 : 2}
              />
              <circle cx={point.x} cy={point.y} r="5" fill={selected ? "#0a1318" : "#7ce8de"} />
              <text
                x={point.x}
                y={point.y + 39}
                textAnchor="middle"
                fill={selected ? "#dffdfa" : "#a8b4c0"}
                fontSize="15"
                fontWeight="600"
              >
                {entity.id}
              </text>
              <text x={point.x} y={point.y + 56} textAnchor="middle" fill="#667483" fontSize="12">
                {entity.kind ?? "entity"}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="flex items-center justify-between border-t border-line px-3 py-2 text-[0.62rem] text-text-dim">
        <span>
          {geometry ? `Geometry in ${geometry.unit ?? "local units"}` : "Automatic topology layout"}
        </span>
        <span>
          {entities.length} entities · {connections.length} connections
        </span>
      </div>
    </div>
  );
}

function layoutPoints(
  entityIds: string[],
  geometry?: SampleGeometry,
): Map<string, { x: number; y: number }> {
  const explicit = new Map(geometry?.points.map((point) => [point.entity_id, point]) ?? []);
  const width = geometry?.width ?? Math.max(...[...explicit.values()].map((point) => point.x), 1);
  const height = geometry?.height ?? Math.max(...[...explicit.values()].map((point) => point.y), 1);
  const result = new Map<string, { x: number; y: number }>();
  entityIds.forEach((id, index) => {
    const point = explicit.get(id);
    if (point) {
      result.set(id, { x: 90 + (point.x / width) * 820, y: 70 + (point.y / height) * 380 });
      return;
    }
    const angle = (index / Math.max(entityIds.length, 1)) * Math.PI * 2 - Math.PI / 2;
    result.set(id, { x: 500 + Math.cos(angle) * 300, y: 280 + Math.sin(angle) * 180 });
  });
  return result;
}

function PropertiesCard({ sample }: { sample: SampleSummary | SampleView }) {
  const content = sample.revision.content;
  const properties = Object.entries(content.properties ?? {});
  if (properties.length === 0 && content.artifacts.length === 0) return null;
  return (
    <section className={detailCard}>
      <SectionHeading icon={<Boxes />} eyebrowText="Metadata" title="Properties & artifacts" />
      {properties.length > 0 && (
        <dl className="grid grid-cols-[minmax(90px,0.7fr)_minmax(0,1fr)] gap-x-3 gap-y-2 text-[0.68rem]">
          {properties.map(([key, value]) => (
            <Fact key={key} term={titleCase(key)}>
              <code className="break-all">
                {typeof value === "string" ? value : JSON.stringify(value)}
              </code>
            </Fact>
          ))}
        </dl>
      )}
      {content.artifacts.length > 0 && (
        <SampleArtifacts sampleId={sample.record.id} revision={sample.revision.revision} />
      )}
    </section>
  );
}

function AnalysisPeek({
  analysis,
  pending,
  error,
  sampleId,
  onOpenRun,
}: {
  analysis?: AnalysisPublication;
  pending: boolean;
  error: unknown;
  sampleId: string;
  onOpenRun: (runId: string) => void;
}) {
  if (pending)
    return (
      <p className="mt-3 border-t border-line pt-3 text-[0.66rem] text-text-dim">
        Loading analysis outputs…
      </p>
    );
  if (error)
    return (
      <p className="mt-3 border-t border-line pt-3 text-[0.66rem] text-red">
        {errorMessage(error)}
      </p>
    );
  if (!analysis) return null;
  return (
    <div className="mt-3 border-t border-line pt-3">
      <p className="mb-2 text-[0.66rem] font-bold text-text-soft">{analysis.title}</p>
      <AnalysisPublicationView
        analysis={analysis}
        getArtifactDownload={(selector) =>
          getSampleAnalysisArtifactDownload(sampleId, analysis.id, selector)
        }
        onOpenRun={onOpenRun}
      />
    </div>
  );
}

function RunRow({
  run,
  sampleId,
  onOpen,
}: {
  run: ProjectRun;
  sampleId: string;
  onOpen: () => void;
}) {
  const binding = run.samples.find((sample) => sample.sample_id === sampleId);
  return (
    <button
      type="button"
      className="grid w-full cursor-pointer grid-cols-[8px_minmax(0,1fr)_auto] items-center gap-2 rounded-md border border-transparent bg-transparent px-2.5 py-2 text-left hover:border-line hover:bg-panel"
      onClick={onOpen}
    >
      <span
        className={classes(
          "size-2 rounded-full",
          run.status === "succeeded" ? "bg-accent" : run.status === "failed" ? "bg-red" : "bg-blue",
        )}
      />
      <span className="grid min-w-0 gap-1">
        <strong className="overflow-hidden text-[0.72rem] text-ellipsis whitespace-nowrap">
          {run.displayName ?? run.experimentId}
        </strong>
        <code className="text-[0.61rem] text-text-dim">{shorten(run.runId, 22)}</code>
        {binding && (
          <span className="text-[0.59rem] text-text-dim">
            {binding.role} · r{binding.revision}
            {binding.context_id ? ` · ${binding.context_id}` : ""}
          </span>
        )}
      </span>
      <time className="text-[0.61rem] text-text-dim" dateTime={run.updatedAt}>
        {run.updatedAt ? formatRelative(run.updatedAt) : "—"}
      </time>
    </button>
  );
}

function HistoryList({
  children,
  pending,
  error,
  empty,
}: {
  children: ReactNode;
  pending: boolean;
  error: unknown;
  empty: string;
}) {
  if (pending) return <p className="text-[0.67rem] text-text-dim">Loading…</p>;
  if (error) return <p className="text-[0.67rem] text-red">{errorMessage(error)}</p>;
  if (!children || (Array.isArray(children) && children.length === 0))
    return <p className="text-[0.67rem] text-text-dim">{empty}</p>;
  return <div className="grid gap-1">{children}</div>;
}

function LoadMoreButton({
  children,
  pending,
  onClick,
}: {
  children: ReactNode;
  pending: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className="mt-2 min-h-8 w-full cursor-pointer rounded-md border border-line bg-panel-soft px-3 text-[0.66rem] font-bold text-text-dim hover:border-line-strong hover:text-text-soft disabled:cursor-wait"
      disabled={pending}
      onClick={onClick}
    >
      {pending ? "Loading…" : children}
    </button>
  );
}

function SampleListItem({
  sample,
  selected,
  onSelect,
}: {
  sample: SampleSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const content = sample.revision.content;
  return (
    <button
      type="button"
      className={classes(
        "grid min-h-[88px] w-full cursor-pointer grid-cols-[minmax(0,1fr)_18px] gap-2 rounded-md border border-transparent bg-transparent p-2.5 text-left hover:border-line hover:bg-[rgb(255_255_255_/_2%)]",
        selected && "border-line-strong bg-panel-strong",
      )}
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
    >
      <span className="grid min-w-0 content-start">
        <span className="flex min-w-0 items-start justify-between gap-2">
          <strong className="overflow-hidden text-[0.8rem] text-ellipsis whitespace-nowrap">
            {content.display_name}
          </strong>
          <StatusDot status={content.status} />
        </span>
        <span className="mt-1 flex items-center gap-1.5 text-[0.63rem] text-text-dim">
          <code className="overflow-hidden text-ellipsis">{sample.record.id}</code>
          <span>·</span>
          <span>{titleCase(sample.record.kind)}</span>
        </span>
        <span className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="rounded-[5px] border border-line px-1.5 py-1 text-[0.58rem] font-bold text-text-dim">
            {sample.run_count} runs
          </span>
          {content.tags.slice(0, 2).map((tag) => (
            <span
              key={tag}
              className="rounded-[5px] bg-accent-soft px-1.5 py-1 text-[0.58rem] text-accent"
            >
              {tag}
            </span>
          ))}
        </span>
      </span>
      <ChevronRight
        size={16}
        className={classes("self-center text-text-dim", selected && "text-accent")}
      />
    </button>
  );
}

function RegistryMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className="grid grid-cols-[auto_auto_minmax(0,1fr)] items-baseline gap-2 border-r border-line px-3 py-2.5 last:border-0 max-[560px]:border-r-0 max-[560px]:not-first:border-t">
      <span className="text-[0.59rem] font-extrabold tracking-[0.1em] text-text-dim uppercase">
        {label}
      </span>
      <strong className="text-[0.78rem] text-accent">{value}</strong>
      <small className="overflow-hidden text-[0.62rem] text-ellipsis whitespace-nowrap text-text-dim">
        {detail}
      </small>
    </article>
  );
}
function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <span className="grid min-w-[68px] justify-items-center border-r border-line px-2.5 py-2 last:border-0">
      <strong className="text-[0.82rem] text-text">{value}</strong>
      <small className="text-[0.57rem] font-bold tracking-[0.08em] text-text-dim uppercase">
        {label}
      </small>
    </span>
  );
}
function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-md border border-line bg-panel-soft px-2 py-1 text-[0.62rem] text-text-soft">
      {children}
    </span>
  );
}
function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={classes(
        "rounded-[5px] border px-2 py-1 text-[0.59rem] font-extrabold tracking-[0.08em] uppercase",
        status === "mounted"
          ? "border-blue/25 bg-blue-soft text-blue"
          : status === "damaged"
            ? "border-red/25 bg-red-soft text-red"
            : status === "retired"
              ? "border-line bg-panel-soft text-text-dim"
              : "border-accent/20 bg-accent-soft text-accent",
      )}
    >
      {titleCase(status)}
    </span>
  );
}
function StatusDot({ status }: { status: string }) {
  return (
    <span
      className={classes(
        "mt-1 size-2 flex-none rounded-full",
        status === "mounted"
          ? "bg-blue"
          : status === "damaged"
            ? "bg-red"
            : status === "retired"
              ? "bg-text-dim"
              : "bg-accent",
      )}
      aria-label={titleCase(status)}
    />
  );
}
function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-text-dim">{term}</dt>
      <dd className="m-0 min-w-0 text-text-soft">{children}</dd>
    </>
  );
}
function SectionHeading({
  icon,
  eyebrowText,
  title,
  id,
  count,
}: {
  icon: ReactNode;
  eyebrowText: string;
  title: string;
  id?: string;
  count?: ReactNode;
}) {
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div>
        <p className={eyebrow}>{eyebrowText}</p>
        <h3 id={id} className="m-0 flex items-center gap-2 text-[0.86rem]">
          <span className="text-accent [&>svg]:size-4">{icon}</span>
          {title}
        </h3>
      </div>
      {count !== undefined && (
        <span className="rounded-md border border-line bg-panel px-2 py-1 text-[0.62rem] font-bold text-text-dim">
          {count}
        </span>
      )}
    </div>
  );
}
function PanelMessage({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <div className="grid justify-items-center px-5 py-10 text-center text-text-dim">
      <span className="mb-3 grid size-10 place-items-center rounded-[10px] border border-line bg-panel text-text-soft [&>svg]:w-[18px]">
        {icon}
      </span>
      <strong className="text-[0.75rem] text-text-soft">{title}</strong>
      <p className="mt-1.5 mb-0 max-w-[240px] text-[0.67rem] leading-normal">{detail}</p>
    </div>
  );
}
function DetailEmpty({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <div className="grid min-h-[590px] place-content-center justify-items-center text-center">
      <span className="mb-4 grid size-14 place-items-center rounded-[14px] border border-line bg-panel-soft text-text-soft [&>svg]:w-6">
        {icon}
      </span>
      <h2 className="m-0 text-base">{title}</h2>
      <p className="mt-2 mb-0 max-w-[420px] text-[0.72rem] leading-[1.55] text-text-dim">
        {detail}
      </p>
    </div>
  );
}

function filterSamples(
  samples: SampleSummary[],
  search: string,
  status: StatusFilter,
): SampleSummary[] {
  const query = search.trim().toLocaleLowerCase();
  return samples.filter((sample) => {
    const content = sample.revision.content;
    if (status !== "all" && content.status !== status) return false;
    if (!query) return true;
    return [
      sample.record.id,
      sample.record.kind,
      content.display_name,
      ...content.aliases,
      ...content.tags,
      content.design_ref,
    ]
      .filter(Boolean)
      .join(" ")
      .toLocaleLowerCase()
      .includes(query);
  });
}
