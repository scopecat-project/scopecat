import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { MeasurementDatasetSchema, MeasurementRecord } from "../../api-contract";
import { apiClient, apiData } from "../../api-client";
import { MeasurementChart, TraceAvailabilityDetails } from "../runs/MeasurementDataPreview";
import {
  measurementEntityAxes,
  measurementTraceChart,
  measurementTraceQueryPlans,
  measurementTraceStatus,
} from "../runs/measurement-visualization";

export function CaptureTraces({
  contentHash,
  runId,
  selection,
  offset,
  records,
  schema,
}: {
  contentHash: string;
  runId: string;
  selection: "acquired" | "selected";
  offset: number;
  records: Pick<MeasurementRecord, "point_index">[];
  schema: MeasurementDatasetSchema;
}) {
  const plans = useMemo(() => measurementTraceQueryPlans(schema), [schema]);
  const [requestedPlan, setRequestedPlan] = useState<string>();
  const [recordIndex, setRecordIndex] = useState(0);
  const [entityIndex, setEntityIndex] = useState<number>();
  const plan = plans.find((item) => item.id === requestedPlan) ?? plans[0];
  const entityAxis = measurementEntityAxes(schema).find((axis) => axis.id === plan?.entityAxisId);
  const preview = useQuery({
    queryKey: [
      "data",
      "traces",
      contentHash,
      runId,
      selection,
      offset + recordIndex,
      plan?.id,
      entityIndex,
    ],
    enabled: plan !== undefined && records.length > 0,
    queryFn: ({ signal }) =>
      apiData(
        apiClient.POST("/api/v1/data/captures/{content_hash}/runs/{run_id}/recording/traces", {
          params: {
            path: { content_hash: contentHash, run_id: runId },
            query: { selection, offset: offset + recordIndex, limit: 1 },
          },
          body: {
            observable_id: plan!.observableId,
            coordinate_id: plan!.coordinateId,
            value_mode: plan!.valueMode,
            max_series: 32,
            max_samples: 4096,
            downsampling: "minmax",
            entity_indices: entityIndex === undefined ? undefined : [entityIndex],
          },
          signal,
        }),
      ),
  });
  const chart = useMemo(() => measurementTraceChart(preview.data), [preview.data]);
  if (plans.length === 0 || records.length === 0) return null;
  return (
    <section className="space-y-3" aria-label="Captured waveforms">
      <p>Waveforms for one record on this page. Long traces use bounded min/max sampling.</p>
      <label>
        Waveform record{" "}
        <select
          value={recordIndex}
          onChange={(event) => setRecordIndex(Number(event.target.value))}
        >
          {records.map((record, index) => (
            <option key={index} value={index}>
              {selection === "acquired" ? "Acquisition" : "Selected record"} {offset + index + 1} ·
              Point {record.point_index}
            </option>
          ))}
        </select>
      </label>
      <label>
        Waveform{" "}
        <select
          value={plan?.id}
          onChange={(event) => {
            setRequestedPlan(event.target.value);
            setEntityIndex(undefined);
          }}
        >
          {plans.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </select>
      </label>
      {entityAxis && (
        <label>
          Waveform entity{" "}
          <select
            value={entityIndex ?? "all"}
            onChange={(event) =>
              setEntityIndex(event.target.value === "all" ? undefined : Number(event.target.value))
            }
          >
            <option value="all">All entities (bounded preview)</option>
            {entityAxis.members.map((member) => (
              <option key={member.identity} value={member.index}>
                {member.label}
              </option>
            ))}
          </select>
        </label>
      )}
      {preview.isPending && <p role="status">Reading waveform preview…</p>}
      {preview.error && <p role="alert">{preview.error.message}</p>}
      {preview.data && (
        <>
          <p role="status">{measurementTraceStatus(preview.data)}</p>
          {chart && <MeasurementChart chart={chart} />}
          <TraceAvailabilityDetails preview={preview.data} />
        </>
      )}
    </section>
  );
}
