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
function Harness({ layoutManaged = false } = {}) {
  const [collapsed, setCollapsed] = useState(false);
  const [current, setCurrent] = useState(results[1]);
  const [recent, setRecent] = useState(results);
  const [pending, setPending] = useState<PendingMeasurement[]>(
    results.map((item) => ({
      selection: item.selection,
      acquisition: item.target.acquisition,
    })),
  );
  return (
    <MeasurementWorkbench
      layoutManaged={layoutManaged}
      collapsed={collapsed}
      onCollapsedChange={setCollapsed}
      description={description}
      connected
      disabled={false}
      collectResults={{}}
      recent={recent}
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
        setRecent((items) =>
          items.map((item) =>
            item.selection.command_id === id ? { ...item, savedRecordId: recordId } : item,
          ),
        );
        setPending((items) => items.filter((item) => item.selection.command_id !== id));
        setCurrent((item) =>
          item?.selection.command_id === id ? { ...item, savedRecordId: recordId } : item,
        );
      }}
    />
  );
}
function show(layoutManaged = false) {
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Harness layoutManaged={layoutManaged} />
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

it.each([true, false])(
  "does not infer a saved receipt from hidden save controls (pending=%s)",
  (isPending) => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MeasurementWorkbench
          collapsed={false}
          onCollapsedChange={vi.fn()}
          description={description}
          connected
          disabled={false}
          collectResults={{}}
          recent={results}
          current={results[0]}
          staged={false}
          saveDisabled
          pending={
            isPending
              ? results.map((item) => ({
                  selection: item.selection,
                  acquisition: item.target.acquisition,
                }))
              : []
          }
          onCollect={vi.fn()}
          onSelect={vi.fn()}
          onSaveAttempt={vi.fn()}
          onSaved={vi.fn()}
        />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("recent-result-one")).toHaveTextContent(
      isPending ? "待保存" : "未保存",
    );
    expect(screen.getByTestId("recent-result-one")).not.toHaveTextContent("已保存");
    const button = screen.queryByRole("button", { name: "保存所选结果" });
    expect(button === null || button.hasAttribute("disabled")).toBe(true);
    expect(saveDirectMeasurement).not.toHaveBeenCalled();
  },
);

it("warns before saving an unverified original without blocking its preservation", async () => {
  const item = {
    ...results[1]!,
    receipt_validation: "unverified" as const,
    validation_reason: "budget exceeded",
  };
  vi.mocked(saveDirectMeasurement).mockResolvedValue({
    id: "diagnostic-original",
  } as SavedMeasurement);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MeasurementWorkbench
        collapsed={false}
        onCollapsedChange={vi.fn()}
        description={description}
        connected
        disabled={false}
        collectResults={{}}
        recent={[item]}
        current={item}
        staged={false}
        pending={[
          {
            selection: item.selection,
            acquisition: item.target.acquisition,
            receipt_validation: item.receipt_validation,
          },
        ]}
        onCollect={vi.fn()}
        onSelect={vi.fn()}
        onSaveAttempt={vi.fn()}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "保存所选结果" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("未验证原件（验证未完成）")).toBeVisible();
  expect(within(dialog).getByText(/保存不表示它是合格科学数据/)).toBeVisible();
  fireEvent.click(within(dialog).getByRole("button", { name: /^保存$/ }));
  await waitFor(() => expect(saveDirectMeasurement).toHaveBeenCalledWith(item.selection));
});

