import { readOperatorSettings } from "../application/OperatorSettings";
// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { hardwareReceiptResponse } from "./receipt-fixture.test-support";
import { ApiError } from "../../api-client";
import type {
  InstrumentInterface,
  InstrumentSession,
  InstrumentSessionLease,
  InstrumentState,
  InstrumentView,
} from "../../api-contract";
import { type SavedSetupRevision as SetupRevision } from "../config/setup-api";
import type { ConfigEntryView } from "../../api-contract";

import {
  getDevices,
  getDeviceDrivers,
  getDriverSource,
  prepareDeviceAccess,
  saveDevice,
  testDeviceConnection,
  retireDevice,
  renameDevice,
  releaseDeviceConnection,
  type DeviceView,
} from "./device-api";
vi.mock("./device-api", () => ({
  getDevices: vi.fn(),
  getDeviceDrivers: vi.fn(),
  getDriverSource: vi.fn(),
  updateDriverSource: vi.fn(),
  prepareDeviceAccess: vi.fn(),
  saveDevice: vi.fn(),
  testDeviceConnection: vi.fn(),
  retireDevice: vi.fn(),
  renameDevice: vi.fn(),
  releaseDeviceConnection: vi.fn(),
}));

import { InstrumentsWorkspace } from "./InstrumentsWorkspace";
import { InstrumentConfigDialog } from "./InstrumentConfigDialog";
import {
  abortInstrumentSession,
  applyInstrumentConfiguredDefaults,
  applyInstrumentState,
  closeInstrumentSession,
  collectInstrumentAcquisition,
  getDriverCatalog,
  getInstruments,
  invokeInstrumentOperation,
  openInstrumentSession,
  readInstrumentState,
  renewInstrumentSession,
  resolveInstrumentAttention,
} from "./instrument-api";

