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
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
        })
      }
    >
      <ConfigurationExchange operator="receiver" onCreated={async () => {}} />
    </QueryClientProvider>,
  );
}
async function upload() {
  const file = new File([JSON.stringify(document)], "configuration.json", {
    type: "application/json",
  });
  Object.defineProperty(file, "text", { value: async () => JSON.stringify(document) });
  fireEvent.change(screen.getByLabelText("Import configuration file"), {
    target: { files: [file] },
  });
  await screen.findByRole("button", { name: "Create my copy" });
}
it("reports native save cancellation, actual path and failure", async () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Export configuration…" }));
  await screen.findByRole("option", { name: "saved" });
  fireEvent.change(screen.getByLabelText("Parameter version"), { target: { value: "saved" } });
  fireEvent.change(screen.getByLabelText("File label"), { target: { value: "Shared inputs" } });
  fireEvent.click(screen.getByRole("button", { name: "Review export" }));
  const button = await screen.findByRole("button", { name: "Save configuration file" });
  fireEvent.click(button);
  await screen.findByText("Configuration save cancelled.");
  expect(JSON.parse(save.mock.calls[0]![0])).toEqual(document);
  save.mockResolvedValue("/chosen/configuration.json");
  fireEvent.click(button);
  await screen.findByText("Configuration saved to /chosen/configuration.json.");
  save.mockRejectedValue(new Error("Disk full"));
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Disk full");
});
it("reviews source without execution and uses the native archive save bridge", async () => {
  mount();
  await upload();
  fireEvent.click(screen.getByText("Attached source files and dependencies"));
  fireEvent.click(screen.getByText("example.py"));
  expect(screen.getByText("raise RuntimeError('not executed')")).toBeVisible();
  expect(saveSource).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Accept source and download…" }));
  await screen.findByText(/Source save cancelled/);
  expect(saveSource).toHaveBeenCalledWith(inspection.content_hash);
  saveSource.mockResolvedValue("/chosen/author-source.zip");
  fireEvent.click(screen.getByRole("button", { name: "Accept source and download…" }));
  await screen.findByText(/Source saved to \/chosen\/author-source.zip/);
  expect(screen.getByRole("link", { name: "Application settings" })).toHaveAttribute(
    "href",
    "#settings",
  );
});
it("retries an unknown network outcome with the same operation identity", async () => {
  mount();
  await upload();
  fireEvent.click(screen.getByRole("button", { name: "Create my copy" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("outcome is not confirmed");
  failDerive = false;
  fireEvent.click(screen.getByRole("button", { name: "Create my copy" }));
  await screen.findByText(/Created parameter branch/);
  expect(attempts).toHaveLength(2);
  expect(attempts[1]).toBe(attempts[0]);
});
