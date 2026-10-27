// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { components } from "../../api-schema";
import { CalibrationTasks } from "./CalibrationTasks";

type View = components["schemas"]["CalibrationTaskView"];
function fixture(): View {
  const check: components["schemas"]["CalibrationCheckRequest"] = {
    setup: { revision_id: "bench", content_hash: "sha256:setup" },
    codec: "scopecat.calibration-check.v1",
    result_output: "check",
    scope: { capability: "readout", targets: ["q0"], conditions: "idle", policy_version: "1" },
    context: {
      parameters: { revision_id: "daily", content_hash: "sha256:params" },
      subject: { kind: "unbound" },
      setup_content_hash: "sha256:setup",
      scenario: null,
    },
    measurement_step: "measure",
    analysis_step: "analyze",
  };
  return {
    task: {
      specification: {
        exit_scope: "current",
        task_id: "round/1",
        plan: { stages: [{ id: "q0", check, depends_on: [] }] },
        calls: {
          q0: {
            definition: { id: "check", version: "1", fingerprint: "sha256:code" },
            intent: {},
            samples: [],
          },
        },
      },
      created_at: "2026-09-23T00:00:00Z",
      mode: "running",
      control_revision: 4,
      dispatch_errors: { q0: "setup changed" },
      attempts: {},
    },
    progress: {
      stages: [{ id: "q0", state: "ready", blocked_by: [] }],
      ready: ["q0"],
      complete: false,
      successful: false,
    },
  };
}
function mount(onProcedure = vi.fn(), workspaceId?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CalibrationTasks projectId="lab" workspaceId={workspaceId} onProcedure={onProcedure} />
    </QueryClientProvider>,
  );
  return onProcedure;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("shows budget stop and retains the initial check link", async () => {
  window.history.replaceState(null, "", "/?task=round%2F1#launch");
  const view = fixture();
  view.task.mode = "finished";
  view.task.stop_reason = "repair_budget_exhausted";
  view.task.specification.repair_budget = { max_repairs: 0, elapsed: "PT1H" };
  view.task.attempts = {
    q0: [
      {
        phase: "check",
        procedure_run_id: "initial-check",
        check: view.task.specification.plan.stages[0]!.check,
      },
    ],
  };
  view.progress.stages[0]!.state = "repair_ready";
  view.progress.stages[0]!.procedure_run_id = "initial-check";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      Response.json(
        new URL(request.url).pathname.endsWith("calibration-tasks")
          ? { items: [view.task], next_cursor: null }
          : view,
      ),
    ),
  );
  const onProcedure = mount();
  expect(await screen.findByText(/已停止接纳： 修复预算已用尽/)).toBeInTheDocument();
  expect(screen.getByText(/每个阶段最多修复一次/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "打开执行： q0" }));
  expect(onProcedure).toHaveBeenCalledWith("initial-check");
});

it("does not show a frozen parameter context before a candidate output is bound", async () => {
  window.history.replaceState(null, "", "/?task=round%2F1#launch");
  const view = fixture();
  view.task.specification.plan.stages[0]!.candidate_from = {
    stage_id: "fit",
    proposal_id: "frequency",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      Response.json(
        new URL(request.url).pathname.endsWith("calibration-tasks")
          ? { items: [view.task], next_cursor: null }
          : view,
      ),
    ),
  );
  mount();
  expect(await screen.findByText(/参数输入尚未绑定/)).toBeInTheDocument();
  expect(screen.queryByText("固定测量上下文")).not.toBeInTheDocument();
});

it("separates passed checks from finalization admission and allows retry", async () => {
  window.history.replaceState(null, "", "/?task=round%2F1#launch");
  const view = fixture();
  view.task.specification.finalization = {
    definition: { id: "finalize", version: "1", fingerprint: "sha256:code" },
    intent: { calibration_task: null },
    samples: [],
  };
  view.progress.complete = true;
  view.progress.successful = true;
  view.task.dispatch_errors = {};
  view.task.finalization_error = "setup changed";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      Response.json(
        new URL(request.url).pathname.endsWith("calibration-tasks")
          ? { items: [view.task], next_cursor: null }
          : view,
      ),
    ),
  );
  mount();
  expect(await screen.findByText(/最终处理已停止接纳： setup changed/)).toBeInTheDocument();
  expect(screen.getByText(/检查通过本身并不验证/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("任务操作者"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("任务控制原因"), { target: { value: "retry" } });
  expect(screen.getByRole("button", { name: "恢复 / 重试接纳" })).toBeEnabled();
});

