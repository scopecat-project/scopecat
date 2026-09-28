import { afterEach, expect, it, vi } from "vitest";
import { requestJson, requestPath } from "../../test/http";
import { saveDevice, renameDevice, testDeviceConnection, type DeviceView } from "./device-api";
afterEach(() => vi.unstubAllGlobals());
const content = {
  driver: { provider_id: "vendor", driver_id: "vna", artifact_hash: "sha256:driver" },
  connection: { kind: "tcpip_socket" as const, host: "192.0.2.40", port: 5025, timeout_seconds: 5 },
  safety: {
    safe_state: [],
    safe_operations: [],
    safe_state_requirement: "best_effort" as const,
    require_safe_success: false,
    require_safe_failure: false,
  },
  access_aliases: [],
};
const device: DeviceView = {
  device: {
    id: "vna",
    label: "Bench VNA",
    state: "available",
    head: { device_id: "vna", revision_id: "connection-1", content_hash: "sha256:connection" },
  },
  revision: { id: "connection-1", device_id: "vna", content, actor: "operator", note: "" },
  availability: "idle",
};
it("saves a reviewed device revision without creating an experiment setup", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(device));
  vi.stubGlobal("fetch", fetch);
  const command = {
    device_id: "vna",
    label: "Bench VNA",
    revision_id: "connection-2",
    expected_head: device.device.head,
    connection: content,
    actor: "operator",
    note: "New cable",
  };
  await saveDevice(command);
  expect(fetch).toHaveBeenCalledOnce();
  expect(requestPath(fetch.mock.calls[0]![0])).toBe("/api/v1/devices");
  expect(await requestJson(fetch.mock.calls[0]![0])).toEqual(command);
});
it("renames without a new connection and tests the reviewed device head", async () => {
  const renamed = { ...device.device, label: "Readout" };
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(Response.json(renamed))
    .mockResolvedValueOnce(Response.json({ status: "connected" }));
  vi.stubGlobal("fetch", fetch);
  expect(await renameDevice(device, "Readout")).toEqual({ ...device, device: renamed });
  await testDeviceConnection(device, "test-retry");
  expect(requestPath(fetch.mock.calls[0]![0])).toBe("/api/v1/devices/vna/name");
  expect(await requestJson(fetch.mock.calls[0]![0])).toEqual({ label: "Readout" });
  expect(await requestJson(fetch.mock.calls[1]![0])).toEqual({
    expected_head: device.device.head,
    operation_id: "test-retry",
    actor: "local-operator",
  });
});