vi.mock("./instrument-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./instrument-api")>()),
  abortInstrumentSession: vi.fn(),
  applyInstrumentConfiguredDefaults: vi.fn(),
  applyInstrumentState: vi.fn(),
  closeInstrumentSession: vi.fn(),
  collectInstrumentAcquisition: vi.fn(),
  getDriverCatalog: vi.fn(),
  getInstruments: vi.fn(),
  invokeInstrumentOperation: vi.fn(),
  openInstrumentSession: vi.fn(),
  readInstrumentState: vi.fn(),
  renewInstrumentSession: vi.fn(),
  resolveInstrumentAttention: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  sessionStorage.clear();
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "local-operator" });
  vi.mocked(getDriverSource).mockResolvedValue({ active: null });
  mockInventory({
    setup: { revision_id: "lab-default", content_hash: "sha256:active" },
    problems: [],
    items: [instrument()],
  });
  vi.mocked(prepareDeviceAccess).mockResolvedValue(setupRevision());
  vi.mocked(getDeviceDrivers).mockResolvedValue({
    items: driverCatalog().drivers.map((item) => ({
      driver_id: item.driver_id,
      provider_id: "test",
      artifact_hash: "sha256:driver",
    })),
  });
  vi.mocked(saveDevice).mockResolvedValue(registeredDevice(instrument()));
  vi.mocked(renameDevice).mockResolvedValue(registeredDevice(instrument()));
  vi.mocked(retireDevice).mockResolvedValue({
    ...registeredDevice(instrument()).device,
    state: "retired",
  });
  vi.mocked(getDriverCatalog).mockResolvedValue(driverCatalog());
  vi.mocked(openInstrumentSession).mockResolvedValue(session());
  vi.mocked(renewInstrumentSession).mockResolvedValue(sessionLease());
  vi.mocked(readInstrumentState).mockResolvedValue(instrumentState());
  vi.mocked(applyInstrumentConfiguredDefaults).mockResolvedValue(
    configuredDefaultsReceipt("applied", instrumentState(6_000_000_000)),
  );
  vi.mocked(applyInstrumentState).mockResolvedValue({
    status: "applied",
    problems: [],
  });
  vi.mocked(closeInstrumentSession).mockResolvedValue();
  vi.mocked(abortInstrumentSession).mockResolvedValue();
  vi.mocked(collectInstrumentAcquisition).mockResolvedValue({
    status: "collected",
    problems: [],
    readback: { values: {} },
  });
  vi.mocked(invokeInstrumentOperation).mockResolvedValue({
    status: "invoked",
    problems: [],
  });
  vi.mocked(testDeviceConnection).mockResolvedValue({
    status: "connected",
    description: {
      instrument_id: "candidate",
      implementation_id: "virtual.rf_source",
      implementation_version: "v1",
      label: "Detected device",
      interfaces: [],
    },
    problems: [],
  });
  vi.mocked(resolveInstrumentAttention).mockResolvedValue();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("instrument workspace", () => {
  it("keeps configuration in the workbench and releases idle connections explicitly", async () => {
    vi.mocked(releaseDeviceConnection).mockResolvedValue({ instrument_ids: ["drive-source"] });
    renderWorkspace();
    await screen.findByRole("button", { name: "连接" });
    fireEvent.click(screen.getByRole("button", { name: "连接与环境" }));
    expect(openInstrumentSession).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "释放物理连接" }));
    await screen.findByText(/上次释放物理连接成功/);
    expect(releaseDeviceConnection).toHaveBeenCalledExactlyOnceWith("drive-source", {
      revision_id: "lab-default",
      content_hash: "sha256:active",
    });
    fireEvent.click(screen.getByRole("button", { name: "编辑设备" }));
    expect(await screen.findByRole("region", { name: "编辑设备" })).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(within(getConnectionEditor()).getByRole("button", { name: "取消" }));
    expect(queryConnectionEditor()).not.toBeInTheDocument();
    expect(saveDevice).not.toHaveBeenCalled();
  });

  it("reports failed physical release without retrying or claiming success", async () => {
    vi.mocked(releaseDeviceConnection).mockRejectedValue(new Error("connection retirement failed"));
    renderWorkspace();
    await screen.findByRole("button", { name: "连接" });
    fireEvent.click(screen.getByRole("button", { name: "连接与环境" }));
    fireEvent.click(screen.getByRole("button", { name: "释放物理连接" }));
    await screen.findByText("connection retirement failed");
    expect(releaseDeviceConnection).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/上次释放物理连接成功/)).not.toBeInTheDocument();
  });

  it("remembers each device tab and requires cancel before switching an edit draft", async () => {
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [instrument(), instrument({ instrument_id: "second" })],
    });
    renderWorkspace();
    await screen.findByRole("button", { name: "连接" });
    fireEvent.click(screen.getByRole("button", { name: "连接与环境" }));
    fireEvent.click(screen.getByRole("button", { name: "编辑设备" }));
    const editor = await findConnectionEditor();
    fireEvent.change(within(editor).getByLabelText("设备名称"), {
      target: { value: "Unsaved name" },
    });
    expect(screen.getByTitle("查看仪器 second")).toBeDisabled();
    fireEvent.click(screen.getByTitle("查看仪器 second"));
    expect(within(editor).getByLabelText("设备名称")).toHaveValue("Unsaved name");
    fireEvent.click(within(editor).getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByTitle("查看仪器 second"));
    expect(screen.getByRole("button", { name: "操作" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "诊断记录" }));
    fireEvent.click(screen.getByTitle("查看仪器 drive-source"));
    expect(screen.getByRole("button", { name: "连接与环境" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    cleanup();
    renderWorkspace();
    await screen.findByTitle("查看仪器 second");
    fireEvent.click(screen.getByTitle("查看仪器 second"));
    expect(screen.getByRole("button", { name: "诊断记录" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(saveDevice).not.toHaveBeenCalled();
    expect(openInstrumentSession).not.toHaveBeenCalled();
  });

  it("shows saved connection fields without editing and masks secrets", async () => {
    const catalog = driverCatalog();
    catalog.drivers[0]!.connections[0]!.options_schema = {
      type: "object",
      properties: {
        root: { type: "string", title: "SDK 目录" },
        addresses: { type: "object", title: "地址表", additionalProperties: { type: "string" } },
        api_token: { type: "string", title: "认证信息", writeOnly: true },
      },
    };
    vi.mocked(getDriverCatalog).mockResolvedValue(catalog);
    const device = registeredDevice(instrument());
    device.revision.content.connection.options = {
      root: "/opt/sdk",
      addresses: { main: "192.0.2.1" },
      api_token: "never-show-this",
      unknown: "hidden-extra",
    };
    vi.mocked(getDevices).mockResolvedValue({ items: [device] });
    renderWorkspace();
    await screen.findByRole("button", { name: "连接" });
    fireEvent.click(screen.getByRole("button", { name: "连接与环境" }));
    expect(await screen.findByText("/opt/sdk")).toBeVisible();
    expect(screen.getByText("main：192.0.2.1")).toBeVisible();
    expect(screen.getByText("已设置（已隐藏）")).toBeVisible();
    expect(screen.queryByText("never-show-this")).not.toBeInTheDocument();
    expect(screen.queryByText("hidden-extra")).not.toBeInTheDocument();
    expect(queryConnectionEditor()).not.toBeInTheDocument();
    expect(openInstrumentSession).not.toHaveBeenCalled();
  });

  it("opens registered devices without choosing an experiment setup", async () => {
    renderWorkspace();
    await screen.findByRole("button", { name: "连接" });
    expect(screen.queryByRole("combobox", { name: "Device context" })).not.toBeInTheDocument();
    expect(openInstrumentSession).not.toHaveBeenCalled();
    await connectInstrument();
    expect(openInstrumentSession).toHaveBeenCalledWith(
      "drive-source",
      "local-operator",
      { revision_id: "lab-default", content_hash: "sha256:active" },
      expect.any(String),
    );
  });

  it("shows unscoped provider problems at workspace level", async () => {
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [
        {
          code: "provider_unavailable",
          message: "The optional vendor provider could not be loaded.",
          phase: "provider_preflight",
          related_locations: [],
        },
      ],
      items: [instrument()],
    });

    renderWorkspace();

    expect(await screen.findByText("仪器 provider 问题")).toBeVisible();
    expect(screen.getByText("The optional vendor provider could not be loaded.")).toBeVisible();
  });

  it("lists connection and ownership without exposing internal identity", async () => {
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [
        instrument(),
        instrument({
          instrument_id: "vna-1",
          driver_id: "keysight.pna",
          connection: {
            kind: "tcpip_socket",
            host: "192.0.2.12",
            port: 5025,
          },
          description: {
            instrument_id: "vna-1",
            implementation_id: "keysight.pna",
            implementation_version: "0.1",
            label: "Readout VNA",
            interfaces: [],
          },
          availability: "active",
          owner_kind: "run",
          owner_id: "run-42",
          owner_actor: null,
        }),
      ],
    });

    renderWorkspace();

    expect((await screen.findAllByText("Drive source"))[0]).toBeVisible();
    expect(screen.getAllByText("虚拟 · 本地模拟器")[0]).toBeVisible();
    expect(screen.getByText("Readout VNA")).toBeVisible();
    expect(screen.getByText("TCP/IP · 192.0.2.12:5025")).toBeVisible();
    expect(screen.getByText("运行中")).toBeVisible();
    expect(screen.queryByText("run-42")).not.toBeInTheDocument();
    expect(screen.queryByText("keysight.pna")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Device context" })).not.toBeInTheDocument();
    expect(openInstrumentSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTitle("查看仪器 vna-1"));
    expect(await screen.findByText("手动控制不可用")).toBeVisible();
    expect(screen.getByRole("button", { name: "编辑设备" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "连接" })).not.toBeInTheDocument();
  });

  it("keeps driver ABI details out of the ordinary interface view", async () => {
    const prefixed = instrument();
    prefixed.description = {
      ...prefixed.description!,
      implementation_version: "v1",
    };
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [prefixed],
    });

    renderWorkspace();

    const heading = await screen.findByTestId("interface-heading");
    expect(within(heading).queryByText("virtual.rf_source")).not.toBeInTheDocument();
    expect(within(heading).queryByText("v1")).not.toBeInTheDocument();
  });

  it("uses session-open state without another read, refreshes explicitly, and closes", async () => {
    vi.mocked(readInstrumentState).mockResolvedValueOnce(instrumentState(6_000_000_000));
    const rendered = renderWorkspace();

    await screen.findAllByText("Drive source");
    expect(openInstrumentSession).not.toHaveBeenCalled();
    await connectInstrument();

    await waitFor(() =>
      expect(openInstrumentSession).toHaveBeenCalledWith(
        "drive-source",
        "local-operator",
        { revision_id: "lab-default", content_hash: "sha256:active" },
        expect.stringMatching(/^ui-open-/),
      ),
    );
    expect(readInstrumentState).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue("5000000000")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() =>
      expect(readInstrumentState).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: "session-1" }),
        "drive-source",
      ),
    );
    expect(await screen.findByDisplayValue("6000000000")).toBeVisible();
    expect(screen.queryByText("session-1")).not.toBeInTheDocument();

    rendered.unmount();

    await waitFor(() => expect(closeInstrumentSession).toHaveBeenCalledWith("session-1", true));
  });

  it("schedules from authoritative lease time and drops a current session on failure", async () => {
    vi.useFakeTimers();
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({
        renewed_at: "2026-07-27T09:00:00Z",
        expires_at: "2026-07-27T09:00:30Z",
      }),
    );
    vi.mocked(renewInstrumentSession)
      .mockResolvedValueOnce(
        sessionLease({
          renewed_at: "2026-07-27T09:00:10Z",
          expires_at: "2026-07-27T09:01:40Z",
        }),
      )
      .mockRejectedValueOnce(new Error("daemon unavailable"));
    renderWorkspace();

    await vi.waitFor(() => expect(screen.getByRole("button", { name: "连接" })).toBeVisible());
    vi.setSystemTime(new Date("2026-07-27T09:00:05Z"));
    fireEvent.click(screen.getByRole("button", { name: "连接" }));
    await vi.waitFor(() => expect(screen.getByText("交互会话已连接")).toBeVisible());

    await act(async () => vi.advanceTimersByTimeAsync(4_000));
    expect(renewInstrumentSession).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(renewInstrumentSession).toHaveBeenCalledOnce();
    expect(renewInstrumentSession).toHaveBeenLastCalledWith("session-1");

    await act(async () => vi.advanceTimersByTimeAsync(29_000));
    expect(renewInstrumentSession).toHaveBeenCalledOnce();
    await act(async () => vi.advanceTimersByTimeAsync(1_000));

    expect(renewInstrumentSession).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/设备会话已中断/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "连接" })).toBeVisible();
  });

  it("shows and applies only session-authoritative configured defaults", async () => {
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ configured_default_instrument_ids: ["drive-source"] }),
    );
    vi.mocked(readInstrumentState).mockResolvedValueOnce(instrumentState(6_000_000_000));
    renderWorkspace();

    await screen.findAllByText("Drive source");
    expect(screen.queryByRole("button", { name: "应用已配置默认值" })).not.toBeInTheDocument();
    await connectInstrument();
    const applyDefaults = await screen.findByRole("button", {
      name: "应用已配置默认值",
    });
    fireEvent.click(applyDefaults);

    await waitFor(() =>
      expect(applyInstrumentConfiguredDefaults).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: "session-1" }),
        "drive-source",
        expect.stringMatching(/^ui-configured-defaults-/),
      ),
    );
    expect(await screen.findByText("已应用配置的默认值。")).toBeVisible();
    expect(screen.getByRole("spinbutton", { name: /CW frequency/ })).toHaveValue(6_000_000_000);
  });

  it("clears stale operation results after applying configured defaults", async () => {
    const withOperations = instrumentWithOperations();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [withOperations],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({
        configured_default_instrument_ids: ["drive-source"],
        descriptions: [withOperations.description!],
      }),
    );
    renderWorkspace();

    await screen.findByText("Reset fault");
    await connectInstrument();
    fireEvent.click(await screen.findByRole("button", { name: "调用 Reset fault" }));
    expect(await screen.findByText("Invoke 回执： 已调用")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "应用已配置默认值" }));
    expect(await screen.findByText("已应用配置的默认值。")).toBeVisible();
    expect(screen.queryByText("Invoke 回执： 已调用")).not.toBeInTheDocument();
  });

  it("hides configured defaults when the pinned session has none", async () => {
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();

    expect(screen.queryByRole("button", { name: "应用已配置默认值" })).not.toBeInTheDocument();
  });

  it("disables configured defaults for staged values and pending interactions", async () => {
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ configured_default_instrument_ids: ["drive-source"] }),
    );
    let finishCollect: (() => void) | undefined;
    vi.mocked(collectInstrumentAcquisition).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishCollect = () =>
            resolve({
              status: "collected",
              problems: [],
              readback: { values: {} },
            });
        }),
    );
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    const frequency = await screen.findByRole("spinbutton", { name: /CW frequency/ });
    const applyDefaults = screen.getByRole("button", {
      name: "应用已配置默认值",
    });

    fireEvent.change(frequency, { target: { value: "6000000000" } });
    expect(applyDefaults).toBeDisabled();
    expect(applyDefaults).toHaveAttribute("title", "请先应用或重置暂存属性");

    fireEvent.click(screen.getByRole("button", { name: "收集 Collect" }));
    await waitFor(() => expect(collectInstrumentAcquisition).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "应用暂存修改" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "刷新状态" })).toBeDisabled();
    expect(applyDefaults).toBeDisabled();
    expect(applyDefaults).toHaveAttribute("title", "请先应用或重置暂存属性");
    finishCollect?.();
    await waitFor(() => expect(screen.getByRole("button", { name: "应用暂存修改" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "重置" }));
    expect(applyDefaults).toBeEnabled();
  });

  it("blocks other device interactions while configured defaults are pending", async () => {
    const withOperations = instrumentWithOperations();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [withOperations],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({
        configured_default_instrument_ids: ["drive-source"],
        descriptions: [withOperations.description!],
      }),
    );
    let finishDefaults:
      | ((receipt: Awaited<ReturnType<typeof applyInstrumentConfiguredDefaults>>) => void)
      | undefined;
    vi.mocked(applyInstrumentConfiguredDefaults).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishDefaults = resolve;
        }),
    );
    renderWorkspace();
    await screen.findByText("Reset fault");
    await connectInstrument();
    const applyDefaults = await screen.findByRole("button", {
      name: "应用已配置默认值",
    });
    await waitFor(() => expect(applyDefaults).toBeEnabled());

    fireEvent.click(applyDefaults);
    await waitFor(() => expect(applyInstrumentConfiguredDefaults).toHaveBeenCalledOnce());

    expect(screen.getByRole("button", { name: "刷新状态" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "释放控制" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "收集 Collect" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "调用 Reset fault" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: /CW frequency/ })).toBeDisabled();
    expect(applyDefaults).toHaveAttribute("title", "请等待当前仪器交互完成");

    finishDefaults?.(configuredDefaultsReceipt("unchanged", instrumentState()));
    await waitFor(() => expect(screen.getByRole("button", { name: "刷新状态" })).toBeEnabled());
  });

  it("shows configured-default rejection problems without losing the session", async () => {
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ configured_default_instrument_ids: ["drive-source"] }),
    );
    vi.mocked(applyInstrumentConfiguredDefaults).mockResolvedValueOnce({
      session_id: "session-1",
      operation_id: "defaults-rejected",
      instrument_id: "drive-source",
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      status: "rejected",
      problems: [
        {
          code: "driver_rejected",
          message: "Driver refused the configured state.",
          phase: "execution",
          related_locations: [],
        },
      ],
    });
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    fireEvent.click(await screen.findByRole("button", { name: "应用已配置默认值" }));

    expect(
      await screen.findByText("已拒绝配置的默认值： Driver refused the configured state."),
    ).toBeVisible();
    expect(screen.getByText("交互会话已连接")).toBeVisible();
    expect(screen.queryByRole("button", { name: "连接" })).not.toBeInTheDocument();
  });

  it("reuses the configured-default operation id while retrying a network failure", async () => {
    mockInstrumentSessionOwnership();
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ configured_default_instrument_ids: ["drive-source"] }),
    );
    vi.mocked(applyInstrumentConfiguredDefaults)
      .mockRejectedValueOnce(new ApiError("Defaults request lost."))
      .mockResolvedValueOnce(configuredDefaultsReceipt("unchanged", instrumentState()));
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    const applyDefaults = await screen.findByRole("button", {
      name: "应用已配置默认值",
    });

    fireEvent.click(applyDefaults);

    await waitFor(() => expect(applyInstrumentConfiguredDefaults).toHaveBeenCalledTimes(2));
    expect(vi.mocked(applyInstrumentConfiguredDefaults).mock.calls[0]?.[2]).toBe(
      vi.mocked(applyInstrumentConfiguredDefaults).mock.calls[1]?.[2],
    );
    expect(await screen.findByText("状态已与配置的默认值一致。")).toBeVisible();
  });

  it("refreshes ownership after a configured-default conflict", async () => {
    mockInstrumentSessionOwnership({ releasedAfterRefresh: true });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ configured_default_instrument_ids: ["drive-source"] }),
    );
    vi.mocked(applyInstrumentConfiguredDefaults).mockRejectedValueOnce(
      new ApiError("The session is no longer active.", 409),
    );
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    fireEvent.click(await screen.findByRole("button", { name: "应用已配置默认值" }));

    expect(await screen.findByText("应用已配置默认值时，交互会话已结束。")).toBeVisible();
    expect(screen.getByRole("button", { name: "连接" })).toBeVisible();
  });

  it("allows an operator to disconnect a daemon-owned interactive session", async () => {
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [
        instrument({
          availability: "active",
          owner_kind: "instrument_session",
          owner_id: "session-stale",
          owner_actor: "Grace",
        }),
      ],
    });
    renderWorkspace();

    expect(await screen.findByText("手动控制不可用")).toBeVisible();
    expect(screen.queryByText("session-stale")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "断开会话" }));

    await waitFor(() => expect(abortInstrumentSession).toHaveBeenCalledWith("session-stale"));
  });

  it("stages typed properties locally and sends one apply command", async () => {
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    const frequency = await screen.findByRole("spinbutton", {
      name: /CW frequency/,
    });
    const readOnly = screen.getByRole("spinbutton", {
      name: /Measured temperature/,
    });
    expect(readOnly).toBeDisabled();

    fireEvent.change(frequency, { target: { value: "6000000000" } });

    expect(applyInstrumentState).not.toHaveBeenCalled();
    expect(screen.getByText("已暂存 1 项属性")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "应用暂存修改" }));

    await waitFor(() =>
      expect(applyInstrumentState).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: "session-1" }),
        "drive-source",
        [
          {
            target: {
              kind: "interface",
              interface_id: "scopecat.rf_output/v1",
              component_path: [],
              property_id: "frequency",
            },
            value: { value: 6_000_000_000, unit: "Hz" },
          },
        ],
        expect.stringMatching(/^ui-apply-/),
      ),
    );
    expect(await screen.findByText("应用回执： 已应用")).toBeVisible();
  });

  it("renders every flat interface property in declaration order", async () => {
    const flatInstrument = instrumentWithFlatDcState();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [flatInstrument],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({
        descriptions: [flatInstrument.description!],
        observed_state: [flatDcInstrumentState()],
      }),
    );
    renderWorkspace();

    await screen.findByText("DC source");
    expect(screen.getByRole("combobox", { name: /Source mode/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /DC output/ })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: /Voltage range/ })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: /Current range/ })).toBeDisabled();

    await connectInstrument();
    await waitFor(() =>
      expect(screen.getByRole("spinbutton", { name: /Voltage range/ })).toHaveValue(5),
    );
    expect(screen.getByRole("spinbutton", { name: /Current range/ })).toHaveValue(0.1);
    expect(screen.getByRole("combobox", { name: /Source mode/ })).toHaveValue("voltage");

    const card = screen
      .getByRole("heading", { name: "DC source", level: 4 })
      .closest('[data-testid^="interface-card-"]');
    if (!card) throw new Error("Expected the DC source interface card.");
    expect(
      within(card as HTMLElement)
        .getAllByTestId("property-label")
        .map((element) => element.textContent),
    ).toEqual(["Source mode", "DC output", "Voltage range", "Current range"]);
  });

  it("uses physical interface mounts, implementation overrides, and device state targets", async () => {
    const mountedInstrument = instrumentWithMountedAndDeviceState();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [mountedInstrument],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({
        descriptions: [mountedInstrument.description!],
        observed_state: [mountedAndDeviceInstrumentState()],
      }),
    );
    renderWorkspace();

    await screen.findByText("Model-specific state");
    await connectInstrument();
    const frequencies = screen.getAllByRole("spinbutton", { name: /CW frequency/ });
    expect(frequencies).toHaveLength(2);
    expect(frequencies[0]).toHaveValue(5_000_000_000);
    expect(frequencies[0]).toBeEnabled();
    expect(frequencies[1]).toHaveValue(6_000_000_000);
    expect(frequencies[1]).toBeDisabled();
    expect(screen.getByRole("textbox", { name: /Serial number/ })).toHaveValue("SN-42");
    expect(screen.getByRole("textbox", { name: /Serial number/ })).toBeDisabled();

    fireEvent.change(frequencies[0]!, { target: { value: "5100000000" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: /Internal LO offset/ }), {
      target: { value: "2000000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "应用暂存修改" }));

    await waitFor(() =>
      expect(applyInstrumentState).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: "session-1" }),
        "drive-source",
        [
          {
            target: {
              kind: "interface",
              interface_id: "scopecat.rf_output/v1",
              component_path: ["channels", "ch1"],
              property_id: "frequency",
            },
            value: { value: 5_100_000_000, unit: "Hz" },
          },
          {
            target: {
              kind: "device",
              schema_id: "example.model_state/v1",
              component_path: [],
              property_id: "internal_lo_offset",
            },
            value: { value: 2_000_000, unit: "Hz" },
          },
        ],
        expect.stringMatching(/^ui-apply-/),
      ),
    );
  });

  it("applies only explicitly staged flat properties", async () => {
    const flatInstrument = instrumentWithFlatDcState();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [flatInstrument],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({
        descriptions: [flatInstrument.description!],
        observed_state: [flatDcInstrumentState()],
      }),
    );
    vi.mocked(applyInstrumentState).mockResolvedValueOnce(flatDcApplyReceipt());
    vi.mocked(readInstrumentState).mockResolvedValueOnce(flatDcInstrumentState("current", 0.2));
    renderWorkspace();

    await screen.findByText("DC source");
    await connectInstrument();
    fireEvent.change(await screen.findByRole("combobox", { name: /Source mode/ }), {
      target: { value: "current" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: /Current range/ }), {
      target: { value: "0.2" },
    });

    const apply = screen.getByRole("button", { name: "应用暂存修改" });
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() =>
      expect(applyInstrumentState).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: "session-1" }),
        "drive-source",
        [
          {
            target: {
              kind: "interface",
              interface_id: "scopecat.dc_source/v2",
              component_path: [],
              property_id: "source_mode",
            },
            value: "current",
          },
          {
            target: {
              kind: "interface",
              interface_id: "scopecat.dc_source/v2",
              component_path: [],
              property_id: "current_range",
            },
            value: { value: 0.2, unit: "A" },
          },
        ],
        expect.stringMatching(/^ui-apply-/),
      ),
    );
  });

  it("keeps flat property drafts independent when one is reset", async () => {
    const flatInstrument = instrumentWithFlatDcState();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [flatInstrument],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({
        descriptions: [flatInstrument.description!],
        observed_state: [flatDcInstrumentState()],
      }),
    );
    renderWorkspace();

    await screen.findByText("DC source");
    await connectInstrument();
    const voltageRange = await screen.findByRole("spinbutton", { name: /Voltage range/ });
    fireEvent.change(voltageRange, {
      target: { value: "8" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: /Current range/ }), {
      target: { value: "0.2" },
    });

    const voltageEditor = voltageRange.closest('[data-testid^="interface-property-"]');
    expect(voltageEditor).not.toBeNull();
    fireEvent.click(
      within(voltageEditor as HTMLElement).getByRole("button", {
        name: "重置暂存值",
      }),
    );

    expect(screen.getByRole("spinbutton", { name: /Voltage range/ })).toHaveValue(5);
    expect(screen.getByRole("spinbutton", { name: /Current range/ })).toHaveValue(0.2);
    expect(screen.getByText("已暂存 1 项属性")).toBeVisible();
    expect(applyInstrumentState).not.toHaveBeenCalled();
  });

  it.each(["available", "active", "quarantined", "unavailable"] as const)(
    "keeps payload-only nested capabilities discoverable without I/O when %s",
    async (availability) => {
      const view = instrumentWithOperations();
      const original = view.description!.interfaces![0]!;
      const payload = original.operations!.find((operation) => operation.id === "upload_waveform")!;
      view.availability = availability;
      view.owner_kind = availability === "active" ? "run" : null;
      view.description!.interfaces = [
        {
          id: original.id,
          label: "Program device",
          components: [{ id: "channel", label: "Program channel", operations: [payload] }],
        },
      ];
      mockInventory({
        setup: { revision_id: "lab-default", content_hash: "sha256:active" },
        problems: [],
        items: [view],
      });
      renderWorkspace();
      expect(await screen.findByText("Upload waveform")).toBeVisible();
      expect(screen.getByText("Program channel")).toBeVisible();
      fireEvent.click(screen.getByText("如何准备与调用"));
      expect(screen.getByText(/在作者 Python 程序中按驱动契约准备 payload/)).toBeVisible();
      expect(screen.getAllByRole("link", { name: /Python 设备会话/ })[0]).toHaveAttribute(
        "href",
        "https://scopecat-project.github.io/scopecat/how-to/control-instruments/#use-a-configured-device-now",
      );
      expect(screen.queryByRole("button", { name: /调用 Invoke/ })).not.toBeInTheDocument();
      expect(openInstrumentSession).not.toHaveBeenCalled();
      expect(readInstrumentState).not.toHaveBeenCalled();
      expect(invokeInstrumentOperation).not.toHaveBeenCalled();
      expect(collectInstrumentAcquisition).not.toHaveBeenCalled();
      expect(applyInstrumentState).not.toHaveBeenCalled();
      expect(testDeviceConnection).not.toHaveBeenCalled();
      const reasons = {
        available: /本页尚未连接/,
        active: /设备正被占用/,
        quarantined: /设备状态不确定/,
        unavailable: /设备不可用。请先处理/,
      };
      expect(screen.getByText(reasons[availability])).toBeVisible();
    },
  );

  it.each([false, true])(
    "explains empty capability declarations (interface present: %s)",
    async (hasInterface) => {
      const view = instrument();
      view.description!.interfaces = hasInterface
        ? [{ id: "test.empty/v1", label: "Empty interface" }]
        : [];
      mockInventory({
        setup: { revision_id: "lab-default", content_hash: "sha256:active" },
        problems: [],
        items: [view],
      });
      renderWorkspace();
      expect(
        await screen.findByText(hasInterface ? /此接口未声明属性/ : /此设备未声明交互能力/),
      ).toBeVisible();
      expect(openInstrumentSession).not.toHaveBeenCalled();
    },
  );

  it.each([
    ["sample", "仅收集上一次执行的结果；请先通过作者程序完成执行。"],
    ["collect_previous", "发起一次新采集并返回结果。"],
    ["execute", undefined],
  ])(
    "uses acquisition descriptions without inferring behavior from %s",
    async (id, description) => {
      const view = instrument();
      const acquisition = view.description!.interfaces![0]!.acquisitions![0]!;
      acquisition.id = id!;
      acquisition.description = description;
      mockInventory({
        setup: { revision_id: "lab-default", content_hash: "sha256:active" },
        problems: [],
        items: [view],
      });
      renderWorkspace();
      expect(
        await screen.findByText(
          description ? `采集契约：${description}` : /此 acquisition 未说明是否发起新采集/,
        ),
      ).toBeVisible();
      expect(screen.getByRole("button", { name: "收集 Collect" })).toBeDisabled();
      expect(collectInstrumentAcquisition).not.toHaveBeenCalled();
      expect(invokeInstrumentOperation).not.toHaveBeenCalled();
      expect(openInstrumentSession).not.toHaveBeenCalled();
    },
  );

  it("fills typed operation arguments locally and invokes once outside staged apply", async () => {
    const withOperations = instrumentWithOperations();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [withOperations],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ descriptions: [withOperations.description!] }),
    );
    vi.mocked(readInstrumentState).mockResolvedValueOnce(instrumentState(7_000_000_000));
    renderWorkspace();

    await screen.findByText("Configure trigger");
    expect(screen.getByRole("button", { name: "调用 Configure trigger" })).toBeDisabled();
    expect(screen.getByText("Upload waveform")).toBeVisible();
    expect(screen.queryByRole("button", { name: "调用 Upload waveform" })).not.toBeInTheDocument();
    expect(screen.queryByText("payload_id")).not.toBeInTheDocument();
    expect(screen.queryByText("waveform/v1")).not.toBeInTheDocument();

    await connectInstrument();
    expect(screen.getByText("Upload waveform")).toBeVisible();
    expect(screen.queryByRole("button", { name: "调用 Upload waveform" })).not.toBeInTheDocument();
    fireEvent.change(await screen.findByRole("combobox", { name: /Enable correction/ }), {
      target: { value: "true" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: /Average count/ }), {
      target: { value: "3" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: /Threshold/ }), {
      target: { value: "0.75" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: /Profile name/ }), {
      target: { value: "fast" },
    });
    fireEvent.change(screen.getByRole("spinbutton", { name: /Settling time/ }), {
      target: { value: "0.25" },
    });

    expect(screen.queryByText(/已暂存/)).not.toBeInTheDocument();
    expect(applyInstrumentState).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "调用 Configure trigger" }));

    await waitFor(() => expect(invokeInstrumentOperation).toHaveBeenCalledOnce());
    expect(invokeInstrumentOperation).toHaveBeenCalledWith(
      expect.objectContaining({ session_id: "session-1" }),
      "drive-source",
      expect.objectContaining({
        interfaceId: "scopecat.rf_output/v1",
        componentPath: [],
        operation: expect.objectContaining({ id: "configure_trigger" }),
      }),
      [
        { id: "enabled", value: true },
        { id: "averages", value: 3 },
        { id: "threshold", value: 0.75 },
        { id: "profile", value: "fast" },
        { id: "settling", value: { value: 0.25, unit: "s" } },
      ],
      expect.stringMatching(/^ui-invoke-/),
    );
    expect(await screen.findByText("Invoke 回执： 已调用")).toBeVisible();
    expect(screen.getByRole("spinbutton", { name: /CW frequency/ })).toHaveValue(7_000_000_000);
    const commandId = vi.mocked(invokeInstrumentOperation).mock.calls[0]?.[4];
    expect(commandId).toBeDefined();
    expect(screen.queryByText(commandId!)).not.toBeInTheDocument();
    expect(screen.queryByText("session-1")).not.toBeInTheDocument();
    expect(applyInstrumentState).not.toHaveBeenCalled();
  });

  it("uses the existing quarantine semantics for an unknown invoke receipt", async () => {
    const withOperations = instrumentWithOperations();
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [withOperations],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ descriptions: [withOperations.description!] }),
    );
    vi.mocked(invokeInstrumentOperation).mockResolvedValueOnce({
      status: "unknown",
      problems: [
        {
          code: "instrument_invoke_unknown",
          message: "The hardware may have accepted the operation.",
          phase: "execution",
          related_locations: [],
        },
      ],
    });
    renderWorkspace();

    await screen.findByText("Reset fault");
    await connectInstrument();
    fireEvent.click(await screen.findByRole("button", { name: "调用 Reset fault" }));

    expect(
      await screen.findByText("操作结果未知。后台服务已隔离此会话，需操作者核对。"),
    ).toBeVisible();
    expect(screen.queryByText("session-1")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "连接" })).toBeVisible();
  });

  it("displays a current Python HTTP receipt through the real collection API", async () => {
    const actual = await vi.importActual<typeof import("./instrument-api")>("./instrument-api");
    vi.mocked(collectInstrumentAcquisition).mockImplementation(actual.collectInstrumentAcquisition);
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(hardwareReceiptResponse()));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    fireEvent.click(await screen.findByRole("button", { name: "收集 Collect" }));
    expect(await screen.findByText("Collect 回执： 已采集")).toBeVisible();
    expect(screen.getByText("15 项返回结果")).toBeVisible();
    expect(screen.getByText("2 项结果不可用")).toBeVisible();
    expect(screen.getByText("原因： 无效、缺失")).toBeVisible();
    expect(screen.getByRole("img", { name: /轨迹预览/i })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it.each([
    [1, "收集结果未知。后台服务已隔离此会话，需操作者核对。"],
    [2, "Collect 回执： 未采集"],
  ] as const)("handles current Python receipt status %i", async (index, message) => {
    const actual = await vi.importActual<typeof import("./instrument-api")>("./instrument-api");
    vi.mocked(collectInstrumentAcquisition).mockImplementation(actual.collectInstrumentAcquisition);
    const fetchMock = vi
      .fn()
      .mockImplementation(() => Promise.resolve(hardwareReceiptResponse(index)));
    vi.stubGlobal("fetch", fetchMock);
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    fireEvent.click(await screen.findByRole("button", { name: "收集 Collect" }));
    expect(await screen.findByText(message)).toBeVisible();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("summarizes unavailable collect results without plotting them", async () => {
    vi.mocked(collectInstrumentAcquisition).mockResolvedValueOnce({
      status: "collected",
      problems: [],
      readback: {
        values: {
          "private-overload-result": {
            kind: "unavailable",
            reason: "overload",
            dtype: "float64",
            unit: "ratio",
            shape: [],
            metadata: {},
          },
          "private-missing-result": {
            kind: "unavailable",
            reason: "missing",
            dtype: "float64",
            unit: "ratio",
            shape: [128],
            metadata: {},
          },
        },
      },
    });
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    fireEvent.click(await screen.findByRole("button", { name: "收集 Collect" }));

    const summary = await screen.findByRole("status");
    expect(within(summary).getByText("2 项结果不可用")).toBeVisible();
    expect(within(summary).getByText("原因： 缺失、过载")).toBeVisible();
    expect(summary).not.toHaveTextContent("private-overload-result");
    expect(summary).not.toHaveTextContent("private-missing-result");
    expect(screen.queryByRole("img", { name: /轨迹预览/i })).not.toBeInTheDocument();
    expect(screen.queryByText("JSON preview")).not.toBeInTheDocument();
  });

  it("reuses command ids while retrying mutations", async () => {
    vi.mocked(applyInstrumentState).mockRejectedValueOnce(new Error("Apply network failed."));
    vi.mocked(collectInstrumentAcquisition).mockRejectedValueOnce(
      new Error("Collect network failed."),
    );
    vi.mocked(closeInstrumentSession).mockRejectedValueOnce(new ApiError("本地后台服务未响应。"));
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    const frequency = await screen.findByRole("spinbutton", { name: /CW frequency/ });
    fireEvent.change(frequency, { target: { value: "6000000000" } });

    fireEvent.click(screen.getByRole("button", { name: "应用暂存修改" }));
    expect(await screen.findByText("Apply network failed.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "应用暂存修改" }));
    await waitFor(() => expect(applyInstrumentState).toHaveBeenCalledTimes(2));
    expect(vi.mocked(applyInstrumentState).mock.calls[0]?.[3]).toBe(
      vi.mocked(applyInstrumentState).mock.calls[1]?.[3],
    );

    fireEvent.click(screen.getByRole("button", { name: "收集 Collect" }));
    expect(await screen.findByText("Collect network failed.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "收集 Collect" }));
    await waitFor(() => expect(collectInstrumentAcquisition).toHaveBeenCalledTimes(2));
    expect(vi.mocked(collectInstrumentAcquisition).mock.calls[0]?.[3]).toBe(
      vi.mocked(collectInstrumentAcquisition).mock.calls[1]?.[3],
    );

    fireEvent.click(screen.getByRole("button", { name: "释放控制" }));
    await waitFor(() => expect(closeInstrumentSession).toHaveBeenCalledTimes(2), {
      timeout: 2_000,
    });
    expect(vi.mocked(closeInstrumentSession).mock.calls).toEqual([["session-1"], ["session-1"]]);
  });

  it("starts a new collect operation after an applied state change", async () => {
    vi.mocked(collectInstrumentAcquisition).mockRejectedValueOnce(
      new Error("Collect request lost."),
    );
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    const frequency = await screen.findByRole("spinbutton", { name: /CW frequency/ });

    fireEvent.click(screen.getByRole("button", { name: "收集 Collect" }));
    expect(await screen.findByText("Collect request lost.")).toBeVisible();
    const staleCollectCommandId = vi.mocked(collectInstrumentAcquisition).mock.calls[0]?.[3];

    fireEvent.change(frequency, { target: { value: "6000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "应用暂存修改" }));
    expect(await screen.findByText("应用回执： 已应用")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "收集 Collect" }));

    await waitFor(() => expect(collectInstrumentAcquisition).toHaveBeenCalledTimes(2));
    expect(vi.mocked(collectInstrumentAcquisition).mock.calls[1]?.[3]).not.toBe(
      staleCollectCommandId,
    );
  });

  it("delegates fixed acquisition planning while showing every declared result", async () => {
    const delegatedInstrument = instrument();
    const description = delegatedInstrument.description!;
    const instrumentInterface = description.interfaces![0]!;
    delegatedInstrument.description = {
      ...description,
      interfaces: [
        {
          ...instrumentInterface,
          acquisitions: [
            {
              id: "monitor",
              label: "Monitor",
              preconditions: [
                {
                  property: {
                    interface_id: instrumentInterface.id,
                    component_path: [],
                    property_id: "output_enabled",
                  },
                  value: true,
                  unavailable_reason: "Enable RF output before collecting.",
                },
              ],
              results: [
                {
                  id: "current",
                  label: "Current sample",
                  dtype: "float64",
                  role: "observable",
                  unit: "A",
                  axes: [
                    {
                      id: "sample",
                      kind: "sample",
                      size: {
                        interface_id: instrumentInterface.id,
                        component_path: [],
                        property_id: "points",
                      },
                    },
                  ],
                },
                {
                  id: "voltage",
                  label: "Voltage sample",
                  dtype: "float64",
                  role: "observable",
                  unit: "V",
                  axes: [],
                },
              ],
            },
          ],
        },
      ],
    };
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [delegatedInstrument],
    });
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ descriptions: [delegatedInstrument.description] }),
    );

    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();

    expect(await screen.findByText("Current sample")).toBeVisible();
    expect(screen.getByText("Voltage sample")).toBeVisible();
    expect(screen.queryByText("Enable RF output before collecting.")).not.toBeInTheDocument();
    const collect = screen.getByRole("button", { name: "收集 Collect" });
    expect(collect).toBeEnabled();
    fireEvent.click(collect);

    await waitFor(() => expect(collectInstrumentAcquisition).toHaveBeenCalledOnce());
    const call = vi.mocked(collectInstrumentAcquisition).mock.calls[0]!;
    expect(call[1]).toBe("drive-source");
    expect(call[2]).toMatchObject({
      interfaceId: instrumentInterface.id,
      componentPath: [],
      acquisition: { id: "monitor" },
    });
    expect(call[3]).toMatch(/^ui-collect-/);
  });
  it("keeps the session available after close retries fail", async () => {
    vi.mocked(closeInstrumentSession)
      .mockRejectedValueOnce(new ApiError("Close request lost."))
      .mockRejectedValueOnce(new ApiError("Close request lost again."));
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();

    fireEvent.click(screen.getByRole("button", { name: "释放控制" }));

    expect(await screen.findByText("Close request lost again.")).toBeVisible();
    expect(screen.getByText("交互会话已连接")).toBeVisible();
    expect(screen.getByRole("button", { name: "释放控制" })).toBeEnabled();
    expect(closeInstrumentSession).toHaveBeenCalledTimes(2);
    expect(vi.mocked(closeInstrumentSession).mock.calls[0]).toEqual(["session-1"]);
    expect(vi.mocked(closeInstrumentSession).mock.calls[1]).toEqual(["session-1"]);

    fireEvent.click(screen.getByRole("button", { name: "释放控制" }));

    await waitFor(() => expect(closeInstrumentSession).toHaveBeenCalledTimes(3));
    expect(vi.mocked(closeInstrumentSession).mock.calls[2]).toEqual(["session-1"]);
    expect(await screen.findByRole("button", { name: "连接" })).toBeVisible();
  });

  it("stays on the connected instrument when closing before selection fails", async () => {
    const monitor = instrument({
      instrument_id: "monitor",
      driver_id: "virtual.temperature",
      connection: { kind: "virtual" },
      description: {
        instrument_id: "monitor",
        implementation_id: "virtual.temperature",
        implementation_version: "v1",
        label: "Fridge monitor",
        interfaces: [],
      },
    });
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [instrument(), monitor],
    });
    vi.mocked(closeInstrumentSession).mockRejectedValueOnce(new Error("Switch close failed."));
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();

    fireEvent.click(screen.getByTitle("查看仪器 monitor"));

    expect(await screen.findByText(/Switch close failed/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Drive source", level: 2 })).toBeVisible();
    expect(vi.mocked(closeInstrumentSession).mock.calls[0]).toEqual(["session-1"]);

    fireEvent.click(screen.getByTitle("查看仪器 monitor"));

    expect(await screen.findByRole("heading", { name: "Fridge monitor", level: 2 })).toBeVisible();
    expect(vi.mocked(closeInstrumentSession).mock.calls[1]).toEqual(["session-1"]);
  });

  it("edits registered connection options without opening a session", async () => {
    const device = instrument({
      driver_id: "keysight.pna",
      connection: { kind: "tcpip_socket", host: "192.0.2.20", port: 5025 },
    });
    mockInventory({ setup: session().setup, items: [device], problems: [] });
    const registered = registeredDevice(device);
    registered.revision.content.connection.options = {
      channel: 1,
      vendor_extension: { calibration: "external" },
    };
    vi.mocked(getDevices).mockResolvedValue({ items: [registered] });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "编辑设备" }));
    const dialog = await findConnectionEditor();
    fireEvent.change(within(dialog).getByLabelText("主机"), { target: { value: "192.0.2.24" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存设备" }));
    await waitFor(() =>
      expect(saveDevice).toHaveBeenCalledWith(
        expect.objectContaining({
          device_id: "drive-source",
          expected_head: registeredDevice(device).device.head,
          connection: expect.objectContaining({
            connection: expect.objectContaining({
              host: "192.0.2.24",
              options: { channel: 1, vendor_extension: { calibration: "external" } },
            }),
          }),
        }),
      ),
    );
    expect(openInstrumentSession).not.toHaveBeenCalled();
  });

  it("explains missing operator attribution before submitting a device connection", async () => {
    vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 0, operator: "" });
    vi.mocked(getDevices).mockResolvedValue({
      items: [
        registeredDevice(
          instrument({
            driver_id: "keysight.pna",
            connection: { kind: "tcpip_socket", host: "192.0.2.20", port: 5025 },
          }),
        ),
      ],
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "编辑设备" }));
    const dialog = await findConnectionEditor();
    fireEvent.change(within(dialog).getByLabelText("主机"), { target: { value: "192.0.2.24" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存设备" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "请先在应用设置中保存操作者名称，再保存设备。",
    );
    expect(saveDevice).not.toHaveBeenCalled();
    expect(openInstrumentSession).not.toHaveBeenCalled();
  });

  it("retains the reviewed head when inventory refreshes during editing", async () => {
    const original = registeredDevice(
      instrument({
        driver_id: "keysight.pna",
        connection: { kind: "tcpip_socket", host: "192.0.2.20", port: 5025 },
      }),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = (device: DeviceView) => (
      <QueryClientProvider client={client}>
        <InstrumentConfigDialog
          device={device}
          catalog={driverCatalog()}
          onCancel={() => {}}
          onPublished={() => {}}
        />
      </QueryClientProvider>
    );
    const rendered = render(view(original));
    fireEvent.change(screen.getByLabelText("主机"), { target: { value: "192.0.2.24" } });
    const refreshed = {
      ...original,
      device: { ...original.device, head: { ...original.device.head, revision_id: "other-edit" } },
    };
    rendered.rerender(view(refreshed));
    const save = screen.getByRole("button", { name: "保存设备" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() => expect(saveDevice).toHaveBeenCalledTimes(1));
    expect(vi.mocked(saveDevice).mock.calls[0]![0].expected_head).toEqual(original.device.head);
  });

  it("loads previous settings only into a cancellable draft and publishes against the current head", async () => {
    const registered = registeredDevice(
      instrument({
        driver_id: "keysight.pna",
        connection: { kind: "tcpip_socket", host: "192.0.2.24", port: 5025 },
      }),
    );
    const previous = {
      ...registered.revision,
      id: "previous-connection",
      content: {
        ...registered.revision.content,
        connection: {
          kind: "tcpip_socket" as const,
          host: "192.0.2.20",
          port: 5025,
          timeout_seconds: 2,
        },
      },
    };
    registered.previous_revision = previous;
    vi.mocked(getDevices).mockResolvedValue({ items: [registered] });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "编辑设备" }));
    let dialog = await findConnectionEditor();
    fireEvent.click(within(dialog).getByRole("button", { name: "使用上一连接版本生成草稿" }));
    expect(within(dialog).getByLabelText("主机")).toHaveValue("192.0.2.20");
    expect(within(dialog).getByRole("region", { name: "连接修改预览" })).toHaveTextContent(
      "192.0.2.24",
    );
    expect(testDeviceConnection).not.toHaveBeenCalled();
    expect(openInstrumentSession).not.toHaveBeenCalled();
    expect(saveDevice).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    await waitFor(() => expect(queryConnectionEditor()).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "编辑设备" }));
    dialog = await findConnectionEditor();
    expect(within(dialog).getByLabelText("主机")).toHaveValue("192.0.2.24");
    fireEvent.click(within(dialog).getByRole("button", { name: "使用上一连接版本生成草稿" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "保存设备" }));
    await waitFor(() => expect(saveDevice).toHaveBeenCalledTimes(1));
    const command = vi.mocked(saveDevice).mock.calls[0]![0];
    expect(command.expected_head).toEqual(registered.device.head);
    expect(command.revision_id).not.toBe(previous.id);
    expect(command.connection.connection).toEqual(previous.content.connection);
  });

  it("replays a save after its committed response is lost and blocks duplicate clicks", async () => {
    vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Alice" });
    const registered = registeredDevice(
      instrument({
        driver_id: "keysight.pna",
        connection: { kind: "tcpip_socket", host: "192.0.2.20", port: 5025 },
      }),
    );
    vi.mocked(getDevices).mockResolvedValue({ items: [registered] });
    let rejectFirst!: (error: Error) => void;
    vi.mocked(saveDevice).mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          rejectFirst = reject;
        }),
    );
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "编辑设备" }));
    const dialog = await findConnectionEditor();
    fireEvent.change(within(dialog).getByLabelText("主机"), { target: { value: "192.0.2.24" } });
    const save = within(dialog).getByRole("button", { name: "保存设备" });
    fireEvent.click(save);
    fireEvent.click(save);
    await waitFor(() => expect(saveDevice).toHaveBeenCalledTimes(1));
    const committed = vi.mocked(saveDevice).mock.calls[0]![0];
    expect(committed.actor).toBe("Alice");
    vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 2, operator: "Bob" });
    vi.mocked(saveDevice).mockImplementation(async (command) => {
      if (JSON.stringify(command) !== JSON.stringify(committed)) {
        throw new ApiError("device connection changed; reload and review before saving", 409);
      }
      return registered;
    });
    await act(async () => rejectFirst(new ApiError("本地后台服务未响应。")));
    await within(dialog).findByRole("alert");
    fireEvent.click(save);
    await waitFor(() => expect(saveDevice).toHaveBeenCalledTimes(2));
    expect(vi.mocked(saveDevice).mock.calls[1]![0]).toEqual(committed);
    await waitFor(() => expect(queryConnectionEditor()).not.toBeInTheDocument());
  });

  it.each(["192.0.2.25", "192.0.2.20"])(
    "uses a new revision for edited input %s and preserves the reviewed head on conflict",
    async (editedHost) => {
      const registered = registeredDevice(
        instrument({
          driver_id: "keysight.pna",
          connection: { kind: "tcpip_socket", host: "192.0.2.20", port: 5025 },
        }),
      );
      vi.mocked(getDevices).mockResolvedValue({ items: [registered] });
      vi.mocked(saveDevice)
        .mockRejectedValueOnce(new ApiError("本地后台服务未响应。"))
        .mockRejectedValueOnce(
          new ApiError("device connection changed; reload and review before saving", 409),
        );
      renderWorkspace();
      fireEvent.click(await screen.findByRole("button", { name: "编辑设备" }));
      const dialog = await findConnectionEditor();
      const host = within(dialog).getByLabelText("主机");
      fireEvent.change(host, { target: { value: "192.0.2.24" } });
      fireEvent.click(within(dialog).getByRole("button", { name: "保存设备" }));
      expect(await within(dialog).findByRole("alert")).toHaveTextContent(/尚未确认/);
      fireEvent.change(host, { target: { value: editedHost } });
      fireEvent.click(within(dialog).getByRole("button", { name: "保存设备" }));
      await waitFor(() => expect(within(dialog).getByRole("alert")).toHaveTextContent(/冲突/));
      const [first, second] = vi.mocked(saveDevice).mock.calls.map(([command]) => command);
      expect(second!.revision_id).not.toBe(first!.revision_id);
      expect(second!.expected_head).toEqual(registered.device.head);
      expect(second!.connection.connection).toMatchObject({ host: editedHost });
    },
  );

  it("tests a registered device without publishing experiment configuration", async () => {
    const device = registeredDevice(instrument());
    vi.mocked(testDeviceConnection).mockImplementation(async () => {
      vi.mocked(getDevices).mockResolvedValue({
        items: [
          {
            ...device,
            last_connection_test: {
              operation_id: "test",
              revision: device.device.head,
              actor: "operator",
              recorded_at: "2026-09-28T00:00:00Z",
              description: instrument().description,
            },
          },
        ],
      });
      return { status: "connected", description: instrument().description, problems: [] };
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "测试连接" }));
    expect(await screen.findByRole("status")).toHaveTextContent("连接测试通过");
    expect(testDeviceConnection).toHaveBeenCalledWith(
      registeredDevice(instrument()),
      expect.any(String),
      "local-operator",
    );
    expect(saveDevice).not.toHaveBeenCalled();
  });

  it("keeps a test error on its revision and removes it when another connection is saved", async () => {
    const device = registeredDevice(instrument());
    vi.mocked(testDeviceConnection).mockRejectedValue(
      new ApiError("Virtual endpoint unavailable", 409),
    );
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "测试连接" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Virtual endpoint unavailable");

    // A refresh or name-only edit must not clear an error for the same connection.
    vi.mocked(getDevices).mockResolvedValue({
      items: [{ ...device, device: { ...device.device, label: "Renamed source" } }],
    });
    fireEvent.click(screen.getByRole("button", { name: /^刷新$/ }));
    await screen.findByRole("heading", { name: "Renamed source" });
    expect(screen.getByRole("alert")).toHaveTextContent("Virtual endpoint unavailable");

    const updated = structuredClone(device);
    updated.device.head.revision_id = updated.revision.id = "connection-v2";
    updated.revision.content.connection.options = { gain: 2 };
    vi.mocked(getDevices).mockResolvedValue({ items: [updated] });
    fireEvent.click(screen.getByRole("button", { name: /^刷新$/ }));
    await screen.findByRole("heading", { name: "Drive source" });
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("此连接版本尚未测试。")).toBeVisible();

    // Failures belonging to the new version must still be shown.
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Virtual endpoint unavailable");
    expect(testDeviceConnection).toHaveBeenLastCalledWith(
      updated,
      expect.any(String),
      "local-operator",
    );
  });

  it("shows quarantined ownership and the operator resolution action", async () => {
    mockInventory({
      setup: { revision_id: "lab-default", content_hash: "sha256:active" },
      problems: [],
      items: [
        instrument({
          availability: "quarantined",
          owner_kind: "instrument_session",
          owner_id: "session-stale",
          owner_actor: "Grace",
        }),
      ],
    });
    renderWorkspace();

    expect(await screen.findByText("需要操作者处理")).toBeVisible();
    expect(screen.queryByText("Grace")).not.toBeInTheDocument();
    expect(screen.queryByText("session-stale")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "处理隔离" }));
    await waitFor(() => expect(resolveInstrumentAttention).toHaveBeenCalledWith("session-stale"));
  });
});

