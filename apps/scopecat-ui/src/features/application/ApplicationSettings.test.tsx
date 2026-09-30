// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  };
  const dependencies = vi.fn().mockResolvedValue("Dependencies ready");
  const client = vi.fn().mockResolvedValue("/authors/.venv/bin/python");
  const restart = vi.fn();
  window.pywebview = {
    api: {
      request_exit: vi.fn(),
      wait_for_idle: vi.fn(),
      status: vi.fn().mockImplementation(async () => ({ ...state })),
      register_source: vi.fn(),
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