it("reopens a task, shows admission failure, and fences a control with the observed revision", async () => {
  window.history.replaceState(null, "", "/?task=round%2F1#launch");
  let view = fixture();
  const commands: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "POST") {
        const command = await request.json();
        commands.push(command);
        view = {
          ...view,
          task: { ...view.task, mode: "paused", control_revision: 5, last_control: command },
        };
        return Response.json(view);
      }
      return Response.json(
        new URL(request.url).pathname.endsWith("calibration-tasks")
          ? { items: [view.task], next_cursor: null }
          : view,
      );
    }),
  );
  mount();
  expect(await screen.findByText(/已停止接纳： setup changed/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "暂停任务" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("任务操作者"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("任务控制原因"), { target: { value: "inspect" } });
  fireEvent.click(screen.getByRole("button", { name: "暂停任务" }));
  await screen.findByText(/推进状态： 已暂停/);
  expect(commands).toEqual([
    {
      task_id: "round/1",
      expected_revision: 4,
      action: "pause",
      actor: "Alice",
      reason: "inspect",
    },
  ]);
  expect(screen.getByText(/已接纳的测量可能继续/)).toBeInTheDocument();
});

it("refreshes a stale control without retrying it and opens the retained execution", async () => {
  window.history.replaceState(null, "", "/?task=round%2F1#launch");
  let view = fixture();
  view.task.dispatch_errors = {};
  view.progress.stages = [{ id: "q0", state: "queued", procedure_run_id: "p1", blocked_by: [] }];
  const controls = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "POST") {
        controls();
        view = { ...view, task: { ...view.task, mode: "cancelled", control_revision: 5 } };
        return Response.json({ detail: "task control revision changed" }, { status: 409 });
      }
      return Response.json(
        new URL(request.url).pathname.endsWith("calibration-tasks")
          ? { items: [view.task], next_cursor: null }
          : view,
      );
    }),
  );
  const open = mount();
  fireEvent.click(await screen.findByRole("button", { name: "打开执行： q0" }));
  expect(open).toHaveBeenCalledWith("p1");
  fireEvent.change(screen.getByLabelText("任务操作者"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("任务控制原因"), { target: { value: "inspect" } });
  fireEvent.click(screen.getByRole("button", { name: "暂停任务" }));
  await screen.findByText("task control revision changed");
  await screen.findByText(/推进状态： 已取消/);
  expect(screen.queryByRole("button", { name: "暂停任务" })).not.toBeInTheDocument();
  expect(controls).toHaveBeenCalledTimes(1);
});

it("loads history only when expanded and paginates retained tasks", async () => {
  const view = fixture();
  const fetcher = vi.fn(async (request: Request) => {
    const cursor = new URL(request.url).searchParams.get("cursor");
    return Response.json({
      items: [
        {
          ...view.task,
          specification: {
            ...view.task.specification,
            task_id: cursor ? "older" : "round/1",
          },
        },
      ],
      next_cursor: cursor ? null : 2,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  mount();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("校准任务"));
  fireEvent.click(await screen.findByRole("button", { name: "加载更早的任务" }));
  await screen.findByRole("button", { name: /older/ });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "加载更早的任务" })).not.toBeInTheDocument(),
  );
});

it("carries the selected course workspace through task history and controls", async () => {
  window.history.replaceState(null, "", "/?task=round%2F1#launch");
  const view = fixture();
  const seen: { method: string; workspace: string | null }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      seen.push({ method: request.method, workspace: request.headers.get("X-Scopecat-Workspace") });
      if (request.method === "POST")
        return Response.json({ ...view, task: { ...view.task, mode: "cancelled" } });
      return Response.json(
        new URL(request.url).pathname.endsWith("calibration-tasks")
          ? { items: [view.task], next_cursor: null }
          : view,
      );
    }),
  );
  mount(undefined, "course-source");
  await screen.findByRole("button", { name: "取消剩余阶段" });
  fireEvent.change(screen.getByLabelText("任务操作者"), { target: { value: "tester" } });
  fireEvent.change(screen.getByLabelText("任务控制原因"), {
    target: { value: "finish course" },
  });
  fireEvent.click(screen.getByRole("button", { name: "取消剩余阶段" }));
  await waitFor(() => expect(seen.some((item) => item.method === "POST")).toBe(true));
  expect(seen.every((item) => item.workspace === "course-source")).toBe(true);
});

vi.mock("../application/OperatorSettings", () => ({
  readOperatorSettings: vi.fn(async () => ({ revision: 1, operator: "operator" })),
}));
