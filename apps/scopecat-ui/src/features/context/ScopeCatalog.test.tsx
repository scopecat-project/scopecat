// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ScopeCatalog } from "./ScopeCatalog";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount(value?: string) {
  const change = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ScopeCatalog kind="batch" owner="project-a" value={value} onChange={change} />
    </QueryClientProvider>,
  );
  return change;
}
it("reads an off-page selected label and paginates without replacing the selection", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname.endsWith("/retained"))
        return Response.json({ id: "retained", name: "Old cooldown" });
      return Response.json(
        url.searchParams.has("before")
          ? { items: [{ id: "retained", name: "Old cooldown" }], next_cursor: null }
          : { items: [{ id: "new", name: "New cooldown" }], next_cursor: 9 },
      );
    }),
  );
  const change = mount("retained");
  await screen.findByRole("option", { name: "Old cooldown" });
  expect(screen.getByLabelText("Experimental batch")).toHaveValue("retained");
  fireEvent.click(await screen.findByRole("button", { name: "Load more batches" }));
  await screen.findByRole("option", { name: "Old cooldown" });
  expect(screen.queryByRole("button", { name: "Load more batches" })).toBeNull();
  expect(change).not.toHaveBeenCalled();
});
it("retries metadata creation under the original ID and requires explicit selection", async () => {
  const ids: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "PUT") {
        const id = new URL(request.url).pathname.split("/").at(-1)!;
        ids.push(id);
        if (ids.length === 1) throw new TypeError("response lost");
        return Response.json({ id, name: "New cooldown" });
      }
      return Response.json({ items: [], next_cursor: null });
    }),
  );
  const change = mount();
  fireEvent.click(screen.getByRole("button", { name: "New batch" }));
  fireEvent.change(screen.getByLabelText("Experimental batch name"), {
    target: { value: "New cooldown" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create batch" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Create batch" }));
  const use = await screen.findByRole("button", { name: "Use New cooldown" });
  expect(ids).toHaveLength(2);
  expect(ids[1]).toBe(ids[0]);
  expect(change).not.toHaveBeenCalled();
  fireEvent.click(use);
  await waitFor(() => expect(change).toHaveBeenCalledWith(ids[0]));
});
