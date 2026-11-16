// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopSession } from "./DesktopSession";
import {
  launchCloseBlocked,
  retainLaunchCloseParticipant,
  resetLaunchCloseWindowForTests,
} from "../launch/launch-close-barrier";
import { registerDeviceClose } from "../instruments/device-close";
import "@testing-library/jest-dom/vitest";

function send(name: string, requestId: string) {
  window.dispatchEvent(new CustomEvent(name, { detail: { requestId } }));
}

afterEach(() => {
  cleanup();
  resetLaunchCloseWindowForTests();
  window.history.replaceState({}, "", "/");
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
  await waitFor(() =>
    expect(acknowledge.mock.calls).toEqual([
      ["native-one", "pending"],
      ["native-one", "saved"],
    ]),
  );
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
  expect(acknowledge).toHaveBeenCalledExactlyOnceWith("late", "pending");
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

it.each(["window", "application"])(
  "locates another pending window and cancels %s close without certifying a late operation",
  async (entry) => {
    let finish!: (status: "saved") => void;
    const operation = new Promise<"saved">((resolve) => {
      finish = resolve;
    });
    const unregister = registerDeviceClose(() => operation);
    const acknowledge = vi.fn().mockResolvedValue(undefined);
    const show = vi.fn().mockResolvedValue(undefined);
    const cancel = vi.fn().mockResolvedValue(true);
    vi.stubGlobal("pywebview", {
      api: {
        complete_close_preflight: acknowledge,
        close_preflight_status: vi.fn().mockResolvedValue({
          requestId: "hanging",
          windows: [
            { windowId: "device-window", label: "Device: hidden-scope", status: "pending" },
          ],
        }),
        show_close_blocker: show,
        cancel_close_preflight: cancel,
        request_exit: vi.fn().mockReturnValue(new Promise(() => {})),
        exit_status: vi
          .fn()
          .mockResolvedValue({ finishing: [], deferred: [], blockers: [], file_operations: 0 }),
      },
    });
    render(<DesktopSession />);
    act(() => {
      if (entry === "application") window.scopecatRequestExit?.();
      send("scopecat:close-preflight", "hanging");
    });
    expect(await screen.findByText(/Device: hidden-scope/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "定位窗口" }));
    await waitFor(() => expect(show).toHaveBeenCalledExactlyOnceWith("hanging", "device-window"));
    fireEvent.click(screen.getByRole("button", { name: "取消本次关闭等待" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledExactlyOnceWith("hanging"));
    expect(launchCloseBlocked()).toBe(true);
    act(() => send("scopecat:close-release", "hanging"));
    expect(launchCloseBlocked()).toBe(false);
    expect(screen.getByText(/已提交的保存或设备操作没有撤销/)).toBeInTheDocument();
    await act(async () => finish("saved"));
    expect(acknowledge.mock.calls).toEqual([["hanging", "pending"]]);
    unregister();
  },
);

it("keeps the close fence after a failed cancellation and accepts the instrument dialog event", async () => {
  const unregister = registerDeviceClose(() => new Promise(() => {}));
  const cancel = vi
    .fn()
    .mockRejectedValueOnce(new Error("bridge disconnected"))
    .mockResolvedValue(true);
  vi.stubGlobal("pywebview", {
    api: {
      complete_close_preflight: vi.fn().mockResolvedValue(undefined),
      cancel_close_preflight: cancel,
    },
  });
  render(<DesktopSession />);
  act(() => send("scopecat:close-preflight", "modal"));
  fireEvent.click(screen.getByRole("button", { name: "取消本次关闭等待" }));
  expect(await screen.findByText(/取消关闭等待未确认/)).toBeInTheDocument();
  expect(launchCloseBlocked()).toBe(true);
  act(() => {
    window.dispatchEvent(new Event("scopecat:cancel-close-wait"));
  });
  await waitFor(() => expect(cancel.mock.calls).toEqual([["modal"], ["modal"]]));
  act(() => send("scopecat:close-release", "modal"));
  expect(launchCloseBlocked()).toBe(false);
  unregister();
});

it("locates the current unsaved draft after navigation without a reload or another save", async () => {
  const flush = vi.fn(async () => "blocked" as const);
  retainLaunchCloseParticipant(
    {},
    {
      freeze() {},
      flush,
      saved: () => false,
      sources: () => [
        { workspace: "author", experiment: "signal", draft: "draft-a", canReturn: true },
      ],
    },
  );
  window.history.replaceState({}, "", "/?desktop=1#settings");
  vi.stubGlobal("pywebview", {
    api: { complete_close_preflight: vi.fn().mockResolvedValue(undefined) },
  });
  render(<DesktopSession />);
  act(() => send("scopecat:close-preflight", "draft"));
  expect(await screen.findByText(/未确认保存：author \/ signal \/ draft-a/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "返回此草稿" })).toBeDisabled();
  act(() => {
    window.dispatchEvent(
      new CustomEvent("scopecat:close-release", {
        detail: { requestId: "draft", message: "Input is not confirmed saved" },
      }),
    );
  });
  fireEvent.click(screen.getByRole("button", { name: "返回此草稿" }));
  const target = new URL(window.location.href);
  expect(target.hash).toBe("#launch");
  expect(target.searchParams.get("workspace")).toBe("author");
  expect(target.searchParams.get("experiment")).toBe("signal");
  expect(target.searchParams.get("draft")).toBe("draft-a");
  expect(target.searchParams.get("desktop")).toBe("1");
  expect(flush).toHaveBeenCalledTimes(1);
});

it("identifies a detached draft without offering an unavailable recovery, and releases the edit fence", async () => {
  const detach = retainLaunchCloseParticipant(
    {},
    {
      freeze() {},
      flush: async () => "blocked",
      saved: () => false,
      sources: () => [
        { workspace: "previous", experiment: "signal", draft: "local-only", canReturn: false },
      ],
    },
  );
  detach();
  vi.stubGlobal("pywebview", {
    api: { complete_close_preflight: vi.fn().mockResolvedValue(undefined) },
  });
  render(<DesktopSession />);
  act(() => send("scopecat:close-preflight", "unavailable"));
  expect(await screen.findByText(/previous \/ signal \/ local-only/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "返回此草稿" })).not.toBeInTheDocument();
  expect(screen.getByText(/原编辑页已不可用/)).toBeInTheDocument();
  act(() => send("scopecat:close-release", "unavailable"));
  expect(launchCloseBlocked()).toBe(false);
});

it("ignores an old cancellation reply after another close begins and deduplicates same-tick cancels", async () => {
  let reject!: (error: Error) => void;
  const cancelled = new Promise<boolean>((_resolve, fail) => {
    reject = fail;
  });
  const cancel = vi.fn().mockReturnValueOnce(cancelled).mockResolvedValue(true);
  const unregister = registerDeviceClose(() => new Promise(() => {}));
  vi.stubGlobal("pywebview", {
    api: {
      complete_close_preflight: vi.fn().mockResolvedValue(undefined),
      cancel_close_preflight: cancel,
    },
  });
  render(<DesktopSession />);
  act(() => send("scopecat:close-preflight", "old"));
  act(() => {
    window.dispatchEvent(new Event("scopecat:cancel-close-wait"));
    window.dispatchEvent(new Event("scopecat:cancel-close-wait"));
  });
  expect(cancel).toHaveBeenCalledExactlyOnceWith("old");
  act(() => {
    send("scopecat:close-release", "old");
    send("scopecat:close-preflight", "new");
  });
  await act(async () => reject(new Error("old request disconnected")));
  expect(screen.queryByText(/old request disconnected/)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消本次关闭等待" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "取消本次关闭等待" }));
  await waitFor(() => expect(cancel.mock.calls).toEqual([["old"], ["new"]]));
  act(() => send("scopecat:close-release", "new"));
  unregister();
});
