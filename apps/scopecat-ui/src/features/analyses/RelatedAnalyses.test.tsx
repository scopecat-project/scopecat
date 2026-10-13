// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { AnalysisPublication, ProjectRun } from "../../types";
import { getRun, getRunAnalysis } from "../runs/run-api";
import { getSample } from "../samples/sample-api";
import { RelatedAnalyses } from "./RelatedAnalyses";

vi.mock("../runs/run-api", () => ({
  getRun: vi.fn(),
  getRunAnalysis: vi.fn(),
  getRunArtifactDownload: vi.fn(),
}));
vi.mock("../samples/sample-api", () => ({
  getSample: vi.fn(),
  getSampleAnalysis: vi.fn(),
  getSampleAnalysisArtifactDownload: vi.fn(),
}));
vi.mock("./AnalysisPublicationView", () => ({
  AnalysisPublicationView: ({
    analysis,
    onOpenRun,
  }: {
    analysis: AnalysisPublication;
    onOpenRun: (runId: string) => void;
  }) => (
    <div>
      {analysis.title}
      <button onClick={() => onOpenRun("other-input")}>Open input</button>
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

it("isolates the same run, publication and source references across data spaces without clearing the cache", async () => {
  let space = "a";
  const onOpenRun = vi.fn();
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const summary = (sample = false) => ({
    subject: sample
      ? { kind: "sample", sample_id: "same-sample" }
      : { kind: "run", run_id: "same-run" },
    record: {
      id: sample ? "sample-publication" : "same-publication",
      role: "record",
      kind: "analysis",
      content_hash: "hash",
    },
    title: `${space} ${sample ? "sample" : "run"} result`,
    analysis_key: "series",
    revision: 1,
    publication_hash: "hash",
    published_at: "2026-10-13T00:00:00Z",
    input_count: 2,
    output_count: 0,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(input instanceof Request ? input.url : String(input), "http://localhost");
      if (url.pathname.endsWith("/record-collections/collection"))
        return Response.json({ id: "collection", name: `Collection ${space}` });
      expect(url.pathname).toBe("/api/v1/analysis-publications");
      expect(url.searchParams.get("input_run_id")).toBe("same-run");
      return Response.json(
        url.searchParams.has("before")
          ? { items: [summary(true)], next_cursor: null }
          : { items: [summary()], next_cursor: 2 },
      );
    }),
  );
  vi.mocked(getRun).mockImplementation(
    async () =>
      ({
        runId: "same-run",
        experimentId: `Experiment ${space}`,
        address: { collection_id: "collection", number: 7, run_id: "same-run" },
      }) as ProjectRun,
  );
  vi.mocked(getSample).mockImplementation(
    async () =>
      ({ revision: { content: { display_name: `Chip ${space}` } } }) as Awaited<
        ReturnType<typeof getSample>
      >,
  );
  vi.mocked(getRunAnalysis).mockImplementation(
    async () =>
      ({ id: "same-publication", title: `Detail ${space}` }) as Awaited<
        ReturnType<typeof getRunAnalysis>
      >,
  );
  const view = () => (
    <QueryClientProvider client={client}>
      <RelatedAnalyses
        projectId={space}
        runId="same-run"
        collectionNames={new Map()}
        onOpenRun={onOpenRun}
      />
    </QueryClientProvider>
  );
  const mounted = render(view());
  await screen.findByText("Collection a #7 · Experiment a");
  fireEvent.click(screen.getByRole("button", { name: "Load older related analyses" }));
  await screen.findByText("Sample Chip a");
  const open = async (title: string) => {
    const row = screen.getByText(title).closest("details")!;
    row.open = true;
    fireEvent(row, new Event("toggle"));
    await screen.findByText(`Detail ${space}`);
  };
  await open("a run result");
  fireEvent.click(screen.getByRole("button", { name: "Open input" }));
  expect(onOpenRun).toHaveBeenCalledWith("other-input");
  space = "b";
  mounted.rerender(view());
  await screen.findByText("b run result");
  await screen.findByText("Collection b #7 · Experiment b");
  expect(screen.queryByText("Detail a")).not.toBeInTheDocument();
  expect(screen.queryByText("a sample result")).not.toBeInTheDocument();
  expect(screen.getByText("b run result").closest("details")).not.toHaveAttribute("open");
  fireEvent.click(screen.getByRole("button", { name: "Load older related analyses" }));
  await screen.findByText("Sample Chip b");
  await open("b run result");
  await waitFor(() => expect(getRunAnalysis).toHaveBeenCalledTimes(2));
  space = "a";
  mounted.rerender(view());
  await screen.findByText("a sample result");
  expect(screen.queryByText("Detail b")).not.toBeInTheDocument();
  await open("a run result");
  expect(getRunAnalysis).toHaveBeenCalledTimes(2);
  client.clear();
});
