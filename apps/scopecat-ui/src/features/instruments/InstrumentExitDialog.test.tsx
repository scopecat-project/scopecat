// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { ApiError } from "../../api-client";
import { INSTRUMENT_REQUEST_WAIT_MS } from "./instrument-request-wait";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useInstrumentFinishRecovery } from "./instrument-finish-recovery";
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
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});
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
it("allows cancelling exit intent but retains the uncertain finish identity", async () => {
  vi.mocked(finishInstrumentSession).mockRejectedValue(new Error("unknown response"));
  const { onCancel } = show();
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("unknown response");
  expect(screen.getByRole("button", { name: "返回工作台（保留待核查状态）" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onCancel).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "核查原收尾结果（保持当前状态）" }));
  await waitFor(() => expect(finishInstrumentSession).toHaveBeenCalledTimes(2));
  expect(vi.mocked(finishInstrumentSession).mock.calls[0]).toEqual(
    vi.mocked(finishInstrumentSession).mock.calls[1],
  );
});

it("bounds a hung save, stops the remaining batch, and preserves late saved evidence", async () => {
  let resolve!: (value: SavedMeasurement) => void;
  vi.mocked(saveDirectMeasurement).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const { onCancel, onFinished } = show();
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "保存全部未保存结果" }));
  expect(screen.getByRole("button", { name: "继续控制" })).toBeEnabled();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(INSTRUMENT_REQUEST_WAIT_MS);
  });
  expect(screen.getByRole("alert")).toHaveTextContent("超过 15 秒");
  expect(saveDirectMeasurement).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "继续控制" }));
  expect(onCancel).toHaveBeenCalledOnce();
  await act(async () => {
    resolve({ id: "saved-one", selection: pending[0]!.selection } as SavedMeasurement);
  });
  expect(screen.getByRole("heading", { name: "未保存结果：1" })).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(onFinished).not.toHaveBeenCalled();
  expect(finishInstrumentSession).not.toHaveBeenCalled();
  expect(saveDirectMeasurement).toHaveBeenCalledOnce();
});

it("releases the parent saving/discard/cancel lock after a proven unadmitted finish", async () => {
  vi.mocked(finishInstrumentSession).mockImplementation(async (_session, command) => {
    throw new ApiError("no configured finish", 409, {
      kind: "instrument_finish_rejected",
      message: "no configured finish",
      session_id: "session",
      operation_id: command.operation_id,
    });
  });
  const { onCancel } = show();
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("未被接纳");
  expect(screen.getByRole("checkbox", { name: /明确放弃这/ })).toBeEnabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  expect(screen.getByRole("button", { name: "保存全部未保存结果" })).toBeEnabled();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(onCancel).toHaveBeenCalledOnce();
});

it("native cancel waiting keeps the dialog and does not let late finish close it", async () => {
  let resolve!: (value: { session_id: string; status: "closed" }) => void;
  vi.mocked(finishInstrumentSession).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const onFinished = vi.fn();
  const onCancel = vi.fn();
  const nativeCancel = vi.fn();
  window.addEventListener("scopecat:cancel-close-wait", nativeCancel);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InstrumentExitDialog
        sessionId="session"
        pending={[]}
        stagedCount={0}
        closeWaiting
        onSaved={vi.fn()}
        onFinished={onFinished}
        onCancel={onCancel}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  await waitFor(() => expect(finishInstrumentSession).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "取消本次关闭等待" }));
  expect(nativeCancel).toHaveBeenCalledOnce();
  expect(onCancel).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeVisible();
  await act(async () => {
    resolve({ session_id: "session", status: "closed" });
  });
  expect(onFinished).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "确认结束控制" })).toBeEnabled();
  window.removeEventListener("scopecat:cancel-close-wait", nativeCancel);
});

it("cancels a pending save wait without claiming that the write was cancelled", async () => {
  let resolve!: (value: SavedMeasurement) => void;
  vi.mocked(saveDirectMeasurement).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const { onCancel } = show();
  fireEvent.click(screen.getByRole("button", { name: "保存全部未保存结果" }));
  fireEvent.click(screen.getByRole("button", { name: "继续控制" }));
  expect(onCancel).toHaveBeenCalledOnce();
  expect(screen.getByRole("alert")).toHaveTextContent("原保存请求可能仍在处理");
  await act(async () => {
    resolve({ id: "saved-one", selection: pending[0]!.selection } as SavedMeasurement);
  });
  expect(saveDirectMeasurement).toHaveBeenCalledOnce();
  expect(screen.getByRole("heading", { name: "未保存结果：1" })).toBeVisible();
});

