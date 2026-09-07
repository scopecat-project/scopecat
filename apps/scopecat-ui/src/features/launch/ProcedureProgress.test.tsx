// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ProcedureProgress } from "./ProcedureProgress";
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
      revision: 1,
      state: "ready",
      created_at: NOW,
      updated_at: NOW,
    },
    dispatch: { management: "paused", worker_running: false },
    current_step: null,
    current_child: null,
    child_runs: [],
    steps: { procedure_run_id: "p1", items: [], next_cursor: null },
    dispatch_blocked_reason: null,
    ...overrides,
  };
}
function mount(component = <ProcedureProgress procedureId="p1" />) {
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
        view({ dispatch: { management: dispatched ? "active" : "paused", worker_running: false } }),
      );
    }),
  );
  const first = mount();
  await screen.findByText("Dispatch paused");
  first.unmount();
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Dispatch existing procedure" }));
  await screen.findByText("Queued for a worker");
  expect(requests.filter((request) => request.startsWith("POST"))).toEqual([
    "POST /api/v1/procedures/p1/dispatch",
  ]);
});

it.each([
  ["waiting_for_input", "Waiting for review"],
  ["attention_required", "Needs attention"],
  ["leased", "Running"],
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
  expect(screen.queryByRole("button", { name: "Dispatch existing procedure" })).toBeNull();
  expect(screen.queryByRole("link", { name: /Review results/ })?.getAttribute("href") ?? null).toBe(
    state === "waiting_for_input" ? "?procedure=p1#decisions" : null,
  );
});

it("shows an uncertain current child as attention even when its parent is ready and managed", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        ...view({
          dispatch: { management: "active", worker_running: false },
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
  expect(await screen.findByText("Needs attention", { exact: true })).toHaveAttribute(
    "role",
    "status",
  );
  expect(screen.queryByText("Queued for a worker")).toBeNull();
  expect(screen.queryByRole("button", { name: "Dispatch existing procedure" })).toBeNull();
  expect(screen.getByRole("link", { name: /Open current child run/ })).toHaveAttribute(
    "href",
    "?procedure=p1&run=retained-child#runs",
  );
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
  await screen.findByText("Failed");
  expect(screen.getByText("Source failed")).toBeVisible();
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
  await screen.findByText("Current step: current-acquisition · Running");
  fireEvent.click(screen.getByRole("button", { name: "Load earlier steps" }));
  await screen.findByText("earlier: Failed");
  expect(screen.getByText("Current step: current-acquisition · Running")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Dispatch existing procedure" })).toBeNull();
});

it("finds durable work from history without copying an ID", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("experiment-launcher")) return Response.json({ entries: [] });
      if (path.endsWith("procedures"))
        return Response.json({ items: [view().procedure], next_cursor: null });
      return Response.json(view());
    }),
  );
  mount(<LaunchWorkspace />);
  fireEvent.click(screen.getByText("Retained procedures"));
  fireEvent.click(await screen.findByRole("button", { name: /temperature-diagnostic/ }));
  await screen.findByText("Dispatch paused");
  expect(new URLSearchParams(window.location.search).get("procedure")).toBe("p1");
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
  expect(screen.getByRole("status")).toHaveTextContent("cancellation is not complete yet");
  rendered.rerender(
    <RunCancellationNotice run={{ status: "cancelled", cancellationRequestedAt: NOW }} />,
  );
  expect(screen.queryByRole("status")).toBeNull();
});
