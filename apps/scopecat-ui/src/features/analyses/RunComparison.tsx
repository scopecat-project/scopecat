import { useOperatorInput } from "../application/use-operator-input";
import * as Controls from "../../ui/Controls";
import { DecisionRuns } from "./DecisionRuns";
import type { MethodResponse } from "openapi-fetch";
import { useEffect, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  useMutation,
  useIsMutating,
} from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { useLocationUrl } from "../../lib/navigation";
import { useRunCollectionName } from "../runs/use-run-collection-name";
import { useRecordCollectionNames } from "../context/scope-catalog";
import { comparisonChartOption } from "../runs/chart-options";
import { secondaryButton } from "../../ui/styles";
import { useComparisonDraft } from "./comparison-draft";
import { EChartRuntime } from "../../ui/EChartRuntime";
import {
  getRuns,
  getRun,
  getOlderRuns,
  getRunAnalysis,
  getRunAnalysisSummaries,
  getOlderRunAnalysisSummaries,
  getRunArtifactDownload,
} from "../runs/run-api";
import { AuthorRefresh } from "../launch/AuthorRefresh";
import { useAuthorWorkspaces } from "../launch/source-api";
import { SourceSelector } from "../launch/SourceSelector";
import { AnalysisPublicationView } from "./AnalysisPublicationView";
import { AnalysisOutputView } from "../runs/AnalysisOutputView";
import { errorMessage, formatDateTime } from "../../lib/presentation";

type Request = components["schemas"]["ComparisonRequest"];
type Inspection = components["schemas"]["ComparisonInspection"];
export type ComparisonHandoff = Extract<
  MethodResponse<typeof apiClient, "post", "/api/v1/run-comparison">,
  { kind: "handoff" }
>;
async function call(request: Partial<Request> & Pick<Request, "action" | "workspace_id">) {
  const body: Request = {
    actor: "operator",
    reason: "",
    model_id: "",
    model_version: "",
    primary_run: "",
    secondary_run: "",
    analysis_id: "",
    analysis_hash: "",
    ...request,
  };
  return apiData(apiClient.POST("/api/v1/run-comparison", { body }));
}
export function selectedPoints(text: string, count: number): number[] {
  if (!text.trim() || text.split(",").some((token) => !token.trim()))
    throw new Error("Select at least one point from each run.");
  const points = text.split(",").map((value) => Number(value.trim()));
  if (
    points.some((value) => !Number.isInteger(value) || value < 0 || value >= count) ||
    new Set(points).size !== points.length
  )
    throw new Error("Point positions must be distinct integers within the displayed run.");
  return points;
}
export function RunComparison(props: {
  projectId: string | undefined;
  onOpenRun: (id: string) => void;
  onHandoff: (handoff: ComparisonHandoff) => void;
}) {
  useLocationUrl();
  if (!props.projectId) return <p role="status">Loading data space…</p>;
  return (
    <ComparisonPage
      key={`${props.projectId}:${window.history.state?.scopecatPosition ?? 0}`}
      {...props}
    />
  );
}

