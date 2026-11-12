import { measurementKey } from "./measurement-identity";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  listPendingMeasurements,
  readPendingMeasurementReceipt,
  type MeasurementSave,
} from "./direct-measurement-api";
import { acquisitionTargets } from "./MeasurementWorkbench";
import type { CurrentMeasurementResult } from "./CurrentMeasurement";
import { waitForInstrumentResponse } from "./instrument-request-wait";

/** Read retained receipts only. A recovered result is never evidence of current device state. */
export function usePendingMeasurements(
  instrumentId: string,
  onRecovered: (result: CurrentMeasurementResult) => void,
) {
  const receiver = useRef(onRecovered);
  useEffect(() => {
    receiver.current = onRecovered;
  }, [onRecovered]);
  const known = useRef(new Set<string>());
  const generation = useRef(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string>();
  const remember = useCallback((selection: MeasurementSave) => {
    known.current.add(measurementKey(selection));
  }, []);
  const recover = useCallback(async () => {
    const request = ++generation.current;
    setBusy(true);
    setError(undefined);
    try {
      const listing = await waitForInstrumentResponse(listPendingMeasurements(instrumentId));
      for (const item of listing.items) {
        if (generation.current !== request) return;
        const key = measurementKey(item.selection);
        if (known.current.has(key)) continue;
        const target = acquisitionTargets(item.description).find(
          (candidate) =>
            candidate.interfaceId === item.intent.interface_id &&
            candidate.acquisition.id === item.intent.acquisition_id &&
            JSON.stringify(candidate.componentPath) ===
              JSON.stringify(item.intent.component_path ?? []),
        );
        if (!target) throw new Error("原采集说明不完整，待保存结果仍保留；请重新核查。");
        const receipt = await waitForInstrumentResponse(
          readPendingMeasurementReceipt(item.selection),
        );
        if (generation.current !== request) return;
        if (known.current.has(key)) continue;
        if (receipt.status !== "collected")
          throw new Error("后台未返回成功采集回执；待保存结果仍待核查。");
        known.current.add(key);
        receiver.current({
          selection: item.selection,
          target,
          receipt,
          receivedAt: item.collected_at,
          relationshipChanged: "从后台恢复的原采集结果，不代表当前设备状态",
        });
      }
    } catch (reason) {
      if (generation.current === request)
        setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (generation.current === request) setBusy(false);
    }
  }, [instrumentId]);
  useEffect(() => {
    const initial = generation.current;
    void Promise.resolve().then(() => {
      if (generation.current === initial) void recover();
    });
    return () => {
      generation.current += 1;
    };
  }, [recover]);
  return { recover, remember, busy, error };
}
