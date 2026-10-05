// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { OpenRunWindow } from "./OpenRunWindow";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("encodes only the exact run in a same-service browser link with no opener", () => {
  const runId = "https://outside.invalid/?run=other&#configuration";
  render(
    <QueryClientProvider client={new QueryClient()}>
      <OpenRunWindow runId={runId} />
    </QueryClientProvider>,
  );
  const link = screen.getByRole("link") as HTMLAnchorElement;
  const target = new URL(link.href);
  expect(target.origin).toBe(window.location.origin);
  expect(target.pathname).toBe("/");
  expect([...target.searchParams]).toEqual([["run", runId]]);
  expect(target.hash).toBe("");
  expect(link.target).toBe("_blank");
  expect(link.rel).toBe("noopener noreferrer");
});

it("uses the native bridge after readiness and reports failures without navigating", async () => {
  const open = vi.fn().mockRejectedValue(new Error("Application is restarting"));
  const original = window.location.href;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <OpenRunWindow runId="run-a" />
    </QueryClientProvider>,
  );
  vi.stubGlobal("pywebview", { api: { open_run_window: open } });
  act(() => {
    window.dispatchEvent(new Event("pywebviewready"));
  });
  expect(screen.queryByRole("link")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open result in new window" }));
  expect((await screen.findByRole("alert")).textContent).toBe("Application is restarting");
  expect(open).toHaveBeenCalledExactlyOnceWith("run-a");
  expect(window.location.href).toBe(original);
});
