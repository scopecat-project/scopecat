import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { EChartRuntime } from "../../ui/EChartRuntime";
import {
  getRuns,
  getOlderRuns,
  getRunAnalysis,
  getRunAnalysisSummaries,
  getOlderRunAnalysisSummaries,
  getRunArtifactDownload,
} from "../runs/run-api";
import { AnalysisPublicationView } from "./AnalysisPublicationView";
import { AnalysisOutputView } from "../runs/AnalysisOutputView";
import { errorMessage, formatDateTime } from "../../lib/presentation";

type Request = components["schemas"]["ComparisonRequest"];
type Inspection = components["schemas"]["ComparisonInspection"];
export type ComparisonHandoff = components["schemas"]["ComparisonHandoff"];
async function call(request: Partial<Request> & Pick<Request, "action">) {
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
export function RunComparison({
  projectId,
  onOpenRun,
  onHandoff,
}: {
  projectId: string | undefined;
  onOpenRun: (id: string) => void;
  onHandoff: (handoff: ComparisonHandoff) => void;
}) {
  const client = useQueryClient();
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [primary, setPrimary] = useState(
    () => new URLSearchParams(location.search).get("compare") ?? "",
  );
  const [secondary, setSecondary] = useState("");
  const [modelId, setModelId] = useState("");
  const [inspection, setInspection] = useState<Inspection>();
  const [leftPoints, setLeftPoints] = useState("");
  const [rightPoints, setRightPoints] = useState("");
  const [parameters, setParameters] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState(
    () => new URLSearchParams(location.search).get("comparison-analysis") ?? "",
  );
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState("operator");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const catalog = useQuery({
    queryKey: ["comparison", projectId, "models"],
    enabled: Boolean(projectId),
    queryFn: async () => {
      const result = await call({ action: "list" });
      if (result.kind !== "catalog") throw new Error("Unexpected comparison catalog");
      return result.models;
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
  const model = catalog.data?.find((item) => item.id === (modelId || catalog.data?.[0]?.id));
  const saved = detail.data;
  const candidate = saved?.outputs.some((output) => output.kind === "parameter_change_proposal");
  const requestFact = saved?.outputs.find(
    (output) => output.kind === "fact" && output.id === "comparison-request",
  );
  const savedRequest =
    requestFact?.kind === "fact" &&
    requestFact.content.schema_id === "scopecat.comparison-request.v1"
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
    window.history.replaceState(null, "", url);
  }
  function changeRuns(left: string, right: string) {
    setPrimary(left);
    setSecondary(right);
    setInspection(undefined);
    setSelected("");
    setError("");
  }
  async function act(action: Request["action"]) {
    if (!model && (action === "fit" || action === "inspect")) return;
    setPending(true);
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
      const result = await call({
        action,
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
        setLeftPoints(result.primary.x.map((_, index) => index).join(","));
        setRightPoints(result.secondary.x.map((_, index) => index).join(","));
      } else if (result.kind === "publication") {
        selectAnalysis(result.analysis_id);
        await client.invalidateQueries({ queryKey: ["comparison", projectId, "history", primary] });
      } else if (result.kind === "handoff") onHandoff(result);
    } catch (caught) {
      if (alive.current) setError(errorMessage(caught));
    } finally {
      if (alive.current) setPending(false);
    }
  }
  const available =
    runs.data?.pages
      .flatMap((page) => page.items)
      .filter((run) => ["succeeded", "failed"].includes(run.status)) ?? [];
  return (
    <section
      className="space-y-4 rounded-lg border border-line bg-panel p-5"
      aria-label="Retained run comparison"
    >
      <h2 className="text-lg font-semibold">Compare and reanalyze retained runs</h2>
      <p>
        No acquisition occurs here. The primary run owns the analysis and candidate base
        configuration; both runs remain independent evidence.
      </p>
      {catalog.error && <p role="alert">{errorMessage(catalog.error)}</p>}
      {catalog.data?.length === 0 && (
        <p>
          Declare a comparison_provider in the lab application to expose a versioned Python model.
        </p>
      )}
      <div className="flex flex-wrap gap-4">
        <label>
          Primary run{" "}
          <select
            aria-label="Primary run"
            value={primary}
            disabled={pending}
            onChange={(event) => changeRuns(event.target.value, secondary)}
          >
            <option value="">Choose retained run</option>
            {available.map((run) => (
              <option key={run.runId} value={run.runId}>
                {run.displayName ?? run.experimentId} · {run.runId}
              </option>
            ))}
          </select>
        </label>
        <label>
          Secondary run{" "}
          <select
            aria-label="Secondary run"
            value={secondary}
            disabled={pending}
            onChange={(event) => changeRuns(primary, event.target.value)}
          >
            <option value="">Choose retained run</option>
            {available.map((run) => (
              <option key={run.runId} value={run.runId}>
                {run.displayName ?? run.experimentId} · {run.runId}
              </option>
            ))}
          </select>
        </label>
        <label>
          Python model{" "}
          <select
            aria-label="Python model"
            disabled={pending}
            value={model?.id ?? ""}
            onChange={(event) => {
              setModelId(event.target.value);
              setParameters({});
              setInspection(undefined);
            }}
          >
            {catalog.data?.map((item) => (
              <option value={item.id} key={item.id}>
                {item.title} · v{item.version}
              </option>
            ))}
          </select>
        </label>
      </div>
      {runs.hasNextPage && (
        <button disabled={runs.isFetchingNextPage} onClick={() => void runs.fetchNextPage()}>
          Load older retained runs
        </button>
      )}
      {model && <p>{model.description}</p>}
      <button
        type="button"
        disabled={pending || !primary || !secondary || !model}
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
          <EChartRuntime
            height={340}
            option={{
              tooltip: { trigger: "item" },
              legend: {},
              xAxis: { type: "value", name: inspection.primary.coordinate },
              yAxis: { type: "value", name: inspection.primary.observable },
              series: [inspection.primary, inspection.secondary].map((curve) => ({
                name: curve.run_id,
                type: "scatter",
                data: curve.x.map((x, index) => [x, curve.y[index]]),
              })),
            }}
          />
          <label className="block">
            Primary selected positions
            <input
              aria-label="Primary selected positions"
              className="w-full"
              value={leftPoints}
              disabled={pending}
              onChange={(event) => setLeftPoints(event.target.value)}
            />
          </label>
          <label className="block">
            Secondary selected positions
            <input
              aria-label="Secondary selected positions"
              className="w-full"
              value={rightPoints}
              disabled={pending}
              onChange={(event) => setRightPoints(event.target.value)}
            />
          </label>
          {model?.parameters.map((parameter) => (
            <label className="block" key={parameter.name}>
              {parameter.label}
              <input
                aria-label={parameter.label}
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
            </label>
          ))}
          <button disabled={pending} onClick={() => void act("fit")}>
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
            <h3>Primary run analysis history</h3>
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
                <h3>
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
                {fit && (
                  <div className="mt-4 flex flex-wrap gap-3">
                    {!candidate && (
                      <button disabled={pending} onClick={() => void act("candidate")}>
                        Create explicit candidate
                      </button>
                    )}
                    <button disabled={pending} onClick={() => void act("handoff")}>
                      Import suggested inputs into Launch
                    </button>
                    {candidate && (
                      <>
                        <label>
                          Review actor
                          <input value={actor} onChange={(event) => setActor(event.target.value)} />
                        </label>
                        <label>
                          Rejection reason
                          <input
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                          />
                        </label>
                        <button
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