function renderWorkspace() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <InstrumentsWorkspace daemonUnavailable={false} />
    </QueryClientProvider>,
  );
}

async function connectInstrument() {
  fireEvent.click(await screen.findByRole("button", { name: "连接" }));
  await screen.findByText("交互会话已连接");
}

function mockInstrumentSessionOwnership({ releasedAfterRefresh = false } = {}) {
  const available = {
    setup: { revision_id: "lab-default", content_hash: "sha256:active" },
    problems: [],
    items: [instrument()],
  };
  const owned = {
    ...available,
    items: [
      instrument({
        availability: "active",
        owner_kind: "instrument_session",
        owner_id: "session-1",
        owner_actor: "local-operator",
      }),
    ],
  };
  vi.mocked(getInstruments)
    .mockResolvedValueOnce(available)
    .mockResolvedValueOnce(owned)
    .mockResolvedValue(releasedAfterRefresh ? available : owned);
}

function instrument(overrides: Partial<InstrumentView> = {}): InstrumentView {
  return {
    instrument_id: "drive-source",
    driver_id: "virtual.rf_source",
    connection: { kind: "virtual" },
    description: {
      instrument_id: "drive-source",
      implementation_id: "virtual.rf_source",
      implementation_version: "0.1",
      label: "Drive source",
      description: "Virtual microwave source",
      interfaces: [
        {
          id: "scopecat.rf_output/v1",
          label: "RF output",
          properties: [
            {
              id: "frequency",
              label: "CW frequency",
              access: "read_write",
              capture: true,
              restore: true,
              value_type: { type: "quantity", finite: true, unit: "Hz" },
            },
            {
              id: "output_enabled",
              label: "RF output",
              access: "read_write",
              capture: true,
              restore: true,
              value_type: { type: "bool" },
            },
            {
              id: "temperature",
              label: "Measured temperature",
              access: "read_only",
              capture: true,
              restore: false,
              value_type: { type: "quantity", finite: true, unit: "K" },
            },
          ],
          operations: [],
          components: [],
          acquisitions: [
            {
              id: "sample",
              label: "Sample",
              results: [
                {
                  id: "trace",
                  label: "Trace",
                  dtype: "float64",
                  role: "observable",
                  unit: "ratio",
                  axes: [{ id: "sample", kind: "sample", size: 3 }],
                },
              ],
            },
          ],
        },
      ],
    },
    availability: "available",
    owner_kind: null,
    owner_id: null,
    owner_actor: null,
    problems: [],
    ...overrides,
  };
}

