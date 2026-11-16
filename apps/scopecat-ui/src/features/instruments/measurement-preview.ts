import { useEffect, useState } from "react";
import type { components } from "../../api-schema";
import type { MeasurementSave } from "./direct-measurement-api";
import { measurementKey } from "./measurement-identity";
import { ResultPreviewCache } from "./result-preview-cache";
export type MeasurementPreview = components["schemas"]["DirectMeasurementPreview"];
export type PreviewField = components["schemas"]["DirectMeasurementPreviewField"];
export type PreviewSource = { recordId: string } | { selection: MeasurementSave };
export const previewSourceKey = (source: PreviewSource) =>
  "recordId" in source ? `saved:${source.recordId}` : measurementKey(source.selection);
const cache = new ResultPreviewCache<MeasurementPreview>();
const MAX_BYTES = 256 * 1024;
// Serialize downloads, including comparison views, rather than fan out raw-sized requests.
let tail: Promise<unknown> = Promise.resolve();
export function readMeasurementPreview(
  source: PreviewSource,
  start: number,
  signal: AbortSignal,
  requestIds?: string[],
) {
  if (signal.aborted) return Promise.reject(signal.reason);
  const key = JSON.stringify([previewSourceKey(source), start, requestIds ?? null]);
  const cached = cache.get(key);
  if (cached) return Promise.resolve(cached);
  const task = tail
    .catch(() => {})
    .then(async () => {
      signal.throwIfAborted();
      const available = cache.get(key);
      if (available) return available;
      const path =
        "recordId" in source
          ? `/api/v1/direct-measurements/${encodeURIComponent(source.recordId)}/preview`
          : "/api/v1/direct-measurements/pending/preview";
      const identity =
        "selection" in source
          ? {
              session_id: source.selection.session_id,
              instrument_id: source.selection.instrument_id,
              command_id: source.selection.command_id,
            }
          : {};
      const boundedSignal = AbortSignal.any([signal, AbortSignal.timeout(15_000)]);
      const response = await fetch(
        new URL(path, globalThis.location?.origin ?? "http://localhost"),
        {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({
            ...identity,
            start,
            limit: 512,
            ...(requestIds ? { request_ids: requestIds } : {}),
          }),
          signal: boundedSignal,
        },
      );
      if (!response.ok) throw new Error(`无法读取局部结果（${response.status}）；原始结果未改变。`);
      if (!response.body) throw new Error("后台未返回预览内容");
      const reader = response.body.getReader();
      const chunks: Uint8Array[] = [];
      let bytes = 0;
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          bytes += chunk.value.byteLength;
          if (bytes > MAX_BYTES) {
            await reader.cancel();
            throw new Error("预览超过安全大小限制");
          }
          chunks.push(chunk.value);
        }
      } finally {
        reader.releaseLock();
      }
      signal.throwIfAborted();
      const content = new Uint8Array(bytes);
      let offset = 0;
      for (const chunk of chunks) {
        content.set(chunk, offset);
        offset += chunk.byteLength;
      }
      const value = JSON.parse(new TextDecoder().decode(content)) as MeasurementPreview;
      if (
        value.method !== "exact_slice" ||
        !Array.isArray(value.fields) ||
        value.fields.reduce((count, field) => count + field.values.length, 0) > 4096
      )
        throw new Error("后台返回无效的有界预览");
      cache.set(key, value, bytes);
      return value;
    });
  tail = task;
  return task;
}
export function useMeasurementPreview(source: PreviewSource, start: number, requestIds?: string[]) {
  const key = JSON.stringify([previewSourceKey(source), start, requestIds ?? null]);
  const [state, setState] = useState<{ key: string; data?: MeasurementPreview; error?: Error }>();
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    readMeasurementPreview(source, start, controller.signal, requestIds).then(
      (data) => {
        if (!controller.signal.aborted) setState({ key, data });
      },
      (error) => {
        if (!controller.signal.aborted)
          setState({ key, error: error instanceof Error ? error : new Error(String(error)) });
      },
    );
    return () => controller.abort();
    // The key captures the complete immutable source identity and window.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, retry]);
  const current = state?.key === key ? state : undefined;
  return {
    data: current?.data,
    error: current?.error,
    isPending: !current,
    retry: () => {
      setState(undefined);
      setRetry((n) => n + 1);
    },
  };
}
