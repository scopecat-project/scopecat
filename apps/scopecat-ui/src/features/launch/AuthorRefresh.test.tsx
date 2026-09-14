// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { AuthorRefresh } from "./AuthorRefresh";

const running = {
  operation_id: "captured-once",
  expected_generation: 1,
  code_revision: { content_hash: "source" },
  status: "running",
  phase: "application import",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};
const state = { enabled: true, generation: 1, active: { content_hash: "old" } };
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthorRefresh projectId="lab" />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("reconnects to captured preparation and waits for cancellation cleanup", async () => {
  let status = "running";
  const writes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("author-revisions")) return Response.json(state);
      if (path.endsWith("author-preparations")) return Response.json([running]);
      if (request.method === "POST") {
        writes.push(path);
        status = "cancelling";
      }
      return Response.json({ ...running, status });
    }),
  );
  mount();
  await screen.findByText(/running: application import/);
  fireEvent.click(screen.getByRole("button", { name: "Cancel preparation" }));
  await screen.findByText(/cancelling: application import/);
  expect(screen.getByRole("button", { name: "Cancel preparation" })).toBeDisabled();
  expect(writes).toEqual(["/api/v1/author-preparations/captured-once/cancel"]);
});

it("retries an ambiguous submission with the identical request", async () => {
  const submitted: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("author-revisions")) return Response.json(state);
      if (request.method === "POST") {
        const body = await request.json();
        submitted.push(body);
        if (submitted.length === 1) throw new TypeError("response lost");
        return Response.json({ ...running, ...body });
      }
      if (path.endsWith("author-preparations")) return Response.json([]);
      return Response.json({ detail: "not found" }, { status: 404 });
    }),
  );
  mount();
  const refresh = await screen.findByRole("button", { name: "Refresh author code" });
  await waitFor(() => expect(refresh).toBeEnabled());
  fireEvent.click(refresh);
  fireEvent.click(await screen.findByRole("button", { name: "Retry same submission" }));
  await screen.findByText(/running: application import/);
  expect(submitted).toHaveLength(2);
  expect(submitted[1]).toEqual(submitted[0]);
});