function instrumentWithFlatDcState(): InstrumentView {
  const view = instrument();
  const description = view.description!;
  const originalRfOutput = description.interfaces![0]!;
  const rfOutput: InstrumentInterface = {
    ...originalRfOutput,
    properties: [
      ...(originalRfOutput.properties ?? []),
      {
        id: "voltage_range",
        label: "RF voltage limit",
        access: "read_write",
        capture: true,
        restore: true,
        value_type: { type: "quantity", finite: true, unit: "V" },
      },
    ],
  };
  const dcSource: InstrumentInterface = {
    id: "scopecat.dc_source/v2",
    label: "DC source",
    properties: [
      {
        id: "source_mode",
        label: "Source mode",
        access: "read_write",
        capture: true,
        restore: true,
        value_type: { type: "string", choices: ["voltage", "current"] },
      },
      {
        id: "output_enabled",
        label: "DC output",
        access: "read_write",
        capture: true,
        restore: true,
        value_type: { type: "bool" },
      },
      {
        id: "voltage_range",
        label: "Voltage range",
        access: "read_write",
        capture: true,
        restore: true,
        value_type: { type: "quantity", finite: true, unit: "V" },
      },
      {
        id: "current_range",
        label: "Current range",
        access: "read_write",
        capture: true,
        restore: true,
        value_type: { type: "quantity", finite: true, unit: "A" },
      },
    ],
    operations: [],
    components: [],
    acquisitions: [],
  };
  view.description = {
    ...description,
    interfaces: [dcSource, rfOutput],
  };
  return view;
}

