// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopFiles, requestOpenFile } from "./DesktopFiles";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("opens from another page and preserves history through cancel, failure, and success", async () => {
  window.history.replaceState(null, "", "/?run=original#runs");
  const hash = "sha256:" + "a".repeat(64);
  const open = vi
    .fn()
    .mockResolvedValueOnce(null)
    .mockRejectedValueOnce(new Error("Invalid capture"))
    .mockResolvedValueOnce({ capture: { content_hash: hash }, created: true });
  vi.stubGlobal("pywebview", { api: { open_capture: open } });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DesktopFiles />
    </QueryClientProvider>,
  );
  fireEvent.keyDown(window, { key: "o", ctrlKey: true });
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  expect(window.location.hash).toBe("#runs");
  expect(window.location.search).toBe("?run=original");
  act(requestOpenFile);
  expect((await screen.findByRole("alert")).textContent).toBe("Invalid capture");
  expect(window.location.hash).toBe("#runs");
  fireEvent.keyDown(window, { key: "o", metaKey: true });
  await screen.findByText("File imported.");
  expect(new URLSearchParams(window.location.search).get("capture")).toBe(hash);
  expect(window.location.hash).toBe("#history");
  expect(screen.queryByRole("alert")).toBeNull();
  act(() => window.history.back());
  await waitFor(() => expect(window.location.hash).toBe("#runs"));
  expect(window.location.search).toBe("?run=original");
  act(() => window.history.forward());
  await waitFor(() => expect(window.location.hash).toBe("#history"));
  expect(open).toHaveBeenCalledTimes(3);
});

it("leaves browser Open shortcuts alone outside the native application", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DesktopFiles />
    </QueryClientProvider>,
  );
  const event = new KeyboardEvent("keydown", { key: "o", ctrlKey: true, cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
});
