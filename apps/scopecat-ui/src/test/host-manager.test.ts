// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
const assets = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../../../packages/lab-tools/src/lab_tools/host_assets",
);
const script = readFileSync(resolve(assets, "app.js"), "utf8");
const markup = readFileSync(resolve(assets, "index.html"), "utf8");
afterEach(() => {
  document.body.innerHTML = "";
  sessionStorage.clear();
});
function mount() {
  document.body.innerHTML = new DOMParser().parseFromString(markup, "text/html").body.innerHTML;
  const state = {
    topics: {},
    workspaces: [],
    operations: [] as object[],
    services: [
      {
        service: {
          id: "service-a",
          name: "Experiment service",
          root: "/lab",
          python: "/lab/python",
        },
        state: "running",
        url: "http://127.0.0.1:9001",
        detail: "",
      },
    ],
  };
  const control = { failOperation: false, failState: false, state };
  let poll: () => Promise<unknown> = () => Promise.resolve(control.state);
  const assign = vi.fn();
  const requests: Array<{ path: string; body?: unknown }> = [];
  const fetch = vi.fn(async (path: string, options: RequestInit) => {
    if (path === "/api/state")
      return control.failState
        ? Response.json({ detail: "manager unavailable" }, { status: 503 })
        : Response.json(state);
    if (path === "/api/operations") {
      if (typeof options.body !== "string") throw new Error("Expected a JSON command");
      const command: unknown = JSON.parse(options.body);
      requests.push({ path, body: command });
      const operation = {
        command,
        status: control.failOperation ? "failed" : "succeeded",
        detail: control.failOperation ? "environment mismatch retained in log" : "ready",
        created: 0,
      };
      state.operations = [operation];
      return Response.json(operation);
    }
    throw new Error(`Unexpected manager request: ${path}`);
  });
  runInNewContext(script, {
    document,
    URL,
    URLSearchParams,
    sessionStorage,
    location: { hash: "#token=manager-secret", pathname: "/", assign },
    history: { replaceState: vi.fn() },
    crypto: { randomUUID: () => "12345678-1234-1234-1234-123456789012" },
    fetch,
    setInterval: (callback: () => Promise<unknown>) => {
      poll = callback;
      return 1;
    },
    clearInterval: vi.fn(),
    setTimeout,
  });
  return { control, requests, assign, poll: () => poll() };
}
it("keeps manager open and reveals an isolated workbench link only after successful verification", async () => {
  const host = mount();
  const start = await screen.findByRole("button", { name: "启动 / 检查工作台" });
  expect(screen.queryByRole("link", { name: "打开工作台（新标签页）" })).not.toBeInTheDocument();
  fireEvent.click(start);
  const link = await screen.findByRole("link", { name: "打开工作台（新标签页）" });
  expect(link).toHaveAttribute("href", "http://127.0.0.1:9001/");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(link.getAttribute("href")).not.toContain("manager-secret");
  expect(host.assign).not.toHaveBeenCalled();
  expect(host.requests).toEqual([
    {
      path: "/api/operations",
      body: {
        action: "service_start",
        service: "service-a",
        id: "12345678123412341234123456789012",
      },
    },
  ]);
  host.control.state.services[0]!.state = "stopped";
  await host.poll();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
it("retains startup failure evidence and never offers the stale running URL", async () => {
  const host = mount();
  host.control.failOperation = true;
  fireEvent.click(await screen.findByRole("button", { name: "启动 / 检查工作台" }));
  await waitFor(() =>
    expect(document.getElementById("notice")).toHaveTextContent(
      "environment mismatch retained in log",
    ),
  );
  expect(screen.getByRole("button", { name: "查看日志" })).toBeVisible();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(host.assign).not.toHaveBeenCalled();
});
it("removes ready links when the manager can no longer verify service state", async () => {
  const host = mount();
  fireEvent.click(await screen.findByRole("button", { name: "启动 / 检查工作台" }));
  await screen.findByRole("link", { name: "打开工作台（新标签页）" });
  host.control.failState = true;
  await host.poll();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(document.getElementById("notice")).toHaveTextContent("manager unavailable");
});
it.each([
  "http://remote.example:9001/",
  "http://127.0.0.1:9001/#token=secret",
  "http://user:secret@127.0.0.1:9001/",
  "javascript:alert(1)",
])("rejects a non-workbench navigation URL %s", async (url) => {
  const host = mount();
  host.control.state.services[0]!.url = url;
  fireEvent.click(await screen.findByRole("button", { name: "启动 / 检查工作台" }));
  await waitFor(() =>
    expect(document.getElementById("notice")).toHaveTextContent("工作台地址无效"),
  );
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(host.assign).not.toHaveBeenCalled();
});
