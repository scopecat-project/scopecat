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
function mount(serviceState = "running", fresh = false) {
  document.body.innerHTML = new DOMParser().parseFromString(markup, "text/html").body.innerHTML;
  const state = {
    setup_defaults: { project: "/home/experiments/main", data_root: "/home/data" },
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
          static_dir: "/lab/gui",
          environment: {
            prefix: "/lab/venv",
            python: "3.14.0",
            scopecat: "0.2.0",
            server: "0.3.0",
          },
        },
        state: serviceState,
        url: "http://127.0.0.1:9001",
        detail: "",
      },
    ],
  };
  const initialService = state.services[0]!;
  if (fresh) state.services = [];
  const control = { failOperation: false, failState: false, state };
  let poll: () => Promise<unknown> = () => Promise.resolve(control.state);
  const assign = vi.fn();
  const confirm = vi.fn(() => true);
  const requests: Array<{ path: string; body?: unknown }> = [];
  const fetch = vi.fn(async (path: string, options: RequestInit) => {
    if (path === "/api/state")
      return control.failState
        ? Response.json({ detail: "manager unavailable" }, { status: 503 })
        : Response.json(state);
    if (path === "/api/operations") {
      if (typeof options.body !== "string") throw new Error("Expected a JSON command");
      const command = JSON.parse(options.body) as { action: string };
      if (command.action === "setup" && !control.failOperation) state.services = [initialService];
      requests.push({ path, body: command });
      const operation = {
        command,
        service: command.action === "setup" ? "service-a" : null,
        status: control.failOperation ? "failed" : "succeeded",
        detail: control.failOperation ? "environment mismatch retained in log" : "ready",
        created: 0,
      };
      state.operations = [operation];
      return Response.json(operation);
    }
    if (path.endsWith("/log")) return Response.json({ text: "probe failure evidence" });
    throw new Error(`Unexpected manager request: ${path}`);
  });
  runInNewContext(script, {
    document,
    confirm,
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
  return { control, requests, assign, confirm, poll: () => poll() };
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

it("rechecks only the stopped registration ID after explicit confirmation, without starting it", async () => {
  const host = mount("stopped");
  fireEvent.click(await screen.findByRole("button", { name: "重新检查环境" }));
  await waitFor(() => expect(host.requests).toHaveLength(1));
  expect(host.confirm).toHaveBeenCalledWith(expect.stringContaining("不会安装软件或启动服务"));
  expect(host.requests[0]?.body).toEqual({
    action: "service_recheck",
    service: "service-a",
    id: "12345678123412341234123456789012",
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "重新检查环境" })).toBeEnabled());
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
  expect(host.control.state.services[0]!.state).toBe("stopped");
});
it.each(["running", "degraded", "stale", "unavailable"])(
  "disables environment rechecking for %s service state",
  async (state) => {
    const host = mount(state);
    const recheck = await screen.findByRole("button", { name: "重新检查环境" });
    expect(recheck).toBeDisabled();
    fireEvent.click(recheck);
    expect(host.requests).toHaveLength(0);
  },
);
it("disables rechecking during another management operation and honors cancellation", async () => {
  const host = mount("stopped");
  host.confirm.mockReturnValue(false);
  fireEvent.click(await screen.findByRole("button", { name: "重新检查环境" }));
  await waitFor(() => expect(host.confirm).toHaveBeenCalledOnce());
  expect(host.requests).toHaveLength(0);
  host.control.state.operations = [
    { status: "running", command: { action: "service_start", service: "service-a" } },
  ];
  await host.poll();
  expect(screen.getByRole("button", { name: "重新检查环境" })).toBeDisabled();
});
it("retains recheck failure evidence and the previously registered identity", async () => {
  const host = mount("stopped");
  host.control.failOperation = true;
  fireEvent.click(await screen.findByRole("button", { name: "重新检查环境" }));
  await waitFor(() =>
    expect(document.getElementById("notice")).toHaveTextContent(
      "environment mismatch retained in log",
    ),
  );
  expect(screen.getByText("Experiment service · 重新检查环境 · 失败")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "查看日志" }));
  await waitFor(() =>
    expect(document.getElementById("log")).toHaveTextContent("probe failure evidence"),
  );
  expect(screen.getByText("0.2.0")).toBeInTheDocument();
  expect(screen.queryByRole("link")).not.toBeInTheDocument();
});
it("labels the last registered paths and package identity as historical information using text", async () => {
  const host = mount("stopped");
  host.control.state.services[0]!.service.root = "<img src=x onerror=alert(1)>";
  await host.poll();
  await screen.findByText("以下信息来自最后一次成功登记或重新检查，不代表当前环境已经通过检查。");
  const fields = {
    项目目录: "<img src=x onerror=alert(1)>",
    "Python 解释器": "/lab/python",
    "GUI 目录": "/lab/gui",
    环境前缀: "/lab/venv",
    "Python 版本": "3.14.0",
    "scopecat 版本": "0.2.0",
    "scopecat-server 版本": "0.3.0",
  };
  for (const [label, value] of Object.entries(fields)) {
    expect(screen.getByText(label, { selector: "dt" }).nextElementSibling).toHaveTextContent(value);
  }
  expect(document.querySelector("img")).not.toBeInTheDocument();
});