it("keeps save and compact selection above content with the full recent list below", () => {
  show();
  const actions = screen.getByLabelText("所选结果操作");
  const content = screen.getByLabelText("当前测量结果");
  expect(actions.compareDocumentPosition(content) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(actions).getByRole("button", { name: "保存所选结果" })).toBeVisible();
  const recent = screen.getByRole("complementary", { name: "近期结果" });
  expect(content.compareDocumentPosition(recent) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(within(actions).getByRole("combobox", { name: "选择近期结果" })).toBeVisible();
  expect(screen.getByTestId("recent-result-one")).toBeVisible();
});

it("shares one selected result between the compact selector and full list without losing save drafts", async () => {
  show();
  const select = screen.getByRole("combobox", { name: "选择近期结果" });
  const options = within(select).getAllByRole("option") as HTMLOptionElement[];
  const first = options[1]!;
  expect(select).toHaveValue(options[0]!.value);
  fireEvent.click(screen.getByRole("button", { name: "保存所选结果" }));
  let dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("标题"), { target: { value: "Retained draft" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
  fireEvent.change(select, { target: { value: first.value } });
  expect(screen.getByTestId("recent-result-one")).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByLabelText("当前测量结果")).toHaveTextContent("Curve one");
  fireEvent.click(screen.getByTestId("recent-result-two"));
  expect(select).toHaveValue(options[0]!.value);
  fireEvent.click(screen.getByRole("button", { name: "保存所选结果" }));
  dialog = screen.getByRole("dialog");
  expect(within(dialog).getByLabelText("标题")).toHaveValue("Retained draft");
});

it("does not present another original when the current result is outside the recent list", () => {
  const onSelect = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MeasurementWorkbench
        collapsed={false}
        onCollapsedChange={vi.fn()}
        description={description}
        connected
        disabled={false}
        collectResults={{}}
        recent={[results[0]!]}
        current={results[1]}
        staged={false}
        pending={[]}
        onCollect={vi.fn()}
        onSelect={onSelect}
        onSaveAttempt={vi.fn()}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const select = screen.getByRole("combobox", { name: "选择近期结果" });
  const displayed = within(select).getByRole("option", {
    name: /当前显示（近期列表外）/,
  }) as HTMLOptionElement;
  expect(displayed).toBeDisabled();
  expect(displayed.selected).toBe(true);
  expect(select).toHaveValue(displayed.value);
  expect(screen.getByLabelText("当前测量结果")).toHaveTextContent("Curve two");
  expect(onSelect).not.toHaveBeenCalled();
  const recent = within(select).getByRole("option", { name: /^1\./ }) as HTMLOptionElement;
  fireEvent.change(select, { target: { value: recent.value } });
  expect(onSelect).toHaveBeenCalledExactlyOnceWith(results[0]);
});

it("keeps the managed action strip sticky within the entire result and recent-list block", () => {
  show(true);
  const actions = screen.getByRole("group", { name: "所选结果操作" });
  const content = screen.getByLabelText("当前测量结果");
  const recent = screen.getByRole("complementary", { name: "近期结果" });
  expect(actions).toHaveClass("sticky", "top-0", "z-10", "bg-panel-soft", "flex-wrap");
  expect(actions.parentElement).toBe(recent.parentElement);
  expect(actions.parentElement).toContainElement(content);
  expect(actions.parentElement!.firstElementChild).toBe(actions);
  expect(within(actions).getByRole("button", { name: "保存所选结果" })).toBeEnabled();
  expect(within(actions).getByRole("combobox", { name: "选择近期结果" })).toBeVisible();
});

it("keeps keyboard scroll targets below a wrapping sticky strip and restores the pane inset on unmount", () => {
  let height = 48;
  let resize!: () => void;
  const disconnect = vi.fn();
  const geometry = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    get height() {
      return height;
    },
    width: 260,
    x: 0,
    y: 0,
    left: 0,
    right: 260,
    top: 0,
    bottom: 48,
    toJSON: () => ({}),
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        resize = callback;
      }
      observe() {}
      disconnect = disconnect;
    },
  );
  try {
    const view = render(
      <div
        data-testid="result-scroll"
        className="device-pane-scroll"
        style={{ scrollPaddingTop: "12px" }}
      >
        <QueryClientProvider client={new QueryClient()}>
          <Harness layoutManaged />
        </QueryClientProvider>
      </div>,
    );
    const pane = screen.getByTestId("result-scroll");
    expect(pane.style.scrollPaddingTop).toBe("56px");
    height = 92;
    resize();
    expect(pane.style.scrollPaddingTop).toBe("100px");
    height = 0;
    resize();
    expect(pane.style.scrollPaddingTop).toBe("100px");
    view.unmount();
    expect(disconnect).toHaveBeenCalledOnce();
    expect(pane.style.scrollPaddingTop).toBe("12px");
  } finally {
    geometry.mockRestore();
    vi.unstubAllGlobals();
  }
});
