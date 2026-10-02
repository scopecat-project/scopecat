// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ExportRun } from "./ExportRun";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows export progress and distinguishes completion, cancellation and failure", async () => {
  let finish: (path: string) => void = vi.fn();
  const pending = new Promise<string>((resolve) => {
    finish = resolve;
  });
  const save = vi
    .fn()
    .mockReturnValueOnce(pending)
    .mockResolvedValueOnce(null)
    .mockRejectedValueOnce(new Error("Destination is read-only"));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ExportRun runId="run-a" />
    </QueryClientProvider>,
  );
  expect(screen.queryByRole("button")).toBeNull();
  vi.stubGlobal("pywebview", { api: { export_run: save } });
  act(() => {
    window.dispatchEvent(new Event("pywebviewready"));
  });
  const button = screen.getByRole("button", { name: "Export Scopecat file…" });
  fireEvent.click(button);
  await screen.findByText("Preparing export and saving file…");
  expect(button).toHaveProperty("disabled", true);
  expect(save).toHaveBeenCalledWith("run-a");
  finish("/chosen/run.scopecat");
  await screen.findByText("Saved to /chosen/run.scopecat");
  fireEvent.click(button);
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  fireEvent.click(button);
  expect((await screen.findByRole("alert")).textContent).toBe("Destination is read-only");
  expect(button).toHaveProperty("disabled", false);
});
