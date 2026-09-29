// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { PracticePanel } from "./PracticePanel";

function response(value: unknown) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("reopens the retained task and defaults cleanup to preserving edited files", async () => {
  const scope = {
    id: "practice",
    title: "My practice",
    state: "active",
    procedure_id: "existing-task",
    directory: "/notes",
  };
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(response({ items: [scope] }))
    .mockResolvedValueOnce(response({ ...scope, state: "cleared", file_disposition: "preserve" }))
    .mockResolvedValueOnce(
      response({ items: [{ ...scope, state: "cleared", file_disposition: "preserve" }] }),
    );
  vi.stubGlobal("fetch", fetch);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PracticePanel reachable />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("link", { name: "Open practice task" })).toHaveAttribute(
    "href",
    "?procedure=existing-task#launch",
  );
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Clear practice…" }));
  expect(screen.getByRole("combobox", { name: "Practice files" })).toHaveValue("preserve");
  fireEvent.click(screen.getByRole("button", { name: "Clear this practice" }));
  expect(await screen.findByText("Practice cleared")).toBeVisible();
  const request = fetch.mock.calls[1]?.[0] as Request;
  expect(await request.json()).toEqual({ files: "preserve" });
  expect(screen.getByRole("link", { name: "Export practice files" })).toBeVisible();
});
