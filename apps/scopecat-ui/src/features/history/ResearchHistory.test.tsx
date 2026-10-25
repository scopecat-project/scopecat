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
  fireEvent.change(screen.getByLabelText("研究项目"), { target: { value: "alpha" } });
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
  expect(screen.getByText("失败")).toBeVisible();
  expect(screen.getByText("结果尚不确定")).toBeVisible();
  expect(screen.queryByRole("button", { name: "移除 run 关联" })).toBeNull();
  expect(screen.getByRole("link", { name: "在新标签页或窗口打开结果" })).toHaveAttribute(
    "href",
    "/?run=run-1&focus=run#runs",
  );
  fireEvent.click(screen.getByText("研究项目 management"));
  fireEvent.click(await screen.findByRole("button", { name: "移除 run 关联" }));
  await waitFor(() =>
    expect(
      requests.some(
        (request) =>
          request.method === "DELETE" &&
          new URL(request.url).pathname === "/api/v1/research-projects/alpha/members/runs/run-1",
      ),
    ).toBe(true),
  );
  fireEvent.click(screen.getByLabelText("显示项目外的 run 以供关联"));
  fireEvent.click(screen.getByText("研究项目 management"));
  await waitFor(() => expect(screen.queryByRole("button", { name: "移除 run 关联" })).toBeNull());
  expect(screen.getByText("Showing runs across all projects for association.")).toBeVisible();
  expect(screen.getByLabelText("研究项目")).toHaveValue("alpha");
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
  expect(screen.queryByLabelText("记录集内 run 编号")).not.toBeInTheDocument();
  await screen.findByRole("option", { name: "Cooldown A" });
  fireEvent.change(screen.getByLabelText("记录集筛选"), { target: { value: "a" } });
  await waitFor(() =>
    expect(requests.some((r) => new URL(r.url).searchParams.get("record_collection") === "a")).toBe(
      true,
    ),
  );
  fireEvent.change(screen.getByLabelText("记录集内 run 编号"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "查找 run" }));
  fireEvent.click(await screen.findByRole("button", { name: "打开记录集内 run #1" }));
  expect(open).toHaveBeenLastCalledWith("a-1");
  fireEvent.click(screen.getByText("Manage collection"));
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
  expect(screen.getByLabelText("记录集筛选")).toHaveValue("a");
  expect(screen.getByLabelText("记录集内 run 编号")).toHaveValue("1");
  fireEvent.change(screen.getByLabelText("记录集筛选"), { target: { value: "b" } });
  expect(screen.queryByRole("button", { name: "打开记录集内 run #1" })).toBeNull();
  expect(screen.getByLabelText("记录集内 run 编号")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("记录集内 run 编号"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "查找 run" }));
  fireEvent.click(await screen.findByRole("button", { name: "打开记录集内 run #1" }));
  expect(open).toHaveBeenLastCalledWith("b-1");
});

it("filters this course by recorded ownership and removes the filter explicitly", async () => {
  window.history.replaceState(null, "", "/?course=course-one#history");
  const requests: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      requests.push(url);
      if (url.pathname === "/api/v1/practice/course-one")
        return Response.json({ id: "course-one", lesson: "parameters", state: "active" });
      if (url.pathname.endsWith("/preview"))
        return Response.json({ record_count: 0, selection: { procedures: [], analyses: [] } });
      return Response.json({ items: [] });
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <ResearchHistory projectId="app" daemonUnavailable={false} onOpenRun={vi.fn()} />
    </QueryClientProvider>,
  );
  await screen.findByText("Course: parameters");
  expect(
    requests.some(
      (url) =>
        url.pathname === "/api/v1/runs" && url.searchParams.get("practice_scope") === "course-one",
    ),
  ).toBe(true);
  expect(screen.queryByText("研究项目 management")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show all Data" }));
  await waitFor(() =>
    expect(
      requests.some(
        (url) => url.pathname === "/api/v1/runs" && !url.searchParams.has("practice_scope"),
      ),
    ).toBe(true),
  );
  window.history.replaceState(null, "", "/");
});
