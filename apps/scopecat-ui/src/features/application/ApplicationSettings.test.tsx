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

it("keeps preparation separate from stopping and applying an update", async () => {
  const installation = {
    python: "/current/python",
    static_dir: "/current/gui",
    environment: { python: "current Python" },
    adapter_identity: "current-capability",
  };
  const candidate = { ...installation, python: "/candidate/python" };
  const state = {
    home: "/application",
    state: "running",
    detail: null,
    installation,
    candidate: null as typeof candidate | null,
  };
  const apply = vi.fn().mockResolvedValue(undefined);
  const dependencies = vi.fn().mockResolvedValue("Dependencies ready");
  const client = vi.fn().mockResolvedValue("/authors/.venv/bin/python");
  const prepare = vi.fn().mockImplementation(async () => {
    state.candidate = candidate;
    return candidate;
  });
  window.pywebview = {
    api: {
      status: vi.fn().mockImplementation(async () => ({ ...state })),
      prepare_update: prepare,
      apply_update: apply,
      register_source: vi.fn(),
      prepare_author_environment: dependencies,
      create_author_environment: client,
      restart: vi.fn(),
      requalify: vi.fn(),
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
  expect(await screen.findByText("/current/python")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Stop and apply prepared update" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Author directory"), { target: { value: "/authors" } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare background dependencies" }));
  expect(await screen.findByText("Dependencies ready")).toBeVisible();
  expect(dependencies).toHaveBeenCalledWith("/authors");
  expect(window.pywebview.api.restart).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Rebuild local Python environment" }));
  expect(await screen.findByText("/authors/.venv/bin/python")).toBeVisible();
  expect(client).toHaveBeenCalledWith("/authors", true);
  fireEvent.change(screen.getByLabelText("Delivery directory"), { target: { value: "/delivery" } });
  fireEvent.click(screen.getByRole("button", { name: "Prepare update" }));
  expect(await screen.findByText("/candidate/python")).toBeVisible();
  expect(prepare).toHaveBeenCalledWith("/delivery");
  expect(apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Stop and apply prepared update" }));
  await waitFor(() => expect(apply).toHaveBeenCalledTimes(1));
});
