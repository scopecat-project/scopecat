// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { getInstanceByDom } from "echarts/core";
import { getWaitingProcedures } from "./features/decisions/decision-api";
vi.mock("./features/decisions/decision-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./features/decisions/decision-api")>()),
  getWaitingProcedures: vi.fn(),
}));
import { getEvents, getHealth } from "./data/project-api";
import {
  getMeasurementLivePreview,
  getMeasurementPreview,
  getMeasurementSlice,
  getMeasurementTracePreview,
  getOlderRuns,
  getRun,
  getRunAnalysisSummaries,
  getRunEvents,
  getRuns,
} from "./features/runs/run-api";
import type { MeasurementDatasetSchema, MeasurementRecord } from "./api-contract";
import type { ProjectRun } from "./types";

vi.mock("./data/project-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./data/project-api")>()),
  getEvents: vi.fn(),
  getHealth: vi.fn(),
}));

vi.mock("./features/runs/run-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./features/runs/run-api")>()),
  getMeasurementLivePreview: vi.fn(),
  getMeasurementPreview: vi.fn(),
  getMeasurementSlice: vi.fn(),
  getMeasurementTracePreview: vi.fn(),
  getOlderRuns: vi.fn(),
  getRun: vi.fn(),
  getRunAnalysisSummaries: vi.fn(),
  getRunEvents: vi.fn(),
  getRuns: vi.fn(),
}));

vi.mock("./features/config/ConfigWorkspace", () => ({
  ConfigWorkspace: ({
    onSelectConfiguration,
  }: {
    onSelectConfiguration?: (choice: {
      kind: "parameters";
      ref: { revision_id: string; content_hash: string };
      overrides: [];
    }) => void;
  }) => (
    <div>
      <p>实验配置</p>
      <button
        onClick={() =>
          onSelectConfiguration?.({
            kind: "parameters",
            ref: { revision_id: "parameters-b", content_hash: "hash-b" },
            overrides: [],
          })
        }
      >
        用于下次实验
      </button>
    </div>
  ),
}));

vi.mock("./features/analyses/AnalysesWorkspace", () => ({
  AnalysesWorkspace: ({
    onOpenRun,
    onSelectAnalysis,
    selectedAnalysisId,
  }: {
    onOpenRun: (runId: string) => void;
    onSelectAnalysis: (analysisId: string) => void;
    selectedAnalysisId?: string;
  }) => (
    <div>
      项目分析工作区
      <span>Selected analysis {selectedAnalysisId ?? "none"}</span>
      <button type="button" onClick={() => onOpenRun("run-2")}>
        Open analysis input run
      </button>
      <button type="button" onClick={() => onSelectAnalysis("analysis-next")}>
        Select next analysis
      </button>
    </div>
  ),
}));

vi.mock("./features/samples/SamplesWorkspace", () => ({
  SamplesWorkspace: ({
    onOpenRun,
    onSelectSample,
    selectedSampleId,
  }: {
    onOpenRun: (runId: string) => void;
    onSelectSample: (sampleId: string) => void;
    selectedSampleId?: string;
  }) => (
    <div>
      Sample workspace
      <span>Selected sample {selectedSampleId ?? "none"}</span>
      <button type="button" onClick={() => onOpenRun("run-2")}>
        Open sample run
      </button>
      <button type="button" onClick={() => onSelectSample("chip-b22")}>
        Select next sample
      </button>
    </div>
  ),
}));

vi.mock("./features/instruments/InstrumentsWorkspace", () => ({
  InstrumentsWorkspace: () => <div>Instrument workspace</div>,
}));

vi.mock("./features/proposals/RunProposals", () => ({
  RunProposals: () => <div>Proposal details</div>,
}));

vi.mock("./features/launch/LaunchWorkspace", () => ({
  LaunchWorkspace: () => <div>Calibration launcher</div>,
}));

const RUNS = [projectRun("run-1"), projectRun("run-2")];
let projectEventListener: ((event: Event) => void) | undefined;
let openEventListener: ((event: Event) => void) | undefined;

