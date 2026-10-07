// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NotebookJourneyPanel } from "./NotebookJourneyPanel";

function show() {
  return render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <NotebookJourneyPanel reachable />
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
        notebook_journey: status,
        prepare_notebook_journey: prepare,
        open_lesson_notebook: open,
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
  expect(prepare).toHaveBeenCalledWith(undefined, "parameters");
  expect(open).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Continue parameters Notebook" })).toBeEnabled();
});
it("keeps desktop preparation distinct from browser-only Help", () => {
  show();
  expect(screen.getByText(/Open Help in the Scopecat desktop/)).toBeVisible();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
it("selects groups without borrowing the parameters receipt and locks selection during preparation", async () => {
  const parameters = {
    directory: "/parameters",
    notebook: "/parameters/notebooks/parameters.ipynb",
    python: "/parameters/.venv/bin/python",
    ready: true,
  };
  const groups = {
    directory: "/groups",
    notebook: "/groups/notebooks/groups.ipynb",
    python: "/groups/.venv/bin/python",
    ready: true,
  };
  let finish!: (value: typeof groups) => void;
  const status = vi
    .fn()
    .mockImplementation(async (topic) => (topic === "parameters" ? parameters : null));
  const prepare = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const open = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: status,
        prepare_notebook_journey: prepare,
        open_lesson_notebook: open,
      },
    },
  });
  show();
  expect(await screen.findByText(parameters.notebook)).toBeVisible();
  fireEvent.change(screen.getByRole("combobox", { name: "Course" }), {
    target: { value: "groups" },
  });
  const start = await screen.findByRole("button", { name: "Start groups Notebook" });
  await vi.waitFor(() => expect(start).toBeEnabled());
  expect(screen.queryByText(parameters.notebook)).not.toBeInTheDocument();
  fireEvent.click(start);
  await vi.waitFor(() => expect(screen.getByRole("combobox")).toBeDisabled());
  await vi.waitFor(() => expect(prepare).toHaveBeenCalledWith(undefined, "groups"));
  status.mockImplementation(async (topic) => (topic === "parameters" ? parameters : groups));
  finish(groups);
  expect(await screen.findByText(groups.notebook)).toBeVisible();
  await vi.waitFor(() => expect(open).toHaveBeenCalledWith("groups"));
  await vi.waitFor(() => expect(screen.getByRole("combobox")).toBeEnabled());
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "parameters" } });
  expect(await screen.findByText(parameters.notebook)).toBeVisible();
  expect(screen.getByRole("button", { name: "Continue parameters Notebook" })).toBeEnabled();
  expect(prepare).toHaveBeenCalledTimes(1);
});