function ComparisonPage({
  projectId,
  onOpenRun,
  onHandoff,
}: {
  projectId: string | undefined;
  onOpenRun: (id: string) => void;
  onHandoff: (handoff: ComparisonHandoff) => void;
}) {
  const client = useQueryClient();
  const sources = useAuthorWorkspaces(projectId);
  const names = useRecordCollectionNames(projectId, Boolean(projectId));
  const { draft, restored, saveDraft, previewKey } = useComparisonDraft(projectId);
  const [workspaceId, setWorkspaceId] = useState(() => draft.workspaceId);
  const availableSources = sources.data?.items.filter((source) => source.available) ?? [];
  if (!workspaceId && availableSources.length === 1 && availableSources[0])
    setWorkspaceId(availableSources[0].id);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [primary, setPrimary] = useState(() => draft.primary);
  const [secondary, setSecondary] = useState(draft.secondary);
  const [modelId, setModelId] = useState(draft.modelId);
  const [inspection, setInspection] = useState<Inspection | undefined>(
    () => client.getQueryData<Inspection>(previewKey) ?? undefined,
  );
  const [leftPoints, setLeftPoints] = useState(draft.leftPoints);
  const [rightPoints, setRightPoints] = useState(draft.rightPoints);
  const [parameters, setParameters] = useState<Record<string, string>>(draft.parameters);
  const [selected, setSelected] = useState(() => draft.selected);
  const [reason, setReason] = useState(draft.reason);
  const {
    actor,
    setActor,
    ready: operatorReady,
  } = useOperatorInput(restored ? draft.actor : undefined);
  const mutationKey = ["comparison-operation", projectId];
  const pending = useIsMutating({ mutationKey }) > 0;
  const operation = useMutation({
    mutationKey,
    mutationFn: call,
    retry: false,
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["comparison", projectId, "history"] });
    },
  });
  const [error, setError] = useState("");
  useEffect(() => {
    if (!operatorReady) return;
    saveDraft({
      workspaceId,
      primary,
      secondary,
      modelId,
      leftPoints,
      rightPoints,
      parameters,
      selected,
      reason,
      actor,
    });
    client.setQueryData(previewKey, inspection ?? null);
  }, [
    operatorReady,
    workspaceId,
    primary,
    secondary,
    modelId,
    leftPoints,
    rightPoints,
    parameters,
    selected,
    reason,
    actor,
    inspection,
    client,
    previewKey,
    saveDraft,
  ]);
  const catalog = useQuery({
    queryKey: ["comparison", projectId, "models", workspaceId],
    enabled: Boolean(
      projectId &&
      sources.data?.items.some((source) => source.id === workspaceId && source.available),
    ),
    queryFn: async () => {
      const result = await call({ action: "list", workspace_id: workspaceId });
      if (result.kind !== "catalog") throw new Error("Unexpected comparison catalog");
      return result;
    },
  });
  const runs = useInfiniteQuery({
    queryKey: ["comparison", projectId, "runs"],
    enabled: Boolean(projectId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      pageParam === undefined ? getRuns(signal) : getOlderRuns(pageParam, signal),
    getNextPageParam: (page) => page.nextCursor,
  });
  const listed = runs.data?.pages.flatMap((page) => page.items) ?? [];
  const primaryDetail = useQuery({
    queryKey: ["comparison", projectId, "selected-run", primary],
    enabled: Boolean(
      projectId && primary && runs.data && !listed.some((run) => run.runId === primary),
    ),
    queryFn: ({ signal }) => getRun(primary, signal),
  });
  const secondaryDetail = useQuery({
    queryKey: ["comparison", projectId, "selected-run", secondary],
    enabled: Boolean(
      projectId && secondary && runs.data && !listed.some((run) => run.runId === secondary),
    ),
    queryFn: ({ signal }) => getRun(secondary, signal),
  });
  const available = [
    ...new Map(
      [...listed, primaryDetail.data, secondaryDetail.data]
        .filter((run) => run !== undefined && ["succeeded", "failed"].includes(run.status))
        .map((run) => [run!.runId, run!]),
    ).values(),
  ];
  const leftCollection = available.find((run) => run.runId === primary)?.address?.collection_id;
  const rightCollection = available.find((run) => run.runId === secondary)?.address?.collection_id;
  const leftName = useRunCollectionName(projectId, leftCollection, names);
  const rightName = useRunCollectionName(projectId, rightCollection, names);
  if (leftCollection && leftName.name) names.set(leftCollection, leftName.name);
  if (rightCollection && rightName.name) names.set(rightCollection, rightName.name);
  const history = useInfiniteQuery({
    queryKey: ["comparison", projectId, "history", primary],
    enabled: Boolean(projectId && primary),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      pageParam === undefined
        ? getRunAnalysisSummaries(primary, signal)
        : getOlderRunAnalysisSummaries(primary, pageParam, signal),
    getNextPageParam: (page) => page.nextCursor,
  });
  const detail = useQuery({
    queryKey: ["comparison", projectId, "detail", primary, selected],
    enabled: Boolean(projectId && primary && selected),
    queryFn: ({ signal }) => getRunAnalysis(primary, selected, signal),
  });
  const model = catalog.data?.models.find(
    (item) => item.id === (modelId || catalog.data?.models[0]?.id),
  );
  const saved = detail.data;
  const candidate = saved?.outputs.some((output) => output.kind === "parameter_change_proposal");
  const requestFact = saved?.outputs.find(
    (output) => output.kind === "fact" && output.id === "comparison-request",
  );
  const savedRequest =
    requestFact?.kind === "fact" &&
    requestFact.content.schema_id === "scopecat.comparison-request.v2"
      ? requestFact.content.value
      : undefined;
  const savedAction =
    savedRequest &&
    typeof savedRequest === "object" &&
    !Array.isArray(savedRequest) &&
    "action" in savedRequest
      ? savedRequest.action
      : undefined;
  const fit = savedAction === "fit" || savedAction === "candidate";
  const review = savedAction === "reject";
  function selectAnalysis(id: string) {
    setSelected(id);
    const url = new URL(location.href);
    url.searchParams.set("compare", primary);
    if (id) url.searchParams.set("comparison-analysis", id);
    else url.searchParams.delete("comparison-analysis");
    window.history.replaceState(window.history.state, "", url);
  }
  function changeRuns(left: string, right: string) {
    setPrimary(left);
    setSecondary(right);
    setInspection(undefined);
    setLeftPoints("");
    setRightPoints("");
    setSelected("");
    setError("");
  }
  async function act(action: Request["action"]) {
    if (!model && (action === "fit" || action === "inspect")) return;
    if (pending) return;
    if (!actor.trim()) {
      setError("Enter a review actor before continuing.");
      return;
    }
    setError("");
    try {
      const modelParameters =
        action === "fit"
          ? Object.fromEntries(
              (model?.parameters ?? []).map((parameter) => {
                const raw = parameters[parameter.name] ?? String(parameter.default);
                const value = Number(raw);
                if (!raw.trim() || !Number.isFinite(value))
                  throw new Error(`${parameter.label} requires a finite number.`);
                return [parameter.name, value];
              }),
            )
          : {};
      const result = await operation.mutateAsync({
        action,
        workspace_id:
          (action === "fit" ? inspection?.workspace_id : catalog.data?.workspace_id) ?? workspaceId,
        code_revision: action === "fit" ? inspection?.code_revision : catalog.data?.code_revision,
        model_id: model?.id ?? "",
        model_version: model?.version ?? "",
        primary_run: primary,
        secondary_run: secondary,
        actor,
        reason,
        analysis_id: saved?.id ?? "",
        analysis_hash: saved?.publicationHash ?? "",
        parameters: modelParameters,
        ...(action === "fit" && inspection
          ? {
              primary: {
                run_id: primary,
                content_hash: inspection.primary.content_hash,
                points: selectedPoints(leftPoints, inspection.primary.x.length),
              },
              secondary: {
                run_id: secondary,
                content_hash: inspection.secondary.content_hash,
                points: selectedPoints(rightPoints, inspection.secondary.x.length),
              },
            }
          : {}),
      });
      if (!alive.current) return;
      if (result.kind === "inspection") {
        setInspection(result);
        setLeftPoints((current) => current || result.primary.x.map((_, index) => index).join(","));
        setRightPoints(
          (current) => current || result.secondary.x.map((_, index) => index).join(","),
        );
      } else if (result.kind === "publication") {
        selectAnalysis(result.analysis_id);
        await client.invalidateQueries({ queryKey: ["comparison", projectId, "history", primary] });
        await client.invalidateQueries({ queryKey: ["analyses"] });
      } else if (result.kind === "handoff") onHandoff(result);
    } catch (caught) {
      if (alive.current) setError(errorMessage(caught));
    }
  }
  const runLabel = (id: string) => {
    const run = available.find((item) => item.runId === id);
    if (!run?.address) return `${run?.displayName ?? run?.experimentId ?? "Run"} · ${id}`;
    const collection = run.address.collection_id;
    const name = names.get(collection);
    const ambiguous =
      !name || [...names].some(([other, title]) => other !== collection && title === name);
    return `${name ?? "Collection name unavailable"}${ambiguous ? ` [${collection}]` : ""} · #${run.address.number}`;
  };
  return (
    <section
      className="space-y-4 rounded-lg border border-line bg-panel p-5"
      aria-label="Retained run comparison"
    >
      <h2 className="text-lg font-semibold">Compare and reanalyze retained runs</h2>
      <details className="rounded border border-line p-3">
        <summary className="cursor-pointer text-sm">
          Analysis code and source details ·{" "}
          {sources.data?.items.find((item) => item.id === workspaceId)?.name ?? "Choose code"}
        </summary>
        <fieldset disabled={pending} className="mt-3 space-y-3">
          <SourceSelector
            context="analysis"
            catalog={sources}
            workspaceId={workspaceId}
            onSelect={(id) => {
              setWorkspaceId(id);
              setInspection(undefined);
              setModelId("");
            }}
          />
          <AuthorRefresh
            context="analysis"
            workspaceId={workspaceId}
            disabled={!workspaceId}
            projectId={projectId}
            onRefreshed={async () => {
              setInspection(undefined);
              await client.invalidateQueries({ queryKey: ["comparison", projectId, "models"] });
            }}
          />
        </fieldset>
      </details>
      <p className="text-sm text-text-soft">
        No acquisition occurs here. The primary run owns the analysis and candidate base
        configuration; both runs remain independent evidence.
      </p>
      {(catalog.error ||
        runs.error ||
        primaryDetail.error ||
        secondaryDetail.error ||
        sources.error) && (
        <p role="alert">
          {errorMessage(
            catalog.error ??
              runs.error ??
              primaryDetail.error ??
              secondaryDetail.error ??
              sources.error,
          )}
        </p>
      )}
      {catalog.data?.models.length === 0 && (
        <p>
          Declare a comparison_provider in the lab application to expose a versioned Python model.
        </p>
      )}
      <Controls.Field>
        Review actor
        <Controls.Input
          value={actor}
          required
          disabled={pending}
          onChange={(event) => setActor(event.target.value)}
        />
      </Controls.Field>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Controls.Field>
          Primary run{" "}
          <Controls.Select
            className="mt-1 block w-full min-w-0"
            aria-label="Primary run"
            value={primary}
            disabled={pending}
            onChange={(event) => changeRuns(event.target.value, secondary)}
          >
            <option value="">Choose retained run</option>
            {primary && !available.some((run) => run.runId === primary) && (
              <option value={primary}>{runLabel(primary)}</option>
            )}
            {available.map((run) => (
              <option key={run.runId} value={run.runId} title={run.runId}>
                {runLabel(run.runId)}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
        <Controls.Field>
          Secondary run{" "}
          <Controls.Select
            className="mt-1 block w-full min-w-0"
            aria-label="Secondary run"
            value={secondary}
            disabled={pending}
            onChange={(event) => changeRuns(primary, event.target.value)}
          >
            <option value="">Choose retained run</option>
            {secondary && !available.some((run) => run.runId === secondary) && (
              <option value={secondary}>{runLabel(secondary)}</option>
            )}
            {available.map((run) => (
              <option key={run.runId} value={run.runId} title={run.runId}>
                {runLabel(run.runId)}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
        <Controls.Field>
          Python model{" "}
          <Controls.Select
            className="mt-1 block w-full min-w-0"
            aria-label="Python model"
            disabled={pending}
            value={model?.id ?? ""}
            onChange={(event) => {
              setModelId(event.target.value);
              setParameters({});
              setInspection(undefined);
            }}
          >
            {catalog.data?.models.map((item) => (
              <option value={item.id} key={item.id}>
                {item.title} · v{item.version}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
      </div>
      {runs.hasNextPage && (
        <button
          className={secondaryButton}
          disabled={runs.isFetchingNextPage}
          onClick={() => void runs.fetchNextPage()}
        >
          Load older retained runs
        </button>
      )}
      {model && <p>{model.description}</p>}
      <button
        className={secondaryButton}
        type="button"
        disabled={pending || !actor.trim() || !primary || !secondary || !model}
        onClick={() => void act("inspect")}
      >
        Inspect compatible data
      </button>
      {inspection && (
        <>
          <p>
            Independent coordinates normalized to {inspection.primary.coordinate_unit ?? "unitless"}
            ; response in {inspection.primary.observable_unit ?? "unitless"}. Point positions are
            zero-based; grids are not joined.
          </p>
          <EChartRuntime height={340} option={comparisonChartOption(inspection, runLabel)} />
        </>
      )}
      {primary && secondary && (
        <>
          {!inspection && (
            <p role="status" className="text-sm text-text-soft">
              Inspect compatible data to check these inputs before fitting. No analysis runs
              automatically.
            </p>
          )}
          <Controls.Field className="block">
            Primary selected positions
            <Controls.Input
              aria-label="Primary selected positions"
              className="mt-1 w-full"
              value={leftPoints}
              disabled={pending}
              onChange={(event) => setLeftPoints(event.target.value)}
            />
          </Controls.Field>
          <Controls.Field className="block">
            Secondary selected positions
            <Controls.Input
              aria-label="Secondary selected positions"
              className="mt-1 w-full"
              value={rightPoints}
              disabled={pending}
              onChange={(event) => setRightPoints(event.target.value)}
            />
          </Controls.Field>
          {model?.parameters.map((parameter) => (
            <Controls.Field className="block" key={parameter.name}>
              {parameter.label}
              <Controls.Input
                aria-label={parameter.label}
                className="mt-1 block w-40"
                type="number"
                step="any"
                disabled={pending}
                min={parameter.minimum ?? undefined}
                max={parameter.maximum ?? undefined}
                value={parameters[parameter.name] ?? String(parameter.default)}
                onChange={(event) =>
                  setParameters({ ...parameters, [parameter.name]: event.target.value })
                }
              />
            </Controls.Field>
          ))}
          <button
            className={secondaryButton}
            disabled={pending || !inspection || !model || Boolean(catalog.error)}
            onClick={() => void act("fit")}
          >
            Fit selected data and save analysis
          </button>
        </>
      )}
      {(error || detail.error || history.error) && (
        <p role="alert">{error || errorMessage(detail.error ?? history.error)}</p>
      )}
      {pending && <p role="status">Running retained-data operation…</p>}
      {primary && (
        <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside>
            <h3 className="text-base font-semibold leading-6">Primary run analysis history</h3>
            {history.data?.pages
              .flatMap((page) => page.items)
              .map((item) => (
                <button
                  className="block w-full border-b border-line py-2 text-left"
                  key={item.id}
                  disabled={pending}
                  onClick={() => selectAnalysis(item.id)}
                >
                  {item.title} · r{item.revision}
                  <small className="block">
                    {formatDateTime(item.publishedAt)} · {item.id}
                  </small>
                </button>
              ))}
            {history.hasNextPage && (
              <button
                className={secondaryButton}
                disabled={history.isFetchingNextPage}
                onClick={() => void history.fetchNextPage()}
              >
                Load older analyses
              </button>
            )}
          </aside>
          <div className="min-w-0">
            {saved && (
              <>
                <h3 className="text-base font-semibold leading-6">
                  {saved.title} · revision {saved.revision}
                </h3>
                <p>
                  {review
                    ? "Independent review recorded. This is not procedure approval."
                    : candidate
                      ? "Candidate only. No configuration activation or calibration validity is implied."
                      : "Saved analysis. No candidate has been accepted by this operation."}
                </p>
                {saved.outputs
                  .filter((output) => output.kind === "figure")
                  .map((output) => (
                    <section className="my-4 rounded border border-line p-3" key={output.id}>
                      <h4>{output.title}</h4>
                      <AnalysisOutputView
                        output={output}
                        getArtifactDownload={(selector) =>
                          getRunArtifactDownload(primary, selector)
                        }
                      />
                    </section>
                  ))}
                <details className="my-4">
                  <summary>Inputs, model parameters and saved evidence</summary>
                  <AnalysisPublicationView
                    analysis={{
                      ...saved,
                      outputs: saved.outputs.filter((output) => output.kind !== "figure"),
                    }}
                    getArtifactDownload={(selector) => getRunArtifactDownload(primary, selector)}
                    onOpenRun={onOpenRun}
                  />
                </details>
                <DecisionRuns
                  projectId={projectId}
                  runId={primary}
                  analysisId={saved.id}
                  publicationHash={saved.publicationHash}
                  onOpenRun={onOpenRun}
                />
                {fit && (
                  <div className="mt-4 flex flex-wrap gap-3">
                    {!candidate && (
                      <button
                        className={secondaryButton}
                        disabled={pending}
                        onClick={() => void act("candidate")}
                      >
                        Create explicit candidate
                      </button>
                    )}
                    <button
                      className={secondaryButton}
                      disabled={pending}
                      onClick={() => void act("handoff")}
                    >
                      Import suggested inputs into Launch
                    </button>
                    {candidate && (
                      <>
                        <a
                          className="text-accent underline"
                          href={`?run=${encodeURIComponent(primary)}&run-analysis=${encodeURIComponent(saved.id)}#runs`}
                        >
                          Review parameter changes and adoption steps
                        </a>

                        <Controls.Field>
                          Rejection reason
                          <Controls.Input
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                          />
                        </Controls.Field>
                        <button
                          className={secondaryButton}
                          disabled={pending || !reason.trim()}
                          onClick={() => void act("reject")}
                        >
                          Record candidate rejection
                        </button>
                      </>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
