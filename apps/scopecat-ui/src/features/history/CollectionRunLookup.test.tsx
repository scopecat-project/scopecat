// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CollectionRunLookup } from "./CollectionRunLookup";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("ignores a late lookup from another collection and never falls back to a global number", async () => {
  let finish!: (response: Response) => void;
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      requests.push(new URL(request.url).pathname);
      return requests.length === 1
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : Promise.resolve(Response.json({ detail: "Number not found" }, { status: 404 }));
    }),
  );
  const cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const open = vi.fn();
  const view = (collection: string) => (
    <QueryClientProvider client={cache}>
      <CollectionRunLookup
        key={collection}
        collection={collection}
        projectId="space"
        unavailable={false}
        onOpenRun={open}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(view("a"));
  fireEvent.change(screen.getByLabelText("Collection run number"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Find run" }));
  rerender(view("b"));
  await act(async () => finish(Response.json({ collection_id: "a", number: 1, run_id: "a-1" })));
  expect(screen.queryByRole("button", { name: "Open collection run #1" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Collection run number"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Find run" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Could not find collection run #1");
  expect(requests).toEqual([
    "/api/v1/record-collections/a/runs/1",
    "/api/v1/record-collections/b/runs/1",
  ]);
  expect(open).not.toHaveBeenCalled();
});
