// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
  prepareDeviceAccess,
  saveDevice,
  testDeviceConnection,
  retireDevice,
  renameDevice,
  type DeviceView,
} from "./device-api";
vi.mock("./device-api", () => ({
  getDevices: vi.fn(),
  getDeviceDrivers: vi.fn(),
  prepareDeviceAccess: vi.fn(),
  saveDevice: vi.fn(),
  testDeviceConnection: vi.fn(),
  retireDevice: vi.fn(),
  renameDevice: vi.fn(),
}));

import { InstrumentsWorkspace } from "./InstrumentsWorkspace";
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
  vi.useRealTimers();
});

describe("instrument workspace", () => {
  it("opens registered devices without choosing an experiment setup", async () => {
    renderWorkspace();
    await screen.findByRole("button", { name: "Connect" });
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

    expect(await screen.findByText("Instrument provider issues")).toBeVisible();
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
    expect(screen.getAllByText("Virtual · local simulator")[0]).toBeVisible();
    expect(screen.getByText("Readout VNA")).toBeVisible();
    expect(screen.getByText("TCP/IP · 192.0.2.12:5025")).toBeVisible();
    expect(screen.getByText("Run in progress")).toBeVisible();
    expect(screen.queryByText("run-42")).not.toBeInTheDocument();
    expect(screen.queryByText("keysight.pna")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Device context" })).not.toBeInTheDocument();
    expect(openInstrumentSession).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTitle("Inspect instrument vna-1"));
    expect(await screen.findByText("Manual controls unavailable")).toBeVisible();
    expect(screen.getByRole("button", { name: "Edit device" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole("button", { name: "Refresh state" }));
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

    await vi.waitFor(() => expect(screen.getByRole("button", { name: "Connect" })).toBeVisible());
    vi.setSystemTime(new Date("2026-07-27T09:00:05Z"));
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await vi.waitFor(() => expect(screen.getByText("Interactive session connected")).toBeVisible());

    await act(async () => vi.advanceTimersByTimeAsync(4_000));
    expect(renewInstrumentSession).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(1_000));
    expect(renewInstrumentSession).toHaveBeenCalledOnce();
    expect(renewInstrumentSession).toHaveBeenLastCalledWith("session-1");

    await act(async () => vi.advanceTimersByTimeAsync(29_000));
    expect(renewInstrumentSession).toHaveBeenCalledOnce();
    await act(async () => vi.advanceTimersByTimeAsync(1_000));

    expect(renewInstrumentSession).toHaveBeenCalledTimes(2);
    expect(screen.getByText(/device session was interrupted/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Connect" })).toBeVisible();
  });

  it("shows and applies only session-authoritative configured defaults", async () => {
    vi.mocked(openInstrumentSession).mockResolvedValue(
      session({ configured_default_instrument_ids: ["drive-source"] }),
    );
    vi.mocked(readInstrumentState).mockResolvedValueOnce(instrumentState(6_000_000_000));
    renderWorkspace();

    await screen.findAllByText("Drive source");
    expect(
      screen.queryByRole("button", { name: "Apply configured defaults" }),
    ).not.toBeInTheDocument();
    await connectInstrument();
    const applyDefaults = await screen.findByRole("button", {
      name: "Apply configured defaults",
    });
    fireEvent.click(applyDefaults);

    await waitFor(() =>
      expect(applyInstrumentConfiguredDefaults).toHaveBeenCalledWith(
        expect.objectContaining({ session_id: "session-1" }),
        "drive-source",
        expect.stringMatching(/^ui-configured-defaults-/),
      ),
    );
    expect(await screen.findByText("Configured defaults applied.")).toBeVisible();
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
    fireEvent.click(await screen.findByRole("button", { name: "Invoke Reset fault" }));
    expect(await screen.findByText("Invoke receipt: Invoked")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Apply configured defaults" }));
    expect(await screen.findByText("Configured defaults applied.")).toBeVisible();
    expect(screen.queryByText("Invoke receipt: Invoked")).not.toBeInTheDocument();
  });

  it("hides configured defaults when the pinned session has none", async () => {
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();

    expect(
      screen.queryByRole("button", { name: "Apply configured defaults" }),
    ).not.toBeInTheDocument();
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
      name: "Apply configured defaults",
    });

    fireEvent.change(frequency, { target: { value: "6000000000" } });
    expect(applyDefaults).toBeDisabled();
    expect(applyDefaults).toHaveAttribute("title", "Apply or reset staged properties first");

    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    await waitFor(() => expect(collectInstrumentAcquisition).toHaveBeenCalledOnce());
    expect(screen.getByRole("button", { name: "Apply staged" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Refresh state" })).toBeDisabled();
    expect(applyDefaults).toBeDisabled();
    expect(applyDefaults).toHaveAttribute("title", "Apply or reset staged properties first");
    finishCollect?.();
    await waitFor(() => expect(screen.getByRole("button", { name: "Apply staged" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
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
      name: "Apply configured defaults",
    });
    await waitFor(() => expect(applyDefaults).toBeEnabled());

    fireEvent.click(applyDefaults);
    await waitFor(() => expect(applyInstrumentConfiguredDefaults).toHaveBeenCalledOnce());

    expect(screen.getByRole("button", { name: "Refresh state" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Collect" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Invoke Reset fault" })).toBeDisabled();
    expect(screen.getByRole("spinbutton", { name: /CW frequency/ })).toBeDisabled();
    expect(applyDefaults).toHaveAttribute(
      "title",
      "Wait for the current instrument interaction to finish",
    );

    finishDefaults?.(configuredDefaultsReceipt("unchanged", instrumentState()));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Refresh state" })).toBeEnabled(),
    );
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
    fireEvent.click(await screen.findByRole("button", { name: "Apply configured defaults" }));

    expect(
      await screen.findByText("Configured defaults rejected: Driver refused the configured state."),
    ).toBeVisible();
    expect(screen.getByText("Interactive session connected")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
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
      name: "Apply configured defaults",
    });

    fireEvent.click(applyDefaults);

    await waitFor(() => expect(applyInstrumentConfiguredDefaults).toHaveBeenCalledTimes(2));
    expect(vi.mocked(applyInstrumentConfiguredDefaults).mock.calls[0]?.[2]).toBe(
      vi.mocked(applyInstrumentConfiguredDefaults).mock.calls[1]?.[2],
    );
    expect(await screen.findByText("State already matched the configured defaults.")).toBeVisible();
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
    fireEvent.click(await screen.findByRole("button", { name: "Apply configured defaults" }));

    expect(
      await screen.findByText("The interactive session ended while applying configured defaults."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Connect" })).toBeVisible();
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

    expect(await screen.findByText("Manual controls unavailable")).toBeVisible();
    expect(screen.queryByText("session-stale")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect session" }));

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
    expect(screen.getByText("1 staged property")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Apply staged" }));

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
    expect(await screen.findByText("Apply receipt: Applied")).toBeVisible();
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
    fireEvent.click(screen.getByRole("button", { name: "Apply staged" }));

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

    const apply = screen.getByRole("button", { name: "Apply staged" });
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
        name: "Reset staged value",
      }),
    );

    expect(screen.getByRole("spinbutton", { name: /Voltage range/ })).toHaveValue(5);
    expect(screen.getByRole("spinbutton", { name: /Current range/ })).toHaveValue(0.2);
    expect(screen.getByText("1 staged property")).toBeVisible();
    expect(applyInstrumentState).not.toHaveBeenCalled();
  });

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
    expect(screen.getByRole("button", { name: "Invoke Configure trigger" })).toBeDisabled();
    expect(screen.queryByText("Upload waveform")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Invoke Upload waveform" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("payload_id")).not.toBeInTheDocument();
    expect(screen.queryByText("waveform/v1")).not.toBeInTheDocument();

    await connectInstrument();
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

    expect(screen.queryByText(/staged propert/)).not.toBeInTheDocument();
    expect(applyInstrumentState).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Invoke Configure trigger" }));

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
    expect(await screen.findByText("Invoke receipt: Invoked")).toBeVisible();
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
    fireEvent.click(await screen.findByRole("button", { name: "Invoke Reset fault" }));

    expect(
      await screen.findByText(
        "The operation result is unknown. The daemon quarantined this session for operator review.",
      ),
    ).toBeVisible();
    expect(screen.queryByText("session-1")).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Connect" })).toBeVisible();
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
    fireEvent.click(await screen.findByRole("button", { name: "Collect" }));

    const summary = await screen.findByRole("status");
    expect(within(summary).getByText("2 results unavailable")).toBeVisible();
    expect(within(summary).getByText("Reasons: Missing, Overload")).toBeVisible();
    expect(summary).not.toHaveTextContent("private-overload-result");
    expect(summary).not.toHaveTextContent("private-missing-result");
    expect(screen.queryByRole("img", { name: /trace preview/i })).not.toBeInTheDocument();
    expect(screen.queryByText("JSON preview")).not.toBeInTheDocument();
  });

  it("reuses command ids while retrying mutations", async () => {
    vi.mocked(applyInstrumentState).mockRejectedValueOnce(new Error("Apply network failed."));
    vi.mocked(collectInstrumentAcquisition).mockRejectedValueOnce(
      new Error("Collect network failed."),
    );
    vi.mocked(closeInstrumentSession).mockRejectedValueOnce(
      new ApiError("The local daemon did not respond."),
    );
    renderWorkspace();
    await screen.findAllByText("Drive source");
    await connectInstrument();
    const frequency = await screen.findByRole("spinbutton", { name: /CW frequency/ });
    fireEvent.change(frequency, { target: { value: "6000000000" } });

    fireEvent.click(screen.getByRole("button", { name: "Apply staged" }));
    expect(await screen.findByText("Apply network failed.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Apply staged" }));
    await waitFor(() => expect(applyInstrumentState).toHaveBeenCalledTimes(2));
    expect(vi.mocked(applyInstrumentState).mock.calls[0]?.[3]).toBe(
      vi.mocked(applyInstrumentState).mock.calls[1]?.[3],
    );

    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    expect(await screen.findByText("Collect network failed.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    await waitFor(() => expect(collectInstrumentAcquisition).toHaveBeenCalledTimes(2));
    expect(vi.mocked(collectInstrumentAcquisition).mock.calls[0]?.[3]).toBe(
      vi.mocked(collectInstrumentAcquisition).mock.calls[1]?.[3],
    );

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
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

    fireEvent.click(screen.getByRole("button", { name: "Collect" }));
    expect(await screen.findByText("Collect request lost.")).toBeVisible();
    const staleCollectCommandId = vi.mocked(collectInstrumentAcquisition).mock.calls[0]?.[3];

    fireEvent.change(frequency, { target: { value: "6000000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply staged" }));
    expect(await screen.findByText("Apply receipt: Applied")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Collect" }));

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
    const collect = screen.getByRole("button", { name: "Collect" });
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

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));

    expect(await screen.findByText("Close request lost again.")).toBeVisible();
    expect(screen.getByText("Interactive session connected")).toBeVisible();
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeEnabled();
    expect(closeInstrumentSession).toHaveBeenCalledTimes(2);
    expect(vi.mocked(closeInstrumentSession).mock.calls[0]).toEqual(["session-1"]);
    expect(vi.mocked(closeInstrumentSession).mock.calls[1]).toEqual(["session-1"]);

    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(closeInstrumentSession).toHaveBeenCalledTimes(3));
    expect(vi.mocked(closeInstrumentSession).mock.calls[2]).toEqual(["session-1"]);
    expect(await screen.findByRole("button", { name: "Connect" })).toBeVisible();
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

    fireEvent.click(screen.getByTitle("Inspect instrument monitor"));

    expect(await screen.findByText(/Switch close failed/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Drive source", level: 2 })).toBeVisible();
    expect(vi.mocked(closeInstrumentSession).mock.calls[0]).toEqual(["session-1"]);

    fireEvent.click(screen.getByTitle("Inspect instrument monitor"));

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
    fireEvent.click(await screen.findByRole("button", { name: "Edit device" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Host"), { target: { value: "192.0.2.24" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save device" }));
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
    fireEvent.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Connection test passed");
    expect(testDeviceConnection).toHaveBeenCalledWith(
      registeredDevice(instrument()),
      expect.any(String),
    );
    expect(saveDevice).not.toHaveBeenCalled();
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

    expect(await screen.findByText("Operator resolution required")).toBeVisible();
    expect(screen.queryByText("Grace")).not.toBeInTheDocument();
    expect(screen.queryByText("session-stale")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resolve quarantine" }));
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
  fireEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await screen.findByText("Interactive session connected");
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
    latest_activation: {
      generation: 3,
      action: "activation",
      entry_id: "lab-default",
      entry_content_hash: "sha256:active",
      actor: "Ada",
      note: "",
      recorded_at: "2026-07-27T08:00:00Z",
    },
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
