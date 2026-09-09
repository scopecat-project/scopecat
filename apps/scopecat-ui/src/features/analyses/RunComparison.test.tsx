// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectRun, RunAnalysis } from "../../types";
import { RunComparison, selectedPoints } from "./RunComparison";
import { getRuns, getRunAnalysis, getRunAnalysisSummaries } from "../runs/run-api";

vi.mock("../launch/AuthorRefresh", () => ({ AuthorRefresh: () => null }));
vi.mock("../../ui/EChartRuntime", () => ({ EChartRuntime: () => <div>Two retained curves</div> }));
vi.mock("./AnalysisPublicationView", () => ({
  AnalysisPublicationView: ({ analysis }: { analysis: RunAnalysis }) => (
    <div>Publication {analysis.id}</div>
  ),
}));
vi.mock("../runs/run-api", () => ({
  getRuns: vi.fn(),
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
      (runId) => ({ runId, experimentId: "signal", status: "succeeded" }) as ProjectRun,
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
          schema_id: "scopecat.comparison-request.v1",
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
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RunComparison projectId="project" onOpenRun={vi.fn()} onHandoff={handoff} />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: "User model · v1" });
  fireEvent.change(screen.getByLabelText("Primary run"), { target: { value: "left" } });
  fireEvent.change(screen.getByLabelText("Secondary run"), { target: { value: "right" } });
  fireEvent.click(screen.getByText("Inspect compatible data"));
  await screen.findByText("Two retained curves");
  fireEvent.change(screen.getByLabelText("Primary selected positions"), {
    target: { value: "2,0" },
  });
  fireEvent.change(screen.getByLabelText("Secondary selected positions"), {
    target: { value: "1,2" },
  });
  fireEvent.change(screen.getByLabelText("Offset"), { target: { value: "0.01" } });
  fireEvent.click(screen.getByText("Fit selected data and save analysis"));
  await screen.findByText("Publication fit");
  expect(requests.find((item) => item.action === "fit")).toMatchObject({
    code_revision: { content_hash: "sha256:inspected" },
    primary: { run_id: "left", content_hash: "hash-primary", points: [2, 0] },
    secondary: { points: [1, 2] },
    parameters: { offset: 0.01 },
  });
  fireEvent.click(screen.getByText("Import suggested inputs into Launch"));
  await waitFor(() =>
    expect(handoff).toHaveBeenCalledWith(
      expect.objectContaining({ source_analysis: "fit", source_hash: "hash-fit" }),
    ),
  );
  fireEvent.click(screen.getByText("Create explicit candidate"));
  await screen.findByText("Publication candidate");
  expect(requests.find((item) => item.action === "candidate")).toMatchObject({
    analysis_id: "fit",
    analysis_hash: "hash-fit",
  });
  fireEvent.change(screen.getByLabelText("Rejection reason"), {
    target: { value: "Insufficient evidence" },
  });
  fireEvent.click(screen.getByText("Record candidate rejection"));
  await screen.findByText("Independent review recorded. This is not procedure approval.");
  expect(requests.find((item) => item.action === "reject")).toMatchObject({
    analysis_id: "candidate",
    analysis_hash: "hash-candidate",
    reason: "Insufficient evidence",
  });
});

it("rejects empty, duplicated and out-of-range positions", () => {
  for (const selection of ["", "0,,2", "0,0", "-1", "3"])
    expect(() => selectedPoints(selection, 3)).toThrow(/Select at least one point|Point positions/);
  expect(selectedPoints("2, 0", 3)).toEqual([2, 0]);
});
