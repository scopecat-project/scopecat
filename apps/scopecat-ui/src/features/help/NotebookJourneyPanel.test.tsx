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
  window.history.replaceState(null, "", "/");
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
it.each(["groups", "refresh", "compute", "calibration", "joint-calibration", "task-calibration"])(
  "selects %s without borrowing the parameters receipt and locks selection during preparation",
  async (topic) => {
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
      .mockImplementation(async (selected) => (selected === "parameters" ? parameters : null));
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
      target: { value: topic },
    });
    const start = await screen.findByRole("button", { name: `Start ${topic} Notebook` });
    await vi.waitFor(() => expect(start).toBeEnabled());
    expect(screen.queryByText(parameters.notebook)).not.toBeInTheDocument();
    fireEvent.click(start);
    await vi.waitFor(() => expect(screen.getByRole("combobox")).toBeDisabled());
    await vi.waitFor(() => expect(prepare).toHaveBeenCalledWith(undefined, topic));
    status.mockImplementation(async (selected) =>
      selected === "parameters" ? parameters : groups,
    );
    finish(groups);
    expect(await screen.findByText(groups.notebook)).toBeVisible();
    await vi.waitFor(() => expect(open).toHaveBeenCalledWith(topic));
    await vi.waitFor(() => expect(screen.getByRole("combobox")).toBeEnabled());
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "parameters" } });
    expect(await screen.findByText(parameters.notebook)).toBeVisible();
    expect(screen.getByRole("button", { name: "Continue parameters Notebook" })).toBeEnabled();
    expect(prepare).toHaveBeenCalledTimes(1);
  },
);

it("returns to the selected course and opens its exact Settings folder without preparing again", async () => {
  const journey = {
    directory: "/authors/notes & experiments",
    notebook: "/authors/notes & experiments/notebooks/groups.ipynb",
    python: "/authors/notes & experiments/.venv/bin/python",
    ready: true,
  };
  const status = vi.fn().mockResolvedValue(journey);
  const prepare = vi.fn();
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: { api: { notebook_journey: status, prepare_notebook_journey: prepare } },
  });
  const first = show();
  fireEvent.change(screen.getByRole("combobox", { name: "Course" }), {
    target: { value: "groups" },
  });
  expect(await screen.findByText(journey.notebook)).toBeVisible();
  fireEvent.click(screen.getByRole("link", { name: "Manage this code folder in Settings" }));
  expect(new URL(window.location.href).searchParams.get("source")).toBe(journey.directory);
  expect(window.location.hash).toBe("#settings");
  first.unmount();
  // A newly mounted Help reads the page selection, not a component-local default.
  show();
  expect(screen.getByRole("combobox", { name: "Course" })).toHaveValue("groups");
  expect(await screen.findByRole("button", { name: "Continue groups Notebook" })).toBeEnabled();
  expect(status).toHaveBeenLastCalledWith("groups");
  expect(prepare).not.toHaveBeenCalled();
});

it("shows unfinished preparation without claiming the folder is ready", async () => {
  window.history.replaceState(null, "", "/?lesson=unknown#help");
  Object.defineProperty(window, "pywebview", {
    configurable: true,
    value: {
      api: {
        notebook_journey: vi.fn().mockResolvedValue({
          directory: "/authors/unfinished",
          notebook: "/authors/unfinished/notebooks/parameters.ipynb",
          python: "/authors/unfinished/.venv/bin/python",
          ready: false,
        }),
      },
    },
  });
  show();
  expect(await screen.findByText(/Preparation unfinished/)).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "Manage this code folder in Settings" }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry preparation" })).toBeEnabled();
  expect(
    screen.queryByRole("button", { name: /Choose another save location/ }),
  ).not.toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Course" })).toHaveValue("parameters");
});
