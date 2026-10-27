// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ClearData } from "./DataCleanup";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
const selection = {
  runs: ["scan"],
  procedures: [],
  analyses: [],
  setups: [],
  setup_definitions: [],
  parameters: [],
  captures: [],
};
function show(
  props: { runs?: string[]; captures?: string[]; onCleared?: () => void } = { runs: ["scan"] },
) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ClearData {...props} />
    </QueryClientProvider>,
  );
}
function response(value: unknown) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
}

it("requires reviewing an explicit expanded selection before clearing retained data", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      response({
        selection,
        fingerprint: "first",
        record_count: 1,
        bytes_to_reclaim: 1024,
        blockers: [
          { owner: "analysis:derived", reason: "Retained scientific content refers to: scan" },
        ],
      }),
    )
    .mockResolvedValueOnce(
      response({
        selection: { ...selection, analyses: ["derived"] },
        fingerprint: "second",
        record_count: 2,
        bytes_to_reclaim: 2048,
        blockers: [],
      }),
    )
    .mockResolvedValueOnce(response({ id: "cleanup", state: "complete", selection }));
  vi.stubGlobal("fetch", fetch);
  show();
  fireEvent.click(screen.getByRole("button", { name: "预览数据清理…" }));
  expect(await screen.findByText("analysis:derived")).toBeVisible();
  expect(screen.getByText(/保留的科学记录仍引用：scan/)).toBeVisible();
  expect(screen.getByRole("button", { name: "删除选定记录与文件" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "纳入此依赖记录并重新预览" }));
  expect(await screen.findByText("分析：derived")).toBeVisible();
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "删除选定记录与文件" }));
  expect(await screen.findByText("记录与文件已清理。")).toBeVisible();
  const request = fetch.mock.calls[2]?.[0] as Request;
  expect(await request.json()).toMatchObject({
    preview: { fingerprint: "second", selection: { analyses: ["derived"] } },
  });
});

it("reports unfinished physical cleanup and resumes the same operation", async () => {
  const captureSelection = { ...selection, runs: [], captures: ["imported"] };
  const cleared = vi.fn();
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      response({
        selection: captureSelection,
        fingerprint: "one",
        record_count: 1,
        bytes_to_reclaim: 1024,
        blockers: [],
      }),
    )
    .mockResolvedValueOnce(
      response({
        id: "retry-me",
        state: "records_removed",
        selection: captureSelection,
        error: "File in use",
      }),
    )
    .mockResolvedValueOnce(
      response({ id: "retry-me", state: "complete", selection: captureSelection }),
    );
  vi.stubGlobal("fetch", fetch);
  show({ captures: ["imported"], onCleared: cleared });
  fireEvent.click(screen.getByRole("button", { name: "预览数据清理…" }));
  fireEvent.click(await screen.findByRole("button", { name: "删除选定记录与文件" }));
  expect(
    await screen.findByText("数据库记录已删除；物理文件清理尚未完成，需要重试。"),
  ).toBeVisible();
  expect(cleared).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "重试清理" }));
  expect(await screen.findByText("记录与文件已清理。")).toBeVisible();
  expect(cleared).toHaveBeenCalledOnce();
  const request = fetch.mock.calls[2]?.[0] as Request;
  expect(request.url).toContain("/data-cleanup/retry-me/resume");
});

it("keeps active-job cleanup blocked and cancellation sends no deletion", async () => {
  const fetch = vi.fn().mockResolvedValue(
    response({
      selection,
      fingerprint: "active",
      record_count: 1,
      bytes_to_reclaim: 0,
      blockers: [
        {
          owner: "run:scan",
          reason:
            "Finish or cancel this task through its normal task controls before clearing data",
        },
      ],
    }),
  );
  vi.stubGlobal("fetch", fetch);
  show();
  fireEvent.click(screen.getByRole("button", { name: "预览数据清理…" }));
  expect(await screen.findByText(/清理数据前，请通过任务的正常入口完成或取消任务/)).toBeVisible();
  expect(screen.getByRole("button", { name: "删除选定记录与文件" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect((fetch.mock.calls[0]![0] as Request).url).toContain("/data-cleanup/preview");
});
