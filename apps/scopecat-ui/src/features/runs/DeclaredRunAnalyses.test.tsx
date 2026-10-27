// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  DeclaredRunAnalyses,
  RunAnalysisActionsCard,
  type RunAnalysisActions,
} from "./DeclaredRunAnalyses";

const catalog: RunAnalysisActions = {
  run_id: "original-run",
  workspace_id: "original-workspace",
  code_revision: { content_hash: "sha256:" + "a".repeat(64) },
  experiment: "signal",
  actions: [
    {
      title: "Check expected peak",
      analysis: "signal.analysis:verify_peak",
      description: "Check retained data against an explicit expected peak.",
      inputs: [
        { name: "expected_frequency", kind: "quantity", choices: [], required: true },
        {
          name: "tolerance",
          kind: "quantity",
          choices: [],
          required: false,
          default: { value: 50, unit: "MHz" },
        },
      ],
    },
  ],
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function show(value = catalog) {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DeclaredRunAnalyses catalog={value} />
    </QueryClientProvider>,
  );
}
it("requires explicit input and execution, keeps units/source, and opens the returned publication", async () => {
  const bodies: unknown[] = [];
  const fetch = vi.fn(async (request: Request) => {
    bodies.push(await request.json());
    return Response.json({
      code_revision: catalog.code_revision,
      analysis_id: "verification-result",
    });
  });
  vi.stubGlobal("fetch", fetch);
  show();
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Expected Frequency")).toBeRequired();
  expect(screen.getByLabelText("Tolerance")).toHaveValue(50);
  expect(screen.getByLabelText("Tolerance 单位")).toHaveValue("MHz");
  fireEvent.change(screen.getByLabelText("Expected Frequency"), { target: { value: "4.8" } });
  fireEvent.change(screen.getByLabelText("Expected Frequency 单位"), { target: { value: "GHz" } });
  fireEvent.click(screen.getByRole("button", { name: "执行 Check expected peak" }));
  expect(await screen.findByRole("link", { name: "查看已保存的结果" })).toHaveAttribute(
    "href",
    "?run=original-run&run-analysis=verification-result#runs",
  );
  expect(bodies).toEqual([
    {
      run_id: catalog.run_id,
      workspace_id: catalog.workspace_id,
      code_revision: catalog.code_revision,
      analysis: "signal.analysis:verify_peak",
      arguments: {
        expected_frequency: { value: 4.8, unit: "GHz" },
        tolerance: { value: 50, unit: "MHz" },
      },
    },
  ]);
  fireEvent.change(screen.getByLabelText("Tolerance"), { target: { value: "25" } });
  expect(screen.queryByRole("link", { name: "查看已保存的结果" })).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("does not offer a guessed action when the retained declaration has none", () => {
  show({ ...catalog, actions: [] });
  expect(screen.queryByRole("button")).toBeNull();
});
it("explains unsupported inputs and makes no execute button", () => {
  show({
    ...catalog,
    actions: [{ ...catalog.actions[0]!, unavailable_reason: "Use Python for this input type." }],
  });
  expect(screen.getByText(/Use Python for this input type./)).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull();
});
it("does not automatically retry a lost analysis response", async () => {
  const fetch = vi.fn(async () => {
    throw new TypeError("connection lost");
  });
  vi.stubGlobal("fetch", fetch);
  show({ ...catalog, actions: [{ ...catalog.actions[0]!, inputs: [] }] });
  fireEvent.click(screen.getByRole("button", { name: "执行 Check expected peak" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("请先检查分析历史");
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("link", { name: "查看已保存的结果" })).toBeNull();
});
it("preserves an explicitly empty string instead of silently restoring its default", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      bodies.push(await request.json());
      return Response.json({ code_revision: catalog.code_revision, analysis_id: "empty-label" });
    }),
  );
  show({
    ...catalog,
    actions: [
      {
        ...catalog.actions[0]!,
        inputs: [
          { name: "label", kind: "text", required: false, default: "original", choices: [] },
        ],
      },
    ],
  });
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "执行 Check expected peak" }));
  await screen.findByRole("link", { name: "查看已保存的结果" });
  expect(bodies[0]).toMatchObject({ arguments: { label: "" } });
});

