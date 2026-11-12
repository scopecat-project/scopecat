// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { PendingResultSettings } from "./PendingResultSettings";

const capacityFixture = {
  memory_bytes: 24,
  work_bytes: 8,
  disk_bytes: 0,
  memory_limit_bytes: 256,
  work_limit_bytes: 128,
  disk_limit_bytes: 1024,
  storage_degraded: false,
};
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("uses the editing revision, rejects conflicting windows and reloads explicitly", async () => {
  let saved = { storage_policy: "allow_temporary_disk", revision: 1 };
  const writes: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.url.endsWith("/pending-result-capacity")) return Response.json(capacityFixture);
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
      <PendingResultSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByLabelText("未保存结果暂存方式")).toHaveValue("allow_temporary_disk");
  fireEvent.change(screen.getByLabelText("未保存结果暂存方式"), {
    target: { value: "allow_temporary_disk" },
  });
  saved = { storage_policy: "memory_only", revision: 2 };
  fireEvent.click(screen.getByRole("button", { name: "保存暂存设置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Operator setting changed");
  expect(writes).toEqual([{ storage_policy: "allow_temporary_disk", revision: 1 }]);
  fireEvent.click(screen.getByRole("button", { name: "重新加载暂存设置" }));
  await waitFor(() =>
    expect(screen.getByLabelText("未保存结果暂存方式")).toHaveValue("memory_only"),
  );
  fireEvent.change(screen.getByLabelText("未保存结果暂存方式"), {
    target: { value: "allow_temporary_disk" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存暂存设置" }));
  expect(await screen.findByRole("status")).toHaveTextContent("暂存设置已保存");
  expect(saved).toEqual({ storage_policy: "allow_temporary_disk", revision: 3 });
});

it("loads memory default without writing and explains existing disk results", async () => {
  const fetch = vi.fn(async (request: Request) =>
    Response.json(
      request.url.endsWith("/pending-result-capacity")
        ? capacityFixture
        : { revision: 0, storage_policy: "memory_only" },
    ),
  );
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PendingResultSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByLabelText("未保存结果暂存方式")).toHaveValue("memory_only");
  expect(screen.getByRole("combobox", { name: "未保存结果暂存方式" })).toHaveValue("memory_only");
  expect(screen.getByText(/之前已接纳的采集仍可能完成临时写盘/)).toBeVisible();
  expect(screen.getByRole("button", { name: "保存暂存设置" })).toBeDisabled();
  expect(fetch.mock.calls).toHaveLength(2);
});

it("does not show a writable default after failed loading", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) =>
      request.url.endsWith("/pending-result-capacity")
        ? Response.json(capacityFixture)
        : Response.json({ detail: "unavailable" }, { status: 503 }),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PendingResultSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("unavailable");
  expect(screen.queryByLabelText("未保存结果暂存方式")).not.toBeInTheDocument();
});

it("reads capacity only on entry, manual refresh and policy save without contacting devices", async () => {
  let reads = 0;
  const paths: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      paths.push(path);
      if (path.endsWith("/pending-result-capacity")) {
        reads += 1;
        return Response.json({ ...capacityFixture, storage_degraded: reads === 2 });
      }
      return Response.json(
        request.method === "PUT"
          ? { ...(await request.json()), revision: 1 }
          : { revision: 0, storage_policy: "memory_only" },
      );
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <PendingResultSettings />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("内存载荷：24 / 256 bytes")).toBeVisible();
  expect(screen.getByText(/不是进程总内存 RSS/)).toBeVisible();
  expect(screen.getByText("工作载荷：8 / 128 bytes")).toBeVisible();
  expect(screen.getByText("临时磁盘载荷：0 / 1,024 bytes")).toBeVisible();
  expect(reads).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "刷新容量" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("临时存储状态异常");
  expect(reads).toBe(2);
  fireEvent.change(screen.getByLabelText("未保存结果暂存方式"), {
    target: { value: "allow_temporary_disk" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存暂存设置" }));
  await waitFor(() => expect(reads).toBe(3));
  expect(
    paths.every((path) =>
      ["/api/v1/pending-result-capacity", "/api/v1/pending-result-settings"].includes(path),
    ),
  ).toBe(true);
  expect(
    client.getQueryCache().find({ queryKey: ["pending-result-capacity"] })?.options,
  ).toMatchObject({ refetchOnWindowFocus: false, refetchOnReconnect: false });
});
