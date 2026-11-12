import type { MeasurementSave } from "./direct-measurement-api";
export function measurementKey(selection: MeasurementSave) {
  return JSON.stringify([selection.session_id, selection.instrument_id, selection.command_id]);
}
export function sameMeasurement(left: MeasurementSave, right: MeasurementSave) {
  return measurementKey(left) === measurementKey(right);
}
