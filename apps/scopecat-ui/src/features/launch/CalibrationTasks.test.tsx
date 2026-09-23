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
      executions: {},
      resolved_checks: {},
    },
    progress: {
      stages: [{ id: "q0", state: "ready", blocked_by: [] }],
      ready: ["q0"],
      complete: false,
      successful: false,
    },
  };
}
function mount(onProcedure = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CalibrationTasks projectId="lab" onProcedure={onProcedure} />
    </QueryClientProvider>,
  );
  return onProcedure;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
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
  expect(await screen.findByText(/Parameter input is not bound yet/)).toBeInTheDocument();
  expect(screen.queryByText("Frozen measurement context")).not.toBeInTheDocument();
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
  expect(await screen.findByText(/Admission stopped: setup changed/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Pause task" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Task operator"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("Task control reason"), { target: { value: "inspect" } });
  fireEvent.click(screen.getByRole("button", { name: "Pause task" }));
  await screen.findByText(/Advancement: paused/);
  expect(commands).toEqual([
    {
      task_id: "round/1",
      expected_revision: 4,
      action: "pause",
      actor: "Alice",
      reason: "inspect",
    },
  ]);
  expect(screen.getByText(/Already admitted measurements may continue/)).toBeInTheDocument();
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
  fireEvent.click(await screen.findByRole("button", { name: "Open execution for q0" }));
  expect(open).toHaveBeenCalledWith("p1");
  fireEvent.change(screen.getByLabelText("Task operator"), { target: { value: "Alice" } });
  fireEvent.change(screen.getByLabelText("Task control reason"), { target: { value: "inspect" } });
  fireEvent.click(screen.getByRole("button", { name: "Pause task" }));
  await screen.findByText("task control revision changed");
  await screen.findByText(/Advancement: cancelled/);
  expect(screen.queryByRole("button", { name: "Pause task" })).not.toBeInTheDocument();
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
          specification: { ...view.task.specification, task_id: cursor ? "older" : "round/1" },
        },
      ],
      next_cursor: cursor ? null : 2,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  mount();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Calibration tasks"));
  fireEvent.click(await screen.findByRole("button", { name: "Load earlier tasks" }));
  await screen.findByRole("button", { name: /older/ });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Load earlier tasks" })).not.toBeInTheDocument(),
  );
});
