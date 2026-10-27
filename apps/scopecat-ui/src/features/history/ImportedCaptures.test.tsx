// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ImportedCaptures } from "./ImportedCaptures";
import { DesktopFiles } from "../application/DesktopFiles";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("imports through the native picker and preserves data after cancelled or failed saves", async () => {
  const capture = {
    content_hash: "sha256:" + "a".repeat(64),
    source_project_id: "origin",
    roots: ["scan-A"],
  };
  let imported = false;
  let finishOpen: (() => void) | undefined;
  const open = vi.fn(
    () =>
      new Promise((resolve) => {
        finishOpen = () => {
          imported = true;
          resolve({ capture, created: false });
        };
      }),
  );
  const save = vi
    .fn()
    .mockResolvedValueOnce(null)
    .mockRejectedValueOnce(new Error("Destination is read-only"));
  vi.stubGlobal("pywebview", {
    api: { open_capture: open, save_capture: save, set_window_title: vi.fn() },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (new URL(request.url).pathname.endsWith("/evidence"))
        return Response.json({
          source_project_id: "origin",
          roots: ["scan-A"],
          runs: [
            {
              snapshot: { run_id: "scan-A" },
              request: { experiment_id: "scan" },
              configuration: {},
            },
          ],
        });
      if (new URL(request.url).pathname.endsWith("/recording"))
        return Response.json({
          record_count: 0,
          selected_record_count: 0,
          next_offset: null,
          items: [],
          dataset_schema: {
            dimensions: [],
            variables: [],
            point_domain: { kind: "point_cloud", columns: [] },
          },
        });
      return Response.json(imported ? [capture] : []);
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ImportedCaptures projectId="space-a" unavailable={false} />
      <DesktopFiles />
    </QueryClientProvider>,
  );
  await screen.findByText("暂无导入数据。");
  fireEvent.click(screen.getByText("导入数据", { exact: true }));
  fireEvent.click(screen.getByRole("button", { name: "打开 Scopecat 文件…" }));
  await screen.findByText("正在打开并检查选定文件…");
  fireEvent.click(screen.getByRole("button", { name: "打开 Scopecat 文件…" }));
  expect(open).toHaveBeenCalledTimes(1);
  finishOpen!();
  await screen.findByText("此数据已在应用中。");
  await screen.findByText("入口运行：scan-A");
  fireEvent.click(screen.getByText("导入数据", { exact: true }));
  expect(screen.getByText("入口运行：scan-A")).toBeVisible();
  expect(screen.getByRole("button", { name: "查看数据" })).toBeVisible();
  expect(screen.getByText("预览数据清理…")).not.toBeVisible();
  await screen.findByRole("region", { name: "文件中的运行" });
  act(() => window.history.back());
  await waitFor(() => expect(screen.queryByRole("region", { name: "文件中的运行" })).toBeNull());
  act(() => window.history.forward());
  await screen.findByRole("region", { name: "文件中的运行" });
  fireEvent.click(screen.getByRole("button", { name: "另存副本…" }));
  await waitFor(() => expect(screen.queryByText("正在保存文件…")).toBeNull());
  expect(screen.queryByText(/^已保存到/)).toBeNull();
  expect(screen.getByText("入口运行：scan-A")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "另存副本…" }));
  await screen.findByRole("alert");
  expect(screen.getByText("Destination is read-only")).toBeTruthy();
  expect(save).toHaveBeenLastCalledWith(capture.content_hash);
  expect(screen.getByText("入口运行：scan-A")).toBeTruthy();
});
