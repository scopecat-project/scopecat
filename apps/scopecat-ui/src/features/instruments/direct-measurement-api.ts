import { decodeCollectReceipt, HARDWARE_RECEIPT_MEDIA_TYPE } from "./hardware-receipt-wire";
import { ApiError, apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
export type MeasurementSave = components["schemas"]["DirectMeasurementSave"];
export type SavedMeasurement = components["schemas"]["DirectMeasurement"];
export function listDirectMeasurements(before?: number) {
  return apiData(apiClient.GET("/api/v1/direct-measurements", { params: { query: { before } } }));
}
export function saveDirectMeasurement(selection: MeasurementSave) {
  return apiData(apiClient.POST("/api/v1/direct-measurements", { body: selection }));
}

export function discardDirectMeasurement(selection: MeasurementSave) {
  const { session_id, instrument_id, command_id } = selection;
  return apiData(
    apiClient.POST("/api/v1/direct-measurements/discard", {
      body: { session_id, instrument_id, command_id },
    }),
  ).then((receipt) => {
    if (
      receipt.status !== "discarded" &&
      receipt.status !== "saved" &&
      receipt.status !== "not_retained"
    )
      throw new Error("后台未确认结果处置，原结果仍保留待核查");
    if (receipt.status === "saved" && (typeof receipt.record_id !== "string" || !receipt.record_id))
      throw new Error("后台未返回已保存记录身份，原结果仍保留待核查");
    return receipt;
  });
}

export function listPendingMeasurements(instrumentId: string) {
  return apiData(
    apiClient.GET("/api/v1/direct-measurements/pending", {
      params: { query: { instrument_id: instrumentId } },
    }),
  );
}

export async function readPendingMeasurementReceipt(selection: MeasurementSave) {
  const { session_id, instrument_id, command_id } = selection;
  let response: Response;
  try {
    response = await globalThis.fetch(
      new URL(
        "/api/v1/direct-measurements/pending/receipt",
        globalThis.location?.origin ?? "http://localhost",
      ),
      {
        method: "POST",
        headers: { Accept: HARDWARE_RECEIPT_MEDIA_TYPE, "Content-Type": "application/json" },
        body: JSON.stringify({ session_id, instrument_id, command_id }),
      },
    );
  } catch {
    throw new ApiError("后台未响应待保存结果读取；原结果仍待核查。");
  }
  if (!response.ok) throw new ApiError("无法读取待保存结果；原结果仍待核查。", response.status);
  try {
    return decodeCollectReceipt(await response.arrayBuffer());
  } catch {
    throw new ApiError("后台返回了无效硬件回执；原结果仍待核查。");
  }
}
