// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApparatusHistory } from "./ApparatusHistory";
import {
  findObjects,
  resolveObject,
  objectHistory,
  getObservation,
  type ObjectRevision,
  type Observation,
} from "./apparatus-api";
import { navigate } from "../../lib/navigation";
vi.mock("./apparatus-api", async (original) => ({
  ...(await original<typeof import("./apparatus-api")>()),
  findObjects: vi.fn(),
  resolveObject: vi.fn(),
  objectHistory: vi.fn(),
  getObservation: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/");
});
const object = (id: string, revision = 2): ObjectRevision => ({
  ref: {
    catalog_id: "space",
    object_id: id,
    revision,
    content_hash: `sha256:${String(revision).repeat(64)}`,
  },
  content: {
    name: "同名输入线",
    kind: "line",
    aliases: ["blue"],
    description: `description ${id} v${revision}`,
  },
  actor: "reader",
  recorded_at: "2026-10-26T10:00:00Z",
});
const observation = (id: string, objectId = "a"): Observation => ({
  id,
  draft: {
    subject: object(objectId, 1).ref,
    title: id,
    actor: "recorder",
    supersedes: "original",
    conditions: { temperature: "unknown" },
    note: "original note",
    run_ids: ["exact-run"],
    attachments: [
      { filename: "same.pptx", content_hash: "sha256:" + "a".repeat(64), size_bytes: 12 },
    ],
  },
  recorded_at: "2026-10-26T11:00:00Z",
});
function setup() {
  window.history.replaceState(null, "", "/?data=apparatus#history");
  vi.mocked(findObjects).mockResolvedValue({ items: [object("a"), object("b")], next_cursor: 8 });
  vi.mocked(resolveObject).mockImplementation(async (ref) => object(ref.object_id, ref.revision));
  vi.mocked(objectHistory).mockResolvedValue({
    items: [observation("correction")],
    next_cursor: 4,
  });
  vi.mocked(getObservation).mockImplementation(async (id) => observation(id));
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const open = vi.fn();
  const view = (projectId = "space") => (
    <QueryClientProvider client={client}>
      <ApparatusHistory
        key={projectId}
        projectId={projectId}
        daemonUnavailable={false}
        onOpenRun={open}
      />
    </QueryClientProvider>
  );
  return { ...render(view()), view, open };
}
it("keeps exact historical descriptions and fetches cross-page corrections by ID", async () => {
  const { open } = setup();
  fireEvent.click(await screen.findByRole("button", { name: "同名输入线 · a" }));
  fireEvent.click(await screen.findByRole("button", { name: "correction · correction" }));
  let detail = await screen.findByRole("article", { name: "观察详情" });
  expect(await within(detail).findByText("description a v1")).toBeVisible();
  expect(screen.getByText("description a v2")).toBeVisible();
  expect(resolveObject).toHaveBeenCalledWith(object("a", 1).ref, expect.any(AbortSignal));
  expect(within(detail).getByText("未知")).toBeVisible();
  const link = within(detail).getByRole("link", { name: "下载原文件：same.pptx" });
  expect(link).toHaveAttribute("download", "same.pptx");
  expect(link.getAttribute("href")).toContain("/correction/attachments/sha256%3A");
  fireEvent.click(within(detail).getByRole("button", { name: "打开 run：exact-run" }));
  expect(open).toHaveBeenCalledWith("exact-run");
  fireEvent.click(screen.getByRole("button", { name: "更早观察" }));
  fireEvent.click(within(detail).getByRole("button", { name: /查看本记录更正的原记录/ }));
  detail = await screen.findByRole("article", { name: "观察详情" });
  await within(detail).findByRole("heading", { name: "original" });
  expect(getObservation).toHaveBeenCalledWith("original", expect.any(AbortSignal));
  expect(new URL(location.href).searchParams.get("apparatus-before")).toBe("4");
});
it("ignores a late A observation after switching to same-name B and isolates a new data space", async () => {
  const { rerender, view } = setup();
  let finish!: (item: Observation) => void;
  vi.mocked(getObservation).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "同名输入线 · a" }));
  fireEvent.click(await screen.findByRole("button", { name: "correction · correction" }));
  fireEvent.click(screen.getByRole("button", { name: "同名输入线 · b" }));
  await screen.findByText("description b v2");
  await act(async () => finish(observation("late A")));
  expect(screen.queryByText("late A")).not.toBeInTheDocument();
  expect(screen.queryByRole("article")).not.toBeInTheDocument();
  const calls = vi.mocked(resolveObject).mock.calls.length;
  rerender(view("other-space"));
  expect(await screen.findByRole("alert")).toHaveTextContent("属于另一个数据空间");
  expect(resolveObject).toHaveBeenCalledTimes(calls);
  expect(screen.queryByText("description b v2")).not.toBeInTheDocument();
});
it("does not substitute first/latest for missing observations or exact description revisions", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "同名输入线 · a" }));
  vi.mocked(getObservation).mockRejectedValue(new Error("not found"));
  fireEvent.click(await screen.findByRole("button", { name: "correction · correction" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("观察不可用：correction");
  expect(screen.queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
  vi.mocked(resolveObject).mockRejectedValue(new Error("exact revision missing"));
  fireEvent.click(screen.getByRole("button", { name: "同名输入线 · b" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "对象描述不可用：exact revision missing",
  );
  expect(screen.queryByText("description a v2")).not.toBeInTheDocument();
});
it("restores search, object and both cursors from a history location", async () => {
  setup();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "blue" } });
  fireEvent.submit(screen.getByRole("textbox").closest("form")!);
  fireEvent.click(await screen.findByRole("button", { name: "同名输入线 · a" }));
  fireEvent.click(await screen.findByRole("button", { name: "更多对象" }));
  fireEvent.click(await screen.findByRole("button", { name: "更早观察" }));
  const saved = location.href;
  fireEvent.click(screen.getByRole("button", { name: "同名输入线 · b" }));
  await screen.findByText("description b v2");
  act(() => navigate(saved));
  await screen.findByText("description a v2");
  expect(screen.getByRole("textbox")).toHaveValue("blue");
  expect(findObjects).toHaveBeenCalledWith("blue", 8, expect.any(AbortSignal));
  expect(objectHistory).toHaveBeenCalledWith("a", 4, expect.any(AbortSignal));
});

it("does not show a same-object-ID observation from another catalog", async () => {
  setup();
  const foreign = observation("foreign");
  foreign.draft.subject.catalog_id = "another-catalog";
  vi.mocked(getObservation).mockResolvedValue(foreign);
  fireEvent.click(await screen.findByRole("button", { name: "同名输入线 · a" }));
  fireEvent.click(await screen.findByRole("button", { name: "correction · correction" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("观察不属于所选目录和对象");
  expect(screen.queryByRole("link", { name: /下载/ })).not.toBeInTheDocument();
});

it("ignores late search results after a newer query", async () => {
  setup();
  await screen.findByRole("button", { name: "同名输入线 · a" });
  let finish!: (page: Awaited<ReturnType<typeof findObjects>>) => void;
  vi.mocked(findObjects).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "slow" } });
  fireEvent.submit(screen.getByRole("textbox").closest("form")!);
  vi.mocked(findObjects).mockResolvedValue({ items: [object("b")] });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "fast" } });
  fireEvent.submit(screen.getByRole("textbox").closest("form")!);
  await screen.findByRole("button", { name: "同名输入线 · b" });
  await act(async () => finish({ items: [object("a")] }));
  expect(screen.queryByRole("button", { name: "同名输入线 · a" })).not.toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("fast");
});
