import type { LaunchCloseStatus } from "../launch/launch-close-barrier";

// One controller per WebView. No session or lease crosses a window boundary.
type Controller = (signal: AbortSignal) => Promise<LaunchCloseStatus>;
let controller: Controller | undefined;
const requests = new Map<string, AbortController>();

export function registerDeviceClose(next: Controller) {
  controller = next;
  return () => {
    if (controller === next) controller = undefined;
  };
}

export async function prepareDeviceClose(requestId: string): Promise<LaunchCloseStatus> {
  if (!controller) return "saved";
  const request = new AbortController();
  requests.set(requestId, request);
  try {
    return await controller(request.signal);
  } catch {
    return "unknown";
  }
}

export function releaseDeviceClose(requestId: string) {
  requests.get(requestId)?.abort();
  requests.delete(requestId);
}
