// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopSession } from "./DesktopSession";

afterEach(() => {
  cleanup();
  delete window.pywebview;
});

it("leaves background work running only after the user chooses it", async () => {
  const exit = vi.fn().mockResolvedValue(undefined);
  window.pywebview = {
    api: {
      exit,
      status: vi.fn(),
      retry: vi.fn(),
      restart: vi.fn(),
      requalify: vi.fn(),
      prepare_update: vi.fn(),
      apply_update: vi.fn(),
      register_source: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(<DesktopSession />);
  expect(exit).not.toHaveBeenCalled();
  act(() => window.scopecatRequestExit?.());
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(exit).not.toHaveBeenCalled();
  act(() => window.scopecatRequestExit?.());
  fireEvent.click(await screen.findByRole("button", { name: "Keep running in background" }));
  await waitFor(() => expect(exit).toHaveBeenCalledWith(true));
});

it("keeps a failed stop recoverable in the current window", async () => {
  const exit = vi.fn().mockRejectedValueOnce(new Error("Device release is still pending"));
  window.pywebview = {
    api: {
      exit,
      status: vi.fn(),
      retry: vi.fn(),
      restart: vi.fn(),
      requalify: vi.fn(),
      prepare_update: vi.fn(),
      apply_update: vi.fn(),
      register_source: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(<DesktopSession />);
  act(() => window.scopecatRequestExit?.());
  fireEvent.click(await screen.findByRole("button", { name: "Stop and close" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Device release is still pending");
  expect(exit).toHaveBeenCalledWith(false);
  expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
});