function instrumentWithOperations(): InstrumentView {
  const view = instrument();
  const description = view.description!;
  const instrumentInterface = description.interfaces![0]!;
  view.description = {
    ...description,
    interfaces: [
      {
        ...instrumentInterface,
        operations: [
          {
            id: "configure_trigger",
            label: "Configure trigger",
            description: "Configure and arm the trigger in one hardware operation.",
            arguments: [
              {
                id: "enabled",
                label: "Enable correction",
                value_type: { type: "bool" },
              },
              {
                id: "averages",
                label: "Average count",
                value_type: { type: "int", minimum: 1, maximum: 16 },
              },
              {
                id: "threshold",
                label: "Threshold",
                value_type: { type: "float", finite: true, minimum: 0, maximum: 1 },
              },
              {
                id: "profile",
                label: "Profile name",
                value_type: { type: "string" },
              },
              {
                id: "settling",
                label: "Settling time",
                value_type: { type: "quantity", finite: true, unit: "s", minimum: 0 },
              },
            ],
          },
          {
            id: "reset_fault",
            label: "Reset fault",
            arguments: [],
          },
          {
            id: "upload_waveform",
            label: "Upload waveform",
            arguments: [
              {
                id: "waveform",
                label: "Waveform file",
                value_type: { type: "payload", schema_id: "waveform/v1" },
              },
            ],
          },
        ],
      },
    ],
  };
  return view;
}

