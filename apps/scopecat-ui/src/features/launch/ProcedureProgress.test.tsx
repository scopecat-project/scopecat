import { installLaunchRecoveryRoutes } from "../../test/launch-recovery-fixture";
import { serviceWorkspaceCatalog } from "../../test/scientific-fixtures";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ProcedureProgress } from "./ProcedureProgress";
import { LaunchDraftProvider } from "./LaunchDraft";
import { LaunchWorkspace } from "./LaunchWorkspace";
import { outputHref, type ProcedureOperatorView } from "./procedure-operator";
import { RunCancellationNotice } from "../runs/RunCancellationNotice";

const NOW = "2026-09-01T00:00:00Z";
function view(overrides: Partial<ProcedureOperatorView> = {}): ProcedureOperatorView {
  return {
    procedure: {
      procedure_run_id: "p1",
      request_key: "original",
      definition: {
        id: "temperature-diagnostic",
        version: "1",
        fingerprint: "sha256:" + "1".repeat(64),
      },
      intent: {},
      intent_hash: "sha256:" + "2".repeat(64),
      samples: [],
      resolved_samples: [],
      revision: 1,
      state: "ready",
      created_at: NOW,
      updated_at: NOW,
    },
    dispatch: { management: "paused", input_continuation_retryable: false, worker_running: false },
    current_step: null,
    current_child: null,
    child_runs: [],
    steps: { procedure_run_id: "p1", items: [], next_cursor: null },
    dispatch_blocked_reason: null,
    ...overrides,
  };
}
function mount(component = <ProcedureProgress procedureId="p1" />) {
  if (component.type === LaunchDraftProvider) installLaunchRecoveryRoutes();
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {component}
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("reopens a paused admission and explicitly dispatches the same procedure", async () => {
  window.history.replaceState(null, "", "/?procedure=p1#launch");
  let dispatched = false;
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(`${request.method} ${new URL(request.url).pathname}`);
      if (request.method === "POST") {
        dispatched = true;
        return Response.json({ procedure_id: "p1", dispatch_error: null });
      }
      return Response.json(
        view({
          dispatch: {
            management: dispatched ? "active" : "paused",
            input_continuation_retryable: false,
            worker_running: false,
          },
        }),
      );
    }),
  );
  const first = mount();
  await screen.findByText("调度已暂停");
  first.unmount();
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "继续任务" }));
  await screen.findByText("等待 Worker");
  expect(requests.filter((request) => request.startsWith("POST"))).toEqual([
    "POST /api/v1/procedures/p1/dispatch",
  ]);
});

it("shows retained worker diagnostics separately from execution status", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        view({
          dispatch: {
            management: "paused",
            input_continuation_retryable: false,
            worker_running: false,
            failure: {
              kind: "dispatch",
              message: "Author workspace is unavailable",
              observed_at: NOW,
            },
            log_path: "C:\\lab-data\\procedure-workers\\abc\\worker.log",
          },
        }),
      ),
    ),
  );
  mount();
  await screen.findByText("Worker 诊断： Author workspace is unavailable");
  expect(screen.getByText("C:\\lab-data\\procedure-workers\\abc\\worker.log")).toBeInTheDocument();
  expect(screen.getByText("调度已暂停")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "继续任务" })).toBeEnabled();
});

it.each([
  ["waiting_for_input", "等待人工判断"],
  ["attention_required", "需要处理"],
  ["leased", "执行中"],
] as const)("presents %s without an unsupported dispatch action", async (state, label) => {
  const data = view();
  data.procedure.state = state;
  data.procedure.attention_reason = state === "attention_required" ? "Child outcome unknown" : null;
  data.dispatch_blocked_reason = "Only ready work can dispatch.";
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(data)),
  );
  mount();
  await screen.findByText(label);
  expect(screen.queryByRole("button", { name: "继续任务" })).toBeNull();
  expect(screen.queryByText(/记录人工判断后，返回此处继续/) !== null).toBe(
    state === "waiting_for_input",
  );
  expect(
    screen.queryByRole("link", { name: /查看结果并作出人工判断/ })?.getAttribute("href") ?? null,
  ).toBe(state === "waiting_for_input" ? "?procedure=p1#decisions" : null);
});

