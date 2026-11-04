// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
  expect(screen.getByRole("button", { name: "继续控制" })).toBeDisabled();
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

afterEach(cleanup);
