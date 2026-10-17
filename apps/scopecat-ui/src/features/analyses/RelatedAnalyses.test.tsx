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
      <span data-testid="publication-id">{analysis.id}</span>
      <details>
        <summary>Sources and execution evidence</summary>
        <button onClick={() => onOpenRun("other-input")}>Open input</button>
      </details>
    </div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
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
      expect(url.searchParams.get("include_owned")).toBe("true");
      return Response.json(
        url.searchParams.has("before")
          ? { items: [summary(), summary(true)], next_cursor: null }
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
  vi.mocked(getRunAnalysis).mockImplementation(async () => ({
    id: "same-publication",
    title: `Detail ${space}`,
    inputs: [],
    outputs: [],
    subject: "run",
    revision: 1,
    publicationHash: "hash",
    publishedAt: "2026-10-13T00:00:00Z",
    executions: [],
  }));
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
  fireEvent.click(screen.getByRole("button", { name: "Load older analyses" }));
  await screen.findByText("Sample Chip a");
  expect(screen.getAllByText("a run result")).toHaveLength(1);
  const open = async (title: string) => {
    const row = screen.getByText(title).closest("details")!;
    row.open = true;
    fireEvent(row, new Event("toggle"));
    await screen.findByText(`Detail ${space}`);
  };
  await open("a run result");
  screen.getByText("Sources and execution evidence").closest("details")!.open = true;
  fireEvent.click(screen.getByRole("button", { name: "Open input" }));
  expect(onOpenRun).toHaveBeenCalledWith("other-input");
  expect(window.history.state.runResults.details).toEqual({ "same-publication": [0] });
  space = "b";
  mounted.rerender(view());
  await screen.findByText("b run result");
  await screen.findByText("Collection b #7 · Experiment b");
  expect(screen.queryByText("Detail a")).not.toBeInTheDocument();
  expect(screen.queryByText("a sample result")).not.toBeInTheDocument();
  expect(screen.getByText("b run result").closest("details")).not.toHaveAttribute("open");
  fireEvent.click(screen.getByRole("button", { name: "Load older analyses" }));
  await screen.findByText("Sample Chip b");
  await open("b run result");
  expect(screen.getByText("Sources and execution evidence").closest("details")).not.toHaveAttribute(
    "open",
  );
  await waitFor(() => expect(getRunAnalysis).toHaveBeenCalledTimes(2));
  space = "a";
  mounted.rerender(view());
  await screen.findByText("a sample result");
  expect(screen.queryByText("Detail b")).not.toBeInTheDocument();
  await open("a run result");
  expect(getRunAnalysis).toHaveBeenCalledTimes(2);
  client.clear();
});

it("keeps an exact off-page publication once when its list page arrives", async () => {
  window.history.replaceState(null, "", "/?run-analysis=exact#runs");
  vi.mocked(getRun).mockResolvedValue({ runId: "owner", experimentId: "Signal" } as ProjectRun);
  vi.mocked(getRunAnalysis).mockResolvedValue({
    id: "exact",
    title: "Exact version",
    revision: 2,
    publicationHash: "hash",
    publishedAt: "2026-10-13T00:00:00Z",
    subject: "run",
    inputs: [],
    outputs: [],
    executions: [],
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      Response.json(
        new URL(request.url).searchParams.has("before")
          ? { items: [publication("exact")], next_cursor: null }
          : { items: [publication("newer")], next_cursor: 2 },
      ),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RelatedAnalyses projectId="space" runId="owner" collectionNames={new Map()} />
    </QueryClientProvider>,
  );
  await screen.findByTestId("publication-id");
  fireEvent.click(await screen.findByRole("button", { name: "Load older analyses" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Load older analyses" })).not.toBeInTheDocument(),
  );
  expect(screen.getAllByTestId("publication-id")).toHaveLength(1);
  expect(screen.getByTestId("publication-id")).toHaveTextContent("exact");
  expect(screen.getByRole("link", { name: "Open saved version" })).toHaveAttribute(
    "href",
    "?run=owner&run-analysis=exact#runs",
  );
  expect(screen.getByText("None declared")).toBeVisible();
});

it("ignores an old-space list response arriving after a switch", async () => {
  let finish!: (value: Response) => void;
  vi.mocked(getRun).mockResolvedValue({ runId: "owner", experimentId: "Signal" } as ProjectRun);
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValue(Response.json({ items: [publication("space-b")], next_cursor: null })),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (space: string) => (
    <QueryClientProvider client={client}>
      <RelatedAnalyses projectId={space} runId="owner" collectionNames={new Map()} />
    </QueryClientProvider>
  );
  const mounted = render(view("a"));
  mounted.rerender(view("b"));
  await screen.findByText("space-b");
  finish(Response.json({ items: [publication("space-a")], next_cursor: null }));
  await waitFor(() => expect(screen.queryByText("space-a")).not.toBeInTheDocument());
});
it("waits for every expanded cold detail before restoring the saved reading position", async () => {
  window.history.replaceState(
    {
      runResults: {
        projectId: "space",
        runId: "owner",
        expanded: ["first", "second"],
        details: { first: [0], second: [0] },
        scroll: 900,
      },
    },
    "",
    "/",
  );
  const scroll = vi.fn(() => {
    for (const summary of screen.getAllByText("Sources and execution evidence")) {
      expect(summary.closest("details")).toHaveAttribute("open");
    }
  });
  vi.stubGlobal("scrollTo", scroll);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({ items: [publication("first"), publication("second")], next_cursor: null }),
    ),
  );
  vi.mocked(getRun).mockResolvedValue({ runId: "owner", experimentId: "Signal" } as ProjectRun);
  let finish!: (value: Awaited<ReturnType<typeof getRunAnalysis>>) => void;
  const detail = {
    id: "first",
    title: "First detail",
    revision: 1,
    publicationHash: "hash",
    publishedAt: "2026-10-13T00:00:00Z",
    subject: "run" as const,
    inputs: [],
    outputs: [],
    executions: [],
  };
  vi.mocked(getRunAnalysis).mockImplementation(async (_run, id) =>
    id === "first"
      ? detail
      : new Promise((resolve) => {
          finish = resolve;
        }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RelatedAnalyses projectId="space" runId="owner" collectionNames={new Map()} />
    </QueryClientProvider>,
  );
  await screen.findByText("First detail");
  expect(scroll).not.toHaveBeenCalled();
  finish({ ...detail, id: "second", title: "Second detail" });
  await screen.findByText("Second detail");
  await waitFor(() => expect(scroll).toHaveBeenCalledExactlyOnceWith(0, 900));
});

function publication(id: string) {
  return {
    subject: { kind: "run", run_id: "owner" },
    record: { id, role: "record", kind: "analysis", content_hash: "hash" },
    title: id,
    analysis_key: "series",
    revision: 1,
    publication_hash: "hash",
    published_at: "2026-10-13T00:00:00Z",
    input_count: 0,
    output_count: 0,
  };
}
