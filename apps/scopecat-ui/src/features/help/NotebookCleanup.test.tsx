// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NotebookCleanup } from "./NotebookCleanup";

const journey = {
  directory: "/authors/course",
  notebook: "lesson.ipynb",
  python: "python",
  ready: true,
  scope_id: "course",
};
const selection = {
  runs: ["scan"],
  procedures: ["task"],
  analyses: [],
  parameters: [],
  setups: [],
  setup_definitions: [],
  branches: [],
  plans: [],
  schedules: [],
  calibration_tasks: [],
  calibration_profiles: [],
  captures: [],
};
const preview = {
  selection,
  fingerprint: "exact",
  blockers: [],
  record_count: 2,
  bytes_to_reclaim: 0,
};
afterEach(() => {
  cleanup();
  delete window.pywebview;
});
function show(blockers: { owner: string; reason: string }[] = []) {
  const inspect = vi
    .fn()
    .mockResolvedValue({ preview: { ...preview, blockers }, files_error: null });
  const clear = vi.fn().mockResolvedValue({ ...journey, ready: false, cleared: true });
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: { api: { preview_notebook_cleanup: inspect, clear_notebook_course: clear } },
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NotebookCleanup
        topic="parameters"
        journey={journey}
        disabled={false}
        onBusyChange={vi.fn()}
      />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByText("清理此课程"));
  return { inspect, clear };
}
it("requires a preview and preserves code by default", async () => {
  const { clear } = show();
  expect(screen.queryByRole("button", { name: "确认课程清理" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "预览课程清理" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认课程清理" }));
  await waitFor(() =>
    expect(clear).toHaveBeenCalledWith(
      "parameters",
      JSON.stringify({ preview, files: "preserve" }),
    ),
  );
});
it("blocks an unfinished task and never requests implicit cancellation", async () => {
  const { clear } = show([{ owner: "procedure:task", reason: "Finish or cancel this task" }]);
  fireEvent.click(screen.getByRole("button", { name: "预览课程清理" }));
  expect(await screen.findByRole("button", { name: "确认课程清理" })).toBeDisabled();
  expect(screen.getByRole("alert")).toHaveTextContent("Finish or cancel");
  expect(clear).not.toHaveBeenCalled();
});
it("sends discard only after explicitly selecting deletion of the named folder", async () => {
  const { clear } = show();
  fireEvent.click(screen.getByRole("button", { name: "预览课程清理" }));
  fireEvent.click(await screen.findByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "确认课程清理" }));
  await waitFor(() =>
    expect(clear).toHaveBeenCalledWith("parameters", JSON.stringify({ preview, files: "discard" })),
  );
});
