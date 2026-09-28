import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  InstrumentAcquisition,
  InstrumentOperation,
  InstrumentSession,
} from "../../api-contract";
import { requestHeaders, requestJson, requestMethod, requestPath } from "../../test/http";
import {
  applyInstrumentConfiguredDefaults,
  applyInstrumentState,
  closeInstrumentSession,
  collectInstrumentAcquisition as sendInstrumentAcquisition,
  connectionSummary,
  getDriverCatalog,
  invokeInstrumentOperation,
  openInstrumentSession,
  probeInstrumentDriver,
  readInstrumentStateMembers,
  readObservedInstrumentStateMembers,
  renewInstrumentSession,
  type InstrumentAcquisitionTarget,
} from "./instrument-api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function session(): InstrumentSession {
  return {
    session_id: "session-1",
    actor: "Ada",
    setup: { revision_id: "lab-default", content_hash: "sha256:active" },
    instrument_ids: ["vna-1"],
    configured_default_instrument_ids: [],
    descriptions: [],
    observed_state: [],
    opened_at: "2026-07-27T08:00:00Z",
    renewed_at: "2026-07-27T08:00:00Z",
    expires_at: "2026-07-27T08:01:00Z",
  };
}

function hardwareReceiptResponse(): Response {
  const header = new TextEncoder().encode(
    JSON.stringify({
      format_id: "scopecat.collect_receipt.v1",
      status: "collected",
      problems: [],
      readback: { values: {}, metadata: {} },
      metadata: {},
    }),
  );
  const content = new Uint8Array(16 + header.byteLength);
  content.set(new TextEncoder().encode("SCRCPT01"));
  new DataView(content.buffer).setBigUint64(8, BigInt(header.byteLength), true);
  content.set(header, 16);
  return new Response(content, {
    status: 200,
    headers: { "Content-Type": "application/vnd.scopecat.hardware-receipt.v1" },
  });
}

describe("instrument connection summary", () => {
  it("summarizes a TCP endpoint", () => {
    expect(
      connectionSummary({
        kind: "tcpip_socket",
        host: "192.0.2.24",
        port: 5025,
      }),
    ).toBe("TCP/IP · 192.0.2.24:5025");
    expect(connectionSummary({ kind: "serial", port: "/dev/ttyUSB0", baud_rate: 115_200 })).toBe(
      "Serial · /dev/ttyUSB0 @ 115200",
    );
    expect(connectionSummary({ kind: "driver_managed" })).toBe("Driver managed");
  });
});

describe("instrument driver catalog", () => {
  it("reads the catalog and probes a candidate binding", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            provider_id: "scopecat.instruments.configured",
            drivers: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ status: "connected", problems: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getDriverCatalog()).resolves.toEqual({
      provider_id: "scopecat.instruments.configured",
      drivers: [],
    });
    await expect(
      probeInstrumentDriver({
        setup: { revision_id: "bench", content_hash: `sha256:${"0".repeat(64)}` },
        operation_id: "connection-test-1",
        actor: "alice",
        binding: {
          id: "source-1",
          driver_id: "virtual.rf_source",
          connection: { kind: "virtual" },
        },
      }),
    ).resolves.toMatchObject({ status: "connected" });

    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe("/api/v1/instrument-drivers");
    expect(requestPath(fetchMock.mock.calls[1]?.[0])).toBe("/api/v1/instrument-drivers/probe");
    await expect(
      requestJson(fetchMock.mock.calls[1]?.[0], fetchMock.mock.calls[1]?.[1]),
    ).resolves.toEqual({
      setup: { revision_id: "bench", content_hash: `sha256:${"0".repeat(64)}` },
      operation_id: "connection-test-1",
      actor: "alice",
      binding: {
        id: "source-1",
        driver_id: "virtual.rf_source",
        connection: { kind: "virtual" },
      },
    });
  });
});