function instrumentWithMountedAndDeviceState(): InstrumentView {
  const view = instrument();
  view.description = {
    ...view.description!,
    components: [
      {
        id: "channels",
        components: [{ id: "ch1" }, { id: "ch2" }],
      },
    ],
    interface_mounts: [
      { interface_id: "scopecat.rf_output/v1", component_path: ["channels", "ch1"] },
      { interface_id: "scopecat.rf_output/v1", component_path: ["channels", "ch2"] },
    ],
    interface_property_implementations: [
      {
        property: {
          interface_id: "scopecat.rf_output/v1",
          component_path: ["channels", "ch2"],
          property_id: "frequency",
        },
        access: "read_only",
        capture: true,
        restore: false,
      },
    ],
    device_schemas: [
      {
        id: "example.model_state/v1",
        label: "Model-specific state",
        members: [
          {
            property: {
              id: "serial_number",
              label: "Serial number",
              access: "read_only",
              capture: true,
              restore: false,
              value_type: { type: "string" },
            },
          },
          {
            property: {
              id: "internal_lo_offset",
              label: "Internal LO offset",
              access: "read_write",
              capture: true,
              restore: true,
              value_type: { type: "quantity", finite: true, unit: "Hz" },
            },
          },
        ],
      },
    ],
  };
  return view;
}

