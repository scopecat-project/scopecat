// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ParametersJourneyPanel } from "./ParametersJourneyPanel";

function show() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ParametersJourneyPanel reachable />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  delete window.pywebview;
});
it("prepares without forms and preserves the saved path when opening the editor fails", async () => {
  const journey = {
    directory: "/authors/lesson",
    notebook: "/authors/lesson/notebooks/parameters.ipynb",
    python: "/authors/lesson/.venv/bin/python",
    ready: true,
  };
  const status = vi.fn().mockResolvedValue(null);
  const prepare = vi.fn().mockImplementation(async () => {
    status.mockResolvedValue(journey);
    return journey;
  });
  const open = vi.fn().mockRejectedValue(new Error("Open the Notebook manually"));
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        parameters_journey: status,
        prepare_parameters_journey: prepare,
        open_parameters_notebook: open,
      },
    },
  });
  show();
  const start = await screen.findByRole("button", { name: "Start parameters Notebook" });
  await vi.waitFor(() => expect(start).toBeEnabled());
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(start);
  expect(await screen.findByRole("alert")).toHaveTextContent("Open the Notebook manually");
  expect(screen.getByText(journey.notebook)).toBeVisible();
  expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "#runs");
  expect(prepare).toHaveBeenCalledWith(undefined);
  expect(open).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Continue parameters Notebook" })).toBeEnabled();
});
it("keeps desktop preparation distinct from browser-only Help", () => {
  show();
  expect(screen.getByText(/Open Help in the Scopecat desktop/)).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
