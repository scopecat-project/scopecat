import type { LaunchCloseStatus } from "../launch/launch-close-barrier";

export type DeviceCloseStatus = LaunchCloseStatus | "cancelled";

// One controller per WebView. No session or lease crosses a window boundary.
type Controller = (
  signal: AbortSignal,
  choosing: () => Promise<void>,
) => Promise<DeviceCloseStatus>;
let controller: Controller | undefined;
const requests = new Map<string, AbortController>();

export function registerDeviceClose(next: Controller) {
  controller = next;
  return () => {
    if (controller === next) {
      controller = undefined;
      for (const request of requests.values()) request.abort();
    }
  };
}

export async function prepareDeviceClose(
  requestId: string,
  choosing: () => Promise<void> = async () => {},
): Promise<DeviceCloseStatus> {
  if (!controller) return "saved";
  const request = new AbortController();
  requests.set(requestId, request);
  try {
    return await controller(request.signal, choosing);
  } catch {
    return "unknown";
  }
}

export function releaseDeviceClose(requestId: string) {
  requests.get(requestId)?.abort();
  requests.delete(requestId);
}
