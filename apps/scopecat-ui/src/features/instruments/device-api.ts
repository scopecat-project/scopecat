import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";

export type DeviceView = components["schemas"]["DeviceView"];
export type DeviceConnection = components["schemas"]["DeviceConnection"];
export type DeviceSaveCommand = components["schemas"]["DeviceSaveCommand"];
export type DriverImplementation = components["schemas"]["DriverImplementationRef"];
export function getDevices(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/devices", { signal }));
}
export function getDeviceDrivers(signal?: AbortSignal) {
  return apiData(apiClient.GET("/api/v1/devices/drivers", { signal }));
}
export function saveDevice(body: DeviceSaveCommand) {
  return apiData(apiClient.POST("/api/v1/devices", { body }));
}
export async function renameDevice(view: DeviceView, label: string): Promise<DeviceView> {
  const device = await apiData(
    apiClient.POST("/api/v1/devices/{device_id}/name", {
      params: { path: { device_id: view.device.id } },
      body: { label },
    }),
  );
  return { ...view, device };
}
export function prepareDeviceAccess(deviceId: string) {
  return apiData(
    apiClient.POST("/api/v1/devices/{device_id}/access", {
      params: { path: { device_id: deviceId } },
    }),
  );
}
export function retireDevice(device: DeviceView) {
  return apiData(
    apiClient.POST("/api/v1/devices/{device_id}/retirement", {
      params: { path: { device_id: device.device.id } },
      body: { expected_head: device.device.head },
    }),
  );
}
export function testDeviceConnection(device: DeviceView, operationId: string) {
  return apiData(
    apiClient.POST("/api/v1/devices/{device_id}/connection-tests", {
      params: { path: { device_id: device.device.id } },
      body: {
        expected_head: device.device.head,
        operation_id: operationId,
        actor: "local-operator",
      },
    }),
  );
}
