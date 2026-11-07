// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopSession } from "./DesktopSession";
import { launchCloseBlocked, retainLaunchCloseParticipant } from "../launch/launch-close-barrier";

function send(name: string, requestId: string) {
  window.dispatchEvent(new CustomEvent(name, { detail: { requestId } }));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("acknowledges once and retains the edit fence until matching host release", async () => {
  const acknowledge = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("pywebview", { api: { complete_close_preflight: acknowledge } });
  render(<DesktopSession />);
  act(() => {
    send("scopecat:close-preflight", "native-one");
    send("scopecat:close-preflight", "native-one");
  });
  expect(launchCloseBlocked()).toBe(true);
  await waitFor(() => expect(acknowledge).toHaveBeenCalledExactlyOnceWith("native-one", "saved"));
  act(() => send("scopecat:close-release", "unrelated"));
  expect(launchCloseBlocked()).toBe(true);
  act(() => send("scopecat:close-release", "native-one"));
  expect(launchCloseBlocked()).toBe(false);
});
it("does not acknowledge a flush that finishes after the host released its request", async () => {
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const detach = retainLaunchCloseParticipant(
    {},
    {
      freeze() {},
      flush: async () => {
        await pending;
        return "saved";
      },
      saved: () => true,
    },
  );
  const acknowledge = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("pywebview", { api: { complete_close_preflight: acknowledge } });
  render(<DesktopSession />);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("scopecat:close-preflight", { detail: { requestId: "late" } }),
    );
    window.dispatchEvent(
      new CustomEvent("scopecat:close-release", {
        detail: { requestId: "late", message: "Timed out" },
      }),
    );
    finish();
    await pending;
  });
  expect(acknowledge).not.toHaveBeenCalled();
  expect(launchCloseBlocked()).toBe(false);
  detach();
});

it("keeps late exit polling isolated from a reopened dialog and stops after unmount", async () => {
  vi.useFakeTimers();
  const report = {
    draining: false,
    finishing: [],
    deferred: [],
    blockers: [],
    file_operations: 0,
    error: null,
  };
  let finishPreviousPoll!: (value: typeof report) => void;
  const previousPoll = new Promise<typeof report>((resolve) => {
    finishPreviousPoll = resolve;
  });
  const exitStatus = vi.fn().mockReturnValueOnce(previousPoll).mockResolvedValue(report);
  vi.stubGlobal("pywebview", {
    api: {
      request_exit: vi.fn().mockResolvedValue({ runs: 1 }),
      exit_status: exitStatus,
      wait_for_idle: vi.fn().mockResolvedValue(undefined),
    },
  });
  const view = render(<DesktopSession />);
  try {
    await act(async () => window.scopecatRequestExit?.());
    expect(exitStatus).toHaveBeenCalledTimes(1);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Cancel" })));
    await act(async () => window.scopecatRequestExit?.());
    expect(exitStatus).toHaveBeenCalledTimes(2);
    await act(async () => finishPreviousPoll({ ...report, file_operations: 99 }));
    expect(screen.queryByText("Finishing 99 file operations.")).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(exitStatus).toHaveBeenCalledTimes(3);
    view.unmount();
    await act(async () => vi.advanceTimersByTimeAsync(2000));
    expect(exitStatus).toHaveBeenCalledTimes(3);
  } finally {
    view.unmount();
    vi.useRealTimers();
  }
});