function session(overrides: Partial<InstrumentSession> = {}): InstrumentSession {
  return {
    session_id: "session-1",
    actor: "local-operator",
    setup: { revision_id: "lab-default", content_hash: "sha256:active" },
    instrument_ids: ["drive-source"],
    configured_default_instrument_ids: [],
    descriptions: [instrument().description!],
    observed_state: [instrumentState()],
    opened_at: "2026-07-27T09:00:00Z",
    renewed_at: "2026-07-27T09:00:00Z",
    expires_at: "2026-07-27T09:01:00Z",
    ...overrides,
  };
}

function sessionLease(overrides: Partial<InstrumentSessionLease> = {}): InstrumentSessionLease {
  return {
    session_id: "session-1",
    renewed_at: "2026-07-27T09:01:00Z",
    expires_at: "2026-07-27T09:02:00Z",
    ...overrides,
  };
}

function configuredDefaultsReceipt(
  status: "applied" | "unchanged",
  state: InstrumentState,
): Awaited<ReturnType<typeof applyInstrumentConfiguredDefaults>> {
  return {
    session_id: "session-1",
    operation_id: "defaults-1",
    instrument_id: "drive-source",
    setup: { revision_id: "lab-default", content_hash: "sha256:active" },
    status,
    problems: [],
    state,
  };
}

function instrumentState(frequency = 5_000_000_000): InstrumentState {
  return {
    instrument_id: "drive-source",
    observations: [
      instrumentObservation("scopecat.rf_output/v1", "frequency", {
        value: frequency,
        unit: "Hz",
      }),
      instrumentObservation("scopecat.rf_output/v1", "output_enabled", false),
      instrumentObservation("scopecat.rf_output/v1", "temperature", {
        value: 0.02,
        unit: "K",
      }),
    ],
  };
}

