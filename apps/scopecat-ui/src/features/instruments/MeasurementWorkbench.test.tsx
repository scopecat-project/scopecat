// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { MeasurementWorkbench, acquisitionTargets } from "./MeasurementWorkbench";
import { saveDirectMeasurement, type SavedMeasurement } from "./direct-measurement-api";
import type { PendingMeasurement } from "./DirectMeasurements";
import type { CurrentMeasurementResult } from "./CurrentMeasurement";
import type { InstrumentDescription } from "../../api-contract";
vi.mock("./direct-measurement-api", () => ({ saveDirectMeasurement: vi.fn() }));
vi.mock("./CurrentMeasurement", () => ({
  CurrentMeasurement: ({ result }: { result: CurrentMeasurementResult }) => (
    <div aria-label="当前测量结果">Curve {result.selection.command_id}</div>
  ),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const description: InstrumentDescription = {
  instrument_id: "vna",
  implementation_id: "virtual",
  implementation_version: "1",
  interfaces: [{ id: "sweep", acquisitions: [{ id: "sweep", label: "扫描", results: [] }] }],
};
const results: CurrentMeasurementResult[] = ["one", "two"].map((id) => ({
  target: acquisitionTargets(description)[0]!,
  receipt: { status: "collected", problems: [] },
  selection: {
    session_id: "session",
    instrument_id: "vna",
    command_id: id,
    title: "",
    line_id: "",
    line_label: "",
    series_id: "batch",
    note: "",
  },
  receivedAt: "2026-01-01T12:00:00Z",
}));
function Harness() {
  const [collapsed, setCollapsed] = useState(false);
  const [current, setCurrent] = useState(results[1]);
  const [pending, setPending] = useState<PendingMeasurement[]>(
    results.map((item) => ({
      selection: item.selection,
      acquisition: item.target.acquisition,
      receipt: item.receipt,
    })),
  );
  return (
    <MeasurementWorkbench
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
      description={description}
      connected
      disabled={false}
      collectResults={{}}
      recent={results}
      current={current}
      staged={false}
      sessionId="session"
      pending={pending}
      onCollect={vi.fn()}
      onSelect={setCurrent}
      onSaveAttempt={(selection) =>
        setPending((items) =>
          items.map((item) =>
            item.selection.command_id === selection.command_id
              ? { ...item, selection, saveAttempted: true }
              : item,
          ),
        )
      }
      onSaved={(id, recordId) => {
        setPending((items) => items.filter((item) => item.selection.command_id !== id));
        setCurrent((item) =>
          item?.selection.command_id === id ? { ...item, savedRecordId: recordId } : item,
        );
      }}
    />
  );
}
function show() {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Harness />
    </QueryClientProvider>,
  );
}
it("selects earlier receipt, saves optional metadata, and keeps the result visible", async () => {
  vi.mocked(saveDirectMeasurement).mockResolvedValue({ id: "saved-one" } as SavedMeasurement);
  show();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByTestId("recent-result-one"));
  expect(screen.getByText("Curve one")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "收起采集面板" }));
  expect(screen.getByText("Curve one")).not.toBeVisible();
  expect(screen.getByText("2 条待保存")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "展开采集面板" }));
  expect(screen.getByTestId("recent-result-one")).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "保存所选结果" }));
  expect(screen.getByRole("dialog")).toHaveClass("p-4");
  fireEvent.change(screen.getByRole("textbox", { name: "标题" }), { target: { value: "Cable A" } });
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /^保存$/ }));
  await screen.findByRole("link", { name: "查看已保存结果" });
  expect(vi.mocked(saveDirectMeasurement).mock.calls[0]?.[0]).toMatchObject({
    command_id: "one",
    title: "Cable A",
  });
  expect(screen.getByText("Curve one")).toBeVisible();
  expect(screen.getByTestId("recent-result-one")).toHaveTextContent("已保存");
});
it("freezes retry payload even after cancelling and selecting a different result", async () => {
  vi.mocked(saveDirectMeasurement)
    .mockRejectedValueOnce(new Error("response lost"))
    .mockResolvedValue({ id: "saved-one" } as SavedMeasurement);
  show();
  fireEvent.click(screen.getByTestId("recent-result-one"));
  fireEvent.click(screen.getByRole("button", { name: "保存所选结果" }));
  fireEvent.change(screen.getByRole("textbox", { name: "备注" }), {
    target: { value: "first note" },
  });
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /^保存$/ }));
  await screen.findByRole("alert");
  expect(screen.getByRole("textbox", { name: "备注" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  fireEvent.click(screen.getByTestId("recent-result-two"));
  fireEvent.click(screen.getByTestId("recent-result-one"));
  fireEvent.click(screen.getByRole("button", { name: "保存所选结果" }));
  expect(screen.getByRole("textbox", { name: "备注" })).toHaveValue("first note");
  fireEvent.click(screen.getByRole("button", { name: "重试保存" }));
  await screen.findByRole("link", { name: "查看已保存结果" });
  expect(vi.mocked(saveDirectMeasurement).mock.calls[1]?.[0]).toEqual(
    vi.mocked(saveDirectMeasurement).mock.calls[0]?.[0],
  );
});
it("does not create a collection panel for a pure output device", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MeasurementWorkbench
        collapsed={false}
        onCollapsedChange={vi.fn()}
        description={{ ...description, interfaces: [] }}
        connected
        disabled={false}
        collectResults={{}}
        recent={[]}
        staged={false}
        pending={[]}
        onCollect={vi.fn()}
        onSelect={vi.fn()}
        onSaveAttempt={vi.fn()}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
  expect(screen.queryByRole("region", { name: "交互采集" })).not.toBeInTheDocument();
});

it("rejects overlong metadata before freezing or sending the request", async () => {
  show();
  fireEvent.click(screen.getByRole("button", { name: "保存所选结果" }));
  const title = screen.getByRole("textbox", { name: "标题" });
  fireEvent.change(title, { target: { value: "x".repeat(501) } });
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: /^保存$/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("最多 500 字");
  expect(saveDirectMeasurement).not.toHaveBeenCalled();
  expect(title).toBeEnabled();
});
