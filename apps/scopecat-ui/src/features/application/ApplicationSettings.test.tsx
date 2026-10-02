// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApplicationSettings } from "./ApplicationSettings";

vi.mock("../instruments/device-api", () => ({
  getDevices: async () => ({ items: [] }),
}));
afterEach(() => {
  cleanup();
  delete window.pywebview;
});

it("creates an example from a chosen parent and keeps cancelled picks harmless", async () => {
  const choose = vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce("/Users/test/Science");
  const create = vi.fn().mockResolvedValue("/Users/test/Science/first experiment");
  const register = vi.fn();
  window.pywebview = {
    api: {
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      status: vi.fn().mockResolvedValue({
        home: "/app",
        sources: [],
        installation: {
          python: "/app/python",
          static_dir: "/app/gui",
          environment: {},
          adapter_identity: null,
        },
      }),
      choose_directory: choose,
      create_source: create,
      register_source: register,
      restart: vi.fn(),
      retry: vi.fn(),
      exit: vi.fn(),
      request_exit: vi.fn(),
      wait_for_idle: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ApplicationSettings />
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "New code folder" }));
  const submit = screen.getByRole("button", { name: "Create folder and prepare Python" });
  fireEvent.click(screen.getByRole("button", { name: "Choose save location…" }));
  await waitFor(() => expect(choose).toHaveBeenCalledTimes(1));
  expect(submit).toBeDisabled();
  expect(create).not.toHaveBeenCalled();
  expect(register).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Choose save location…" })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: "Choose save location…" }));
  expect(await screen.findByText("/Users/test/Science")).toBeVisible();
  fireEvent.change(screen.getByLabelText("New folder name"), {
    target: { value: "first experiment" },
  });
  fireEvent.click(submit);
  await waitFor(() =>
    expect(create).toHaveBeenCalledWith("/Users/test/Science", "first experiment"),
  );
});

it("keeps author dependencies independent and uses native application updates", async () => {
  const installation = {
    python: "/current/python",
    static_dir: "/current/gui",
    environment: { python: "current Python" },
    adapter_identity: "current-capability",
  };
  const state = {
    home: "/application",
    state: "running",
    detail: null,
    installation,
    sources: [{ directory: "/authors", python: "/authors/.venv/bin/python" }],
  };
  const dependencies = vi.fn().mockResolvedValue("Dependencies ready");
  const client = vi.fn().mockResolvedValue("/authors/.venv/bin/python");
  const restart = vi.fn();
  window.pywebview = {
    api: {
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      request_exit: vi.fn(),
      wait_for_idle: vi.fn(),
      status: vi.fn().mockImplementation(async () => ({ ...state })),
      register_source: vi.fn(),
      choose_directory: vi.fn(),
      create_source: vi.fn(),
      prepare_author_environment: dependencies,
      create_author_environment: client,
      restart,
      retry: vi.fn(),
      exit: vi.fn(),
    },
  };
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ApplicationSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("/current/python")).not.toBeVisible();
  fireEvent.click(screen.getByText("Technical diagnostics"));
  expect(screen.getByText("/current/python")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Stop and apply prepared update" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Author directory"), { target: { value: "/authors" } });
  fireEvent.click(screen.getByText("Dependencies and environment repair"));
  fireEvent.click(screen.getByRole("button", { name: "Prepare background dependencies" }));
  expect(await screen.findByText("Dependencies ready")).toBeVisible();
  expect(dependencies).toHaveBeenCalledWith("/authors");
  expect(restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Rebuild local Python environment" }));
  expect(await screen.findByText("/authors/.venv/bin/python")).toBeVisible();
  expect(client).toHaveBeenCalledWith("/authors", true);
  expect(screen.queryByLabelText("Delivery directory")).toBeNull();
  expect(screen.getByText(/install the new version/)).toBeVisible();
});
