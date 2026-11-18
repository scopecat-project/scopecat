import { execFileSync } from "node:child_process";
// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProgressiveBindings } from "./ProgressiveBindings";
import {
  inspectBindings,
  bindingOptions,
  checkBindings,
  type BindingOptions,
  type BindingRequest,
} from "./instrument-bindings-api";
import { saveSetupDefinition } from "../config/setup-api";
vi.mock("./instrument-bindings-api", () => ({
  inspectBindings: vi.fn(),
  bindingOptions: vi.fn(),
  checkBindings: vi.fn(),
}));
vi.mock("../config/setup-api", () => ({ saveSetupDefinition: vi.fn() }));
const request: BindingRequest = {
  action: "preview",
  parameter_sweeps: [],
  request_key: "",
  scan_mode: "cartesian",
  experiment: "capture",
  version: "1",
  workspace_id: "work",
  inputs: { points: 51 },
  actor: "alice",
  selection: {
    configuration: {
      kind: "parameters",
      ref: { revision_id: "p", content_hash: "sha256:p" },
      overrides: [],
    },
  },
};
const requirements: BindingOptions["requirements"] = {
  kind: "binding_requirements",
  workspace_id: "work",
  experiment_id: "capture",
  version: "1",
  inputs_hash: "sha256:inputs",
  supported: true,
  requirements: [{ port_id: "vna", interface_id: "network/v1" }],
  diagnostics: [],
};
const options: BindingOptions = {
  requirements,
  diagnostics: [],
  candidates: [
    {
      port_id: "vna",
      device_id: "device-a",
      device_label: "VNA A",
      connection_revision_id: "connection-a",
      interface_id: "network/v1",
      component_path: ["ch1"],
    },
    {
      port_id: "vna",
      device_id: "device-a",
      device_label: "VNA A",
      connection_revision_id: "connection-a",
      interface_id: "network/v1",
      component_path: ["ch2"],
    },
  ],
};
const definition = { instruments: [], topology: {}, routing: {}, domain_target: null };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(inspectBindings).mockResolvedValue(requirements);
  vi.mocked(bindingOptions).mockResolvedValue(options);
  vi.mocked(checkBindings).mockResolvedValue({ definition, diagnostics: [] });
});
afterEach(cleanup);
function mount() {
  const onChecked = vi.fn();
  const onEdit = vi.fn();
  const props = { request: () => request, disabled: false, actor: "alice", onChecked, onEdit };
  return { ...render(<ProgressiveBindings {...props} />), props, onChecked };
}
async function choose() {
  fireEvent.click(screen.getByRole("button", { name: "检查本实验所需设备" }));
  await screen.findByLabelText("设备与端点 · vna");
  expect(screen.getByLabelText("设备与端点 · vna")).toHaveValue("");
  expect(screen.getByRole("button", { name: "检查并用于本次准备" })).toBeDisabled();
  for (const [label, value] of [
    ["设备与端点 · vna", "1"],
    ["开始时 · vna", "preserve"],
    ["成功后 · vna", "release"],
    ["失败后 · vna", "abort_and_release"],
  ])
    fireEvent.change(screen.getByLabelText(label!), { target: { value } });
}
it("uses exact declared endpoint and explicit policies, without saving or starting", async () => {
  const { onChecked } = mount();
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "检查并用于本次准备" }));
  await waitFor(() => expect(onChecked).toHaveBeenCalledOnce());
  expect(inspectBindings).toHaveBeenCalledWith(request);
  expect(checkBindings).toHaveBeenCalledWith({
    requirements,
    choices: [
      {
        port_id: "vna",
        device_id: "device-a",
        connection_revision_id: "connection-a",
        component_path: ["ch2"],
        run_start: "preserve",
        success_action: "release",
        failure_action: "abort_and_release",
      },
    ],
    scenario: undefined,
  });
  expect(saveSetupDefinition).not.toHaveBeenCalled();
  expect(screen.getByText(/此选择只保留在当前打开的编辑器中/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("设备与端点 · vna"), { target: { value: "0" } });
  expect(screen.queryByText(/绑定检查通过/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("成功后 · vna")).toHaveValue("");
});
it("reports unsupported topology without fetching or guessing endpoints", async () => {
  vi.mocked(inspectBindings).mockResolvedValue({
    ...requirements,
    supported: false,
    requirements: [],
    diagnostics: [{ code: "unsupported", message: "Needs explicit topology" }],
  });
  mount();
  fireEvent.click(screen.getByRole("button", { name: "检查本实验所需设备" }));
  expect(await screen.findByText("Needs explicit topology")).toBeVisible();
  expect(bindingOptions).not.toHaveBeenCalled();
  expect(checkBindings).not.toHaveBeenCalled();
});
it("keeps a backend stale-revision rejection and requires correction", async () => {
  vi.mocked(checkBindings).mockResolvedValue({
    definition: null,
    diagnostics: [{ code: "binding_invalid", message: "device changed; reload declarations" }],
  });
  const { onChecked } = mount();
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "检查并用于本次准备" }));
  await screen.findByText("device changed; reload declarations");
  expect(onChecked).not.toHaveBeenCalled();
});
it("does not adopt a cancelled or detached response", async () => {
  let done!: (value: BindingOptions) => void;
  vi.mocked(bindingOptions).mockImplementation(
    () =>
      new Promise((resolve) => {
        done = resolve;
      }),
  );
  const { unmount, onChecked } = mount();
  fireEvent.click(screen.getByRole("button", { name: "检查本实验所需设备" }));
  await waitFor(() => expect(bindingOptions).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: "取消检查" }));
  await act(async () => done(options));
  expect(screen.queryByLabelText("设备与端点 · vna")).not.toBeInTheDocument();
  unmount();
  expect(onChecked).not.toHaveBeenCalled();
});
it("saves only by explicit name and preserves checked input on save failure", async () => {
  vi.mocked(saveSetupDefinition).mockRejectedValue(new Error("save failed"));
  mount();
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "检查并用于本次准备" }));
  await screen.findByText(/绑定检查通过/);
  fireEvent.click(screen.getByText("另存为可复用 setup（可选）"));
  fireEvent.change(screen.getByLabelText("复用名称"), { target: { value: "bench" } });
  fireEvent.click(screen.getByRole("button", { name: "保存为可复用 setup" }));
  await screen.findByRole("alert");
  expect(saveSetupDefinition).toHaveBeenCalledWith(definition, "bench", "alice");
  expect(screen.getByLabelText("复用名称")).toHaveValue("bench");
  expect(screen.getByLabelText("设备与端点 · vna")).toHaveValue("1");
});
it("withdraws a late checked response when disabled by navigation or preparation", async () => {
  let done!: (value: { definition: typeof definition; diagnostics: [] }) => void;
  vi.mocked(checkBindings).mockImplementation(
    () =>
      new Promise((resolve) => {
        done = resolve;
      }),
  );
  const { props, rerender, onChecked } = mount();
  await choose();
  fireEvent.click(screen.getByRole("button", { name: "检查并用于本次准备" }));
  rerender(<ProgressiveBindings {...props} disabled />);
  await act(async () => done({ definition, diagnostics: [] }));
  expect(onChecked).not.toHaveBeenCalled();
});

