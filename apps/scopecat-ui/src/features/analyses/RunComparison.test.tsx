// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectRun, RunAnalysis } from "../../types";
import { RunComparison, selectedPoints } from "./RunComparison";
import { getRun, getRuns, getRunAnalysis, getRunAnalysisSummaries } from "../runs/run-api";

vi.mock("../launch/AuthorRefresh", () => ({ AuthorRefresh: () => null }));
vi.mock("../../ui/EChartRuntime", () => ({ EChartRuntime: () => <div>Two retained curves</div> }));
vi.mock("./AnalysisPublicationView", () => ({
  AnalysisPublicationView: ({ analysis }: { analysis: RunAnalysis }) => (
    <div>Publication {analysis.id}</div>
  ),
}));
vi.mock("../runs/run-api", () => ({
  getRuns: vi.fn(),
  getRun: vi.fn(),
  getOlderRuns: vi.fn(),
  getRunAnalysis: vi.fn(),
  getRunAnalysisSummaries: vi.fn(),
  getOlderRunAnalysisSummaries: vi.fn(),
  getRunArtifactDownload: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("retains ordered selections and exact publication through explicit candidate, rejection and handoff", async () => {
  vi.mocked(getRuns).mockResolvedValue({
    items: ["left", "right"].map(
      (runId, index) =>
        ({
          runId,
          experimentId: "signal",
          status: "succeeded",
          address: { collection_id: "collection", number: index + 1, run_id: runId },
        }) as ProjectRun,
    ),
  });
  vi.mocked(getRunAnalysisSummaries).mockResolvedValue({ items: [] });
  vi.mocked(getRunAnalysis).mockImplementation(async (_run, id): Promise<RunAnalysis> => {
    const outputId = "comparison-request";
    const outputs: RunAnalysis["outputs"] = [
      {
        kind: "fact",
        id: outputId,
        title: outputId,
        metadata: {},
        content: {
          schema_id: "scopecat.comparison-request.v2",
          schema_codec: "scopecat.analysis-fact-schema.v1",
          schema_hash: "sha256:test",
          codec: "json",
          value: { action: id === "review" ? "reject" : id },
        },
      },
    ];
    if (id === "candidate")
      outputs.push({
        kind: "parameter_change_proposal",
        id: "carrier",
        title: "Carrier",
        metadata: {},
        content: { proposal_id: "carrier", record_ref: "candidate" },
      });
    return {
      id,
      title: id,
      key: "comparison",
      revision: 1,
      publicationHash: `hash-${id}`,
      subject: "run",
      publishedAt: "2026-09-09T00:00:00Z",
      inputs: [],
      executions: [],
      outputs,
    };
  });
  const requests: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (new URL(request.url).pathname.endsWith("/record-collections"))
        return Response.json({
          items: [{ id: "collection", name: "Morning sweep" }],
          next_cursor: null,
        });
      if (new URL(request.url).pathname.endsWith("/author-workspaces"))
        return Response.json({
          items: [{ id: "test-source", name: "Test code", available: true }],
        });
      expect(new URL(request.url).pathname).toBe("/api/v1/run-comparison");
      const body = (await request.json()) as Record<string, unknown>;
      requests.push(body);
      if (body.action === "list")
        return Response.json({
          kind: "catalog",
          code_revision: { content_hash: "sha256:catalog" },
          models: [
            {
              id: "model",
              version: "1",
              title: "User model",
              description: "Lab owned",
              coordinate: "frequency",
              observable: "signal",
              parameters: [{ name: "offset", label: "Offset", default: 0 }],
            },
          ],
        });
      if (body.action === "inspect")
        return Response.json({
          kind: "inspection",
          code_revision: { content_hash: "sha256:inspected" },
          ...Object.fromEntries(
            ["primary", "secondary"].map((role, index) => [
              role,
              {
                run_id: index ? "right" : "left",
                content_hash: `hash-${role}`,
                coordinate: "frequency",
                observable: "signal",
                coordinate_unit: "GHz",
                observable_unit: "V",
                x: [4.7, 4.8, 4.9],
                y: [0.8, 1, 0.8],
              },
            ]),
          ),
        });
      if (body.action === "handoff")
        return Response.json({
          kind: "handoff",
          source_run: "left",
          source_analysis: "fit",
          source_hash: "hash-fit",
          request: {
            action: "preview",
            experiment: "signal",
            version: "1",
            request_key: "",
            actor: "operator",
          },
        });
      const id = body.action === "reject" ? "review" : String(body.action);
      return Response.json({
        kind: "publication",
        run_id: "left",
        analysis_id: id,
        publication_hash: `hash-${id}`,
      });
    }),
  );
  const handoff = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (projectId = "project") => (
    <QueryClientProvider client={client}>
      <RunComparison projectId={projectId} onOpenRun={vi.fn()} onHandoff={handoff} />
    </QueryClientProvider>
  );
  window.history.replaceState(null, "", "/?compare=left&comparison-analysis=fit#analyses");
  const waiting = render(
    <QueryClientProvider client={client}>
      <RunComparison projectId={undefined} onOpenRun={vi.fn()} onHandoff={handoff} />
    </QueryClientProvider>,
  );
  expect(window.history.state).toBeNull();
  waiting.unmount();
  window.history.replaceState(null, "", "/");
  let mounted = render(view());
  await screen.findByRole("option", { name: "User model · v1" });
  expect(screen.getAllByRole("option", { name: "Morning sweep · #1" })).toHaveLength(2);
  fireEvent.change(screen.getByLabelText("主 run"), { target: { value: "left" } });
  fireEvent.change(screen.getByLabelText("对照 run"), { target: { value: "right" } });
  fireEvent.click(screen.getByText("检查兼容数据"));
  await screen.findByText("Two retained curves");
  fireEvent.change(screen.getByLabelText("主 run 选点位置"), {
    target: { value: "2,0" },
  });
  fireEvent.change(screen.getByLabelText("对照 run 选点位置"), {
    target: { value: "1,2" },
  });
  fireEvent.change(screen.getByLabelText("Offset"), { target: { value: "0.01" } });
  const beforeRecovery = requests.filter((request) => request.action !== "list").length;
  mounted.unmount();
  mounted = render(view());
  await screen.findByText("Two retained curves");
  expect(screen.getByLabelText("对照 run")).toHaveValue("right");
  expect(screen.getByLabelText("主 run 选点位置")).toHaveValue("2,0");
  expect(screen.getByLabelText("Offset")).toHaveValue(0.01);
  mounted.unmount();
  client.removeQueries({ queryKey: ["comparison-preview"] });
  mounted = render(view());
  expect(screen.queryByText("Two retained curves")).not.toBeInTheDocument();
  expect(screen.getByLabelText("主 run 选点位置")).toHaveValue("2,0");
  expect(screen.getByText("拟合选中数据并保存 analysis")).toBeDisabled();
  expect(requests.filter((request) => request.action !== "list")).toHaveLength(beforeRecovery);
  fireEvent.click(screen.getByText("检查兼容数据"));
  await screen.findByText("Two retained curves");
  expect(screen.getByLabelText("主 run 选点位置")).toHaveValue("2,0");
  fireEvent.click(screen.getByText("拟合选中数据并保存 analysis"));
  await screen.findByText("Publication fit");
  expect(requests.find((item) => item.action === "fit")).toMatchObject({
    code_revision: { content_hash: "sha256:inspected" },
    primary: { run_id: "left", content_hash: "hash-primary", points: [2, 0] },
    secondary: { points: [1, 2] },
    parameters: { offset: 0.01 },
  });
  fireEvent.click(screen.getByText("将建议输入导入准备"));
  await waitFor(() =>
    expect(handoff).toHaveBeenCalledWith(
      expect.objectContaining({ source_analysis: "fit", source_hash: "hash-fit" }),
    ),
  );
  fireEvent.click(screen.getByText("创建候选"));
  await screen.findByText("Publication candidate");
  expect(screen.getByRole("link", { name: "检查参数变更与接纳步骤" })).toHaveAttribute(
    "href",
    "?run=left&run-analysis=candidate#runs",
  );
  expect(requests.find((item) => item.action === "candidate")).toMatchObject({
    analysis_id: "fit",
    analysis_hash: "hash-fit",
  });
  fireEvent.change(screen.getByLabelText("拒绝原因"), {
    target: { value: "Insufficient evidence" },
  });
  fireEvent.click(screen.getByText("记录候选拒绝"));
  await screen.findByText("独立判断已记录，不代表任务已获批准。");
  expect(requests.find((item) => item.action === "reject")).toMatchObject({
    analysis_id: "candidate",
    analysis_hash: "hash-candidate",
    reason: "Insufficient evidence",
  });
  mounted.rerender(view("another-data-space"));
  await act(async () => {});
  expect(screen.getByLabelText("主 run")).toHaveValue("");
  expect(screen.getByLabelText("对照 run")).toHaveValue("");
  expect(screen.queryByText("Two retained curves")).not.toBeInTheDocument();
});