describe("instrument session lease", () => {
  it("renews with an empty heartbeat request", async () => {
    const renewed = {
      session_id: "session-1",
      renewed_at: "2026-07-27T08:00:10Z",
      expires_at: "2026-07-27T08:01:10Z",
    };
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(
        new Response(JSON.stringify(renewed), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(renewInstrumentSession("session-1")).resolves.toEqual(renewed);
    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/instrument-sessions/session-1/heartbeat",
    );
    expect(requestMethod(fetchMock.mock.calls[0]?.[0], fetchMock.mock.calls[0]?.[1])).toBe("POST");
    const request = fetchMock.mock.calls[0]?.[0];
    if (!(request instanceof Request)) throw new Error("Expected a Request.");
    expect(request.body).toBeNull();
  });
});

describe("interactive collection request shaping", () => {
  it("selects exact member targets for cached and fresh state reads", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ instrument_id: "vna-1", generation: 2, entries: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ instrument_id: "vna-1", observations: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const targets = [
      {
        kind: "device" as const,
        schema_id: "example.model_state/v1",
        component_path: [],
        property_id: "serial_number",
      },
    ];

    await readObservedInstrumentStateMembers(session(), "vna-1", targets);
    await readInstrumentStateMembers(session(), "vna-1", targets);

    expect(fetchMock.mock.calls.map(([input]) => requestPath(input))).toEqual([
      "/api/v1/instrument-sessions/session-1/instruments/vna-1/state/observed",
      "/api/v1/instrument-sessions/session-1/instruments/vna-1/state/read",
    ]);
    for (const [input, init] of fetchMock.mock.calls) {
      await expect(requestJson(input, init)).resolves.toEqual({ targets });
    }
  });

  it("applies configured defaults without exposing assignments to the browser", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            session_id: "session-1",
            operation_id: "defaults-retry",
            instrument_id: "vna-1",
            setup: { revision_id: "lab-default", content_hash: "sha256:active" },
            status: "unchanged",
            problems: [],
            state: { instrument_id: "vna-1", properties: [] },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await applyInstrumentConfiguredDefaults(session(), "vna-1", "defaults-retry");

    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/instrument-sessions/session-1/instruments/vna-1/configured-defaults/apply",
    );
    await expect(
      requestJson(fetchMock.mock.calls[0]?.[0], fetchMock.mock.calls[0]?.[1]),
    ).resolves.toEqual({ operation_id: "defaults-retry" });
  });

  it("shapes GUI operation arguments without exposing a payload map", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            status: "invoked",
            problems: [],
            state: { instrument_id: "vna-1", properties: [] },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const operation: InstrumentOperation = {
      id: "recalibrate",
      arguments: [
        {
          id: "delay",
          value_type: { type: "quantity", finite: true, unit: "s" },
        },
      ],
    };

    await invokeInstrumentOperation(
      session(),
      "vna-1",
      {
        interfaceId: "scopecat.network_sweep/v1",
        componentPath: ["source"],
        operation,
      },
      [{ id: "delay", value: { value: 0.25, unit: "s" } }],
      "invoke-retry",
    );

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/instrument-sessions/session-1/instruments/vna-1/invoke",
    );
    await expect(
      requestJson(fetchMock.mock.calls[0]?.[0], fetchMock.mock.calls[0]?.[1]),
    ).resolves.toEqual({
      command_id: "invoke-retry",
      instrument_id: "vna-1",
      resource_id: "vna-1",
      interface_id: "scopecat.network_sweep/v1",
      component_path: ["source"],
      operation_id: "recalibrate",
      arguments: [{ id: "delay", value: { value: 0.25, unit: "s" } }],
    });
  });

  it("sends only acquisition identity for daemon-side planning", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(hardwareReceiptResponse()),
    );
    vi.stubGlobal("fetch", fetchMock);
    const target: InstrumentAcquisitionTarget = {
      interfaceId: "scopecat.network_sweep/v1",
      componentPath: ["readout"],
      acquisition: {
        id: "sweep",
        results: [
          {
            id: "trace",
            dtype: "float64",
            role: "observable",
            axes: [
              {
                id: "frequency",
                kind: "frequency",
                size: {
                  interface_id: "scopecat.sweep_control/v1",
                  component_path: ["sweep"],
                  property_id: "points",
                },
                unit: "Hz",
              },
            ],
          },
        ],
      },
    };

    await sendInstrumentAcquisition(session(), "vna-1", target, "collect-retry");

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(requestPath(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/instrument-sessions/session-1/instruments/vna-1/collect",
    );
    expect(
      requestHeaders(fetchMock.mock.calls[0]?.[0], fetchMock.mock.calls[0]?.[1]).get("Accept"),
    ).toBe("application/vnd.scopecat.hardware-receipt.v1");
    await expect(
      requestJson(fetchMock.mock.calls[0]?.[0], fetchMock.mock.calls[0]?.[1]),
    ).resolves.toEqual({
      command_id: "collect-retry",
      instrument_id: "vna-1",
      interface_id: "scopecat.network_sweep/v1",
      component_path: ["readout"],
      acquisition_id: "sweep",
      result_ids: [],
    });
  });
  it("uses caller idempotency ids across repeated mutating requests", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(
        requestPath(input).endsWith("/collect")
          ? hardwareReceiptResponse()
          : new Response("{}", {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const acquisition: InstrumentAcquisition = {
      id: "sweep",
      results: [{ id: "trace", dtype: "float64", role: "observable", axes: [] }],
    };
    const target = {
      interfaceId: "scopecat.network_sweep/v1",
      componentPath: [],
      acquisition,
    };
    const operationTarget = {
      interfaceId: "scopecat.network_sweep/v1",
      componentPath: [],
      operation: { id: "recalibrate", arguments: [] },
    };

    await openInstrumentSession("vna-1", "Ada", session().setup, "open-retry");
    await openInstrumentSession("vna-1", "Ada", session().setup, "open-retry");
    const [openInput, openInit] = fetchMock.mock.calls[0]!;
    await expect(requestJson(openInput, openInit)).resolves.toEqual({
      setup: session().setup,
      operation_id: "open-retry",
      actor: "Ada",
      instrument_ids: ["vna-1"],
    });
    const properties = [
      {
        target: {
          kind: "interface" as const,
          interface_id: "scopecat.network_sweep/v1",
          component_path: [],
          property_id: "points",
        },
        value: 201,
      },
    ];
    await applyInstrumentState(session(), "vna-1", properties, "apply-retry");
    await applyInstrumentState(session(), "vna-1", properties, "apply-retry");
    await invokeInstrumentOperation(session(), "vna-1", operationTarget, [], "invoke-retry");
    await invokeInstrumentOperation(session(), "vna-1", operationTarget, [], "invoke-retry");
    await sendInstrumentAcquisition(session(), "vna-1", target, "collect-retry");
    await sendInstrumentAcquisition(session(), "vna-1", target, "collect-retry");
    await closeInstrumentSession("session-1");
    await closeInstrumentSession("session-1");

    const bodies = await Promise.all(
      fetchMock.mock.calls
        .slice(0, 8)
        .map(([input, init]) =>
          requestJson<{ command_id?: string; operation_id?: string }>(input, init),
        ),
    );
    expect(bodies.slice(0, 2).map((body) => body.operation_id)).toEqual([
      "open-retry",
      "open-retry",
    ]);
    expect(bodies.slice(2, 4).map((body) => body.command_id)).toEqual([
      "apply-retry",
      "apply-retry",
    ]);
    expect(bodies.slice(4, 6).map((body) => body.command_id)).toEqual([
      "invoke-retry",
      "invoke-retry",
    ]);
    expect(bodies.slice(4, 6).map((body) => body.operation_id)).toEqual([
      "recalibrate",
      "recalibrate",
    ]);
    expect(bodies.slice(6, 8).map((body) => body.command_id)).toEqual([
      "collect-retry",
      "collect-retry",
    ]);
    expect(fetchMock.mock.calls.slice(8, 10).map(([input]) => requestPath(input))).toEqual([
      "/api/v1/instrument-sessions/session-1/close",
      "/api/v1/instrument-sessions/session-1/close",
    ]);
  });
});
