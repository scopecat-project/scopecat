import { useQuery } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { apiClient, apiData } from "../../api-client";

export function RunMeasuredCosts({ runId }: { runId: string }) {
  const query = useQuery({
    queryKey: ["run-measured-costs", runId],
    queryFn: () =>
      apiData(
        apiClient.GET("/api/v1/runs/{run_id}/measured-costs", {
          params: { path: { run_id: runId } },
        }),
      ),
    refetchInterval: 2000,
  });
  if (query.error) return <p role="status">Measured costs unavailable: {query.error.message}</p>;
  return query.data ? <RunMeasuredCostsContent costs={query.data} /> : null;
}

const seconds = (value: number | null | undefined) =>
  value == null ? "Unavailable" : `${value.toPrecision(4)} s`;
const bytes = (value: number | null | undefined) => (value == null ? "Unavailable" : `${value} B`);

export function RunMeasuredCostsContent({
  costs,
}: {
  costs: components["schemas"]["RunMeasuredCosts"];
}) {
  const operations = costs.operations ?? [];
  const finalizations = costs.finalizations ?? [];
  return (
    <section aria-label="Measured run costs" className="rounded border border-line p-4 space-y-2">
      <h3 className="font-semibold">Measured run costs</h3>
      <p>
        Initial planning: {seconds(costs.compilation?.seconds)} · Lazy target compilation:
        Unavailable
      </p>
      <p>
        Hardware finalization:{" "}
        {finalizations.length
          ? finalizations.map((item) => seconds(item.seconds)).join(", ")
          : "Unavailable"}{" "}
        · Terminal commit: Unavailable
      </p>
      <p className="text-sm">
        Measured wall intervals may overlap adapter transfer and acquisition intervals. They are not
        an additive total. Retained bytes are a live-storage gauge. Warm means connection reuse;
        each admitted run still establishes its setup.
      </p>
      {costs.truncated && (
        <p role="status">Partial history: only the latest 128 saved events are represented.</p>
      )}
      <details>
        <summary>{operations.length} saved physical operation measurements</summary>
        {operations.length === 0 && <p>Operation measurements unavailable.</p>}
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Operation / connection</th>
                <th>Server wall</th>
                <th>Transfer</th>
                <th>Acquire</th>
                <th>Uploaded</th>
                <th>Reused</th>
                <th>Rendered</th>
                <th>Retained</th>
              </tr>
            </thead>
            <tbody>
              {operations.map((item, index) => (
                <tr key={`${item.operation_id}-${item.operation}-${index}`}>
                  <td>
                    {item.instrument_id} / {item.operation} / {item.status}
                    <br />
                    {item.connection_context} · {item.connection_generation.slice(0, 12)}
                    <br />
                    {item.measured?.source ?? "Adapter measurements unavailable"}
                    <br />
                    {item.measured?.unavailable_reason}
                  </td>
                  <td>{seconds(item.wall_seconds)}</td>
                  <td>{seconds(item.measured?.transfer_seconds)}</td>
                  <td>{seconds(item.measured?.acquire_seconds)}</td>
                  <td>{bytes(item.measured?.uploaded_bytes)}</td>
                  <td>{bytes(item.measured?.reused_bytes)}</td>
                  <td>{bytes(item.measured?.rendered_bytes)}</td>
                  <td>{bytes(item.measured?.retained_bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