it("requires explicit nonempty simulation capabilities and retains them in the plan", async () => {
  const { onChecked } = mount();
  await choose();
  fireEvent.click(screen.getByLabelText("这是软件模拟，需要记录模型及局限"));
  for (const [field, value] of [
    ["模拟名称", "Synthetic VNA"],
    ["模型 ID", "scopecat.virtual.vna"],
    ["模型版本", "v1"],
    ["模拟局限（每行一项）", "Not physical evidence"],
  ])
    fireEvent.change(screen.getByLabelText(field!), { target: { value } });
  expect(screen.getByRole("button", { name: "检查并用于本次准备" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("模拟能力（每行一项）"), {
    target: { value: "Synthetic network sweep" },
  });
  fireEvent.click(screen.getByRole("button", { name: "检查并用于本次准备" }));
  await waitFor(() => expect(onChecked).toHaveBeenCalledOnce());
  // Validate the exact UI-produced payload against the Python contract, not only mocks.
  const payload = JSON.stringify(onChecked.mock.calls[0]![0].scenario);
  const validated = execFileSync(
    "uv",
    [
      "run",
      "--no-sync",
      "python",
      "-c",
      "import sys; from scopecat.records.execution_scenario import SoftwareExecutionScenario; print(SoftwareExecutionScenario.model_validate_json(sys.argv[1]).model_dump_json())",
      payload,
    ],
    { encoding: "utf8" },
  );
  expect(JSON.parse(validated).capabilities).toEqual(["Synthetic network sweep"]);
  expect(onChecked.mock.calls[0]![0].scenario).toEqual({
    kind: "software",
    id: "scopecat.virtual.vna",
    label: "Synthetic VNA",
    model_id: "scopecat.virtual.vna",
    model_version: "v1",
    capabilities: ["Synthetic network sweep"],
    limitations: ["Not physical evidence"],
  });
});