it("shows an uncertain current child as attention even when its parent is ready and managed", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...view({
          dispatch: {
            management: "active",
            input_continuation_retryable: false,
            worker_running: false,
          },
          dispatch_blocked_reason: "A child run has an unknown outcome; inspect retained evidence.",
        }),
        current_child: {
          step_key: "diagnostic",
          run: {
            control: {
              state: "attention_required",
              sequence: 1,
              completed_point_count: 1,
              attention_reason: "External effect needs reconciliation",
              point_plan: {
                initial_point_count: 1,
                accepted_point_count: 1,
                point_limit: 1,
                decision_count: 0,
                optimizer_attempt_count: 0,
                operator_request_count: 0,
                plan_closed: true,
              },
              admission: {
                run_id: "retained-child",
                admitted_at: NOW,
                plan: {
                  experiment_id: "diagnostic",
                  point_count: 1,
                  initial_point_count: 1,
                  point_limit: 1,
                  coordinates: [],
                  sampled_points: [],
                  sampled_points_truncated: false,
                },
              },
            },
            snapshot: {
              run_id: "retained-child",
              config_content_hash: "sha256:config",
              outcome: { result: "interrupted", certainty: "indeterminate" },
            },
            resources: [],
          },
        },
      }),
    ),
  );
  mount();
  expect(await screen.findByText("需要处理", { exact: true })).toHaveAttribute("role", "status");
  expect(screen.getByRole("alert")).toHaveTextContent("采集结果尚不确定");
  expect(screen.queryByText("等待 Worker")).toBeNull();
  expect(screen.queryByRole("button", { name: "继续任务" })).toBeNull();
  expect(screen.getByRole("link", { name: /查看已保存结果/ })).toHaveAttribute(
    "href",
    "?procedure=p1&run=retained-child#runs",
  );
  expect(screen.getByRole("link", { name: "在新标签页或窗口打开结果" })).toHaveAttribute(
    "href",
    "/?run=retained-child&focus=run#runs",
  );
  fireEvent.click(screen.getByRole("link", { name: /查看已保存结果/ }));
  expect(window.location.search).toBe("?procedure=p1&run=retained-child");
  expect(window.location.hash).toBe("#runs");
  expect(window.history.state.scopecatPosition).toBe(1);
});

it("shows failed closure and its reason without suggesting retry", async () => {
  const data = view();
  data.procedure.state = "closed";
  data.procedure.closure = { status: "failed", reason: "Source failed", closed_at: NOW };
  data.dispatch_blocked_reason = "Only ready work can dispatch.";
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(data)),
  );
  mount();
  await screen.findByText("失败");
  expect(screen.getByText("Source failed")).toBeVisible();
  expect(screen.queryByText(/Waiting for your explicit Continue/)).toBeNull();
  expect(screen.queryByRole("button", { name: /dispatch|retry/i })).toBeNull();
});

it("retains current phase while loading earlier step history", async () => {
  const data = view();
  data.current_step = {
    procedure_run_id: "p1",
    step_key: "current-acquisition",
    attempt: 1,
    operation: "run",
    intent_hash: "sha256:" + "1".repeat(64),
    inputs: [],
    revision: 1,
    state: "running",
    started_at: NOW,
    updated_at: NOW,
  };
  data.steps.next_cursor = 2;
  data.dispatch_blocked_reason = "A child run has an unknown outcome.";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      Response.json(
        new URL(request.url).searchParams.has("cursor")
          ? {
              ...data,
              steps: {
                ...data.steps,
                next_cursor: null,
                items: [
                  {
                    ...data.current_step,
                    step_key: "earlier",
                    state: "failed",
                    failure_reason: "Earlier failure",
                  },
                ],
              },
            }
          : data,
      ),
    ),
  );
  mount();
  await screen.findByText("当前步骤： current-acquisition · 执行中");
  fireEvent.click(screen.getByRole("button", { name: "加载更早步骤" }));
  await screen.findByText("earlier: 失败");
  expect(screen.getByText("当前步骤： current-acquisition · 执行中")).toBeVisible();
  expect(screen.queryByRole("button", { name: "继续任务" })).toBeNull();
});

it("finds durable work from history without copying an ID", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("author-workspaces")) return Response.json(serviceWorkspaceCatalog);
      if (path.endsWith("experiment-plans")) return Response.json({ items: [] });
      if (path.endsWith("experiment-launcher")) return Response.json({ entries: [] });
      if (path.endsWith("procedures"))
        return Response.json({ items: [view().procedure], next_cursor: null });
      return Response.json(view());
    }),
  );
  mount(
    <LaunchDraftProvider projectId="test-project">
      <LaunchWorkspace />
    </LaunchDraftProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "已提交任务" }));
  fireEvent.click(await screen.findByRole("button", { name: /temperature-diagnostic/ }));
  await screen.findByText("调度已暂停");
  expect(new URLSearchParams(window.location.search).get("submitted_task")).toBe("p1");
  expect(new URLSearchParams(window.location.search).get("procedure")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "在监看中打开" }));
  expect(new URLSearchParams(window.location.search).get("procedure")).toBe("p1");
  expect(new URLSearchParams(window.location.search).get("workbench")).toBeNull();
});

