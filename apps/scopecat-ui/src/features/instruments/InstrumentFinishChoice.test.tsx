// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { ApiError } from "../../api-client";
import { useInstrumentFinishRecovery } from "./instrument-finish-recovery";
import { INSTRUMENT_REQUEST_WAIT_MS } from "./instrument-request-wait";
import { InstrumentFinishChoice } from "./InstrumentFinishChoice";
import { finishInstrumentSession, getInstrumentFinishPlan } from "./instrument-finish-api";

vi.mock("./instrument-finish-api", () => ({
  finishInstrumentSession: vi.fn(),
  getInstrumentFinishPlan: vi.fn(),
}));
vi.mock("./instrument-api", () => ({ createInstrumentCommandId: () => "finish-fixed" }));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getInstrumentFinishPlan).mockResolvedValue({
    actions: [],
    configured_finish_available: true,
  });
});
function show() {
  const onFinished = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <InstrumentFinishChoice sessionId="session" onFinished={onFinished} onCancel={vi.fn()} />
    </QueryClientProvider>,
  );
  return onFinished;
}
it("keeps a failed finish selection and retries the identical command", async () => {
  vi.mocked(finishInstrumentSession).mockRejectedValueOnce(new Error("unknown outcome"));
  vi.mocked(finishInstrumentSession).mockResolvedValueOnce({
    session_id: "session",
    status: "closed",
  });
  const onFinished = show();
  const configured = screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" });
  await waitFor(() => expect(configured).toBeEnabled());
  fireEvent.click(configured);
  expect(await screen.findByRole("alert")).toHaveTextContent("未知");
  expect(onFinished).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "保持当前状态并结束" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "返回工作台（保留待核查状态）" })).toBeEnabled();
  fireEvent.click(configured);
  await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
  expect(vi.mocked(finishInstrumentSession).mock.calls).toEqual([
    ["session", { operation_id: "finish-fixed", mode: "configured_finish" }],
    ["session", { operation_id: "finish-fixed", mode: "configured_finish" }],
  ]);
});
it("does not guess a finish action when the device has no configured scope", async () => {
  vi.mocked(getInstrumentFinishPlan).mockResolvedValue({
    actions: [],
    configured_finish_available: false,
  });
  show();
  expect(screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  await waitFor(() =>
    expect(finishInstrumentSession).toHaveBeenCalledWith("session", {
      operation_id: "finish-fixed",
      mode: "keep_current",
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

it("unlocks a rejected selection only with the matching structured pre-admission receipt", async () => {
  vi.mocked(finishInstrumentSession).mockRejectedValueOnce(
    new ApiError("no configured finish", 409, {
      kind: "instrument_finish_rejected",
      message: "no configured finish",
      session_id: "session",
      operation_id: "finish-fixed",
    }),
  );
  vi.mocked(finishInstrumentSession).mockResolvedValueOnce({
    session_id: "session",
    status: "closed",
  });
  const onFinished = show();
  const configured = screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" });
  await waitFor(() => expect(configured).toBeEnabled());
  fireEvent.click(configured);
  expect(await screen.findByRole("alert")).toHaveTextContent("未被接纳");
  expect(screen.getByRole("button", { name: "继续控制" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  await waitFor(() => expect(onFinished).toHaveBeenCalledOnce());
  expect(vi.mocked(finishInstrumentSession).mock.calls[1]?.[1].mode).toBe("keep_current");
});

it.each([
  new ApiError("no configured finish action for every selected device", 409),
  new ApiError("rejected other operation", 409, {
    kind: "instrument_finish_rejected",
    message: "no configured finish",
    session_id: "session",
    operation_id: "another-operation",
  }),
  new ApiError("rejected other session", 409, {
    kind: "instrument_finish_rejected",
    message: "no configured finish",
    session_id: "another-session",
    operation_id: "finish-fixed",
  }),
])("does not treat generic or mismatched rejections as unadmitted: %s", async (error) => {
  vi.mocked(finishInstrumentSession).mockRejectedValue(error);
  show();
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("未知");
  expect(screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" })).toBeDisabled();
});

it("bounds a hung finish and retains its identity across dismissal; late success does not exit", async () => {
  let resolve!: (value: { session_id: string; status: "closed" }) => void;
  vi.mocked(finishInstrumentSession).mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const onFinished = vi.fn();
  function Harness() {
    const recovery = useInstrumentFinishRecovery("session");
    const [visible, setVisible] = useState(true);
    return visible ? (
      <InstrumentFinishChoice
        sessionId="session"
        finishRecovery={recovery}
        onFinished={onFinished}
        onCancel={() => setVisible(false)}
      />
    ) : (
      <button onClick={() => setVisible(true)}>重新打开</button>
    );
  }
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Harness />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" })).toBeEnabled(),
  );
  vi.useFakeTimers();
  fireEvent.click(screen.getByRole("button", { name: "执行上述关闭 / 归零配置并结束" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(INSTRUMENT_REQUEST_WAIT_MS);
  });
  expect(screen.getByRole("alert")).toHaveTextContent("超过 15 秒");
  fireEvent.click(screen.getByRole("button", { name: "返回工作台（保留待核查状态）" }));
  fireEvent.click(screen.getByRole("button", { name: "重新打开" }));
  expect(screen.getByRole("button", { name: "保持当前状态并结束" })).toBeDisabled();
  await act(async () => {
    resolve({ session_id: "session", status: "closed" });
  });
  expect(onFinished).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "确认结束控制" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "确认结束控制" }));
  expect(onFinished).toHaveBeenCalledOnce();
  expect(finishInstrumentSession).toHaveBeenCalledOnce();
});

it("uses the same command after timeout and ignores a stale failure after confirmed success", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(finishInstrumentSession).mockReturnValueOnce(
    new Promise((_, failed) => {
      reject = failed;
    }),
  );
  vi.mocked(finishInstrumentSession).mockResolvedValueOnce({
    session_id: "session",
    status: "closed",
  });
  vi.useFakeTimers();
  const onFinished = show();
  const original = screen.getByRole("button", { name: "保持当前状态并结束" });
  fireEvent.click(original);
  fireEvent.click(original);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(INSTRUMENT_REQUEST_WAIT_MS);
  });
  expect(finishInstrumentSession).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "核查原收尾结果（保持当前状态）" }));
  await act(async () => {});
  expect(onFinished).toHaveBeenCalledOnce();
  expect(vi.mocked(finishInstrumentSession).mock.calls[0]).toEqual(
    vi.mocked(finishInstrumentSession).mock.calls[1],
  );
  await act(async () => {
    reject(new Error("late transport failure"));
  });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(onFinished).toHaveBeenCalledOnce();
});

it("accepts a late explicit rejection as evidence but never exits", async () => {
  let reject!: (reason: ApiError) => void;
  vi.mocked(finishInstrumentSession).mockReturnValueOnce(
    new Promise((_, failed) => {
      reject = failed;
    }),
  );
  vi.useFakeTimers();
  const onFinished = show();
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(INSTRUMENT_REQUEST_WAIT_MS);
  });
  await act(async () => {
    reject(
      new ApiError("not admitted", 409, {
        kind: "instrument_finish_rejected",
        session_id: "session",
        operation_id: "finish-fixed",
        message: "not admitted",
      }),
    );
  });
  expect(screen.getByRole("alert")).toHaveTextContent("未被接纳");
  expect(screen.getByRole("button", { name: "继续控制" })).toBeEnabled();
  expect(onFinished).not.toHaveBeenCalled();
});

it("does not treat a mismatched successful response as confirmed", async () => {
  vi.mocked(finishInstrumentSession).mockResolvedValue({
    session_id: "another-session",
    status: "closed",
  });
  const onFinished = show();
  fireEvent.click(screen.getByRole("button", { name: "保持当前状态并结束" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("不匹配");
  expect(onFinished).not.toHaveBeenCalled();
});
