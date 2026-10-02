// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopSession } from "./DesktopSession";

afterEach(() => {
  cleanup();
  delete window.pywebview;
});

it("does not dismiss automatic quit until cancellation is acknowledged", async () => {
  const wait_for_idle = vi.fn().mockResolvedValue(undefined);
  window.pywebview = {
    api: {
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      wait_for_idle,
      request_exit: vi.fn().mockResolvedValue({ runs: 1 }),
      exit: vi.fn(),
      status: vi.fn(),
      retry: vi.fn(),
      restart: vi.fn(),
      register_source: vi.fn(),
      choose_directory: vi.fn(),
      create_source: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(<DesktopSession />);
  act(() => window.scopecatRequestExit?.());
  const waitButton = await screen.findByRole("button", { name: "Quit when work finishes" });
  await waitFor(() => expect(waitButton).toBeEnabled());
  fireEvent.click(waitButton);
  await screen.findByText(/Waiting for work to finish/);
  wait_for_idle.mockRejectedValueOnce(new Error("Could not cancel automatic quit"));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not cancel");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByText(/Waiting for work to finish/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(wait_for_idle.mock.calls).toEqual([[true], [false], [false]]);
});

it("leaves background work running only after the user chooses it", async () => {
  const exit = vi.fn().mockResolvedValue(undefined);
  window.pywebview = {
    api: {
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      exit,
      request_exit: vi
        .fn()
        .mockResolvedValue({ runs: 1, procedures: 0, instrument_sessions: 0, requests: 0 }),
      wait_for_idle: vi.fn().mockResolvedValue(undefined),
      status: vi.fn(),
      retry: vi.fn(),
      restart: vi.fn(),
      register_source: vi.fn(),
      choose_directory: vi.fn(),
      create_source: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(<DesktopSession />);
  expect(exit).not.toHaveBeenCalled();
  act(() => window.scopecatRequestExit?.());
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(exit).not.toHaveBeenCalled();
  act(() => window.scopecatRequestExit?.());
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Keep running in background" })).toBeEnabled(),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Keep running in background" }));
  await waitFor(() => expect(exit).toHaveBeenCalledWith(true));
});

it("keeps a failed stop recoverable in the current window", async () => {
  let failStop!: (error: Error) => void;
  const stop = new Promise<void>((_resolve, reject) => {
    failStop = reject;
  });
  const exit = vi.fn().mockReturnValue(stop);
  window.pywebview = {
    api: {
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      exit,
      request_exit: vi
        .fn()
        .mockResolvedValue({ runs: 1, procedures: 0, instrument_sessions: 0, requests: 0 }),
      wait_for_idle: vi.fn().mockResolvedValue(undefined),
      status: vi.fn(),
      retry: vi.fn(),
      restart: vi.fn(),
      register_source: vi.fn(),
      choose_directory: vi.fn(),
      create_source: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(<DesktopSession />);
  act(() => window.scopecatRequestExit?.());
  await waitFor(() => expect(screen.getByRole("button", { name: "Stop and close" })).toBeEnabled());
  fireEvent.click(await screen.findByRole("button", { name: "Stop and close" }));
  expect(screen.getByRole("status")).toHaveTextContent("Stopping work and releasing devices");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  await act(async () => failStop(new Error("Device release is still pending")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Device release is still pending");
  expect(exit).toHaveBeenCalledWith(false);
  expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
});

it("shows progress immediately and prevents duplicate quit requests", async () => {
  let finishCheck!: (value: null) => void;
  const check = new Promise<null>((resolve) => {
    finishCheck = resolve;
  });
  const request_exit = vi.fn().mockReturnValue(check);
  window.pywebview = {
    api: {
      set_window_title: vi.fn(),
      open_capture: vi.fn(),
      save_capture: vi.fn(),
      export_run: vi.fn(),
      save_captured_artifact: vi.fn(),
      request_exit,
      exit: vi.fn(),
      wait_for_idle: vi.fn(),
      status: vi.fn(),
      retry: vi.fn(),
      restart: vi.fn(),
      register_source: vi.fn(),
      choose_directory: vi.fn(),
      create_source: vi.fn(),
      prepare_author_environment: vi.fn(),
      create_author_environment: vi.fn(),
    },
  };
  render(<DesktopSession />);
  act(() => window.scopecatRequestExit?.());
  expect(screen.getByRole("status")).toHaveTextContent("Checking unfinished work");
  expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  act(() => window.scopecatRequestExit?.());
  expect(request_exit).toHaveBeenCalledTimes(1);
  await act(async () => finishCheck(null));
  expect(screen.getByRole("status")).toHaveTextContent("Closing Scopecat");
  expect(screen.getByRole("button", { name: "Stop and close" })).toBeDisabled();
});
