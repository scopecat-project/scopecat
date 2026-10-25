import * as Controls from "../../ui/Controls";
import type { ObjectParameterContext, ObjectParameterPanel } from "../config/object-parameters";
import { SampleArtifacts } from "./SampleArtifacts";
import { SampleCapabilities } from "./SampleCapabilities";
import { CurrentCapabilities } from "./CurrentCapabilities";
import { useCallback, useMemo, useState, type ReactNode } from "react";
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
  { key: "all", label: "全部" },
  { key: "available", label: "可用" },
  { key: "mounted", label: "已装载" },
  { key: "received", label: "已接收" },
  { key: "retired", label: "已退役" },
  { key: "damaged", label: "已损坏" },
];
const EMPTY_SAMPLES: SampleSummary[] = [];

export function SamplesWorkspace({
  onEditParameters,
  onParameterPanel,
  selectedSampleId,
  selectedSampleRevision,
  onSelectSample,
  onOpenRun,
  daemonUnavailable,
}: {
  onEditParameters?: (context: ObjectParameterContext) => Promise<void>;
  onParameterPanel?: (panel: ObjectParameterPanel | null) => void;
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
        aria-label="样品登记状态"
      >
        <RegistryMetric
          label="样品"
          value={samplesQuery.isSuccess ? String(samples.length) : "—"}
          detail={samplesQuery.hasNextPage ? "已加载的物理标识" : "稳定物理标识"}
        />
        <RegistryMetric
          label="使用中"
          value={samplesQuery.isSuccess ? String(activeCount) : "—"}
          detail="可用或已装载"
        />
        <RegistryMetric
          label="已有映射"
          value={samplesQuery.isSuccess ? String(mappedCount) : "—"}
          detail="含实体拓扑"
        />
      </section>

      <div className="grid min-h-[650px] grid-cols-[minmax(300px,350px)_minmax(0,1fr)] items-start overflow-hidden rounded-lg border border-line bg-panel max-[940px]:block max-[940px]:overflow-visible max-[940px]:bg-transparent">
        <aside
          className="sticky top-[60px] flex max-h-[calc(100vh-72px)] min-h-[650px] flex-col border-r border-line bg-panel-soft px-3 pt-3.5 pb-3 max-[940px]:static max-[940px]:mb-3 max-[940px]:max-h-none max-[940px]:min-h-0 max-[940px]:rounded-lg max-[940px]:border max-[940px]:border-line"
          aria-labelledby="samples-heading"
        >
          <div className="flex items-start justify-between px-1 pb-3">
            <div>
              <p className={eyebrow}>物理对象登记</p>
              <h2 id="samples-heading" className="m-0 text-[1.08rem] font-[650] tracking-[-0.02em]">
                芯片与样品
              </h2>
            </div>
            {samplesQuery.isFetching && (
              <LoaderCircle
                className="mt-2 animate-spin text-text-dim"
                size={17}
                aria-label="正在刷新样品"
              />
            )}
          </div>

          <Controls.Field className="flex min-h-10 items-center gap-2.5 rounded-[9px] border border-line bg-bg px-3 text-text-dim focus-within:border-[rgb(128_163_207_/_55%)]">
            <Search size={16} aria-hidden="true" />
            <span className="sr-only">搜索样品</span>
            <Controls.Input
              className="w-full min-w-0 border-0 bg-transparent placeholder:text-text-dim"
              type="search"
              placeholder="名称、ID、别名、标签"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </Controls.Field>

          <div className="my-2.5 flex gap-1 overflow-x-auto pb-0.5" aria-label="筛选样品">
            {STATUS_FILTERS.map((item) => (
              <button
                key={item.key}
                type="button"
                className={classes(
                  "min-h-7 flex-none cursor-pointer rounded-[7px] border border-transparent bg-transparent px-2 text-xs font-bold text-text-dim hover:text-text-soft",
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
                title="正在读取登记信息"
                detail="正在加载稳定样品标识。"
              />
            )}
            {samplesQuery.isError && (
              <PanelMessage
                icon={<Unplug />}
                title="登记信息不可用"
                detail={errorMessage(samplesQuery.error)}
              />
            )}
            {samplesQuery.isSuccess && samples.length === 0 && (
              <PanelMessage
                icon={<Boxes />}
                title="尚无样品"
                detail="绑定到运行前，请先登记芯片或物理样品。"
              />
            )}
            {samplesQuery.isSuccess && samples.length > 0 && filteredSamples.length === 0 && (
              <PanelMessage
                icon={<Search />}
                title="没有匹配样品"
                detail="请尝试其他生命周期状态或搜索词。"
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
                加载更早样品
              </LoadMoreButton>
            )}
          </div>
        </aside>

        <section className="min-h-[650px] min-w-0 p-[clamp(18px,2vw,28px)] max-[940px]:rounded-lg max-[940px]:border max-[940px]:border-line max-[940px]:bg-panel">
          {daemonUnavailable ? (
            <DetailEmpty
              icon={<Unplug />}
              title="连接后台服务"
              detail="样品登记信息直接从此项目读取。"
            />
          ) : selectedSampleId ? (
            <SampleDetail
              key={selectedSampleId}
              onEditParameters={onEditParameters}
              onParameterPanel={onParameterPanel}
              sampleId={selectedSampleId}
              selectedRevision={selectedSampleRevision}
              summary={selectedSummary}
              onOpenRun={onOpenRun}
              onSelectSample={onSelectSample}
            />
          ) : samplesQuery.isPending ? (
            <DetailEmpty
              icon={<LoaderCircle className="animate-spin" />}
              title="正在加载样品"
              detail="正在读取项目样品登记信息。"
            />
          ) : (
            <DetailEmpty
              icon={<CircleDot />}
              title="尚未选择样品"
              detail="选择芯片或样品以查看拓扑、运行历史、分析和版本。"
            />
          )}
        </section>
      </div>
    </>
  );
}

function ObjectPanelSlot({
  sample,
  entityId,
  onPanel,
}: {
  sample: SampleView["revision"];
  entityId?: string;
  onPanel?: (panel: ObjectParameterPanel | null) => void;
}) {
  const { sample_id: sampleId, revision, content_hash: contentHash } = sample;
  const attach = useCallback(
    (node: HTMLDivElement | null) => {
      onPanel?.(node ? { node, sampleId, revision, contentHash, entityId } : null);
    },
    [onPanel, sampleId, revision, contentHash, entityId],
  );
  return (
    <div className="group/object-panel">
      <div ref={attach} aria-label="样品工作参数面板" />
      <div className="group-has-[section]/object-panel:hidden rounded-lg border border-line bg-panel-soft p-3 text-sm grid gap-2">
        <strong>对象参数</strong>
        <p>在此打开工作表，再选择地图对象以编辑其直接引用的数值。</p>
        <Controls.Button
          onClick={() => {
            const context = document.getElementById("sample-parameter-context");
            context?.setAttribute("open", "");
            context?.scrollIntoView({ block: "center", behavior: "smooth" });
          }}
        >
          选择工作参数上下文
        </Controls.Button>
      </div>
    </div>
  );
}

function SampleDetail({
  onEditParameters,
  onParameterPanel,
  sampleId,
  selectedRevision,
  summary,
  onOpenRun,
  onSelectSample,
}: {
  onEditParameters?: (context: ObjectParameterContext) => Promise<void>;
  onParameterPanel?: (panel: ObjectParameterPanel | null) => void;
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
        title="正在加载样品"
        detail={`正在读取 ${sampleId}。`}
      />
    );
  }
  if (detailQuery.isError && !effective) {
    return (
      <DetailEmpty icon={<Unplug />} title="样品不可用" detail={errorMessage(detailQuery.error)} />
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
            <span className="text-xs font-bold tracking-[0.08em] text-text-dim uppercase">
              {titleCase(effective.record.kind)}
            </span>
            <span className="text-xs text-text-dim">版本 {revision.revision}</span>
          </div>
          <h2 className="m-0 text-[clamp(1.35rem,2.4vw,2rem)] font-[650] tracking-[-0.035em]">
            {content.display_name}
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-dim">
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
          <MiniMetric label="运行记录" value={String(effective.run_count)} />
          <MiniMetric
            label="分析"
            value={`${analyses.length}${analysesQuery.hasNextPage ? "+" : ""}`}
          />
          <MiniMetric label="实体" value={String(topology?.entities?.length ?? 0)} />
        </div>
      </header>

      {selectedRevisionQuery.isError && (
        <section className="flex items-center justify-between gap-3 rounded-md border border-[rgb(255_140_136_/_27%)] bg-red-soft px-3 py-2 text-xs text-red">
          <span>{errorMessage(selectedRevisionQuery.error)}</span>
          <button
            type="button"
            className="cursor-pointer rounded-md border border-line-strong bg-panel px-2 py-1 text-text-soft"
            onClick={() => onSelectSample(sampleId)}
          >
            查看当前版本
          </button>
        </section>
      )}

      {historical && (
        <section className="flex items-center justify-between gap-3 rounded-md border border-[rgb(237_201_111_/_23%)] bg-yellow-soft px-3 py-2 text-xs text-yellow">
          <span>
            正在查看历史版本 {revision.revision}；当前版本为 {effective.record.active_revision}.
          </span>
          <button
            type="button"
            className="cursor-pointer rounded-md border border-line-strong bg-panel px-2 py-1 text-text-soft"
            onClick={() => onSelectSample(sampleId)}
          >
            查看当前版本
          </button>
        </section>
      )}

      {content.relations.length > 0 && (
        <section className="flex flex-wrap items-center gap-2" aria-label="样品关系">
          <GitBranch size={14} className="text-text-dim" aria-hidden="true" />
          {content.relations.map((relation) => (
            <button
              type="button"
              key={`${relation.kind}:${relation.sample_id}`}
              className="cursor-pointer rounded-md border border-line bg-panel-soft px-2 py-1 text-xs text-text-soft hover:border-line-strong"
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
          className={classes(
            detailCard,
            "min-h-[360px] self-start sticky top-20 max-[1120px]:static",
          )}
          aria-labelledby="sample-map-heading"
        >
          <SectionHeading
            icon={<MapIcon />}
            eyebrowText="物理投影"
            title="样品地图"
            id="sample-map-heading"
          />
          <SampleMap
            topology={topology}
            geometry={content.geometry ?? undefined}
            selectedEntityId={selectedEntityId}
            onSelectEntity={setSelectedEntityId}
          />
        </section>

        <div className="grid content-start gap-4 min-w-0">
          <ObjectPanelSlot
            sample={revision}
            entityId={selectedEntityId}
            onPanel={onParameterPanel}
          />
          <section className={detailCard}>
            <SectionHeading
              icon={<Activity />}
              eyebrowText="上下文"
              title={entity ? "选中实体" : "样品信息"}
            />
            {entity ? (
              <dl className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
                <Fact term="ID">
                  <code>{entity.id}</code>
                </Fact>
                <Fact term="类型">{titleCase(entity.kind ?? "entity")}</Fact>
                <Fact term="关联">
                  {String(
                    topology?.connections?.filter((connection) =>
                      connection.endpoints.includes(entity.id),
                    ).length ?? 0,
                  )}
                </Fact>
              </dl>
            ) : (
              <dl className="grid grid-cols-[90px_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
                <Fact term="创建时间">
                  {effective.record.created_at ? formatDateTime(effective.record.created_at) : "—"}
                </Fact>
                <Fact term="最近运行">
                  {effective.last_run_at ? formatRelative(effective.last_run_at) : "无运行记录"}
                </Fact>
                <Fact term="连接">{String(topology?.connections?.length ?? 0)}</Fact>
                <Fact term="产物">{String(content.artifacts.length)}</Fact>
              </dl>
            )}
          </section>

          <PropertiesCard sample={displayedSample} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 max-[900px]:grid-cols-1">
        <CurrentCapabilities
          key={`current:${sampleId}:${revision.revision}`}
          sampleId={sampleId}
          revision={revision.revision}
          sample={revision}
          onEditParameters={onEditParameters}
        />
        <SampleCapabilities
          key={`${sampleId}:${revision.revision}`}
          sampleId={sampleId}
          revision={revision.revision}
          runs={runs}
        />
        <section className={detailCard} aria-labelledby="sample-runs-heading">
          <SectionHeading
            icon={<FlaskConical />}
            eyebrowText="来源"
            title="运行记录"
            id="sample-runs-heading"
            count={effective.run_count}
          />
          <HistoryList
            pending={runsQuery.isPending}
            error={runsQuery.error}
            empty="尚无运行绑定到此样品。"
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
              加载更早运行
            </LoadMoreButton>
          )}
        </section>

        <section className={detailCard} aria-labelledby="sample-analyses-heading">
          <SectionHeading
            icon={<Activity />}
            eyebrowText="历次记录"
            title="分析"
            id="sample-analyses-heading"
            count={`${analyses.length}${analysesQuery.hasNextPage ? "+" : ""}`}
          />
          <HistoryList
            pending={analysesQuery.isPending}
            error={analysesQuery.error}
            empty="尚未发布样品级分析。"
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
                  <strong className="overflow-hidden text-xs text-ellipsis whitespace-nowrap">
                    {analysis.title}
                  </strong>
                  <span className="text-xs text-text-dim">
                    {analysis.input_count} 项输入 · {analysis.output_count} 项输出
                  </span>
                </span>
                <span className="text-xs text-text-dim">r{analysis.revision}</span>
              </button>
            ))}
          </HistoryList>
          {analysesQuery.hasNextPage && (
            <LoadMoreButton
              pending={analysesQuery.isFetchingNextPage}
              onClick={() => void analysesQuery.fetchNextPage()}
            >
              加载更早分析
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
            eyebrowText="审计记录"
            title="版本历史"
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
                  aria-label={`查看版本 ${item.revision}`}
                  aria-pressed={item.revision === revision.revision}
                  className={classes(
                    "grid size-[22px] cursor-pointer place-items-center rounded-full border border-line-strong bg-panel text-xs font-bold text-accent",
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
                  <strong className="text-xs">{item.content.display_name}</strong>
                  <span className="text-xs text-text-dim">
                    {item.note ||
                      (index === revisions.length - 1 && !revisionsQuery.hasNextPage
                        ? "首次登记"
                        : "版本已记录")}{" "}
                    · {item.actor}
                  </span>
                </button>
                <time className="text-xs text-text-dim" dateTime={item.recorded_at}>
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
              加载更早版本
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
        <strong className="text-sm text-text-soft">无实体地图</strong>
        <span className="mt-1 text-xs">请为样品版本添加拓扑和可选几何信息。</span>
      </div>
    );
  }
  return (
    <div className="mt-3 overflow-hidden rounded-md border border-line bg-[radial-gradient(circle_at_center,rgb(128_163_207_/_6%),transparent_68%)]">
      <svg
        className="block aspect-[16/9] min-h-[280px] w-full"
        viewBox="0 0 1000 560"
        role="img"
        aria-label="样品拓扑地图"
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
              aria-label={`选择对象 ${entity.id}`}
              aria-pressed={selected}
              tabIndex={0}
              className="cursor-pointer"
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
      <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-text-dim">
        <span>{geometry ? `几何单位：${geometry.unit ?? "本地单位"}` : "自动拓扑布局"}</span>
        <span>
          {entities.length} 个实体 · {connections.length} 条连接
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
      <SectionHeading icon={<Boxes />} eyebrowText="元数据" title="属性与产物" />
      {properties.length > 0 && (
        <dl className="grid grid-cols-[minmax(90px,0.7fr)_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
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
      <p className="mt-3 border-t border-line pt-3 text-xs text-text-dim">正在加载分析输出…</p>
    );
  if (error)
    return <p className="mt-3 border-t border-line pt-3 text-xs text-red">{errorMessage(error)}</p>;
  if (!analysis) return null;
  return (
    <div className="mt-3 border-t border-line pt-3">
      <p className="mb-2 text-xs font-bold text-text-soft">{analysis.title}</p>
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
        <strong className="overflow-hidden text-xs text-ellipsis whitespace-nowrap">
          {run.displayName ?? run.experimentId}
        </strong>
        <code className="text-xs text-text-dim">{shorten(run.runId, 22)}</code>
        {binding && (
          <span className="text-xs text-text-dim">
            {binding.role} · r{binding.revision}
            {binding.context_id ? ` · ${binding.context_id}` : ""}
          </span>
        )}
      </span>
      <time className="text-xs text-text-dim" dateTime={run.updatedAt}>
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
  if (pending) return <p className="text-xs text-text-dim">正在加载…</p>;
  if (error) return <p className="text-xs text-red">{errorMessage(error)}</p>;
  if (!children || (Array.isArray(children) && children.length === 0))
    return <p className="text-xs text-text-dim">{empty}</p>;
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
      className="mt-2 min-h-8 w-full cursor-pointer rounded-md border border-line bg-panel-soft px-3 text-xs font-bold text-text-dim hover:border-line-strong hover:text-text-soft disabled:cursor-wait"
      disabled={pending}
      onClick={onClick}
    >
      {pending ? "正在加载…" : children}
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
          <strong className="overflow-hidden text-sm text-ellipsis whitespace-nowrap">
            {content.display_name}
          </strong>
          <StatusDot status={content.status} />
        </span>
        <span className="mt-1 flex items-center gap-1.5 text-xs text-text-dim">
          <code className="overflow-hidden text-ellipsis">{sample.record.id}</code>
          <span>·</span>
          <span>{titleCase(sample.record.kind)}</span>
        </span>
        <span className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="rounded-[5px] border border-line px-1.5 py-1 text-xs font-bold text-text-dim">
            {sample.run_count} 次运行
          </span>
          {content.tags.slice(0, 2).map((tag) => (
            <span
              key={tag}
              className="rounded-[5px] bg-accent-soft px-1.5 py-1 text-xs text-accent"
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
      <span className="text-xs font-extrabold tracking-[0.1em] text-text-dim uppercase">
        {label}
      </span>
      <strong className="text-sm text-accent">{value}</strong>
      <small className="overflow-hidden text-xs text-ellipsis whitespace-nowrap text-text-dim">
        {detail}
      </small>
    </article>
  );
}
function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <span className="grid min-w-[68px] justify-items-center border-r border-line px-2.5 py-2 last:border-0">
      <strong className="text-sm text-text">{value}</strong>
      <small className="text-xs font-bold tracking-[0.08em] text-text-dim uppercase">{label}</small>
    </span>
  );
}
function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="rounded-md border border-line bg-panel-soft px-2 py-1 text-xs text-text-soft">
      {children}
    </span>
  );
}
function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={classes(
        "rounded-[5px] border px-2 py-1 text-xs font-extrabold tracking-[0.08em] uppercase",
        status === "mounted"
          ? "border-blue/25 bg-blue-soft text-blue"
          : status === "damaged"
            ? "border-red/25 bg-red-soft text-red"
            : status === "retired"
              ? "border-line bg-panel-soft text-text-dim"
              : "border-accent/20 bg-accent-soft text-accent",
      )}
    >
      {STATUS_FILTERS.find((item) => item.key === status)?.label ?? status}
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
      aria-label={STATUS_FILTERS.find((item) => item.key === status)?.label ?? status}
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
        <h3 id={id} className="m-0 flex items-center gap-2 text-sm">
          <span className="text-accent [&>svg]:size-4">{icon}</span>
          {title}
        </h3>
      </div>
      {count !== undefined && (
        <span className="rounded-md border border-line bg-panel px-2 py-1 text-xs font-bold text-text-dim">
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
      <strong className="text-sm text-text-soft">{title}</strong>
      <p className="mt-1.5 mb-0 max-w-[240px] text-xs leading-normal">{detail}</p>
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
      <p className="mt-2 mb-0 max-w-[420px] text-xs leading-[1.55] text-text-dim">{detail}</p>
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