it("only permits unavailable-session UI exit after read-only recovery and pending disposition", async () => {
  const onRecoveryFinished = vi.fn();
  const client = new QueryClient();
  const properties = {
    sessionId: "session",
    pending,
    stagedCount: 1,
    sessionUnavailable: true,
    onSaved: vi.fn(),
    onCancel: vi.fn(),
    onFinished: vi.fn(),
    onRecoveryFinished,
  };
  const { rerender } = render(
    <QueryClientProvider client={client}>
      <InstrumentExitDialog {...properties} />
    </QueryClientProvider>,
  );
  expect(screen.queryByRole("button", { name: "保持当前状态并结束" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认待办并关闭窗口" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  expect(screen.getByRole("button", { name: "确认待办并关闭窗口" })).toBeDisabled();
  rerender(
    <QueryClientProvider client={client}>
      <InstrumentExitDialog {...properties} recoveryCloseAllowed />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "确认待办并关闭窗口" }));
  expect(onRecoveryFinished).toHaveBeenCalledOnce();
  expect(finishInstrumentSession).not.toHaveBeenCalled();
});

it("requires fresh pending disposition after cancelled finish is confirmed while the dialog is closed", async () => {
  let resolve!: (value: { session_id: string; status: "closed" }) => void;
  vi.mocked(finishInstrumentSession).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const onFinished = vi.fn();
  function Harness() {
    const finishRecovery = useInstrumentFinishRecovery("session");
    const [open, setOpen] = useState(true);
    return open ? (
      <InstrumentExitDialog
        sessionId="session"
        pending={pending}
        stagedCount={0}
        finishRecovery={finishRecovery}
        onSaved={vi.fn()}
        onCancel={() => setOpen(false)}
        onFinished={onFinished}
      />
    ) : (
      <button onClick={() => setOpen(true)}>重开退出待办</button>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  await waitFor(() => expect(finishInstrumentSession).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "返回工作台（保留待核查状态）" }));
  await act(async () => {
    resolve({ session_id: "session", status: "closed" });
  });
  expect(onFinished).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "重开退出待办" }));
  expect(screen.getByRole("button", { name: "确认结束控制" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存全部未保存结果" })).toBeEnabled();
  const discard = screen.getByRole("checkbox", { name: /明确放弃这/ });
  expect(discard).not.toBeChecked();
  expect(discard).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "确认结束控制" }));
  expect(onFinished).not.toHaveBeenCalled();
  fireEvent.click(discard);
  fireEvent.click(screen.getByRole("button", { name: "确认结束控制" }));
  expect(onFinished).toHaveBeenCalledOnce();
  expect(finishInstrumentSession).toHaveBeenCalledOnce();
});

it("external native cancellation stops unsubmitted batch items but retains the submitted save", async () => {
  let resolve!: (value: SavedMeasurement) => void;
  vi.mocked(saveDirectMeasurement).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  show();
  fireEvent.click(screen.getByRole("button", { name: "保存全部未保存结果" }));
  act(() => {
    window.dispatchEvent(new Event("scopecat:device-close-cancelled"));
  });
  expect(screen.getByRole("alert")).toHaveTextContent("原保存请求可能仍在处理");
  await act(async () => {
    resolve({ id: "saved-one", selection: pending[0]!.selection } as SavedMeasurement);
  });
  expect(saveDirectMeasurement).toHaveBeenCalledOnce();
  expect(screen.getByRole("heading", { name: "未保存结果：1" })).toBeVisible();
});

