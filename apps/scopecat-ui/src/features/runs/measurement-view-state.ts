import type { MeasurementDatasetSchema } from "../../api-contract";

// Entity membership is reconciled by exact kind/id below the view boundary.
// Reordering or adding members does not invalidate an existing entity choice.
export function measurementSelectionSchemaKey(schema?: MeasurementDatasetSchema): string {
  return JSON.stringify([
    schema?.dataset_id,
    schema?.point_domain,
    schema?.dimensions.map((dimension) =>
      dimension.kind === "entity" ? { id: dimension.id, kind: dimension.kind } : dimension,
    ),
    schema?.variables?.map(({ source_entity_products, ...variable }) => ({
      ...variable,
      entity_dimension: source_entity_products?.dimension_id,
    })),
  ]);
}
