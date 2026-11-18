// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { scenarioFixture } from "../../test/scenario-fixture";
import { getDevices, getDeviceDeclaration, type DeviceView } from "../instruments/device-api";
import { SetupPanel } from "./SetupPanel";
import { getConfigurationTemplates, getSetupDefinitions, saveSetupDefinition } from "./setup-api";

vi.mock("./setup-api", () => ({
  getConfigurationTemplates: vi.fn(),
  importConfigurationTemplate: vi.fn(),
  getSetupDefinitions: vi.fn(),
  saveSetupDefinition: vi.fn(),
}));
vi.mock("../instruments/device-api", () => ({
  getDevices: vi.fn(),
  getDeviceDeclaration: vi.fn(),
}));
const definition = {
  topology: { entities: [] },
  routing: { roles: [], routes: [] },
  instruments: [],
  domain_target: null,
  scenario: scenarioFixture,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getConfigurationTemplates).mockResolvedValue({ items: [] });
  vi.mocked(getDevices).mockResolvedValue({ items: [] });
  vi.mocked(getSetupDefinitions).mockResolvedValue({
    items: [
      {
        id: "bench",
        definition,
        actor: "operator",
        note: "",
        purpose: "experiment",
      },
    ],
  });
});
afterEach(cleanup);
function renderPanel(resolveOperator?: () => Promise<string>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SetupPanel operator="operator" resolveOperator={resolveOperator} />
    </QueryClientProvider>,
  );
  return client;
}
it("edits a definition without copying connections or selecting a global setup", async () => {
  vi.mocked(saveSetupDefinition).mockResolvedValue({
    id: "resolved",
    content_hash: "sha256:resolved",
    actor: "operator",
    note: "",
    resolution: {
      definition_id: "bench revised",
      definition_hash: "sha256:definition",
      devices: [],
    },
    setup: {
      topology: {},
      instrument_registry: { instruments: [] },
      routing: {},
      domain_target: null,
    },
  });
  renderPanel();
  await screen.findByRole("option", { name: "bench" });
  fireEvent.change(screen.getByLabelText("已保存的 setup"), { target: { value: "bench" } });
  fireEvent.change(screen.getByLabelText("另存为"), { target: { value: "bench revised" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 setup" }));
  await waitFor(() =>
    expect(saveSetupDefinition).toHaveBeenCalledWith(definition, "bench revised", "operator"),
  );
  expect(screen.queryByText("Confirm setup selection")).not.toBeInTheDocument();
});
it("starts an empty definition and preserves the draft when saving fails", async () => {
  vi.mocked(saveSetupDefinition).mockRejectedValue(
    new Error("Device connection changed; review Devices"),
  );
  renderPanel();
  fireEvent.click(screen.getByRole("button", { name: "新建 setup" }));
  expect(screen.getByRole("button", { name: "保存 setup" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("另存为"), { target: { value: "diagnostic" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 setup" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Device connection changed");
  expect(screen.getByLabelText("另存为")).toHaveValue("diagnostic");
});

it("retains setup attribution for an uncertain retry of the same named revision", async () => {
  const resolve = vi.fn(async () => "Alice");
  vi.mocked(saveSetupDefinition).mockRejectedValue(new Error("Response lost"));
  renderPanel(resolve);
  fireEvent.click(screen.getByRole("button", { name: "新建 setup" }));
  fireEvent.change(screen.getByLabelText("另存为"), { target: { value: "my-setup" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 setup" }));
  await screen.findByRole("alert");
  resolve.mockResolvedValue("Bob");
  fireEvent.click(screen.getByRole("button", { name: "保存 setup" }));
  await waitFor(() => expect(saveSetupDefinition).toHaveBeenCalledTimes(2));
  expect(vi.mocked(saveSetupDefinition).mock.calls[1]).toEqual(
    vi.mocked(saveSetupDefinition).mock.calls[0],
  );
  expect(vi.mocked(saveSetupDefinition).mock.calls[0]![2]).toBe("Alice");
  expect(resolve).toHaveBeenCalledTimes(1);
});

const registered: DeviceView = {
  device: {
    id: "dc",
    label: "DC",
    state: "available",
    head: { device_id: "dc", revision_id: "rev-1", content_hash: "sha256:device" },
  },
  revision: {
    id: "rev-1",
    device_id: "dc",
    actor: "operator",
    note: "",
    content: {
      driver: { provider_id: "lab", driver_id: "dual-dc", artifact_hash: "sha256:driver" },
      connection: { kind: "virtual" },
      access_aliases: [],
    },
  },
  availability: "idle",
};
it("reads only the bound device revision declaration and still saves incomplete mappings", async () => {
  const incomplete = {
    ...definition,
    instruments: [
      {
        id: "source",
        device_id: "dc",
        default_state: [],
        run_start: "preserve" as const,
        success_action: "release" as const,
        failure_action: "abort_and_release" as const,
      },
    ],
    routing: {
      routes: [
        {
          id: "bias",
          instrument_id: "source",
          endpoints: [
            { interface_id: "scopecat.dc_bias/v99", component_path: ["channels", "ch9"] },
          ],
        },
      ],
    },
  };
  vi.mocked(getDevices).mockResolvedValue({ items: [registered] });
  vi.mocked(getSetupDefinitions).mockResolvedValue({
    items: [
      { id: "bench", definition: incomplete, actor: "operator", note: "", purpose: "experiment" },
    ],
  });
  vi.mocked(getDeviceDeclaration).mockResolvedValue({
    instrument_id: "dc",
    implementation_id: "dual-dc",
    implementation_version: "1",
    interfaces: [{ id: "scopecat.dc_bias/v1" }],
    interface_mounts: [
      { interface_id: "scopecat.dc_bias/v1", component_path: ["channels", "ch1"] },
    ],
  });
  vi.mocked(saveSetupDefinition).mockRejectedValue(new Error("keep editor open"));
  renderPanel();
  await screen.findByRole("option", { name: "bench" });
  fireEvent.change(screen.getByLabelText("已保存的 setup"), { target: { value: "bench" } });
  expect(await screen.findByText(/能力版本不匹配/)).toBeVisible();
  expect(getDeviceDeclaration).toHaveBeenCalledWith("dc", "rev-1", expect.any(AbortSignal));
  fireEvent.click(screen.getByRole("button", { name: "保存 setup" }));
  await waitFor(() =>
    expect(saveSetupDefinition).toHaveBeenCalledWith(incomplete, "bench (revised)", "operator"),
  );
});
it("shows failed declaration reads as unknown without substituting another device", async () => {
  vi.mocked(getDevices).mockResolvedValue({ items: [registered] });
  vi.mocked(getSetupDefinitions).mockResolvedValue({
    items: [
      {
        id: "bench",
        definition: {
          ...definition,
          instruments: [
            {
              id: "source",
              device_id: "dc",
              default_state: [],
              run_start: "preserve" as const,
              success_action: "release" as const,
              failure_action: "abort_and_release" as const,
            },
          ],
        },
        actor: "operator",
        note: "",
        purpose: "experiment",
      },
    ],
  });
  vi.mocked(getDeviceDeclaration).mockRejectedValue(new Error("installed driver changed"));
  renderPanel();
  await screen.findByRole("option", { name: "bench" });
  fireEvent.change(screen.getByLabelText("已保存的 setup"), { target: { value: "bench" } });
  fireEvent.change(screen.getByLabelText("设备绑定"), { target: { value: "source" } });
  expect(await screen.findByText(/声明未知：无法核对/)).toBeVisible();
  expect(screen.getByLabelText("已声明能力与端点").children).toHaveLength(1);
});

it("withdraws stale declaration choices after a successful read refetches with 409", async () => {
  vi.mocked(getDevices).mockResolvedValue({ items: [registered] });
  vi.mocked(getSetupDefinitions).mockResolvedValue({
    items: [
      {
        id: "bench",
        definition: {
          ...definition,
          instruments: [
            {
              id: "source",
              device_id: "dc",
              default_state: [],
              run_start: "preserve",
              success_action: "release",
              failure_action: "abort_and_release",
            },
          ],
        },
        actor: "operator",
        note: "",
        purpose: "experiment",
      },
    ],
  });
  vi.mocked(getDeviceDeclaration).mockResolvedValue({
    instrument_id: "dc",
    implementation_id: "dual-dc",
    implementation_version: "1",
    interfaces: [{ id: "scopecat.dc_bias/v1" }],
  });
  const client = renderPanel();
  await screen.findByRole("option", { name: "bench" });
  fireEvent.change(screen.getByLabelText("已保存的 setup"), { target: { value: "bench" } });
  fireEvent.change(screen.getByLabelText("设备绑定"), { target: { value: "source" } });
  await screen.findByRole("option", { name: "scopecat.dc_bias/v1 · 根组件" });
  vi.mocked(getDeviceDeclaration).mockRejectedValue(new Error("409: registered driver changed"));
  await act(async () => {
    await client.invalidateQueries({ queryKey: ["device-declaration"] });
  });
  expect(await screen.findByText(/声明未知：无法核对/)).toBeVisible();
  expect(
    screen.queryByRole("option", { name: "scopecat.dc_bias/v1 · 根组件" }),
  ).not.toBeInTheDocument();
});
