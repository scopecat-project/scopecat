// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ProcedureWorkerLog } from "./ProcedureWorkerLog";

function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ProcedureWorkerLog procedureId="p1" />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("reads on demand, marks truncation, and renders output as plain text", async () => {
  const fetcher = vi.fn(async (request: Request) => {
    const url = new URL(request.url);
    expect(url.pathname).toBe("/api/v1/procedures/p1/worker-log");
    expect(url.searchParams.get("max_bytes")).toBe("16384");
    return Response.json({
      available: true,
      text: "<script>failure()</script>\nTraceback",
      total_bytes: 20000,
      truncated: true,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  mount();
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("最近的 Worker 输出"));
  await screen.findByText(/仅显示最后 16 KiB/);
  expect(screen.getByText(/<script>failure/).tagName).toBe("PRE");
  expect(document.querySelector("script")).toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("distinguishes a missing log from empty output and hides stale text on failure", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ available: false, text: "", total_bytes: 0, truncated: false }),
    )
    .mockResolvedValueOnce(
      Response.json({ available: true, text: "", total_bytes: 0, truncated: false }),
    )
    .mockResolvedValueOnce(
      Response.json({ available: true, text: "old output", total_bytes: 10, truncated: false }),
    )
    .mockResolvedValueOnce(Response.json({ detail: "Log read failed" }, { status: 500 }));
  vi.stubGlobal("fetch", fetcher);
  mount();
  fireEvent.click(screen.getByText("最近的 Worker 输出"));
  await screen.findByText("本次执行尚未创建 Worker 日志。");
  fireEvent.click(screen.getByRole("button", { name: "刷新 Worker 输出" }));
  await screen.findByText("Worker 日志为空。");
  fireEvent.click(screen.getByRole("button", { name: "刷新 Worker 输出" }));
  await screen.findByText("old output");
  fireEvent.click(screen.getByRole("button", { name: "刷新 Worker 输出" }));
  await screen.findByText("Log read failed");
  expect(screen.queryByText("old output")).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(4);
});