beforeEach(() => {
  vi.mocked(getWaitingProcedures).mockResolvedValue({ items: [] });
  projectEventListener = undefined;
  openEventListener = undefined;
  window.history.replaceState(null, "", "#configuration");
  vi.stubGlobal("scrollTo", vi.fn());
  vi.stubGlobal(
    "EventSource",
    class {
      addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
        const callback = (event: Event) => {
          if (typeof listener === "function") {
            listener(event);
          } else {
            listener.handleEvent(event);
          }
        };
        if (type === "open") openEventListener = callback;
        if (type === "project") projectEventListener = callback;
      }
      removeEventListener(type: string) {
        if (type === "open") openEventListener = undefined;
        if (type === "project") projectEventListener = undefined;
      }
      close() {}
    },
  );
  vi.mocked(getHealth).mockResolvedValue({
    status: "ok",
    projectId: "local:test",
    projectName: "Test lab",
    projectRoot: "/tmp/test-lab",
    details: {},
  });
  vi.mocked(getRuns).mockResolvedValue({ items: RUNS });
  vi.mocked(getOlderRuns).mockResolvedValue({ items: [] });
  vi.mocked(getRun).mockImplementation(async (runId) => projectRun(runId));
  vi.mocked(getEvents).mockResolvedValue([]);
  vi.mocked(getRunEvents).mockResolvedValue([]);
  vi.mocked(getMeasurementLivePreview).mockResolvedValue({
    active: false,
    receivedRecordCount: 0,
    durableRecordCount: 0,
  });
  vi.mocked(getMeasurementPreview).mockResolvedValue({ items: [] });
  vi.mocked(getMeasurementSlice).mockResolvedValue({
    items: [],
    selectedPointCount: 0,
    offset: 0,
    windowPointCount: 0,
    truncated: false,
  });
  vi.mocked(getMeasurementTracePreview).mockResolvedValue(tracePreview("magnitude"));
  vi.mocked(getRunAnalysisSummaries).mockResolvedValue({ items: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("replaces the exact-run opening link without leaving a stale run action", async () => {
  window.history.replaceState(null, "", "/?run=run-1");
  renderApp();
  expect(await screen.findByRole("link", { name: "在新标签页或窗口打开结果" })).toHaveAttribute(
    "href",
    "/?run=run-1&focus=run#runs",
  );
  fireEvent.click(screen.getByTitle("Inspect run run-2"));
  await waitFor(() => {
    const links = screen.getAllByRole("link", { name: "在新标签页或窗口打开结果" });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("href", "/?run=run-2&focus=run#runs");
  });
});

it("opens Help through navigation and a direct hash without leaving the workbench", async () => {
  renderApp();
  fireEvent.click(screen.getByRole("button", { name: /Help$/ }));
  expect(await screen.findByRole("heading", { name: "帮助" })).toBeVisible();
  expect(window.location.hash).toBe("#help");
  expect(screen.getByRole("button", { name: /Help$/ })).toHaveAttribute("aria-current", "page");
  fireEvent.click(screen.getByRole("button", { name: /Experiments$/ }));
  expect(await screen.findByText("Calibration launcher")).toBeVisible();
  act(() => {
    window.history.replaceState(null, "", "#help");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });
  expect(await screen.findByRole("heading", { name: "帮助" })).toBeVisible();
});

describe("config provenance navigation", () => {
  it("shows unknown decision attention when the actual query fails instead of retaining zero", async () => {
    const client = createQueryClient();
    renderApp(client);
    await waitFor(() => expect(screen.getByLabelText("待处理判断")).toHaveTextContent("0 待处理"));
    vi.mocked(getWaitingProcedures).mockRejectedValue(new Error("Decision query disconnected"));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["procedure-decisions"] });
    });
    await waitFor(() => expect(screen.getByLabelText("待处理判断")).toHaveTextContent("状态未知"));
    expect(screen.getByLabelText("待处理判断")).not.toHaveTextContent("0 待处理");
  });
  it("restores page and record selections through back and forward", async () => {
    window.history.replaceState(null, "", "/?sample=chip-a17#samples");
    renderApp();
    await screen.findByText("Selected sample chip-a17");
    fireEvent.click(screen.getByRole("button", { name: "Select next sample" }));
    await screen.findByText("Selected sample chip-b22");
    fireEvent.click(screen.getByRole("button", { name: /Devices and drivers$/ }));
    await screen.findByText("Instrument workspace");
    act(() => window.history.back());
    await screen.findByText("Selected sample chip-b22");
    act(() => window.history.back());
    await screen.findByText("Selected sample chip-a17");
    act(() => window.history.forward());
    await screen.findByText("Selected sample chip-b22");
    act(() => window.history.forward());
    expect(await screen.findByText("Instrument workspace")).toBeVisible();
  });

  it("returns to the same run after editing and still follows explicit run history", async () => {
    window.history.replaceState(null, "", "/?run=run-2");
    renderApp();
    await screen.findByTitle("run-2");
    fireEvent.click(screen.getByRole("button", { name: /Configuration$/ }));
    expect(window.location.search).not.toContain("run=");
    fireEvent.click(screen.getByRole("button", { name: /Experiments$/ }));
    await screen.findByText("Calibration launcher");
    fireEvent.click(screen.getByRole("button", { name: /Runs$/ }));
    await waitFor(() => expect(window.location.search).toBe("?run=run-2"));
    expect(await screen.findByTitle("run-2")).toHaveTextContent("run-2");
    expect(screen.getByTitle("Inspect run run-2")).toHaveAttribute("aria-current", "true");
    fireEvent.click(screen.getByTitle("Inspect run run-1"));
    await screen.findByTitle("run-1");
    act(() => window.history.back());
    expect(await screen.findByTitle("run-2")).toHaveTextContent("run-2");
    expect(screen.getByTitle("Inspect run run-2")).toHaveAttribute("aria-current", "true");
    act(() => window.history.forward());
    expect(await screen.findByTitle("run-1")).toHaveTextContent("run-1");
    expect(screen.getByTitle("Inspect run run-1")).toHaveAttribute("aria-current", "true");
  });

  it("retains sample revision and analysis selection while their pages are inactive", async () => {
    window.history.replaceState(null, "", "/?sample=chip-a17&sample-revision=3#samples");
    renderApp();
    await screen.findByText("Selected sample chip-a17");
    fireEvent.click(screen.getByRole("button", { name: /Analyses$/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Select next analysis" }));
    fireEvent.click(screen.getByRole("button", { name: /Configuration$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Samples$/ }));
    expect(await screen.findByText("Selected sample chip-a17")).toBeVisible();
    expect(new URLSearchParams(window.location.search).get("sample-revision")).toBe("3");
    fireEvent.click(screen.getByRole("button", { name: /Analyses$/ }));
    expect(await screen.findByText("Selected analysis analysis-next")).toBeVisible();
  });

  it("does not add an extra history entry for automatic first-run selection", async () => {
    window.history.replaceState(null, "", "/");
    const before = window.history.length;
    renderApp();
    await waitFor(() => expect(window.location.search).toContain("run="));
    expect(window.history.length).toBe(before);
    const selected = window.location.href;
    const back = vi.spyOn(window.history, "back");
    act(() => {
      window.dispatchEvent(new Event("scopecat:back"));
    });
    expect(back).not.toHaveBeenCalled();
    back.mockRestore();
    fireEvent.click(screen.getByRole("button", { name: /Help$/ }));
    await screen.findByRole("heading", { name: "帮助" });
    act(() => {
      window.dispatchEvent(new Event("scopecat:back"));
    });
    await waitFor(() => expect(window.location.href).toBe(selected));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Runs$/ })).toHaveAttribute("aria-current", "page"),
    );
    act(() => {
      window.dispatchEvent(new Event("scopecat:forward"));
    });
    await screen.findByRole("heading", { name: "帮助" });
  });

  it("restores a sample deep link and opens its run", async () => {
    window.history.replaceState(null, "", "/?sample=chip-a17#samples");
    renderApp();

    expect(await screen.findByText("Selected sample chip-a17")).toBeVisible();
    expect(screen.getByRole("button", { name: /Samples$/ })).toHaveAttribute(
      "aria-current",
      "page",
    );

    fireEvent.click(screen.getByRole("button", { name: "Select next sample" }));
    expect(window.location.search).toBe("?sample=chip-b22");
    expect(window.location.hash).toBe("#samples");

    fireEvent.click(screen.getByRole("button", { name: "Open sample run" }));
    expect(window.location.search).toBe("?run=run-2");
    expect(window.location.hash).toBe("");
  });

  it("opens an exact run binding in the sample workspace", async () => {
    const run = projectRun("run-1");
    run.samples = [
      {
        role: "subject",
        sample_id: "chip-a17",
        revision: 2,
        content_hash: `sha256:${"a".repeat(64)}`,
        kind: "chip",
        display_name: "Chip A17",
      },
    ];
    vi.mocked(getRuns).mockResolvedValue({ items: [run] });
    vi.mocked(getRun).mockResolvedValue(run);
    window.history.replaceState(null, "", "/?run=run-1");
    renderApp();

    fireEvent.click(await screen.findByText("样品与计划来源"));
    expect(screen.getByTitle("chip-a17 · exact revision 2")).toBeVisible();
    fireEvent.click(screen.getByTitle("chip-a17 · exact revision 2"));

    expect(await screen.findByText("Selected sample chip-a17")).toBeVisible();
    expect(window.location.search).toBe("?sample=chip-a17&sample-revision=2");
    expect(window.location.hash).toBe("#samples");
  });

  it("restores the analyses route and opens an input in the Runs view", async () => {
    window.history.replaceState(null, "", "/#analyses");
    renderApp();

    expect(await screen.findByText("项目分析工作区")).toBeVisible();
    expect(screen.getByRole("button", { name: /Analyses$/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(getRuns).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Open analysis input run" }));

    expect(screen.getByRole("button", { name: /Runs$/ })).toHaveAttribute("aria-current", "page");
    expect(window.location.search).toBe("?run=run-2");
    expect(window.location.hash).toBe("");
  });

  it("restores and updates an exact analysis deep link", async () => {
    window.history.replaceState(null, "", "/?analysis=analysis-review-r2#analyses");
    renderApp();

    expect(await screen.findByText("Selected analysis analysis-review-r2")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Select next analysis" }));

    expect(window.location.search).toBe("?analysis=analysis-next");
    expect(window.location.hash).toBe("#analyses");
  });

  it("restores and updates the Instruments hash route", async () => {
    window.history.replaceState(null, "", "/#instruments");
    renderApp();

    expect(await screen.findByText("Instrument workspace")).toBeVisible();
    expect(screen.getByRole("button", { name: /Devices and drivers$/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(getRuns).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Configuration$/ }));
    expect(window.location.hash).toBe("#configuration");
    fireEvent.click(screen.getByRole("button", { name: /Devices and drivers$/ }));
    expect(window.location.hash).toBe("#instruments");
  });

  it("invalidates canonical queries after project events", async () => {
    window.history.replaceState(null, "", "/#instruments");
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderApp(queryClient);
    await screen.findByText("Instrument workspace");
    await waitFor(() => expect(projectEventListener).toBeDefined());

    invalidate.mockClear();
    act(() => emitProjectEvent("run-1", "instrument_session_opened"));

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["instruments"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["sample-revisions"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run-contents"] });
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run-content"] });
    });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: ["experiment-launcher"] });
  });

  it("does not mount the run browser while configuration is active", async () => {
    renderApp();

    await screen.findByText("实验配置");
    expect(await screen.findByText("Test lab · 参数与绑定")).toBeVisible();
    expect(getRuns).not.toHaveBeenCalled();
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe("#configuration");
    expect(screen.getByRole("button", { name: /Configuration$/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("keeps the configuration editor mounted across page navigation", async () => {
    renderApp();
    // Establish project identity before checking navigation within that project.
    await screen.findByText("已连接", { exact: true });
    const editor = await screen.findByText("实验配置");
    fireEvent.click(screen.getByRole("button", { name: /Runs$/ }));
    expect(editor).toBeInTheDocument();
    expect(editor).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /Configuration$/ }));
    expect(screen.getByText("实验配置")).toBe(editor);
    expect(editor).toBeVisible();
  });

  it("selects the first indexed run when no explicit run was requested", async () => {
    window.history.replaceState(null, "", "/");

    renderApp();

    await waitFor(() =>
      expect(screen.getByTitle("Inspect run run-1")).toHaveAttribute("aria-current", "true"),
    );
    expect(screen.getByTitle("run-1")).toHaveTextContent("run-1");
    expect(window.location.search).toBe("?run=run-1");
  });

  it("restores the selected run from the URL", async () => {
    window.history.replaceState(null, "", "/?run=run-2#configuration");

    renderApp();

    expect(screen.getByRole("button", { name: /Configuration$/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    fireEvent.click(screen.getByRole("button", { name: /Runs$/ }));
    await waitFor(() =>
      expect(screen.getByTitle("Inspect run run-2")).toHaveAttribute("aria-current", "true"),
    );
    expect(screen.getByTitle("run-2")).toHaveTextContent("run-2");
  });

  it("loads and merges older run pages", async () => {
    window.history.replaceState(null, "", "/");
    vi.mocked(getRuns).mockResolvedValue({
      items: RUNS.map((run, index) => ({ ...run, sequence: index + 20 })),
      nextCursor: 20,
    });
    vi.mocked(getOlderRuns).mockResolvedValue({
      items: [
        { ...projectRun("run-old"), sequence: 2 },
        { ...projectRun("run-1"), sequence: 1 },
      ],
    });

    renderApp();
    expect(await screen.findByText("0 active in loaded runs")).toBeVisible();
    expect(screen.getByText("No flags in loaded runs")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "加载更早 run" }));

    expect(await screen.findByTitle("Inspect run run-old")).toBeVisible();
    expect(getOlderRuns).toHaveBeenCalledWith(20);
    expect(screen.getAllByTitle("Inspect run run-1")).toHaveLength(1);
    expect(
      screen.getAllByTitle(/^Inspect run /).map((button) => button.getAttribute("title")),
    ).toEqual(["Inspect run run-2", "Inspect run run-1", "Inspect run run-old"]);
    expect(screen.queryByRole("button", { name: "加载更早 run" })).not.toBeInTheDocument();
  });

  it.each(["run_admitted", "run_state_changed"])(
    "refreshes filtered research runs for %s without losing the selected project",
    async (kind) => {
      window.history.replaceState(null, "", "/#history");
      let changed = false;
      const requests: URL[] = [];
      vi.stubGlobal(
        "fetch",
        vi.fn(async (request: Request) => {
          const url = new URL(request.url);
          requests.push(url);
          if (url.pathname === "/api/v1/research-projects")
            return Response.json({
              items: [{ id: "alpha", name: "Alpha", revision: 1, description: "" }],
            });
          if (url.pathname.endsWith("/members/samples")) return Response.json({ ids: [] });
          if (url.pathname === "/api/v1/data/captures") return Response.json([]);
          if (url.pathname === "/api/v1/runs")
            return Response.json({
              items: [
                {
                  snapshot: {
                    run_id: changed && kind === "run_admitted" ? "new-run" : "old-run",
                    created_at: "2026-10-27T00:00:00Z",
                    samples: [],
                    outcome: { result: changed ? "succeeded" : "failed", certainty: "known" },
                  },
                  control: {
                    state: "closed",
                    admission: { display_name: changed ? "New retained run" : "Old retained run" },
                  },
                },
              ],
            });
          return Response.json({ items: [] });
        }),
      );
      renderApp();
      await screen.findByRole("option", { name: "Alpha" });
      fireEvent.change(screen.getByRole("combobox", { name: "研究项目" }), {
        target: { value: "alpha" },
      });
      await screen.findByRole("button", { name: "Old retained run" });
      await waitFor(() =>
        expect(requests.some((url) => url.searchParams.get("research_project") === "alpha")).toBe(
          true,
        ),
      );
      changed = true;
      act(() => emitProjectEvent(kind === "run_admitted" ? "new-run" : "old-run", kind));
      expect(await screen.findByRole("button", { name: "New retained run" })).toBeVisible();
      expect(screen.getByRole("combobox", { name: "研究项目" })).toHaveValue("alpha");
      expect(
        requests
          .filter((url) => url.pathname === "/api/v1/runs")
          .at(-1)
          ?.searchParams.get("research_project"),
      ).toBe("alpha");
    },
  );

  it("retains three loaded run pages and an outstanding page when the head advances", async () => {
    window.history.replaceState(null, "", "/?run=run-60");
    vi.mocked(getRuns).mockResolvedValue(runPage([60, 59], 59));
    vi.mocked(getOlderRuns)
      .mockResolvedValueOnce(runPage([58, 57], 57))
      .mockResolvedValueOnce(runPage([56, 55], 55));
    const client = createQueryClient();
    renderApp(client);
    fireEvent.click(await screen.findByRole("button", { name: "加载更早 run" }));
    await screen.findByTitle("Inspect run run-57");
    fireEvent.click(screen.getByRole("button", { name: "加载更早 run" }));
    fireEvent.click(await screen.findByTitle("Inspect run run-55"));
    let complete!: (value: ReturnType<typeof runPage>) => void;
    vi.mocked(getOlderRuns).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    fireEvent.click(screen.getByRole("button", { name: "加载更早 run" }));
    await waitFor(() => expect(complete).toBeDefined());
    vi.mocked(getRuns).mockResolvedValue(runPage([62, 61], 61));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["runs"] });
    });
    await screen.findByTitle("Inspect run run-62");
    expect(screen.getByTitle("Inspect run run-55")).toHaveAttribute("aria-current", "true");
    expect(screen.getByTitle("Inspect run run-59")).toBeVisible();
    await act(async () => complete(runPage([54, 53])));
    await screen.findByTitle("Inspect run run-53");
    expect(
      screen.getAllByTitle(/^Inspect run /).map((button) => button.getAttribute("title")),
    ).toEqual([62, 61, 60, 59, 58, 57, 56, 55, 54, 53].map((id) => `Inspect run run-${id}`));
  });

  it("fills a new head gap before continuing the retained tail without duplicate rows", async () => {
    window.history.replaceState(null, "", "/?run=run-55");
    vi.mocked(getRuns).mockResolvedValue(runPage([60, 59], 59));
    vi.mocked(getOlderRuns)
      .mockResolvedValueOnce(runPage([58, 57], 57))
      .mockResolvedValueOnce(runPage([56, 55], 55));
    const client = createQueryClient();
    renderApp(client);
    fireEvent.click(await screen.findByRole("button", { name: "加载更早 run" }));
    await screen.findByTitle("Inspect run run-57");
    fireEvent.click(screen.getByRole("button", { name: "加载更早 run" }));
    await screen.findByTitle("Inspect run run-55");
    vi.mocked(getRuns).mockResolvedValue(runPage([66, 65], 65));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["runs"] });
    });
    await screen.findByTitle("Inspect run run-66");
    vi.mocked(getOlderRuns)
      .mockResolvedValueOnce(runPage([64, 63], 63))
      .mockResolvedValueOnce(runPage([62, 61, 60, 59], 59))
      .mockResolvedValueOnce(runPage([54, 53]));
    fireEvent.click(screen.getByRole("button", { name: "加载未读取区间" }));
    await screen.findByTitle("Inspect run run-63");
    fireEvent.click(screen.getByRole("button", { name: "加载未读取区间" }));
    await screen.findByTitle("Inspect run run-61");
    fireEvent.click(screen.getByRole("button", { name: "加载更早 run" }));
    await screen.findByTitle("Inspect run run-53");
    expect(vi.mocked(getOlderRuns).mock.calls.map(([cursor]) => cursor)).toEqual([
      59, 57, 65, 63, 55,
    ]);
    expect(
      screen.getAllByTitle(/^Inspect run /).map((button) => button.getAttribute("title")),
    ).toEqual(Array.from({ length: 14 }, (_, i) => `Inspect run run-${66 - i}`));
    expect(screen.getByTitle("Inspect run run-55")).toHaveAttribute("aria-current", "true");
    expect(screen.queryByRole("button", { name: "加载更早 run" })).not.toBeInTheDocument();
  });

  it("rejects an old store page even after switching away and back", async () => {
    window.history.replaceState(null, "", "/?run=run-60");
    vi.mocked(getRuns).mockResolvedValue(runPage([60, 59], 59));
    let complete!: (value: ReturnType<typeof runPage>) => void;
    vi.mocked(getOlderRuns).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    const client = createQueryClient();
    renderApp(client);
    fireEvent.click(await screen.findByRole("button", { name: "加载更早 run" }));
    await waitFor(() => expect(complete).toBeDefined());
    const original = client.getQueryData(["health"]) as Awaited<ReturnType<typeof getHealth>>;
    vi.mocked(getRuns).mockResolvedValue(runPage([20, 19], 19));
    act(() => {
      client.setQueryData(["health"], { ...original, projectId: "other-store" });
    });
    await screen.findByTitle("Inspect run run-20");
    expect(screen.queryByTitle("Inspect run run-59")).not.toBeInTheDocument();
    vi.mocked(getRuns).mockResolvedValue(runPage([60, 59], 59));
    act(() => {
      client.setQueryData(["health"], original);
    });
    await screen.findByTitle("Inspect run run-59");
    await act(async () => complete(runPage([58, 57])));
    expect(screen.queryByTitle("Inspect run run-57")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Inspect run run-20")).not.toBeInTheDocument();
  });

  it("reports a lost daemon even while slower queries still hold data", async () => {
    window.history.replaceState(null, "", "/");
    vi.mocked(getHealth)
      .mockResolvedValueOnce({
        status: "online",
        projectId: "local:test",
        projectName: "Test lab",
        projectRoot: "/tmp/test-lab",
        details: {},
      })
      .mockRejectedValue(new Error("daemon stopped"));
    vi.mocked(getRuns)
      .mockResolvedValueOnce({ items: RUNS })
      .mockRejectedValue(new Error("daemon stopped"));

    renderApp();

    expect((await screen.findAllByText("状态未知（online）", { exact: true }))[0]).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "刷新项目数据" }));

    expect(await screen.findByText("连接已断开", { exact: true })).toBeVisible();
    expect(screen.getByText("连接已中断。")).toBeVisible();
  });

  it("uses durable transitions without treating a started point as complete", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const running = {
      ...projectRun("run-1"),
      status: "running" as const,
      stateLabel: "执行中",
      plan: {
        coordinateSpecs: [],
        adaptiveCoordinateIds: [],
        adaptiveRegionCount: 0,
        adaptiveRegions: [],
        adaptiveRegionsTruncated: false,
        sampledPoints: [],
        sampledPointsTruncated: false,
        coordinateIds: [],
        recordIds: [],
        pointCount: 3,
        initialPointCount: 3,
        pointLimit: 3,
      },
    };
    vi.mocked(getRuns).mockResolvedValue({ items: [running] });
    vi.mocked(getRun).mockResolvedValue(running);
    vi.mocked(getRunEvents).mockResolvedValue([
      {
        id: 1,
        runId: "run-1",
        kind: "execution_transition_committed",
        payload: {
          stage: "compute",
          state: "started",
          point_index: 2,
          evidence: {},
        },
      },
      {
        id: 2,
        runId: "run-1",
        kind: "execution_transition_committed",
        payload: {
          stage: "append_measurement",
          state: "completed",
          evidence: { start_index: 0, record_count: 1 },
        },
      },
    ]);

    renderApp();

    await screen.findByRole("region", { name: "run 进度" });
    fireEvent.click(screen.getByText("执行证据", { exact: true }));
    expect(await screen.findByText(/0 个点已有持久化执行证据/)).toBeVisible();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("shows adaptive coverage against its point limit", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const running = {
      ...projectRun("run-1"),
      status: "running" as const,
      stateLabel: "执行中",
      progressCompleted: 2,
      pointPlan: {
        initialPointCount: 1,
        acceptedPointCount: 3,
        pointLimit: 4,
        decisionCount: 2,
        optimizerAttemptCount: 1,
        operatorRequestCount: 1,
        closed: false,
      },
      plan: {
        coordinateSpecs: [],
        adaptiveCoordinateIds: ["frequency"],
        adaptiveScope: "per_region" as const,
        adaptiveRegionCount: 1,
        adaptiveRegions: [{ id: "region-0", coordinates: {}, initial_point_count: 1 }],
        adaptiveRegionsTruncated: false,
        sampledPoints: [],
        sampledPointsTruncated: false,
        coordinateIds: ["frequency"],
        recordIds: ["signal"],
        initialPointCount: 1,
        pointLimit: 4,
      },
    };
    vi.mocked(getRuns).mockResolvedValue({ items: [running] });
    vi.mocked(getRun).mockResolvedValue(running);

    renderApp();

    const progress = await screen.findByRole("region", { name: "run 进度" });
    expect(progress).toHaveTextContent("3 accepted points · plan open");
    fireEvent.click(screen.getByText("执行证据", { exact: true }));
    expect(await screen.findByText(/2 个点已有持久化执行证据/)).toBeVisible();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("keeps distinct records in one bounded measurement preview", async () => {
    window.history.replaceState(null, "", "/");
    vi.mocked(getMeasurementPreview).mockResolvedValue({
      items: [measurementRecord(0, 1, "dataset-a"), measurementRecord(0, 2, "dataset-b")],
      truncated: true,
    });

    renderApp();
    expect(await screen.findByRole("region", { name: "测量数据摘要" })).toHaveTextContent(
      "当前记录预览 2",
    );
    expect(getMeasurementPreview).toHaveBeenCalledWith("run-1", expect.any(AbortSignal));
    expect(screen.getByTestId("measurement-preview")).toHaveTextContent(
      '"dataset_id": "dataset-b"',
    );
  });

  it("counts only saved preview records when live data is inactive", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    vi.mocked(getMeasurementPreview).mockResolvedValue({
      items: [measurementRecord(0, 1)],
      truncated: true,
    });
    vi.mocked(getMeasurementLivePreview).mockResolvedValue({
      active: false,
      latest: measurementRecord(1, 2),
      receivedRecordCount: 2,
      durableRecordCount: 1,
    });

    renderApp();
    await waitFor(() => expect(getMeasurementLivePreview).toHaveBeenCalled());
    expect(await screen.findByRole("region", { name: "测量数据摘要" })).toHaveTextContent(
      "当前记录预览 1",
    );
    const progress = screen.getByRole("region", { name: "run 进度" });
    expect(progress).toHaveTextContent("已保存预览记录 1+");
    expect(progress).not.toHaveTextContent(/已接收记录 \d/);
    expect(screen.queryByText(/最新接收记录（点索引/)).not.toBeInTheDocument();
  });

  it("shows the latest daemon-received record before it is durable", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const running = {
      ...projectRun("run-1"),
      status: "running" as const,
      stateLabel: "执行中",
      plan: {
        coordinateSpecs: [],
        adaptiveCoordinateIds: [],
        adaptiveRegionCount: 0,
        adaptiveRegions: [],
        adaptiveRegionsTruncated: false,
        sampledPoints: [],
        sampledPointsTruncated: false,
        coordinateIds: [],
        recordIds: [],
        pointCount: 3,
        initialPointCount: 3,
        pointLimit: 3,
      },
    };
    vi.mocked(getRuns).mockResolvedValue({ items: [running] });
    vi.mocked(getRun).mockResolvedValue(running);
    vi.mocked(getMeasurementLivePreview).mockResolvedValue({
      active: true,
      latest: measurementRecord(0, 1.25),
      receivedRecordCount: 1,
      durableRecordCount: 0,
    });

    renderApp();

    expect(await screen.findByRole("region", { name: "测量数据摘要" })).toHaveTextContent(
      "run 已接收 1",
    );
    expect(screen.getByText(/最新接收记录（点索引 0）尚未保存/)).toBeVisible();
    expect(screen.getByRole("region", { name: "run 进度" })).toHaveTextContent("已接收记录 1");
    expect(screen.getByRole("region", { name: "run 进度" })).toHaveTextContent("已保存记录 0");
    expect(screen.getByRole("region", { name: "run 进度" })).not.toHaveTextContent(
      "已保存预览记录",
    );
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it.each([
    "measurement_dataset_initialized",
    "measurements_sealed",
    "run_coverage_advanced",
    "measurement_projection_advanced",
  ])(
    "refreshes bounded measurement queries for %s without closing its disclosure",
    async (kind) => {
      window.history.replaceState(null, "", "/?run=run-1");
      vi.mocked(getMeasurementPreview).mockResolvedValue({
        items: [measurementRecord(0, 0)],
        truncated: false,
      });

      const queryClient = createQueryClient();
      renderApp(queryClient);
      await waitFor(() => expect(getMeasurementPreview).toHaveBeenCalledTimes(1));
      fireEvent.click(screen.getByText("Raw records", { exact: true }));
      expect(screen.getByTestId("measurement-preview")).toBeVisible();
      expect(projectEventListener).toBeDefined();
      queryClient.setQueryData(["measurements", "run-2"], {
        items: [measurementRecord(9, 9)],
        truncated: false,
      });
      queryClient.setQueryData(["measurement-trace", "run-2", "trace", "{}"], {
        stale: true,
      });

      queryClient.setQueryData(["measurement-slice", "run-2", "{}"], { stale: true });
      queryClient.setQueryData(["measurements", "live", "run-2"], { stale: true });

      act(() => {
        emitProjectEvent("run-2", kind);
      });
      await waitFor(() =>
        expect(queryClient.getQueryState(["measurements", "run-2"])?.isInvalidated).toBe(true),
      );
      expect(
        queryClient.getQueryState(["measurement-trace", "run-2", "trace", "{}"])?.isInvalidated,
      ).toBe(true);
      expect(queryClient.getQueryState(["measurement-slice", "run-2", "{}"])?.isInvalidated).toBe(
        true,
      );
      expect(queryClient.getQueryState(["measurements", "live", "run-2"])?.isInvalidated).toBe(
        true,
      );
      expect(getMeasurementPreview).toHaveBeenCalledTimes(1);

      act(() => {
        emitProjectEvent("run-1", kind);
      });
      await waitFor(() => expect(getMeasurementPreview).toHaveBeenCalledTimes(2));
      expect(screen.getByTestId("measurement-preview")).toBeVisible();
      expect(vi.mocked(getMeasurementPreview).mock.calls.map(([runId]) => runId)).toEqual([
        "run-1",
        "run-1",
      ]);
    },
  );

  it("queries only the selected bounded trace mode and authored slice", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const schema = traceDatasetSchema();
    vi.mocked(getMeasurementPreview).mockResolvedValue({ schema, items: [] });
    vi.mocked(getMeasurementSlice).mockResolvedValue({
      items: [],
      selectedPointCount: 4,
      offset: 0,
      windowPointCount: 4,
      truncated: false,
    });
    vi.mocked(getMeasurementTracePreview).mockImplementation(async (_runId, selection) =>
      tracePreview(selection.valueMode),
    );

    renderApp();

    expect(
      await screen.findByRole("img", {
        name: "S21 magnitude: |S21| [ratio] by Frequency [GHz]",
      }),
    ).toBeVisible();
    await waitFor(() =>
      expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
        "run-1",
        {
          observableId: "response",
          coordinateId: "frequency",
          fixedAxisIndices: { bias: 0 },
          valueMode: "magnitude",
        },
        expect.any(AbortSignal),
      ),
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Measurement trace" }), {
      target: { value: "trace:response:frequency:phase" },
    });
    expect(
      await screen.findByRole("img", {
        name: "S21 phase: phase(S21) [rad] by Frequency [GHz]",
      }),
    ).toBeVisible();
    expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
      "run-1",
      expect.objectContaining({ valueMode: "phase", fixedAxisIndices: { bias: 0 } }),
      expect.any(AbortSignal),
    );

    fireEvent.change(screen.getByRole("combobox", { name: "Bias slice" }), {
      target: { value: "1" },
    });
    await waitFor(() =>
      expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
        "run-1",
        expect.objectContaining({ valueMode: "phase", fixedAxisIndices: { bias: 1 } }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("retains the mounted trace through measurement-event refreshes and resets a new query identity", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const schema = traceDatasetSchema();
    vi.mocked(getMeasurementPreview).mockResolvedValue({ schema, items: [] });
    const original = tracePreview("magnitude");
    original.series.push({
      ...original.series[0]!,
      point_index: 1,
      logical_point_id: "point-1",
      label: "Point 1",
    });
    original.returned_series_count = 2;
    original.returned_sample_count = original.source_sample_count = 6;
    vi.mocked(getMeasurementTracePreview).mockResolvedValue(original);
    const client = createQueryClient();
    renderApp(client);
    const plot = await screen.findByRole("img", {
      name: "S21 magnitude: |S21| [ratio] by Frequency [GHz]",
    });
    await waitFor(() =>
      expect(getInstanceByDom(plot.firstElementChild as HTMLElement)).toBeDefined(),
    );
    const chart = getInstanceByDom(plot.firstElementChild as HTMLElement)!;
    chart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start: 20, end: 60 });
    chart.dispatchAction({ type: "legendToggleSelect", name: "Point 1" });
    const range = () => {
      const zoom = (
        chart.getOption().dataZoom as Array<{ startValue: number; endValue: number }>
      )[0]!;
      return [zoom.startValue, zoom.endValue];
    };
    const expectedRange = range();
    for (const response of [
      original,
      {
        ...original,
        returned_sample_count: 8,
        source_sample_count: 8,
        series: original.series.map((series) => ({
          ...series,
          x: [...series.x, 5.2],
          y: [...series.y, 0.4],
          available_sample_count: 4,
          source_sample_count: 4,
        })),
      },
    ]) {
      let complete!: (value: typeof original) => void;
      vi.mocked(getMeasurementTracePreview).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            complete = resolve;
          }),
      );
      act(() => emitProjectEvent("run-1", "measurements_sealed"));
      await waitFor(() => expect(complete).toBeDefined());
      expect(chart.isDisposed()).not.toBe(true);
      await screen.findByText("正在刷新有界波形；图表显示上次成功读取的数据。");
      expect(getInstanceByDom(plot.firstElementChild as HTMLElement)).toBe(chart);
      await act(async () => complete(response));
      await waitFor(() =>
        expect(
          screen.queryByText("正在刷新有界波形；图表显示上次成功读取的数据。"),
        ).not.toBeInTheDocument(),
      );
      expect(getInstanceByDom(plot.firstElementChild as HTMLElement)).toBe(chart);
      expect(range()).toEqual(expectedRange);
      expect(chart.getOption().legend).toEqual([
        expect.objectContaining({ selected: expect.objectContaining({ "Point 1": false }) }),
      ]);
    }
    let completeSelection!: (value: typeof original) => void;
    vi.mocked(getMeasurementTracePreview).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          completeSelection = resolve;
        }),
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Measurement trace" }), {
      target: { value: "trace:response:frequency:phase" },
    });
    await screen.findByText("正在读取有界波形预览…");
    expect(
      screen.queryByRole("img", { name: "S21 magnitude: |S21| [ratio] by Frequency [GHz]" }),
    ).not.toBeInTheDocument();
    expect(chart.isDisposed()).toBe(true);
    await act(async () => completeSelection(tracePreview("phase")));
    const phase = await screen.findByRole("img", {
      name: "S21 phase: phase(S21) [rad] by Frequency [GHz]",
    });
    await waitFor(() =>
      expect(getInstanceByDom(phase.firstElementChild as HTMLElement)).toBeDefined(),
    );
    expect(getInstanceByDom(phase.firstElementChild as HTMLElement)!.getOption().dataZoom).toEqual(
      expect.arrayContaining([expect.objectContaining({ start: 0, end: 100 })]),
    );
    const phaseChart = getInstanceByDom(phase.firstElementChild as HTMLElement)!;
    phaseChart.dispatchAction({ type: "dataZoom", dataZoomIndex: 0, start: 20, end: 60 });
    let completeRun!: (value: typeof original) => void;
    vi.mocked(getMeasurementTracePreview).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          completeRun = resolve;
        }),
    );
    fireEvent.click(screen.getByTitle("Inspect run run-2"));
    await screen.findByText("正在读取有界波形预览…");
    await waitFor(() => expect(phaseChart.isDisposed()).toBe(true));
    expect(
      screen.queryByRole("img", { name: "S21 phase: phase(S21) [rad] by Frequency [GHz]" }),
    ).not.toBeInTheDocument();
    await act(async () => completeRun(original));
    const nextRun = await screen.findByRole("img", {
      name: "S21 magnitude: |S21| [ratio] by Frequency [GHz]",
    });
    await waitFor(() =>
      expect(getInstanceByDom(nextRun.firstElementChild as HTMLElement)).toBeDefined(),
    );
    expect(
      getInstanceByDom(nextRun.firstElementChild as HTMLElement)!.getOption().dataZoom,
    ).toEqual(expect.arrayContaining([expect.objectContaining({ start: 0, end: 100 })]));
  });

  it("forwards entity chip selection to the bounded trace query", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const schema = entityTraceDatasetSchema();
    vi.mocked(getMeasurementPreview).mockResolvedValue({ schema, items: [] });
    vi.mocked(getMeasurementSlice).mockResolvedValue({
      items: [],
      selectedPointCount: 4,
      offset: 0,
      windowPointCount: 4,
      truncated: false,
    });
    vi.mocked(getMeasurementTracePreview).mockImplementation(async (_runId, selection) =>
      tracePreview(selection.valueMode),
    );

    renderApp();

    const q1 = await screen.findByRole("button", { name: "Qubits Q1" });
    fireEvent.click(q1);

    await waitFor(() =>
      expect(getMeasurementTracePreview).toHaveBeenLastCalledWith(
        "run-1",
        expect.objectContaining({
          observableId: "response",
          coordinateId: "frequency",
          entities: [expect.objectContaining({ kind: "qubit", id: "q1" })],
        }),
        expect.any(AbortSignal),
      ),
    );
  });

  it("refreshes canonical queries and the experiment catalog whenever SSE connects", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    const queryClient = createQueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    renderApp(queryClient);

    fireEvent.click(await screen.findByText("执行诊断与来源"));
    expect(await screen.findByRole("heading", { name: "Recent events" })).toBeVisible();
    await waitFor(() => expect(openEventListener).toBeDefined());
    const initialCounts = canonicalQueryCallCounts();

    act(() => {
      emitSseOpen();
    });
    await waitFor(() =>
      expect(canonicalQueryCallCounts()).toEqual(initialCounts.map((count) => count + 1)),
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["experiment-launcher"] });
    invalidate.mockClear();
    const connectedCounts = canonicalQueryCallCounts();

    act(() => {
      emitSseOpen();
    });
    await waitFor(() =>
      expect(canonicalQueryCallCounts()).toEqual(connectedCounts.map((count) => count + 1)),
    );
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["experiment-launcher"] });
  });

  it("labels the bounded run event timeline honestly", async () => {
    window.history.replaceState(null, "", "/?run=run-1");
    vi.mocked(getRunEvents).mockResolvedValue(
      Array.from({ length: 500 }, (_, index) => ({
        id: index + 1,
        runId: "run-1",
        kind: "execution_transition_committed",
        payload: { point_index: index },
      })),
    );

    renderApp();

    fireEvent.click(await screen.findByText("执行诊断与来源"));
    expect(await screen.findByRole("heading", { name: "Recent events" })).toBeVisible();
    expect(
      screen.getByText("Showing the latest 500 events; older events are not loaded."),
    ).toBeVisible();
    expect(screen.getAllByText("Execution transition")).toHaveLength(500);
  });
});

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderApp(queryClient = createQueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

function projectRun(runId: string): ProjectRun {
  return {
    runId,
    experimentId: "ramsey",
    tags: [],
    status: "succeeded",
    stateLabel: "已完成",
    updatedAt: "2026-07-24T08:00:00Z",
    pointPlan: {
      initialPointCount: 0,
      acceptedPointCount: 0,
      pointLimit: 0,
      decisionCount: 0,
      optimizerAttemptCount: 0,
      operatorRequestCount: 0,
      closed: true,
      stopReason: "static point plan",
    },
    plan: {
      initialPointCount: 0,
      pointLimit: 0,
      coordinateIds: [],
      coordinateSpecs: [],
      adaptiveCoordinateIds: [],
      adaptiveRegionCount: 0,
      adaptiveRegions: [],
      adaptiveRegionsTruncated: false,
      sampledPoints: [],
      sampledPointsTruncated: false,
      recordIds: [],
    },
    resources: [],
    samples: [],
    contents: [],
  };
}

function measurementRecord(
  pointIndex: number,
  signal: number,
  datasetId?: string,
): MeasurementRecord {
  return {
    run_id: "run-1",
    logical_point_id: `${datasetId ?? "point"}-${pointIndex}`,
    point_index: pointIndex,
    coordinates: {},
    observables: {
      signal: {
        kind: "scalar",
        dtype: "float64",
        unit: "ratio",
        value: signal,
      },
    },
    metadata: datasetId ? { dataset_id: datasetId } : {},
  };
}

function traceDatasetSchema(): MeasurementDatasetSchema {
  return {
    format_version: "scopecat.measurement_dataset_schema.v17",
    dataset_id: "raw-measurements",
    record_schema: "scopecat.measurement_record.v10",
    point_domain: {
      kind: "product_grid",
      axes: [traceAxis("row", [0, 1]), traceAxis("column", [0, 1]), traceAxis("bias", [0, 1])],
    },
    dimensions: [
      { id: "point", kind: "point", size: 8 },
      { id: "sample", kind: "sample", size: null },
    ],
    variables: [
      {
        id: "row",
        role: "coordinate",
        dtype: "float64",
        dims: ["point"],
      },
      {
        id: "column",
        role: "coordinate",
        dtype: "float64",
        dims: ["point"],
      },
      {
        id: "bias",
        label: "Bias",
        role: "coordinate",
        dtype: "float64",
        unit: "V",
        dims: ["point"],
      },
      {
        id: "frequency",
        label: "Frequency",
        role: "coordinate",
        dtype: "float64",
        unit: "GHz",
        dims: ["point", "sample"],
        recording_group_id: "readout",
      },
      {
        id: "response",
        label: "S21",
        role: "observable",
        dtype: "complex128",
        unit: "ratio",
        dims: ["point", "sample"],
        recording_group_id: "readout",
      },
    ],
    primary_coordinates: ["row", "column", "bias", "frequency"],
    primary_observables: ["response"],
  };
}

function entityTraceDatasetSchema(): MeasurementDatasetSchema {
  const schema = traceDatasetSchema();
  return {
    ...schema,
    dimensions: [
      schema.dimensions[0]!,
      {
        id: "qubit",
        kind: "entity",
        label: "Qubits",
        size: 2,
        index: {
          kind: "entity",
          values: [
            { id: "q0", kind: "qubit", metadata: { label: "Q0" } },
            { id: "q1", kind: "qubit", metadata: { label: "Q1" } },
          ],
        },
      },
      schema.dimensions[1]!,
    ],
    variables: schema.variables?.map((variable) =>
      variable.id === "frequency" || variable.id === "response"
        ? {
            ...variable,
            dims: ["point", "qubit", "sample"],
            source_entity_products: {
              dimension_id: "qubit",
              product_ids: ["q0-readout", "q1-readout"],
            },
            ...(variable.id === "response"
              ? {
                  entity_acquisition: {
                    policy: "all_or_nothing" as const,
                    cohort_id: "readout-cohort",
                  },
                }
              : {}),
          }
        : variable,
    ),
  };
}

function traceAxis(id: string, values: number[]) {
  return {
    id,
    size: values.length,
    source: {
      kind: "values" as const,
      values: values.map((value) => ({
        kind: "scalar" as const,
        dtype: "float64" as const,
        value,
      })),
    },
  };
}

function tracePreview(mode: "imag" | "magnitude" | "phase" | "real" | "value") {
  return {
    coordinate_id: "frequency",
    coordinate_label: "Frequency",
    coordinate_unit: "GHz",
    dimension_id: "sample",
    downsampling: "minmax" as const,
    layout: "overlay" as const,
    fixed_axis_indices: { bias: 0 },
    observable_id: "response",
    observable_label: "S21",
    observable_unit: "ratio",
    recording_group_id: "readout",
    returned_sample_count: 3,
    returned_series_count: 1,
    inspected_series_count: 4,
    failures: [],
    samples_reduced: false,
    selected_series_count: 4,
    series: [
      {
        label: "Point 0",
        logical_point_id: "point-0",
        point_index: 0,
        source_sample_count: 3,
        available_sample_count: 3,
        unavailable_reasons: [],
        x: [4.9, 5, 5.1],
        y: [0.1, 0.2, 0.3],
      },
    ],
    source_sample_count: 3,
    truncated_series: false,
    value_mode: mode,
    value_unit: mode === "phase" ? "rad" : "ratio",
  };
}

function emitProjectEvent(runId: string, kind: string): void {
  if (!projectEventListener) throw new Error("project SSE listener is not ready");
  projectEventListener(
    new MessageEvent("project", {
      data: JSON.stringify({ event_id: 42, run_id: runId, kind, payload: {} }),
    }),
  );
}

function emitSseOpen(): void {
  if (!openEventListener) throw new Error("SSE open listener is not ready");
  openEventListener(new Event("open"));
}

function canonicalQueryCallCounts(): number[] {
  return [
    vi.mocked(getRuns).mock.calls.length,
    vi.mocked(getEvents).mock.calls.length,
    vi.mocked(getRun).mock.calls.length,
    vi.mocked(getRunEvents).mock.calls.length,
    vi.mocked(getMeasurementPreview).mock.calls.length,
  ];
}

it("retains the calibration route and procedure when navigating back", async () => {
  window.history.replaceState(null, "", "/?procedure=p1#configuration");
  renderApp();
  fireEvent.click(await screen.findByRole("button", { name: /Experiments$/ }));
  expect(await screen.findByText("Calibration launcher")).toBeVisible();
  expect(window.location.hash).toBe("#launch");
  expect(new URLSearchParams(window.location.search).get("procedure")).toBe("p1");
});

it("suspends hidden Data queries, resumes same-space drafts, and resets them across data spaces", async () => {
  window.history.replaceState(null, "", "/#history");
  let space = "space-a";
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/record-collections")
        return Response.json({
          items: [{ id: "same-id", name: `${space} collection`, description: "", revision: 1 }],
        });
      if (url.pathname === "/api/v1/research-projects")
        return Response.json({
          items: [
            {
              id: space === "space-a" ? "alpha" : "beta",
              name: space === "space-a" ? "Alpha" : "Beta",
              description: "",
              revision: 1,
            },
          ],
        });
      if (request.method === "PUT")
        return Response.json({
          id: url.pathname.split("/").at(-1),
          ...(await request.json()),
          revision: 1,
        });
      if (url.pathname.endsWith("/members/samples")) return Response.json({ ids: [] });
      if (url.pathname === "/api/v1/data/captures")
        return Response.json([{ content_hash: space, roots: [space], source_project_id: space }]);
      return Response.json({ items: [] });
    }),
  );
  const health = () => ({
    status: "ok",
    projectId: space,
    projectName: space,
    projectRoot: `/tmp/${space}`,
    details: {},
  });
  vi.mocked(getHealth).mockResolvedValue(health());
  const cache = createQueryClient();
  renderApp(cache);
  await screen.findByRole("option", { name: "Alpha" });
  fireEvent.change(screen.getByLabelText("研究项目"), { target: { value: "alpha" } });
  await screen.findByRole("option", { name: "space-a collection" });
  fireEvent.change(screen.getByLabelText("记录集筛选"), {
    target: { value: "same-id" },
  });
  fireEvent.change(screen.getByLabelText("记录集内 run 编号"), { target: { value: "17" } });
  fireEvent.click(screen.getByText("研究项目管理"));
  fireEvent.change(screen.getByLabelText("项目名称"), {
    target: { value: "Unsubmitted Alpha rename" },
  });
  fireEvent.change(screen.getByLabelText("工作点"), { target: { value: "bias-a" } });
  await waitFor(() => expect(cache.isFetching({ queryKey: ["research"] })).toBe(0));
  await screen.findByText("来源：space-a");
  fireEvent.click(screen.getByRole("button", { name: /Configuration$/ }));
  await waitFor(() => {
    expect(
      cache
        .getQueryCache()
        .findAll({ queryKey: ["research"] })
        .every((query) => query.getObserversCount() === 0),
    ).toBe(true);
    expect(
      cache
        .getQueryCache()
        .findAll({ queryKey: ["data", "captures"] })
        .every((query) => query.getObserversCount() === 0),
    ).toBe(true);
  });
  const before = requests.length;
  await act(async () => {
    await cache.invalidateQueries({ queryKey: ["research"] });
    await cache.invalidateQueries({ queryKey: ["data", "captures"] });
  });
  expect(requests).toHaveLength(before);
  fireEvent.click(screen.getByRole("button", { name: /Data$/ }));
  await waitFor(() => expect(requests.length).toBeGreaterThan(before));
  expect(screen.getByLabelText("研究项目")).toHaveValue("alpha");
  expect(screen.getByLabelText("项目名称")).toHaveValue("Unsubmitted Alpha rename");
  expect(screen.getByLabelText("工作点")).toHaveValue("bias-a");
  expect(screen.getByLabelText("记录集筛选")).toHaveValue("same-id");
  expect(screen.getByLabelText("记录集内 run 编号")).toHaveValue("17");

  fireEvent.click(screen.getByRole("button", { name: /Configuration$/ }));
  space = "space-b";
  vi.mocked(getHealth).mockResolvedValue(health());
  act(() => {
    cache.setQueryData(["health"], health());
  });
  fireEvent.click(screen.getByRole("button", { name: /Data$/ }));
  await screen.findByRole("option", { name: "Beta" });
  expect(screen.queryByRole("option", { name: "Alpha" })).toBeNull();
  expect(screen.getByLabelText("研究项目")).toHaveValue("");
  expect(screen.getByLabelText("工作点")).toHaveValue("");
  expect(screen.getByLabelText("记录集筛选")).toHaveValue("");
  expect(screen.queryByLabelText("记录集内 run 编号")).not.toBeInTheDocument();
  await screen.findByRole("option", { name: "space-b collection" });
  expect(screen.queryByRole("option", { name: "space-a collection" })).toBeNull();
  expect(screen.getByText("研究项目管理").parentElement).not.toHaveAttribute("open");
  fireEvent.click(screen.getByText("研究项目管理"));
  expect(screen.getByLabelText("项目名称")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Create project" })).toBeDisabled();
  await screen.findByText("来源：space-b");
  expect(screen.queryByText("来源：space-a")).toBeNull();
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});

it("opens preparation when adopting parameters while preserving the fixed monitor procedure", async () => {
  window.history.replaceState(null, "", "/?procedure=run-a&workbench=submitted#configuration");
  renderApp();
  // Health replaces the project-keyed provider; click the current window, not its detached loading node.
  await screen.findByTitle("Test lab");
  fireEvent.click(screen.getByRole("button", { name: "用于下次实验" }));
  await waitFor(() => expect(screen.getByText("Calibration launcher")).toBeVisible());
  const url = new URL(window.location.href);
  expect(url.hash).toBe("#launch");
  expect(url.searchParams.get("preparation")).toBe("1");
  expect(url.searchParams.has("workbench")).toBe(false);
  expect(url.searchParams.get("procedure")).toBe("run-a");
});

function runPage(ids: number[], nextCursor?: number) {
  return { items: ids.map((id) => ({ ...projectRun(`run-${id}`), sequence: id })), nextCursor };
}
