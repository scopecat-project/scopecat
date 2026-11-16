import type { LaunchCloseStatus } from "../launch/launch-close-barrier";

export type DeviceCloseStatus = LaunchCloseStatus | "cancelled";

// One controller per WebView. No session or lease crosses a window boundary.
type Controller = (
  signal: AbortSignal,
  choosing: () => Promise<void>,
) => Promise<DeviceCloseStatus>;
let controller: Controller | undefined;
const requests = new Map<
  string,
  { controller: AbortController; result: Promise<DeviceCloseStatus> }
>();

export function registerDeviceClose(next: Controller) {
  controller = next;
  return () => {
    if (controller === next) {
      controller = undefined;
      for (const request of requests.values()) request.controller.abort();
    }
  };
}

export function prepareDeviceClose(
  requestId: string,
  choosing: () => Promise<void> = async () => {},
): Promise<DeviceCloseStatus> {
  const existing = requests.get(requestId);
  if (existing) return existing.result;
  if (!controller) return Promise.resolve("saved");
  const request = new AbortController();
  const selected = controller;
  const result = new Promise<DeviceCloseStatus>((resolve) => {
    const cancelled = () => resolve("unknown");
    request.signal.addEventListener("abort", cancelled, { once: true });
    // Releasing the close intent must not wait for or abort a submitted HTTP operation.
    void Promise.resolve()
      .then(() =>
        request.signal.aborted ? ("unknown" as const) : selected(request.signal, choosing),
      )
      .then(resolve, () => resolve("unknown"))
      .finally(() => request.signal.removeEventListener("abort", cancelled));
  });
  requests.set(requestId, { controller: request, result });
  return result;
}

export function releaseDeviceClose(requestId: string) {
  requests.get(requestId)?.controller.abort();
  requests.delete(requestId);
}
