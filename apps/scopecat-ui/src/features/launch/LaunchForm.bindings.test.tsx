// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { useEffect, useRef } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { installLaunchRecoveryRoutes } from "../../test/launch-recovery-fixture";
import { LaunchDraftProvider, useLaunchDraft } from "./LaunchDraft";
import { LaunchForm } from "./LaunchForm";
import type { LaunchCatalogEntry } from "./launch-api";
vi.mock("./MeasurementContext", () => ({ MeasurementContext: () => null }));
vi.mock("./PlanSave", () => ({ PlanSave: () => null }));
const entry: LaunchCatalogEntry = {
  id: "capture",
  version: "1",
  title: "Capture",
  description: "Capture",
  actions: ["preview", "submit"],
  controls: [],
  request: { properties: { points: { type: "integer", title: "点数", default: 51 } } },
  kind: "diagnostic",
  configuration_effect: "none",
};
function Form() {
  const context = useLaunchDraft();
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    void context.select(entry).then(() =>
      context.selectConfiguration({
        kind: "parameters",
        ref: { revision_id: "p", content_hash: "sha256:p" },
        overrides: [],
      }),
    );
  }, [context]);
  return context.draft ? <LaunchForm entry={entry} catalogReady onAdmitted={() => {}} /> : null;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
it("discovers current request without setup, explicitly binds, then previews through the same request", async () => {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  const requirements = {
    kind: "binding_requirements",
    workspace_id: "",
    experiment_id: "capture",
    version: "1",
    inputs_hash: "sha256:inputs",
    supported: true,
    requirements: [{ port_id: "vna", interface_id: "network/v1" }],
    diagnostics: [],
  };
  vi.stubGlobal("fetch", async (request: Request) => {
    const path = new URL(request.url).pathname;
    const body = await request.json();
    calls.push({ path, body });
    if (path.endsWith("/experiment-launcher/bindings")) return Response.json(requirements);
    if (path.endsWith("/setup/bindings/options"))
      return Response.json({
        requirements,
        candidates: [
          {
            port_id: "vna",
            device_id: "a",
            device_label: "VNA A",
            connection_revision_id: "rev-a",
            interface_id: "network/v1",
            component_path: [],
          },
        ],
        diagnostics: [],
      });
    if (path.endsWith("/setup/bindings/check"))
      return Response.json({
        definition: { topology: {}, instruments: [], routing: {}, domain_target: null },
        diagnostics: [],
      });
    if (path.endsWith("/experiment-launcher/preview"))
      return Response.json({ detail: "preview received exact bindings" }, { status: 422 });
    throw new Error(`Unexpected ${path}`);
  });
  installLaunchRecoveryRoutes();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LaunchDraftProvider projectId="test">
        <Form />
      </LaunchDraftProvider>
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: "检查本实验所需设备" });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "检查本实验所需设备" })).toBeEnabled(),
  );
  expect(screen.getByRole("button", { name: "预览" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "检查本实验所需设备" }));
  await screen.findByLabelText("设备与端点 · vna");
  for (const [label, value] of [
    ["设备与端点 · vna", "0"],
    ["开始时 · vna", "preserve"],
    ["成功后 · vna", "release"],
    ["失败后 · vna", "abort_and_release"],
  ])
    fireEvent.change(screen.getByLabelText(label!), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "检查并用于本次准备" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "预览" })).toBeEnabled());
  expect(
    calls.some((call) => call.path.endsWith("/preview") || call.path.endsWith("/submit")),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "预览" }));
  await screen.findByText("preview received exact bindings");
  const discovery = calls.find((call) => call.path.endsWith("/experiment-launcher/bindings"))!.body;
  const preview = calls.find((call) => call.path.endsWith("/preview"))!.body;
  expect(discovery.inputs).toEqual({ points: 51 });
  expect(preview.inputs).toEqual(discovery.inputs);
  expect(preview.selection).toEqual(discovery.selection);
  expect(preview.instrument_bindings).toMatchObject({
    requirements,
    choices: [{ device_id: "a", connection_revision_id: "rev-a" }],
  });
  fireEvent.change(screen.getByLabelText("点数"), { target: { value: "101" } });
  expect(screen.queryByLabelText("设备与端点 · vna")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "预览" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("点数"), { target: { value: "51" } });
  expect(screen.queryByLabelText("设备与端点 · vna")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "预览" })).toBeDisabled();
  expect(calls.some((call) => call.path.endsWith("/submit"))).toBe(false);
  client.clear();
});
