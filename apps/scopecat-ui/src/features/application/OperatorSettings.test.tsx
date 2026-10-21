// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { OperatorSettings } from "./OperatorSettings";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uses the editing revision, rejects conflicting windows and reloads explicitly", async () => {
  let saved = { operator: "Alice", revision: 1 };
  const writes: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "GET") return Response.json(saved);
      const command = await request.json();
      writes.push(command);
      if (command.revision !== saved.revision)
        return Response.json(
          { detail: "Operator setting changed; reload before saving" },
          { status: 409 },
        );
      saved = { ...command, revision: saved.revision + 1 };
      return Response.json(saved);
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <OperatorSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByLabelText("Operator name")).toHaveValue("Alice");
  fireEvent.change(screen.getByLabelText("Operator name"), { target: { value: "Carol" } });
  saved = { operator: "Bob", revision: 2 };
  fireEvent.click(screen.getByRole("button", { name: "Save operator" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Operator setting changed");
  expect(writes).toEqual([{ operator: "Carol", revision: 1 }]);
  fireEvent.click(screen.getByRole("button", { name: "Reload operator" }));
  await waitFor(() => expect(screen.getByLabelText("Operator name")).toHaveValue("Bob"));
  fireEvent.change(screen.getByLabelText("Operator name"), { target: { value: "Carol" } });
  fireEvent.click(screen.getByRole("button", { name: "Save operator" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Operator saved");
  expect(saved).toEqual({ operator: "Carol", revision: 3 });
});