it("loads the author directory only after expansion and explains old declarations", async () => {
  const fetch = vi.fn(async () => Response.json({ ...catalog, actions: [] }));
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunAnalysisActionsCard runId="original-run" />
    </QueryClientProvider>,
  );
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("分析此结果"));
  expect(await screen.findByText(/保存的源码未声明分析/)).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("分析此结果"));
  fireEvent.click(screen.getByText("分析此结果"));
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("keeps edited inputs when the optional section is collapsed and reopened", async () => {
  const fetch = vi.fn(async () => Response.json(catalog));
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunAnalysisActionsCard runId="original-run" />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("分析此结果"));
  const tolerance = await screen.findByLabelText("Tolerance");
  fireEvent.change(tolerance, { target: { value: "25" } });
  fireEvent.click(screen.getByText("分析此结果"));
  fireEvent.click(screen.getByText("分析此结果"));
  expect(screen.getByLabelText("Tolerance")).toHaveValue(25);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("reloads a failed directory explicitly without executing or retrying on reopen", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ detail: "Retained execution environment is unavailable" }, { status: 503 }),
    )
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunAnalysisActionsCard runId={catalog.run_id} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("分析此结果"));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Retained execution environment is unavailable",
  );
  expect(screen.getByText(/若应用连接中断/)).toHaveTextContent("或联系作者");
  expect(
    screen.queryByText(/daemon connection|Restore the original author environment/),
  ).toBeNull();
  fireEvent.click(screen.getByText("分析此结果"));
  fireEvent.click(screen.getByText("分析此结果"));
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "重新加载分析声明" }));
  fireEvent.click(screen.getByRole("button", { name: /重新加载/ }));
  expect(fetch).toHaveBeenCalledTimes(2);
  finish(Response.json(catalog));
  expect(await screen.findByLabelText("Tolerance")).toHaveValue(50);
  expect(fetch.mock.calls.every(([request]) => request.method === "GET")).toBe(true);
});

it("does not confuse missing retained identity with an empty declaration", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(null)),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunAnalysisActionsCard runId={catalog.run_id} />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("分析此结果"));
  expect(await screen.findByText(/未保存作者源码身份/)).toBeInTheDocument();
  expect(screen.queryByText(/保存的源码未声明分析/)).toBeNull();
});

it("resets expansion on run change and ignores the preceding run's pending directory", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce(Response.json({ ...catalog, run_id: "second-run" }));
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient();
  const card = (runId: string) => (
    <QueryClientProvider client={client}>
      <RunAnalysisActionsCard runId={runId} />
    </QueryClientProvider>
  );
  const view = render(card(catalog.run_id));
  fireEvent.click(screen.getByText("分析此结果"));
  await screen.findByText("正在加载已声明的分析…");
  view.rerender(card("second-run"));
  finish(Response.json(catalog));
  expect(screen.getByText("分析此结果").closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByLabelText("Tolerance")).toBeNull();
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("分析此结果"));
  await screen.findByLabelText("Tolerance");
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("blocks duplicate submits while preserving edits after an execution failure", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetch);
  show({
    ...catalog,
    actions: [
      {
        ...catalog.actions[0]!,
        inputs: [
          { name: "label", kind: "text", required: false, default: "original", choices: [] },
        ],
      },
    ],
  });
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "my edit" } });
  const form = screen.getByRole("form");
  fireEvent.submit(form);
  fireEvent.submit(form);
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  finish(Response.json({ detail: "Original environment unavailable" }, { status: 503 }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Label")).toHaveValue("my edit");
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("does not show a preceding run's edits or late analysis receipt on the next run", async () => {
  let finish!: (response: Response) => void;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    ),
  );
  const first = {
    ...catalog,
    actions: [
      {
        ...catalog.actions[0]!,
        inputs: [
          {
            name: "label",
            kind: "text" as const,
            required: false,
            default: "original",
            choices: [],
          },
        ],
      },
    ],
  };
  const client = new QueryClient();
  const form = (value: RunAnalysisActions) => (
    <QueryClientProvider client={client}>
      <DeclaredRunAnalyses catalog={value} />
    </QueryClientProvider>
  );
  const view = render(form(first));
  fireEvent.change(screen.getByLabelText("Label"), { target: { value: "first run edit" } });
  fireEvent.submit(screen.getByRole("form"));
  await vi.waitFor(() => expect(finish).toBeDefined());
  view.rerender(form({ ...first, run_id: "second-run" }));
  finish(Response.json({ code_revision: catalog.code_revision, analysis_id: "first-result" }));
  await vi.waitFor(() => expect(client.isMutating()).toBe(0));
  expect(screen.getByLabelText("Label")).toHaveValue("original");
  expect(screen.queryByRole("link", { name: "查看已保存的结果" })).toBeNull();
});

it("keeps an in-flight action locked when navigating away and back", async () => {
  let finish!: (response: Response) => void;
  const fetch = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        finish = resolve;
      }),
  );
  vi.stubGlobal("fetch", fetch);
  const value = { ...catalog, actions: [{ ...catalog.actions[0]!, inputs: [] }] };
  const client = new QueryClient();
  const form = (runId: string) => (
    <QueryClientProvider client={client}>
      <DeclaredRunAnalyses catalog={{ ...value, run_id: runId }} />
    </QueryClientProvider>
  );
  const view = render(form(catalog.run_id));
  fireEvent.submit(screen.getByRole("form"));
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  view.rerender(form("second-run"));
  expect(screen.getByRole("button")).toBeEnabled();
  view.rerender(form(catalog.run_id));
  expect(screen.getByRole("button", { name: "正在分析…" })).toBeDisabled();
  fireEvent.submit(screen.getByRole("form"));
  expect(fetch).toHaveBeenCalledTimes(1);
  finish(Response.json({ code_revision: catalog.code_revision, analysis_id: "first-result" }));
  await vi.waitFor(() => expect(screen.getByRole("button")).toBeEnabled());
});
