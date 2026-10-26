// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConfigurationExchange } from "./ConfigurationExchange";
vi.mock("./parameter-api", () => ({
  getParameterRevisions: async () => ({ items: [{ id: "saved" }] }),
}));
vi.mock("./setup-api", () => ({ getSetupDefinitions: async () => ({ items: [] }) }));
vi.mock("../instruments/device-api", () => ({ getDevices: async () => ({ items: [] }) }));
const document = {
  label: "Shared inputs",
  format: "scopecat.configuration-exchange.v1",
  origin_store: "sender",
  catalog: { id: "catalog", definitions: [] },
  parameters: { id: "values", values: [] },
  parameter_origin: { revision_id: "saved", content_hash: "sha256:" + "a".repeat(64) },
  values_included: false,
  source: {
    files: { "example.py": btoa("raise RuntimeError('not executed')") },
    manifest: { python: "3.14", import_requirements: ["example"] },
  },
};
const inspection = { content_hash: "sha256:" + "b".repeat(64), document, notices: [] };
const save = vi.fn(),
  saveSource = vi.fn();
let failDerive = true;
let attempts: string[];
beforeEach(() => {
  attempts = [];
  failDerive = true;
  save.mockReset().mockResolvedValue(null);
  saveSource.mockReset().mockResolvedValue(null);
  vi.stubGlobal("pywebview", {
    api: { save_configuration: save, save_configuration_source: saveSource },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: Request | string, init?: RequestInit) => {
      const request =
        input instanceof Request ? input : new Request(new URL(input, "http://localhost"), init);
      const path = new URL(request.url).pathname;
      if (path.endsWith("/author-workspaces")) return Response.json({ items: [] });
      if (path.endsWith("/imports") && request.method === "GET") return Response.json([]);
      if (path.endsWith("/export")) return Response.json(document);
      if (path.endsWith("/inspect") || path.endsWith("/imports")) return Response.json(inspection);
      if (path.endsWith("/derive")) {
        attempts.push(await request.text());
        if (failDerive) throw new TypeError("connection lost");
        return Response.json({
          content_hash: inspection.content_hash,
          branch: { name: "My inputs", revision: document.parameter_origin },
          source_pending: true,
          setup_pending: false,
        });
      }
      throw new Error(`Unexpected ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function mount(operator = "receiver", resolveOperator?: () => Promise<string>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const component = (name: string) => (
    <QueryClientProvider client={client}>
      <ConfigurationExchange
        operator={name}
        resolveOperator={resolveOperator}
        onCreated={async () => {}}
      />
    </QueryClientProvider>
  );
  const view = render(component(operator));
  return (name: string) => view.rerender(component(name));
}
async function upload() {
  const file = new File([JSON.stringify(document)], "configuration.json", {
    type: "application/json",
  });
  Object.defineProperty(file, "text", { value: async () => JSON.stringify(document) });
  fireEvent.change(screen.getByLabelText("导入配置文件"), {
    target: { files: [file] },
  });
  await screen.findByRole("button", { name: "创建我的副本" });
}
it("reports native save cancellation, actual path and failure", async () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: "导出配置…" }));
  await screen.findByRole("option", { name: "saved" });
  fireEvent.change(screen.getByLabelText("参数版本"), { target: { value: "saved" } });
  fireEvent.change(screen.getByLabelText("文件标签"), { target: { value: "Shared inputs" } });
  fireEvent.click(screen.getByRole("button", { name: "核对导出内容" }));
  const button = await screen.findByRole("button", { name: "保存配置文件" });
  fireEvent.click(button);
  await screen.findByText("已取消保存配置。");
  expect(JSON.parse(save.mock.calls[0]![0])).toEqual(document);
  save.mockResolvedValue("/chosen/configuration.json");
  fireEvent.click(button);
  await screen.findByText("配置已保存到 /chosen/configuration.json。");
  save.mockRejectedValue(new Error("Disk full"));
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Disk full");
});
it("reviews source without execution and uses the native archive save bridge", async () => {
  mount();
  await upload();
  fireEvent.click(screen.getByText("附带源码文件与依赖"));
  fireEvent.click(screen.getByText("example.py"));
  expect(screen.getByText("raise RuntimeError('not executed')")).toBeVisible();
  expect(saveSource).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "接受源码并下载…" }));
  await screen.findByText(/已取消保存源码/);
  expect(saveSource).toHaveBeenCalledWith(inspection.content_hash);
  saveSource.mockResolvedValue("/chosen/author-source.zip");
  fireEvent.click(screen.getByRole("button", { name: "接受源码并下载…" }));
  await screen.findByText(/源码已保存到 \/chosen\/author-source.zip/);
  expect(screen.getByRole("link", { name: "应用设置" })).toHaveAttribute("href", "#settings");
});
it("retries an unknown network outcome with the same operation identity", async () => {
  mount();
  await upload();
  fireEvent.click(screen.getByRole("button", { name: "创建我的副本" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("结果尚未确认");
  failDerive = false;
  fireEvent.click(screen.getByRole("button", { name: "创建我的副本" }));
  await screen.findByText(/已创建参数分支/);
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toBe(attempts[0]);
});

it("retains the derive actor when another operation refreshes the shared automatic default", async () => {
  const resolve = vi.fn(async () => "Alice");
  const changeDisplayedDefault = mount("Alice", resolve);
  await upload();
  fireEvent.click(screen.getByRole("button", { name: "创建我的副本" }));
  await screen.findByRole("alert");
  expect(JSON.parse(attempts[0]!).actor).toBe("Alice");
  resolve.mockResolvedValue("Bob");
  changeDisplayedDefault("Bob");
  failDerive = false;
  fireEvent.click(screen.getByRole("button", { name: "创建我的副本" }));
  await screen.findByText(/已创建参数分支/);
  expect(attempts[1]).toBe(attempts[0]);
  expect(resolve).toHaveBeenCalledTimes(1);
});
