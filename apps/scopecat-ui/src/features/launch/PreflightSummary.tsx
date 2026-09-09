import type { components } from "../../api-schema";
import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";
import { PlannedSettings } from "./PlannedSettings";

type Quantity =
  | components["schemas"]["ExactQuantity"]
  | components["schemas"]["BoundedQuantity"]
  | components["schemas"]["UnknownQuantity"];

function QuantityValue({ quantity }: { quantity: Quantity }) {
  return (
    <span className="block" title={quantity.basis}>
      {quantity.kind === "exact"
        ? `Exact: ${quantity.value} ${quantity.unit}`
        : quantity.kind === "bounded"
          ? `Bounded: ${quantity.lower}–${quantity.upper} ${quantity.unit}`
          : `Unknown (${quantity.unit})`}
      <span className="block text-xs text-muted-foreground">{quantity.basis}</span>
    </span>
  );
}
const costLabels: Record<string, string> = {
  wall_time: "Wall-clock time",
  waveform_playback_time: "Waveform playback time",
  waveform_bytes: "Waveform buffer",
  result_bytes: "Result buffer",
  retained_bytes: "Retained dataset size",
  batches: "Total batches",
  batch_point_capacity: "Batch point capacity",
};

export function PreflightSummary({
  entry,
  preview,
}: {
  entry: LaunchCatalogEntry;
  preview: LaunchPreview;
}) {
  return (
    <div className="space-y-3">
      <h3 role="status" className="font-semibold">
        Preview ready
      </h3>
      <p>{preview.summary}</p>
      <p>
        {preview.point_count} initial points in the first experiment · Configuration{" "}
        {preview.config_source.kind === "parameter_context"
          ? preview.config_source.context.entry_id
          : preview.config_source.entry_id}
      </p>
      <p>
        {entry.configuration_effect === "none"
          ? "This procedure does not change the default configuration."
          : entry.configuration_effect === "candidate"
            ? "Produces a candidate. Accepting it as default is a separate operator action."
            : "Changes the default configuration only after review."}
      </p>
      {entry.review && <p>{entry.review.instructions}</p>}
      {!preview.preflight ? (
        <p>
          Detailed preflight not provided. Procedure scope, shots and cost estimates are unknown.
        </p>
      ) : (
        <>
          <p>{preview.preflight.scope_basis}</p>
          {preview.preflight.stages.map((stage) => (
            <section
              key={stage.id}
              aria-label={stage.label}
              className="border rounded p-3 space-y-2"
            >
              <h4 className="font-semibold">{stage.label}</h4>
              <p>{stage.configuration_meaning}</p>
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <dt>Selected entities</dt>
                <dd>
                  {stage.entity_ids === null ? "Unknown" : stage.entity_ids.join(", ") || "None"}
                </dd>
                <dt>Planned executions</dt>
                <dd>
                  <QuantityValue quantity={stage.executions} />
                </dd>
                <dt>
                  {stage.point_scope === "static_plan"
                    ? "Planned points per execution"
                    : "Possible acquired points"}
                </dt>
                <dd>
                  <QuantityValue quantity={stage.points_per_execution} />
                </dd>
                <dt>Initial proposed points</dt>
                <dd>{stage.initial_proposed_points}</dd>
                <dt>Shots per point per entity</dt>
                <dd>
                  <QuantityValue quantity={stage.shots_per_point_per_entity} />
                </dd>
              </dl>
              <p className="text-sm">
                Preview budget: {stage.sampled_points}/{stage.sampled_point_limit} displayed points;{" "}
                {stage.selected_points}/{stage.selected_point_limit} selected points inspected.
              </p>
              <table className="w-full text-sm">
                <caption className="text-left font-medium">Products and retention</caption>
                <thead>
                  <tr>
                    <th className="text-left">Product</th>
                    <th>Retention</th>
                    <th>Dimensions / shape (scope below)</th>
                    <th>Type / unit</th>
                  </tr>
                </thead>
                <tbody>
                  {stage.products.map((product) => (
                    <tr key={`${product.retention}:${product.id}`}>
                      <td>{product.id}</td>
                      <td>
                        {product.retention === "retained"
                          ? "Retained (planned dataset)"
                          : "Transient (per point)"}
                      </td>
                      <td>
                        {product.dims.length
                          ? product.dims
                              .map((dim, i) => `${dim}: ${product.shape[i] ?? "unknown"}`)
                              .join(" × ")
                          : "scalar"}
                      </td>
                      <td>
                        {product.dtype}
                        {product.unit ? ` / ${product.unit}` : ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <ul className="text-sm space-y-2">
                {stage.costs.map((cost, i) => (
                  <li key={`${cost.metric}:${i}`}>
                    <strong>{costLabels[cost.metric] ?? cost.metric}</strong> (
                    {cost.scope === "inspected_artifact" ? "inspected artifact only" : cost.scope})
                    {cost.target_id && <span> · Target: {cost.target_id}</span>}
                    <QuantityValue quantity={cost.quantity} />
                  </li>
                ))}
              </ul>
              <PlannedSettings stage={stage} />
              <details>
                <summary>Selected-point inspection and configuration fingerprint</summary>
                <pre className="overflow-auto text-xs p-3">
                  {JSON.stringify(
                    {
                      config_content_hash: stage.config_content_hash,
                      inspections: stage.inspections,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </section>
          ))}
        </>
      )}
      <details>
        <summary>Resolved inputs and configuration</summary>
        <pre className="overflow-auto text-xs p-3">
          {JSON.stringify(
            { inputs: preview.resolved_inputs, config_source: preview.config_source },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}
