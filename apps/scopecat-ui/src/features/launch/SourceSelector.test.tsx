// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SourceSelector } from "./SourceSelector";
import { useAuthorWorkspaces } from "./source-api";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("shows unavailable retained sources and never chooses a different owner implicitly", async () => {
  const onSelect = vi.fn();
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(new URL(request.url).pathname);
      return new Response(
        JSON.stringify({
          items: [
            { id: "legacy", name: "Service source", available: true, unavailable_reason: null },
            {
              id: "restored",
              name: "Saved source",
              available: false,
              unavailable_reason: "Local source is not bound",
            },
          ],
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  function Harness() {
    const catalog = useAuthorWorkspaces("project");
    return <SourceSelector catalog={catalog} workspaceId="restored" onSelect={onSelect} />;
  }
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <Harness />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Local source is not bound");
  expect(screen.getByLabelText("Code workspace")).toHaveValue("restored");
  expect(screen.getByRole("option", { name: /Saved source/ })).toBeDisabled();
  expect(onSelect).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Code workspace"), { target: { value: "legacy" } });
  expect(onSelect).toHaveBeenCalledWith("legacy");
  expect(requests).toEqual(["/api/v1/author-workspaces"]);
});