it("shows first-run setup, preserves edits during polling, and enters the created workbench", async () => {
  const host = mount("running", true);
  const project = await screen.findByLabelText("主要代码文件夹");
  await waitFor(() => expect(project).toHaveValue("/home/experiments/main"));
  expect(document.getElementById("setup-panel")).toHaveAttribute("open");
  fireEvent.input(project, { target: { value: "D:\\实验代码" } });
  fireEvent.input(screen.getByLabelText("数据保存目录（可选）"), {
    target: { value: "E:\\科学记录" },
  });
  await host.poll();
  expect(project).toHaveValue("D:\\实验代码");
  fireEvent.submit(document.getElementById("setup-form")!);
  await waitFor(() => expect(host.assign).toHaveBeenCalledWith("http://127.0.0.1:9001/"));
  expect(host.requests[0]?.body).toEqual({
    id: "12345678123412341234123456789012",
    action: "setup",
    setup: {
      mode: "create",
      project: "D:\\实验代码",
      data_root: "E:\\科学记录",
      settings_file: null,
      environment_bundle: null,
      name: null,
    },
  });
});
it("connects an existing code folder without replacing its data binding and retains failure evidence", async () => {
  const host = mount("running", true);
  await waitFor(() =>
    expect(screen.getByLabelText("主要代码文件夹")).toHaveValue("/home/experiments/main"),
  );
  fireEvent.change(screen.getByLabelText("接入方式"), { target: { value: "connect" } });
  fireEvent.input(screen.getByLabelText("主要代码文件夹"), { target: { value: "/existing/lab" } });
  expect(
    screen.getByText("留空保留该项目已有的数据绑定。填写新目录不会迁移已有记录。"),
  ).toBeVisible();
  fireEvent.input(screen.getByLabelText("实验室本机设置 JSON 文件（可选）"), {
    target: { value: "/machine/lab.json" },
  });
  await host.poll();
  expect(screen.getByLabelText("实验室本机设置 JSON 文件（可选）")).toHaveValue(
    "/machine/lab.json",
  );
  fireEvent.input(screen.getByLabelText("实验室离线交付目录（可选）"), {
    target: { value: "/delivery/runtime" },
  });
  host.control.failOperation = true;
  fireEvent.submit(document.getElementById("setup-form")!);
  await waitFor(() =>
    expect(document.getElementById("notice")).toHaveTextContent(
      "environment mismatch retained in log",
    ),
  );
  expect(host.requests[0]?.body).toMatchObject({
    action: "setup",
    setup: {
      mode: "connect",
      project: "/existing/lab",
      data_root: null,
      settings_file: "/machine/lab.json",
      environment_bundle: "/delivery/runtime",
      name: null,
    },
  });
  expect(host.assign).not.toHaveBeenCalled();
  expect(screen.getByLabelText("主要代码文件夹")).toHaveValue("/existing/lab");
  expect(screen.getByRole("button", { name: "查看日志" })).toBeVisible();
});
it("keeps setup secondary for registered services and disables it during durable work", async () => {
  const host = mount();
  await screen.findByRole("button", { name: "启动 / 检查工作台" });
  expect(document.getElementById("setup-panel")).not.toHaveAttribute("open");
  host.control.state.operations = [
    { status: "running", command: { action: "setup", setup: { project: "/new" } } },
  ];
  await host.poll();
  expect(document.getElementById("setup-fields")).toBeDisabled();
  fireEvent.submit(document.getElementById("setup-form")!);
  expect(host.requests).toHaveLength(0);
});
it("does not redirect after setup if fresh service state cannot be verified", async () => {
  const host = mount("running", true);
  await waitFor(() =>
    expect(screen.getByLabelText("主要代码文件夹")).toHaveValue("/home/experiments/main"),
  );
  host.control.failState = true;
  fireEvent.submit(document.getElementById("setup-form")!);
  await waitFor(() =>
    expect(document.getElementById("notice")).toHaveTextContent("manager unavailable"),
  );
  expect(host.assign).not.toHaveBeenCalled();
});
