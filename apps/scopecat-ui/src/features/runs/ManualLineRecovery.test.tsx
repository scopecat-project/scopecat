// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ManualLineRecovery } from "./ManualLineRecovery";
import { reconfirmManualLine } from "./manual-line-recovery";
import { readOperatorSettings } from "../application/OperatorSettings";

vi.mock("./manual-line-recovery", () => ({ reconfirmManualLine: vi.fn() }));
vi.mock("../application/OperatorSettings", () => ({ readOperatorSettings: vi.fn() }));
const original = {
  procedure_run_id: "procedure-1",
  step_key: "measure/wiring",
  request_hash: `sha256:${"a".repeat(64)}`,
  line_id: "line-1",
  actor: "original-operator",
  runtime_id: "old-runtime",
};
function show(pendingOnly = false) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ManualLineRecovery
        runId="run-1"
        fingerprint={"b".repeat(64)}
        original={original}
        configHash="frozen-config"
        pendingOnly={pendingOnly}
      />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  localStorage.clear();
  vi.resetAllMocks();
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Alice" });
});
afterEach(cleanup);

it("requires explicit wiring confirmation and preserves the original run reference", async () => {
  vi.mocked(reconfirmManualLine).mockResolvedValue("recorded");
  show();
  await screen.findByDisplayValue("Alice");
  expect(screen.getByRole("button", { name: "确认接线并继续" }).hasAttribute("disabled")).toBe(
    true,
  );
  expect(reconfirmManualLine).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认接线并继续" }));
  await waitFor(() => expect(reconfirmManualLine).toHaveBeenCalledTimes(1));
  expect(vi.mocked(reconfirmManualLine).mock.calls[0]).toEqual([
    "run-1",
    "b".repeat(64),
    { original, actor: "Alice", request_key: expect.any(String) },
  ]);
});

it("retains uncertain actor and command across reopening, including an already queued child", async () => {
  vi.mocked(reconfirmManualLine).mockRejectedValueOnce(new Error("Response lost"));
  const first = show();
  await screen.findByDisplayValue("Alice");
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认接线并继续" }));
  await screen.findByText("Response lost");
  const command = vi.mocked(reconfirmManualLine).mock.calls[0];
  first.unmount();
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 2, operator: "Bob" });
  vi.mocked(reconfirmManualLine).mockResolvedValueOnce("recorded");
  show(true);
  expect(screen.getByLabelText<HTMLInputElement>("线路确认操作者").value).toBe("Alice");
  expect(screen.getByLabelText("线路确认操作者").hasAttribute("disabled")).toBe(true);
  expect(reconfirmManualLine).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "重试已保存的确认" }));
  await waitFor(() => expect(reconfirmManualLine).toHaveBeenCalledTimes(2));
  expect(vi.mocked(reconfirmManualLine).mock.calls[1]).toEqual(command);
});

it("does not automatically renew an expired recorded confirmation", async () => {
  vi.mocked(reconfirmManualLine).mockResolvedValueOnce("expired");
  show();
  await screen.findByDisplayValue("Alice");
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认接线并继续" }));
  await screen.findByText(/先前确认仍被保留，但已在重启后失效/);
  expect(screen.getByRole<HTMLInputElement>("checkbox").checked).toBe(false);
  expect(screen.getByRole("button", { name: "确认接线并继续" }).hasAttribute("disabled")).toBe(
    true,
  );
  expect(reconfirmManualLine).toHaveBeenCalledTimes(1);
});
