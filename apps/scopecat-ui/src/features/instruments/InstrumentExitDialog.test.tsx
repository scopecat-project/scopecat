// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { InstrumentExitDialog } from "./InstrumentExitDialog";
import type { PendingMeasurement } from "./DirectMeasurements";
import { saveDirectMeasurement, type SavedMeasurement } from "./direct-measurement-api";
import { finishInstrumentSession, getInstrumentFinishPlan } from "./instrument-finish-api";
vi.mock("./direct-measurement-api", () => ({ saveDirectMeasurement: vi.fn() }));
vi.mock("./instrument-finish-api", () => ({
  finishInstrumentSession: vi.fn(),
  getInstrumentFinishPlan: vi.fn(),
}));
const pending: PendingMeasurement[] = ["one", "two"].map((id) => ({
  selection: {
    title: "",
    session_id: "session",
    instrument_id: "scope",
    command_id: id,
    line_id: id,
    series_id: "series",
    line_label: id,
    note: "fixed attribution",
  },
  acquisition: { id: "sweep", results: [] },
  receipt: { status: "collected", problems: [] },
}));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getInstrumentFinishPlan).mockResolvedValue({
    actions: [],
    configured_finish_available: true,
  });
  vi.mocked(finishInstrumentSession).mockResolvedValue({ session_id: "session", status: "closed" });
});
afterEach(cleanup);
function show() {
  const onFinished = vi.fn();
  const onCancel = vi.fn();
  function Harness() {
    const [items, setItems] = useState(pending);
    return (
      <InstrumentExitDialog
        sessionId="session"
        pending={items}
        stagedCount={1}
        onSaved={(id) =>
          setItems((current) => current.filter((item) => item.selection.command_id !== id))
        }
        onCancel={onCancel}
        onFinished={onFinished}
      />
    );
  }
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Harness />
    </QueryClientProvider>,
  );
  return { onFinished, onCancel };
}
it("saves independently, retains failed items and retries the same attribution before finish", async () => {
  vi.mocked(saveDirectMeasurement).mockImplementation(
    async (selection) => ({ selection }) as SavedMeasurement,
  );
  vi.mocked(saveDirectMeasurement).mockResolvedValueOnce({
    selection: pending[0]!.selection,
  } as SavedMeasurement);
  vi.mocked(saveDirectMeasurement).mockRejectedValueOnce(new Error("disk full"));
  const { onFinished } = show();
  expect(screen.getByRole("button", { name: "保持当前状态并结束" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存全部未保存结果" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("disk full");
  expect(screen.getByRole("heading", { name: "未保存结果：1" })).toBeVisible();
  expect(screen.getByRole("button", { name: "保持当前状态并结束" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保存全部未保存结果" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "保持当前状态并结束" })).toBeEnabled(),
  );
  expect(vi.mocked(saveDirectMeasurement).mock.calls.map(([selection]) => selection)).toEqual([
    pending[0]!.selection,
    pending[1]!.selection,
    pending[1]!.selection,
  ]);
  expect(finishInstrumentSession).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
});
it("explicit discard allows either existing output choice but cancellation does not discard", () => {
  const { onFinished, onCancel } = show();
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  expect(screen.getByRole("button", { name: "保持当前状态并结束" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "继续控制" }));
  expect(onCancel).toHaveBeenCalledOnce();
  expect(onFinished).not.toHaveBeenCalled();
  expect(saveDirectMeasurement).not.toHaveBeenCalled();
  expect(finishInstrumentSession).not.toHaveBeenCalled();
});
it("locks cancellation and the alternate output choice after an uncertain finish", async () => {
  vi.mocked(finishInstrumentSession).mockRejectedValue(new Error("unknown response"));
  const { onCancel } = show();
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("unknown response");
  expect(screen.getByRole("button", { name: "继续控制" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onCancel).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  await waitFor(() => expect(finishInstrumentSession).toHaveBeenCalledTimes(2));
  expect(vi.mocked(finishInstrumentSession).mock.calls[0]).toEqual(
    vi.mocked(finishInstrumentSession).mock.calls[1],
  );
});