it("allows fresh disposition and same-identity verification after an unknown finish is reopened", async () => {
  vi.mocked(finishInstrumentSession).mockRejectedValueOnce(new Error("unknown finish"));
  vi.mocked(finishInstrumentSession).mockResolvedValueOnce({
    session_id: "session",
    status: "closed",
  });
  const onFinished = vi.fn();
  function Harness() {
    const finishRecovery = useInstrumentFinishRecovery("session");
    const [open, setOpen] = useState(true);
    return open ? (
      <InstrumentExitDialog
        sessionId="session"
        pending={pending}
        stagedCount={0}
        finishRecovery={finishRecovery}
        onSaved={vi.fn()}
        onCancel={() => setOpen(false)}
        onFinished={onFinished}
      />
    ) : (
      <button onClick={() => setOpen(true)}>重开退出待办</button>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("unknown finish");
  fireEvent.click(screen.getByRole("button", { name: "返回工作台（保留待核查状态）" }));
  fireEvent.click(screen.getByRole("button", { name: "重开退出待办" }));
  expect(screen.getByRole("button", { name: "核查原收尾结果（保持当前状态）" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存全部未保存结果" })).toBeEnabled();
  const discard = screen.getByRole("checkbox", { name: /明确放弃这/ });
  expect(discard).not.toBeChecked();
  expect(discard).toBeEnabled();
  fireEvent.click(discard);
  fireEvent.click(screen.getByRole("button", { name: "核查原收尾结果（保持当前状态）" }));
  await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
  expect(vi.mocked(finishInstrumentSession).mock.calls[0]).toEqual(
    vi.mocked(finishInstrumentSession).mock.calls[1],
  );
});

it("requires unknown-history acknowledgement for a verified closed session and never sends cleanup", () => {
  const onRecoveryFinished = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <InstrumentExitDialog
        sessionId="session"
        pending={pending}
        stagedCount={0}
        sessionUnavailable
        recoveryCloseAllowed
        recoveryUnresolved
        onRecoveryFinished={onRecoveryFinished}
        onSaved={vi.fn()}
        onFinished={vi.fn()}
        onCancel={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const finish = screen.getByRole("button", { name: "确认待办并关闭窗口" });
  fireEvent.click(screen.getByRole("checkbox", { name: /明确放弃这/ }));
  expect(finish).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "我理解历史设备动作结果仍未知，关闭窗口不会确认或重发动作",
    }),
  );
  fireEvent.click(finish);
  expect(onRecoveryFinished).toHaveBeenCalledOnce();
  expect(finishInstrumentSession).not.toHaveBeenCalled();
  expect(saveDirectMeasurement).not.toHaveBeenCalled();
});

it("can only close UI for a fixed unknown finish after independent closed-session evidence and acknowledgement", async () => {
  vi.mocked(finishInstrumentSession).mockRejectedValueOnce(new Error("unknown response"));
  const onRecoveryFinished = vi.fn();
  const onFinished = vi.fn();
  function Harness() {
    const finishRecovery = useInstrumentFinishRecovery("session");
    const [verifiedClosed, setVerifiedClosed] = useState(false);
    return (
      <>
        <button onClick={() => setVerifiedClosed(true)}>独立核实已结束</button>
        <InstrumentExitDialog
          sessionId="session"
          pending={[]}
          stagedCount={0}
          finishRecovery={finishRecovery}
          sessionUnavailable={verifiedClosed}
          recoveryCloseAllowed={verifiedClosed}
          recoveryUnresolved
          onRecoveryFinished={onRecoveryFinished}
          onSaved={vi.fn()}
          onFinished={onFinished}
          onCancel={vi.fn()}
        />
      </>
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Harness />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("unknown response");
  expect(screen.queryByRole("button", { name: "确认待办并关闭窗口" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "独立核实已结束", hidden: true }));
  expect(screen.getByRole("button", { name: "确认待办并关闭窗口" })).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "我理解历史设备动作结果仍未知，关闭窗口不会确认或重发动作",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "确认待办并关闭窗口" }));
  expect(onRecoveryFinished).toHaveBeenCalledOnce();
  expect(onFinished).not.toHaveBeenCalled();
  expect(finishInstrumentSession).toHaveBeenCalledOnce();
});

it("resets independent discard and unknown-history consent for a new close review without replacing the submitted identity", () => {
  const cancelWaiting = vi.fn();
  const onRecoveryFinished = vi.fn();
  const finishRecovery = {
    state: {
      command: { operation_id: "original-finish", mode: "keep_current" as const },
      phase: "pending" as const,
    },
    start: vi.fn(),
    cancelWaiting,
  };
  const properties = {
    sessionId: "session",
    pending,
    stagedCount: 0,
    sessionUnavailable: true,
    recoveryCloseAllowed: true,
    recoveryUnresolved: true,
    finishRecovery,
    onSaved: vi.fn(),
    onCancel: vi.fn(),
    onFinished: vi.fn(),
    onRecoveryFinished,
  };
  const client = new QueryClient();
  const { rerender } = render(
    <QueryClientProvider client={client}>
      <InstrumentExitDialog {...properties} reviewRequest={1} />
    </QueryClientProvider>,
  );
  const discard = screen.getByRole("checkbox", { name: /明确放弃这/ });
  const acknowledge = screen.getByRole("checkbox", {
    name: "我理解历史设备动作结果仍未知，关闭窗口不会确认或重发动作",
  });
  expect(discard).toBeEnabled();
  fireEvent.click(discard);
  fireEvent.click(acknowledge);
  expect(screen.getByRole("button", { name: "确认待办并关闭窗口" })).toBeEnabled();
  rerender(
    <QueryClientProvider client={client}>
      <InstrumentExitDialog {...properties} reviewRequest={2} />
    </QueryClientProvider>,
  );
  expect(discard).not.toBeChecked();
  expect(acknowledge).not.toBeChecked();
  expect(screen.getByRole("button", { name: "确认待办并关闭窗口" })).toBeDisabled();
  expect(cancelWaiting).toHaveBeenCalledOnce();
  expect(finishRecovery.state.command.operation_id).toBe("original-finish");
  expect(finishRecovery.start).not.toHaveBeenCalled();
  expect(onRecoveryFinished).not.toHaveBeenCalled();
});
