// @vitest-environment jsdom
import { act, cleanup, render, waitFor } from "@testing-library/react";
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