function mountedAndDeviceInstrumentState(): InstrumentState {
  return {
    instrument_id: "drive-source",
    observations: [
      memberObservation(
        {
          kind: "interface",
          interface_id: "scopecat.rf_output/v1",
          component_path: ["channels", "ch1"],
          property_id: "frequency",
        },
        { value: 5_000_000_000, unit: "Hz" },
      ),
      memberObservation(
        {
          kind: "interface",
          interface_id: "scopecat.rf_output/v1",
          component_path: ["channels", "ch2"],
          property_id: "frequency",
        },
        { value: 6_000_000_000, unit: "Hz" },
      ),
      memberObservation(
        {
          kind: "device",
          schema_id: "example.model_state/v1",
          component_path: [],
          property_id: "serial_number",
        },
        "SN-42",
      ),
      memberObservation(
        {
          kind: "device",
          schema_id: "example.model_state/v1",
          component_path: [],
          property_id: "internal_lo_offset",
        },
        { value: 1_000_000, unit: "Hz" },
      ),
    ],
  };
}

function flatDcInstrumentState(
  mode: "current" | "voltage" = "voltage",
  currentRange = 0.1,
  frequency = 5_000_000_000,
): InstrumentState {
  return {
    instrument_id: "drive-source",
    observations: [
      instrumentObservation("scopecat.dc_source/v2", "source_mode", mode),
      instrumentObservation("scopecat.dc_source/v2", "output_enabled", false),
      instrumentObservation("scopecat.dc_source/v2", "voltage_range", {
        value: 5,
        unit: "V",
      }),
      instrumentObservation("scopecat.dc_source/v2", "current_range", {
        value: currentRange,
        unit: "A",
      }),
      ...(instrumentState(frequency).observations ?? []),
    ],
  };
}

function instrumentObservation(
  interfaceId: string,
  propertyId: string,
  value: NonNullable<InstrumentState["observations"]>[number]["value"],
): NonNullable<InstrumentState["observations"]>[number] {
  return memberObservation(
    {
      kind: "interface",
      interface_id: interfaceId,
      component_path: [],
      property_id: propertyId,
    },
    value,
  );
}

function memberObservation(
  target: NonNullable<InstrumentState["observations"]>[number]["target"],
  value: NonNullable<InstrumentState["observations"]>[number]["value"],
): NonNullable<InstrumentState["observations"]>[number] {
  return {
    target,
    value,
    source: "hardware_query",
    entity_ids: [],
    channel_bindings: [],
  };
}

function flatDcApplyReceipt(): Awaited<ReturnType<typeof applyInstrumentState>> {
  return {
    status: "applied",
    problems: [],
  };
}

function activeConfig(): ConfigEntryView {
  return {
    entry: {
      id: "lab-default",
      content_hash: "sha256:active",
      config_ref: "entries/lab-default.json",
      source: { kind: "direct_config_profile" },
      actor: "Ada",
      note: "",
      recorded_at: "2026-07-27T08:00:00Z",
    },
    config: {
      id: "lab",
      system: {
        id: "system",
        topology: { entities: [] },
        instrument_registry: { instruments: [configuredInstrument()] },
        routing: { roles: [], routes: [] },
        domain_target: null,
        parameter_catalog: { id: "parameters", definitions: [] },
      },
      parameter_snapshot: { id: "parameters", values: [] },
    },
  };
}

function configuredInstrument() {
  return {
    id: "drive-source",
    exclusivity_key: "drive-source",
    driver_id: "virtual.rf_source",
    connection: { kind: "virtual" as const },
    default_state: [],
    run_start: "preserve" as const,
    success_action: "release" as const,
    failure_action: "abort_and_release" as const,
  };
}

function driverCatalog(): Awaited<ReturnType<typeof getDriverCatalog>> {
  return {
    provider_id: "scopecat.instruments.configured",
    drivers: [
      {
        driver_id: "virtual.rf_source",
        implementation_version: "v1",
        label: "Virtual RF source",
        connections: [{ kind: "virtual", options_schema: { type: "object", properties: {} } }],
      },
      {
        driver_id: "keysight.pna",
        implementation_version: "v1",
        label: "Keysight PNA",
        manufacturer: "Keysight",
        model: "PNA",
        connections: [
          {
            kind: "tcpip_socket",
            options_schema: {
              type: "object",
              properties: {
                channel: {
                  type: "integer",
                  title: "Channel",
                  default: 1,
                  minimum: 1,
                },
              },
            },
          },
        ],
      },
      {
        driver_id: "yokogawa.gs200",
        implementation_version: "v1",
        label: "Yokogawa GS200",
        manufacturer: "Yokogawa",
        model: "GS200",
        connections: [
          {
            kind: "tcpip_socket",
            options_schema: {
              type: "object",
              properties: {
                monitor_option: {
                  type: "boolean",
                  title: "Monitor Option",
                  default: false,
                },
                remote_sense: {
                  type: "boolean",
                  title: "Remote Sense",
                  default: false,
                },
                guard_enabled: {
                  type: "boolean",
                  title: "Guard Enabled",
                  default: false,
                },
              },
            },
          },
        ],
      },
      {
        driver_id: "example.controller",
        implementation_version: "v1",
        label: "Example controller",
        connections: [
          {
            kind: "driver_managed",
            options_schema: {
              type: "object",
              required: ["box_addresses", "master_board_name"],
              properties: {
                dac_channels: {
                  type: "array",
                  title: "DAC Channels",
                  default: ["readout"],
                  items: { type: "string" },
                },
                box_addresses: {
                  type: "object",
                  title: "Box Addresses",
                  additionalProperties: { type: "string" },
                },
                master_board_name: {
                  type: "string",
                  title: "Master Board Name",
                },
              },
            },
          },
        ],
      },
    ],
  };
}

function setupRevision(active = activeConfig()): SetupRevision {
  const { topology, instrument_registry, routing, domain_target, scenario } = active.config.system;
  return {
    resolution: { definition_id: "bench", definition_hash: "sha256:definition", devices: [] },
    id: "lab-default",
    content_hash: "sha256:active",
    actor: "Ada",
    note: "",
    setup: {
      topology,
      instrument_registry,
      routing: routing ?? { roles: [], routes: [] },
      domain_target: domain_target ?? null,
      scenario,
    },
  };
}

function registeredDevice(item: InstrumentView): DeviceView {
  const connection =
    item.connection.kind === "tcpip_socket"
      ? { ...item.connection, timeout_seconds: 5 }
      : item.connection.kind === "serial"
        ? {
            ...item.connection,
            timeout_seconds: 5,
            write_timeout_seconds: 5,
            data_bits: 8 as const,
            stop_bits: 1 as const,
            parity: "none" as const,
            dsrdtr: false,
            rtscts: false,
            xonxoff: false,
          }
        : item.connection;
  const content = {
    driver: { driver_id: item.driver_id, provider_id: "test", artifact_hash: "sha256:driver" },
    connection,
    safety: {
      safe_state: [],
      safe_operations: [],
      safe_state_requirement: "best_effort" as const,
      require_safe_success: false,
      require_safe_failure: false,
    },
    access_aliases: [],
  };
  return {
    device: {
      id: item.instrument_id,
      label: item.description?.label ?? item.instrument_id,
      state: "available",
      head: {
        device_id: item.instrument_id,
        revision_id: item.instrument_id + "-v1",
        content_hash: "sha256:connection",
      },
    },
    revision: {
      id: item.instrument_id + "-v1",
      device_id: item.instrument_id,
      content,
      actor: "operator",
      note: "",
    },
    availability:
      item.availability === "active" || item.availability === "quarantined"
        ? item.availability
        : "idle",
    owner_kind: item.owner_kind,
    owner_id: item.owner_id,
  };
}
function mockInventory(view: Awaited<ReturnType<typeof getInstruments>>) {
  vi.mocked(getDevices).mockResolvedValue({ items: view.items.map(registeredDevice) });
  return vi.mocked(getInstruments).mockResolvedValue(view);
}

vi.mock("../application/OperatorSettings", () => ({
  readOperatorSettings: vi.fn(async () => ({ revision: 1, operator: "local-operator" })),
}));

it("keeps the original session-open actor when retrying after Settings changes", async () => {
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 1, operator: "Alice" });
  vi.mocked(openInstrumentSession).mockRejectedValueOnce(new Error("Open response lost"));
  renderWorkspace();
  fireEvent.click(await screen.findByRole("button", { name: "连接" }));
  await screen.findByText("Open response lost");
  expect(vi.mocked(openInstrumentSession).mock.calls[0]![1]).toBe("Alice");
  vi.mocked(readOperatorSettings).mockResolvedValue({ revision: 2, operator: "Bob" });
  await connectInstrument();
  expect(vi.mocked(openInstrumentSession).mock.calls[1]).toEqual(
    vi.mocked(openInstrumentSession).mock.calls[0],
  );
  expect(readOperatorSettings).toHaveBeenCalledTimes(1);
});

function queryConnectionEditor() {
  return screen.queryByRole("dialog") ?? screen.queryByRole("region", { name: "编辑设备" });
}
function getConnectionEditor() {
  const editor = queryConnectionEditor();
  if (!editor) throw new Error("Connection editor not found");
  return editor;
}
async function findConnectionEditor() {
  return waitFor(getConnectionEditor);
}