it("links exact run and analysis publications while retaining the parent context", () => {
  expect(outputHref({ kind: "run", run_id: "child" }, "p1")).toBe("?procedure=p1&run=child#runs");
  expect(
    outputHref(
      { kind: "analysis", analysis_record_id: "result", subject: { kind: "run", run_id: "child" } },
      "p1",
    ),
  ).toBe("?procedure=p1&run-analysis=result&run=child#runs");
  expect(
    outputHref(
      {
        kind: "analysis",
        analysis_record_id: "result",
        subject: { kind: "sample", sample_id: "sample-1" },
      },
      "p1",
    ),
  ).toBe("?procedure=p1&sample-analysis=result&sample=sample-1#samples");
});

it("does not label a run cancellation request as completion", () => {
  const rendered = render(
    <RunCancellationNotice run={{ status: "running", cancellationRequestedAt: NOW }} />,
  );
  expect(screen.getByRole("status")).toHaveTextContent("取消尚未完成");
  rendered.rerender(
    <RunCancellationNotice run={{ status: "cancelled", cancellationRequestedAt: NOW }} />,
  );
  expect(screen.queryByRole("status")).toBeNull();
});

it("links the immutable failed source and retained acquisition of a recovery", async () => {
  const data = view();
  data.procedure.recovery = {
    adapter_id: "analysis-only",
    procedure_run_id: "old-failure",
    revision: 7,
    definition: data.procedure.definition,
    run_step: {
      step_key: "sample",
      attempt: 1,
      revision: 2,
      intent_hash: "sha256:" + "3".repeat(64),
    },
    failed_analysis_step: {
      step_key: "summary",
      attempt: 1,
      revision: 2,
      intent_hash: "sha256:" + "4".repeat(64),
    },
    retained_run: { kind: "run", run_id: "retained-sample" },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(data)),
  );
  mount();
  expect(await screen.findByRole("link", { name: "失败任务 old-failure" })).toHaveAttribute(
    "href",
    "?procedure=old-failure#launch",
  );
  expect(screen.getByRole("link", { name: "保留 run retained-sample" })).toHaveAttribute(
    "href",
    "?run=retained-sample#runs",
  );
});

it("opens retained result links in place and preserves modified-click behavior", async () => {
  window.history.replaceState(null, "", "/?procedure=p1&run=previous#launch");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json(
        view({
          steps: {
            procedure_run_id: "p1",
            next_cursor: null,
            items: [
              {
                procedure_run_id: "p1",
                step_key: "experiment",
                revision: 1,
                inputs: [],
                intent_hash: "sha256:" + "3".repeat(64),
                operation: "run",
                attempt: 1,
                state: "succeeded",
                output: { kind: "run", run_id: "exact-run" },
              },
            ],
          },
        }),
      ),
    ),
  );
  mount();
  const result = await screen.findByRole("link", { name: "打开 run" });
  // A modified click belongs to the browser; it must not change this window.
  expect(fireEvent.click(result, { ctrlKey: true })).toBe(true);
  expect(window.location.search).toBe("?procedure=p1&run=previous");
  expect(fireEvent.click(result)).toBe(false);
  expect(window.location.search).toBe("?procedure=p1&run=exact-run");
  expect(window.location.hash).toBe("#runs");
  expect(fireEvent.click(screen.getByRole("link", { name: "重新打开此任务" }))).toBe(false);
  expect(window.location.search).toBe("?procedure=p1");
  expect(window.location.hash).toBe("#launch");
});

it("cancels a retained course procedure with its source workspace", async () => {
  const data = view();
  data.workspace_id = "course-one";
  data.procedure.source = null;
  const headers: (string | null)[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "POST") {
        headers.push(request.headers.get("X-Scopecat-Workspace"));
        return Response.json({ run: data.procedure });
      }
      return Response.json(data);
    }),
  );
  mount();
  fireEvent.change(await screen.findByLabelText("取消操作人"), {
    target: { value: "Alice" },
  });
  fireEvent.change(screen.getByLabelText("取消原因"), {
    target: { value: "Stop course" },
  });
  fireEvent.click(screen.getByRole("button", { name: "取消等待任务" }));
  await screen.findByRole("button", { name: "取消等待任务" });
  expect(headers).toEqual(["course-one"]);
});

vi.mock("../application/OperatorSettings", () => ({
  readOperatorSettings: vi.fn(async () => ({ revision: 1, operator: "operator" })),
}));

it("preserves operator focus and text selection when live task state refreshes", async () => {
  const data = view();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(data)),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ProcedureProgress procedureId="p1" />
    </QueryClientProvider>,
  );
  const reason = await screen.findByLabelText<HTMLInputElement>("取消原因");
  fireEvent.change(reason, { target: { value: "Keep this operator reason" } });
  reason.focus();
  reason.setSelectionRange(5, 9);
  await act(async () => {
    client.setQueryData(["procedure-operator", "p1"], {
      pages: [{ ...data, procedure: { ...data.procedure, revision: 2 } }],
      pageParams: [undefined],
    });
  });
  expect(reason).toHaveFocus();
  expect(reason).toHaveValue("Keep this operator reason");
  expect([reason.selectionStart, reason.selectionEnd]).toEqual([5, 9]);
});
