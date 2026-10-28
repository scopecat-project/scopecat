// Shared with the request: navigation must not silently change the fetch bound.
export const MEASUREMENT_WINDOW_LIMIT = 4096;

export function lastMeasurementWindowOffset(pointCount: number): number | undefined {
  if (!Number.isSafeInteger(pointCount) || pointCount <= 0) return undefined;
  return Math.floor((pointCount - 1) / MEASUREMENT_WINDOW_LIMIT) * MEASUREMENT_WINDOW_LIMIT;
}
