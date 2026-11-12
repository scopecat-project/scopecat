// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { listPendingMeasurements, readPendingMeasurementReceipt } from "./direct-measurement-api";
import { usePendingMeasurements } from "./use-pending-measurements";
import type { InstrumentCollectReceipt } from "../../api-contract";
vi.mock("./direct-measurement-api", () => ({
  listPendingMeasurements: vi.fn(),
  readPendingMeasurementReceipt: vi.fn(),
}));
const item: Awaited<ReturnType<typeof listPendingMeasurements>>["items"][number] = {
  selection: {
    session_id: "old-session",
    instrument_id: "scope",
    command_id: "original-collect",
    title: "",
    note: "",
    line_id: "",
    line_label: "",
    series_id: "",
  },
  collected_at: "2026-11-12T01:00:00Z",
  description: {
    instrument_id: "scope",
    implementation_id: "original-driver",
    implementation_version: "1",
    interfaces: [
      {
        id: "original-interface",
        acquisitions: [{ id: "sweep", label: "Original sweep", results: [] }],
      },
    ],
  },
  intent: {
    instrument_id: "scope",
    command_id: "original-collect",
    interface_id: "original-interface",
    acquisition_id: "sweep",
    component_path: [],
    result_ids: [],
    retain_result: true,
  },
};
const receipt: InstrumentCollectReceipt = { status: "collected", problems: [] };
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(listPendingMeasurements).mockResolvedValue({ items: [item] });
  vi.mocked(readPendingMeasurementReceipt).mockResolvedValue(receipt);
});
afterEach(cleanup);
it("recovers the original result and description across sessions, then skips already known identities", async () => {
  const receive = vi.fn();
  const { result } = renderHook(() => usePendingMeasurements("scope", receive));
  await waitFor(() => expect(receive).toHaveBeenCalledOnce());
  expect(receive.mock.calls[0]![0]).toMatchObject({
    selection: item.selection,
    target: { interfaceId: "original-interface", acquisition: { label: "Original sweep" } },
    receipt,
    receivedAt: item.collected_at,
  });
  expect(receive.mock.calls[0]![0].relationshipChanged).toContain("不代表当前设备状态");
  await act(async () => {
    await result.current.recover();
  });
  expect(receive).toHaveBeenCalledOnce();
  expect(readPendingMeasurementReceipt).toHaveBeenCalledExactlyOnceWith(item.selection);
});
it("does not resurrect a locally resolved result when its pending receipt read arrives late", async () => {
  let resolve!: (value: InstrumentCollectReceipt) => void;
  vi.mocked(readPendingMeasurementReceipt).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const receive = vi.fn();
  const { result } = renderHook(() => usePendingMeasurements("scope", receive));
  await waitFor(() => expect(readPendingMeasurementReceipt).toHaveBeenCalledOnce());
  act(() => result.current.remember(item.selection));
  await act(async () => {
    resolve(receipt);
  });
  expect(receive).not.toHaveBeenCalled();
});
it("ignores receipt recovery after unmount", async () => {
  let resolve!: (value: InstrumentCollectReceipt) => void;
  vi.mocked(readPendingMeasurementReceipt).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const receive = vi.fn();
  const { unmount } = renderHook(() => usePendingMeasurements("scope", receive));
  await waitFor(() => expect(readPendingMeasurementReceipt).toHaveBeenCalledOnce());
  unmount();
  await act(async () => {
    resolve(receipt);
  });
  expect(receive).not.toHaveBeenCalled();
});
it("retains existing recovered results on listing failure and supports explicit retry", async () => {
  const receive = vi.fn();
  const { result } = renderHook(() => usePendingMeasurements("scope", receive));
  await waitFor(() => expect(receive).toHaveBeenCalledOnce());
  vi.mocked(listPendingMeasurements).mockRejectedValueOnce(new Error("offline"));
  await act(async () => {
    await result.current.recover();
  });
  expect(result.current.error).toBe("offline");
  expect(receive).toHaveBeenCalledOnce();
  await act(async () => {
    await result.current.recover();
  });
  expect(result.current.error).toBeUndefined();
  expect(receive).toHaveBeenCalledOnce();
});

it("recovers reused command IDs from distinct sessions independently", async () => {
  const other = { ...item, selection: { ...item.selection, session_id: "another-session" } };
  vi.mocked(listPendingMeasurements).mockResolvedValue({ items: [item, other] });
  const receive = vi.fn();
  renderHook(() => usePendingMeasurements("scope", receive));
  await waitFor(() => expect(receive).toHaveBeenCalledTimes(2));
  expect(receive.mock.calls.map(([result]) => result.selection.session_id)).toEqual([
    "old-session",
    "another-session",
  ]);
  expect(readPendingMeasurementReceipt).toHaveBeenCalledTimes(2);
});
