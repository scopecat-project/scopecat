// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { SubmittedWork } from "./SubmittedWork";

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});
function task(id: string) {
  return {
    procedure_run_id: id,
    definition: { id: `experiment-${id}`, version: "1" },
    state: "ready",
    revision: 7,
    created_at: "2026-10-22T00:00:00Z",
  };
}
function operator(id: string, state = "ready") {
  return {
    procedure: {
      ...task(id),
      state,
      resource_wait: state === "ready" ? { step_key: "acquire", run_id: `child-${id}` } : undefined,
    },
    workspace_id: "author-source",
    dispatch: { management: "active", worker_running: state === "leased" },
    current_step: null,
    current_child: null,
    child_runs: [],
    steps: { items: [], next_cursor: null },
    dispatch_blocked_reason: "Waiting on resources",
  };
}
function mount(fetcher: (request: Request) => Promise<Response>) {
  window.history.replaceState(null, "", "/?procedure=A&workbench=submitted#launch");
  vi.stubGlobal("fetch", fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const open = vi.fn();
  const view = render(
    <QueryClientProvider client={client}>
      <SubmittedWork onOpenMonitor={open} />
    </QueryClientProvider>,
  );
  return { client, open, view };
}
function transport(read: (id: string) => Promise<Response>, writes: Request[] = []) {
  return async (request: Request) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/operator-settings"))
      return Response.json({ revision: 1, operator: "tester" });
    if (url.pathname.endsWith("/procedures"))
      return Response.json({ items: [task("B"), task("C")], next_cursor: null });
    if (request.method === "POST") {
      writes.push(request);
      return Response.json({});
    }
    return read(url.pathname.split("/").at(-2)!);
  };
}
it("inspects B inline while monitor A remains fixed, reuses its cancellation and closes only the view", async () => {
  const writes: Request[] = [];
  const { open } = mount(transport(async (id) => Response.json(operator(id)), writes));
  fireEvent.click(await screen.findByRole("button", { name: /experiment-B/ }));
  await screen.findByRole("heading", { name: "已提交任务 B" });
  await screen.findByText("等待资源", { exact: true });
  expect(new URL(window.location.href).searchParams.get("procedure")).toBe("A");
  expect(open).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("取消等待任务", { selector: "summary" }));
  fireEvent.change(screen.getByLabelText("取消原因"), {
    target: { value: "No longer needed" },
  });
  fireEvent.click(screen.getByRole("button", { name: "取消等待任务" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(new URL(writes[0]!.url).pathname).toBe("/api/v1/procedures/B/cancel");
  expect(await writes[0]!.json()).toEqual({
    procedure_run_id: "B",
    expected_run_revision: 7,
    actor: "tester",
    reason: "No longer needed",
  });
  expect(writes[0]!.headers.get("X-Scopecat-Workspace")).toBe("author-source");
  fireEvent.click(screen.getByRole("button", { name: "在监看中打开" }));
  expect(open).toHaveBeenCalledWith("B");
  fireEvent.click(screen.getByRole("button", { name: "关闭检查" }));
  expect(screen.queryByRole("heading", { name: "执行进度" })).not.toBeInTheDocument();
  expect(new URL(window.location.href).searchParams.get("procedure")).toBe("A");
  expect(writes).toHaveLength(1);
});
it("does not show a late B response or its action state after selecting C", async () => {
  let release!: (response: Response) => void;
  const delayed = new Promise<Response>((resolve) => {
    release = resolve;
  });
  const reads: string[] = [];
  mount(
    transport(async (id) => {
      reads.push(id);
      return id === "B" ? delayed : Response.json(operator(id, "leased"));
    }),
  );
  fireEvent.click(await screen.findByRole("button", { name: /experiment-B/ }));
  await waitFor(() => expect(reads).toContain("B"));
  fireEvent.click(screen.getByRole("button", { name: /experiment-C/ }));
  await screen.findByText("执行中", { exact: true });
  await act(async () => release(Response.json(operator("B"))));
  expect(screen.getByRole("heading", { name: "已提交任务 C" })).toBeVisible();
  expect(screen.queryByText("取消等待任务", { selector: "summary" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("取消后续步骤", { selector: "summary" }));
  expect(screen.getByRole("button", { name: "当前步骤结束后停止" })).toBeDisabled();
  expect(new URL(window.location.href).searchParams.get("procedure")).toBe("A");
});
it("uses the existing stale-state fence to disable cancellation after disconnection", async () => {
  let offline = false;
  const { client } = mount(
    transport(async (id) => {
      if (offline) throw new Error("offline");
      return Response.json(operator(id));
    }),
  );
  fireEvent.click(await screen.findByRole("button", { name: /experiment-B/ }));
  await screen.findByText("等待资源", { exact: true });
  fireEvent.click(screen.getByText("取消等待任务", { selector: "summary" }));
  fireEvent.change(screen.getByLabelText("取消原因"), { target: { value: "Cancel B" } });
  expect(screen.getByRole("button", { name: "取消等待任务" })).toBeEnabled();
  offline = true;
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["procedure-operator", "B"] });
  });
  expect(await screen.findByRole("alert")).toHaveTextContent("无法更新任务状态");
  expect(screen.getByRole("button", { name: "取消等待任务" })).toBeDisabled();
  expect(new URL(window.location.href).searchParams.get("procedure")).toBe("A");
});

it("shows the retained child resource owner without changing the monitor binding", async () => {
  mount(
    transport(async (id) =>
      Response.json({
        ...operator(id),
        current_child: {
          step_key: "acquire",
          run: {
            control: {
              state: "queued",
              sequence: 1,
              completed_point_count: 0,
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
                run_id: "child-B",
                admitted_at: "2026-10-22",
                plan: {
                  experiment_id: "signal",
                  point_count: 1,
                  initial_point_count: 1,
                  point_limit: 1,
                  coordinates: [],
                  sampled_points: [],
                  sampled_points_truncated: false,
                },
              },
            },
            snapshot: { run_id: "child-B", config_content_hash: "sha256:config" },
            resources: [
              {
                resource: { id: "shared-source" },
                blocked_by: { owner_kind: "run", owner_id: "run-A", status: "held" },
              },
            ],
          },
        },
      }),
    ),
  );
  fireEvent.click(await screen.findByRole("button", { name: /experiment-B/ }));
  const owner = await screen.findByRole("link", { name: "其他 run" });
  expect(owner).toHaveAttribute("href", "?procedure=B&run=run-A#runs");
  expect(owner.parentElement).toHaveTextContent(/等待资源 shared-source.*占用者：.*其他 run/);
  expect(new URL(window.location.href).searchParams.get("procedure")).toBe("A");
});
