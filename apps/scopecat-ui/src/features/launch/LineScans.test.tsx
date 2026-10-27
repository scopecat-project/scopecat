// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LineScanWorkspace } from "./LineScans";
const openPlan = vi.hoisted(() => vi.fn(async () => true));
vi.mock("./LaunchDraft", () => ({
  useLaunchDraft: () => ({
    projectId: "project",
    workspaceId: "practice",
    openPlan,
    draft: { revision: 1 },
    isCurrent: () => true,
    recovery: { canLeave: () => true },
  }),
}));
vi.mock("../application/use-operator-input", () => ({
  useOperatorInput: () => ({
    actor: "operator",
    freshSnapshot: async () => "operator",
    setActor: vi.fn(),
  }),
}));
const template = {
  workspace_id: "practice",
  experiment: "signal",
  version: "1",
  scientific_binding: {
    subject: { kind: "unbound" },
    config_content_hash: "config",
    setup_content_hash: "setup",
  },
  selection: { configuration: { kind: "unselected" } },
  inputs: {},
  control_edits: {},
};
const line = { line_id: "line_1", label: "A" };
const scan = { scan_id: "lines_1", name: "Readout", revision: 1, template, lines: [line] };
function mount(post: (request: Request) => Promise<Response>, launcher?: Promise<Response>) {
  const requests: Request[] = [];
  vi.stubGlobal("fetch", async (request: Request) => {
    requests.push(request.clone());
    const path = new URL(request.url).pathname;
    if (request.method === "POST") return post(request);
    if (path.endsWith("experiment-plans"))
      return Response.json({
        items: [{ ref: { plan_id: "plan" }, name: "Template", definition: template }],
      });
    if (path.endsWith("experiment-launcher"))
      return launcher ?? Response.json({ entries: [{ id: "signal", version: "1" }] });
    if (path.endsWith("/lines_1"))
      return Response.json({
        scan,
        lines: [{ line, state: "not_started", attempts: [] }],
        blocked_reason: null,
      });
    return Response.json({ items: [scan] });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const rendered = render(
    <QueryClientProvider client={client}>
      <LineScanWorkspace />
    </QueryClientProvider>,
  );
  return { requests, ...rendered };
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  openPlan.mockClear();
  openPlan.mockResolvedValue(true);
  window.history.replaceState(null, "", "/");
});
async function fill() {
  fireEvent.click(screen.getByText("创建线路清单"));
  await screen.findByRole("option", { name: "Template" });
  fireEvent.change(screen.getByLabelText("测量模板"), { target: { value: "plan" } });
  fireEvent.change(screen.getByLabelText("线路清单名称"), { target: { value: "Readout" } });
  fireEvent.change(screen.getByLabelText("物理线路，每行一条"), {
    target: { value: "A\nB" },
  });
}
it("carries workspace scope and permits correction after a definite rejection", async () => {
  const { requests } = mount(async () =>
    Response.json({ detail: "Invalid list" }, { status: 422 }),
  );
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "保存线路清单" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("线路清单名称")).toBeEnabled();
  expect(screen.getByRole("button", { name: "保存线路清单" })).toBeEnabled();
  expect(
    requests.every((request) => request.headers.get("X-Scopecat-Workspace") === "practice"),
  ).toBe(true);
});
it("retries the exact retained list command after an uncertain response", async () => {
  const commands: unknown[] = [];
  mount(async (request) => {
    commands.push(await request.json());
    throw new TypeError("Response lost");
  });
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "保存线路清单" }));
  const retry = await screen.findByRole("button", { name: "重试保存线路清单" });
  await waitFor(() => expect(retry).toBeEnabled());
  expect(screen.getByLabelText("线路清单名称")).toBeDisabled();
  fireEvent.click(retry);
  await waitFor(() => expect(commands).toHaveLength(2));
  expect(commands[1]).toEqual(commands[0]);
});
it("does not prepare a response after the user chooses another list", async () => {
  window.history.replaceState(null, "", "/?line-scan=lines_1");
  let resolve!: (response: Response) => void;
  const launcher = new Promise<Response>((done) => {
    resolve = done;
  });
  const { requests } = mount(async () => Response.json(scan), launcher);
  fireEvent.click(await screen.findByRole("button", { name: "准备 A" }));
  await waitFor(() =>
    expect(requests.some((request) => request.url.includes("experiment-launcher"))).toBe(true),
  );
  fireEvent.change(screen.getByLabelText("线路清单"), { target: { value: "" } });
  await act(async () => {
    resolve(Response.json({ entries: [{ id: "signal", version: "1" }] }));
  });
  expect(openPlan).not.toHaveBeenCalled();
  expect(window.location.search).not.toContain("preparation");
});
it("does not navigate when draft adoption is declined", async () => {
  window.history.replaceState(null, "", "/?line-scan=lines_1");
  openPlan.mockResolvedValue(false);
  mount(async () => Response.json(scan));
  fireEvent.click(await screen.findByRole("button", { name: "准备 A" }));
  await waitFor(() => expect(openPlan).toHaveBeenCalledOnce());
  expect(window.location.search).not.toContain("preparation");
});
