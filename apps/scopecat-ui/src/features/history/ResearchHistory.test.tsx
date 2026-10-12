// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ResearchHistory } from "./ResearchHistory";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("filters retained runs and removes only their project association", async () => {
  const requests: Request[] = [];
  const open = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/record-collections") return Response.json({ items: [] });
      if (url.pathname === "/api/v1/data/captures") return Response.json([]);
      if (url.pathname === "/api/v1/research-projects")
        return Response.json({
          items: [
            {
              id: "alpha",
              name: "Alpha",
              description: "",
              revision: 1,
              created_at: "2026-09-17T00:00:00Z",
              updated_at: "2026-09-17T00:00:00Z",
            },
          ],
        });
      if (url.pathname === "/api/v1/samples")
        return Response.json({
          items: [{ record: { id: "chip" }, revision: { content: { display_name: "Chip" } } }],
        });
      if (url.pathname.endsWith("/members/samples")) return Response.json({ ids: ["chip"] });
      if (request.method === "DELETE")
        return Response.json({
          project_id: "alpha",
          kind: "runs",
          identity: "run-1",
          present: false,
        });
      if (url.pathname === "/api/v1/runs")
        return Response.json({
          items: [
            {
              snapshot: {
                run_id: "run-1",
                created_at: "2026-09-17T00:00:00Z",
                samples: [],
                outcome: { result: "failed", certainty: "indeterminate" },
              },
              control: { state: "closed", admission: { display_name: "Retained scan" } },
              deployment_id: "bench-1",
            },
          ],
        });
      throw new Error(`Unexpected request: ${request.method} ${url.pathname}`);
    }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <ResearchHistory projectId="space-a" daemonUnavailable={false} onOpenRun={open} />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: "Alpha" });
  fireEvent.change(screen.getByLabelText("Research project"), { target: { value: "alpha" } });
  fireEvent.change(screen.getByLabelText("Sample", { exact: true }), { target: { value: "chip" } });
  await waitFor(() =>
    expect(
      requests.some((request) => {
        const url = new URL(request.url);
        return (
          url.pathname === "/api/v1/runs" &&
          url.searchParams.get("research_project") === "alpha" &&
          url.searchParams.get("sample_id") === "chip"
        );
      }),
    ).toBe(true),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Retained scan" }));
  expect(open).toHaveBeenCalledWith("run-1");
  expect(screen.getByText("failed")).toBeVisible();
  expect(screen.getByText("Outcome indeterminate")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Remove run association" })).toBeNull();
  expect(screen.getByRole("link", { name: "Open result in new tab or window" })).toHaveAttribute(
    "href",
    "/?run=run-1",
  );
  fireEvent.click(screen.getByText("Research project management"));
  fireEvent.click(await screen.findByRole("button", { name: "Remove run association" }));
  await waitFor(() =>
    expect(
      requests.some(
        (request) =>
          request.method === "DELETE" &&
          new URL(request.url).pathname === "/api/v1/research-projects/alpha/members/runs/run-1",
      ),
    ).toBe(true),
  );
  fireEvent.click(screen.getByLabelText("Show runs outside this project to associate"));
  fireEvent.click(screen.getByText("Research project management"));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Remove run association" })).toBeNull(),
  );
  expect(screen.getByText("Showing runs across all projects for association.")).toBeVisible();
  expect(screen.getByLabelText("Research project")).toHaveValue("alpha");
  expect(screen.getByLabelText("Sample", { exact: true })).toHaveValue("chip");
  expect(
    requests.some(
      (request) =>
        request.method === "DELETE" && new URL(request.url).pathname === "/api/v1/runs/run-1",
    ),
  ).toBe(false);
});

it("scopes short numbers to a collection and retains that filter after renaming", async () => {
  const open = vi.fn();
  const requests: Request[] = [];
  let name = "Cooldown A";
  const collection = () => ({
    id: "a",
    name,
    description: "original",
    revision: name === "Cooldown A" ? 1 : 2,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      const url = new URL(request.url);
      if (url.pathname === "/api/v1/record-collections")
        return Response.json({
          items: [collection(), { ...collection(), id: "b", name: "Cooldown B" }],
        });
      if (url.pathname === "/api/v1/record-collections/a" && request.method === "PUT") {
        const body = await request.clone().json();
        name = body.name;
        return Response.json(collection());
      }
      if (url.pathname.match(/record-collections\/[ab]\/runs\/1$/))
        return Response.json({
          collection_id: url.pathname.split("/")[4],
          number: 1,
          run_id: url.pathname.includes("/a/") ? "a-1" : "b-1",
        });
      if (url.pathname === "/api/v1/data/captures") return Response.json([]);
      return Response.json({ items: [] });
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ResearchHistory projectId="space-a" daemonUnavailable={false} onOpenRun={open} />
    </QueryClientProvider>,
  );
  expect(screen.getByLabelText("Collection run number")).toBeDisabled();
  await screen.findByRole("option", { name: "Cooldown A" });
  fireEvent.change(screen.getByLabelText("Record collection filter"), { target: { value: "a" } });
  await waitFor(() =>
    expect(requests.some((r) => new URL(r.url).searchParams.get("record_collection") === "a")).toBe(
      true,
    ),
  );
  fireEvent.change(screen.getByLabelText("Collection run number"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Find run" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open collection run #1" }));
  expect(open).toHaveBeenLastCalledWith("a-1");
  fireEvent.click(screen.getByRole("button", { name: "Rename collection" }));
  fireEvent.change(screen.getByLabelText("New collection name"), { target: { value: "Renamed" } });
  fireEvent.click(screen.getByRole("button", { name: "Save collection name" }));
  await screen.findByRole("option", { name: "Renamed" });
  const rename = requests.find((request) => request.method === "PUT")!;
  expect(await rename.clone().json()).toEqual({
    name: "Renamed",
    description: "original",
    expected_revision: 1,
  });
  expect(screen.getByLabelText("Record collection filter")).toHaveValue("a");
  expect(screen.getByLabelText("Collection run number")).toHaveValue("1");
  fireEvent.change(screen.getByLabelText("Record collection filter"), { target: { value: "b" } });
  expect(screen.queryByRole("button", { name: "Open collection run #1" })).toBeNull();
  expect(screen.getByLabelText("Collection run number")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("Collection run number"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Find run" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open collection run #1" }));
  expect(open).toHaveBeenLastCalledWith("b-1");
});