it("rejects empty, duplicated and out-of-range positions", () => {
  for (const selection of ["", "0,,2", "0,0", "-1", "3"])
    expect(() => selectedPoints(selection, 3)).toThrow(/至少选择一个点|点位置必须/);
  expect(selectedPoints("2, 0", 3)).toEqual([2, 0]);
});

it("resolves an older selected run and disambiguates equal collection names and numbers", async () => {
  vi.mocked(getRuns).mockResolvedValue({ items: [addressedRun("recent", "b")] });
  vi.mocked(getRun).mockResolvedValue(addressedRun("old", "a"));
  vi.mocked(getRunAnalysisSummaries).mockResolvedValue({ items: [] });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (new URL(request.url).pathname.endsWith("/record-collections"))
        return Response.json({
          items: [
            { id: "a", name: "Repeated" },
            { id: "b", name: "Repeated" },
          ],
          next_cursor: null,
        });
      return Response.json({ items: [] });
    }),
  );
  window.history.replaceState(null, "", "/?compare=old#analyses");
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RunComparison projectId="space" onOpenRun={vi.fn()} onHandoff={vi.fn()} />
    </QueryClientProvider>,
  );
  await screen.findAllByRole("option", { name: "Repeated [a] · #1" });
  expect(screen.getAllByRole("option", { name: "Repeated [b] · #1" })).toHaveLength(2);
  expect(screen.getByLabelText("主 run")).toHaveValue("old");
  expect(getRun).toHaveBeenCalledWith("old", expect.any(AbortSignal));
});

const addressedRun = (runId: string, collection: string) =>
  ({
    runId,
    experimentId: "signal",
    status: "succeeded",
    address: { run_id: runId, collection_id: collection, number: 1 },
  }) as ProjectRun;

vi.mock("../application/OperatorSettings", () => ({
  readOperatorSettings: vi.fn(async () => ({ revision: 1, operator: "operator" })),
}));
