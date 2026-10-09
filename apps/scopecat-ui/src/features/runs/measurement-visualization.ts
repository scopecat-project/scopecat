import type {
  ComplexComponents,
  MeasurementDatasetSchema,
  MeasurementRecord as ScientificMeasurementRecord,
  MeasurementTracePreview,
  MeasurementValue as ScientificMeasurementValue,
} from "../../api-contract";
import type { components } from "../../api-schema";

type MeasurementRecord =
  | ScientificMeasurementRecord
  | components["schemas"]["MeasurementRecordPreview"];
type MeasurementValue =
  | ScientificMeasurementValue
  | components["schemas"]["MeasurementArraySummary"];

type SchemaVariable = NonNullable<MeasurementDatasetSchema["variables"]>[number];
type ProductGridSchemaAxis = Extract<
  MeasurementDatasetSchema["point_domain"],
  { kind: "product_grid" }
>["axes"][number];
type VariableRole = SchemaVariable["role"];
type MeasurementScalar = Extract<MeasurementValue, { kind: "scalar" }>;
type MeasurementScalarValue = Extract<MeasurementValue, { kind: "scalar" }>["value"];
type MeasurementArray = Extract<MeasurementValue, { kind: "array" }>;
type MeasurementPartitionedArray = Extract<MeasurementValue, { kind: "partitioned_array" }>;

interface VariableDescriptor {
  id: string;
  role: VariableRole;
  dtype: SchemaVariable["dtype"];
  unit?: string;
  dims: string[];
  label: string;
  recordingGroupId?: string;
  entityAxisId?: string;
  entityAxisOffset?: number;
}

interface NumericPoint {
  x: number;
  y: number;
  color?: number;
}

export type NumericValueMode = "imag" | "magnitude" | "phase" | "real" | "value";

const COMPLEX_VALUE_MODES: NumericValueMode[] = ["magnitude", "phase", "real", "imag"];

export interface MeasurementChartSeries {
  id: string;
  label: string;
  points: NumericPoint[];
  status?: string;
}

export interface MeasurementEntityAxisMember {
  id: string;
  identity: string;
  index: number;
  kind?: string;
  label: string;
}

export interface MeasurementEntityAxis {
  id: string;
  label: string;
  members: MeasurementEntityAxisMember[];
  size: number;
}

export type MeasurementEntitySelection = Readonly<Record<string, readonly number[]>>;

export interface MeasurementChartGrid {
  xValues: number[];
  yValues: number[];
}

interface MeasurementChartFixedCoordinateBase {
  id: string;
  label: string;
  unit?: string;
  disambiguateIndex?: boolean;
}

export type MeasurementChartFixedCoordinate = MeasurementChartFixedCoordinateBase &
  ({ value: MeasurementScalarValue; index?: number } | { value?: never; index: number });

export interface MeasurementSliceAxis {
  id: string;
  label: string;
  unit?: string;
  size: number;
  source: ProductGridSchemaAxis["source"];
}

export interface MeasurementHeatmapCapability {
  xAxis: MeasurementSliceAxis;
  yAxis: MeasurementSliceAxis;
  observableIds: string[];
}

export interface MeasurementSliceSelection {
  xAxisId: string;
  yAxisId?: string;
}

export interface MeasurementSlicePlan {
  numericAxes: MeasurementSliceAxis[];
  scalarObservableIds: string[];
  varyingAxes: MeasurementSliceAxis[];
  fixedAxes: MeasurementSliceAxis[];
  variableIds: string[];
  heatmap?: MeasurementHeatmapCapability;
}

export interface MeasurementTraceQueryPlan {
  id: string;
  label: string;
  observableId: string;
  coordinateId?: string;
  valueMode: NumericValueMode;
  entityAxisId?: string;
}

interface MeasurementChartPlanBase {
  id: string;
  title: string;
  xLabel: string;
  yLabel: string;
  colorLabel?: string;
  note?: string;
  series: MeasurementChartSeries[];
  layout?: "overlay" | "small-multiples";
}

export type MeasurementChartPlan = MeasurementChartPlanBase &
  (
    | {
        kind: "heatmap";
        grid: MeasurementChartGrid;
        fixedCoordinates: MeasurementChartFixedCoordinate[];
      }
    | {
        kind: "color-scatter" | "line" | "scatter";
        fixedCoordinates?: never;
        grid?: never;
      }
  );

export interface MeasurementTableColumn {
  id: string;
  label: string;
  role: "point" | VariableRole;
}

export interface MeasurementTableRow {
  id: string;
  cells: string[];
}

export interface MeasurementTableModel {
  columns: MeasurementTableColumn[];
  rows: MeasurementTableRow[];
}

/** Derive only schema-safe automatic plots; unsupported shapes remain tabular. */
export function planMeasurementCharts(
  records: MeasurementRecord[],
  schema?: MeasurementDatasetSchema,
  fixedAxisIndices?: Readonly<Record<string, number>>,
  entitySelection: MeasurementEntitySelection = {},
  selection?: MeasurementSliceSelection,
): MeasurementChartPlan[] {
  if (records.length === 0 || schema === undefined) return [];
  const variables = variableDescriptors(schema);
  const observables = orderObservables(variables, schema);
  const entityAxes = measurementEntityAxes(schema);
  const entityAxisById = new Map(entityAxes.map((axis) => [axis.id, axis]));
  if (selection && schema.point_domain.kind === "product_grid") {
    const plan = measurementSlicePlan(schema, selection);
    if (!plan) return [];
    if (plan.heatmap) {
      return productGridHeatmaps(
        records,
        variables,
        observables,
        schema,
        fixedAxisIndices,
        selection,
      );
    }
    const coordinate = plan.varyingAxes[0]?.id;
    const selectedVariables = variables.filter(
      (variable) => variable.role !== "coordinate" || variable.id === coordinate,
    );
    const expected = plan.varyingAxes[0]?.size;
    const scalarPlans = observables
      .filter((variable) => variable.dims.length === 1 && isNumericVariable(variable))
      .flatMap((observable) =>
        scalarChart(records, selectedVariables, observable, schema, coordinate).map(
          (chart): MeasurementChartPlan => ({
            ...chart,
            id: `slice:${observable.id}:${chart.id.split(":").at(-1)}`,
            grid: undefined,
            fixedCoordinates: undefined,
            kind:
              chart.kind === "line" &&
              chart.series.every((series) => series.points.length === expected)
                ? "line"
                : "scatter",
            note: [
              chart.note,
              "Only available values are plotted; missing points are not interpolated.",
            ]
              .filter(Boolean)
              .join(" "),
          }),
        ),
      );
    const entityPlans = observables.flatMap((observable) => {
      const axis = observable.entityAxisId
        ? entityAxisById.get(observable.entityAxisId)
        : undefined;
      if (!axis || observable.dims.length !== 2 || !isNumericVariable(observable)) return [];
      return entityScalarCharts(
        records,
        selectedVariables,
        observable,
        axis,
        selectedEntityIndices(axis, entitySelection),
        schema,
        coordinate,
      ).map((chart): MeasurementChartPlan => ({
        ...chart,
        id: `slice:${observable.id}:${axis.id}:${chart.id.split(":").at(-1)}`,
        grid: undefined,
        fixedCoordinates: undefined,
        kind:
          chart.kind === "line" && chart.series.every((series) => series.points.length === expected)
            ? "line"
            : "scatter",
      }));
    });
    return [...scalarPlans, ...entityPlans];
  }
  const gridPlans = productGridHeatmaps(records, variables, observables, schema, fixedAxisIndices);
  const colorPlans = pointCloudColorCharts(records, variables, observables, schema);
  const entityPlans = observables.flatMap((observable) => {
    const axis = observable.entityAxisId ? entityAxisById.get(observable.entityAxisId) : undefined;
    if (!axis || !isNumericVariable(observable)) return [];
    const selected = selectedEntityIndices(axis, entitySelection);
    if (observable.dims.length === 2) {
      return entityScalarCharts(records, variables, observable, axis, selected, schema);
    }
    return [];
  });
  const scalarPlans = observables
    .filter((variable) => variable.dims.length === 1)
    .flatMap((observable) => scalarChart(records, variables, observable, schema));
  return [...gridPlans, ...colorPlans, ...entityPlans, ...scalarPlans];
}

/** Choose bounded server projection axes for one product-grid slice. */
export function measurementSlicePlan(
  schema?: MeasurementDatasetSchema,
  selection?: MeasurementSliceSelection,
): MeasurementSlicePlan | undefined {
  if (schema?.point_domain.kind !== "product_grid") return undefined;
  const variables = variableDescriptors(schema);
  const variablesById = new Map(variables.map((variable) => [variable.id, variable]));
  const axes: ProductGridAxis[] = schema.point_domain.axes.map((axis) => {
    const variable = variablesById.get(axis.id);
    return {
      axis,
      variable:
        variable && variable.role === "coordinate" && variable.dims.length === 1
          ? variable
          : undefined,
    };
  });
  if (axes.length === 0 || axes.some(({ axis }) => !validGridSize(axis.size))) return undefined;
  const numericAxes = axes.filter(hasRealNumericVariable);
  const xAxis = numericAxes.find(({ axis }) => axis.id === selection?.xAxisId) ?? numericAxes[0];
  const yAxis = numericAxes.find(
    ({ axis }) => axis.id === selection?.yAxisId && axis.id !== xAxis?.axis.id,
  );
  const varyingAxes =
    selection && xAxis
      ? [xAxis, ...(yAxis ? [yAxis] : [])]
      : numericAxes.length > 0
        ? numericAxes.slice(0, 2)
        : axes.slice(0, 1);
  const varyingAxisIds = new Set(varyingAxes.map(({ axis }) => axis.id));
  const observables = variables.filter(
    (variable) =>
      variable.role === "observable" && variable.dims.length === 1 && isNumericVariable(variable),
  );
  // Entity-local traces remain on bounded/live record paths instead of inflating a 4,096-point slice.
  const entitySliceVariables = variables.filter(
    (variable) =>
      variable.entityAxisId !== undefined &&
      variable.dims.length === 2 &&
      isNumericVariable(variable),
  );
  const heatmapAxes = selection
    ? varyingAxes.filter(hasRealNumericVariable)
    : numericAxes.slice(0, 2);
  const heatmap =
    heatmapAxes.length === 2 && observables.length > 0
      ? {
          xAxis: sliceAxisDescriptor(heatmapAxes[0]!),
          yAxis: sliceAxisDescriptor(heatmapAxes[1]!),
          observableIds: observables.map((observable) => observable.id),
        }
      : undefined;
  return {
    numericAxes: numericAxes.map(sliceAxisDescriptor),
    scalarObservableIds: observables.map((variable) => variable.id),
    varyingAxes: varyingAxes.map(sliceAxisDescriptor),
    fixedAxes: axes.filter(({ axis }) => !varyingAxisIds.has(axis.id)).map(sliceAxisDescriptor),
    variableIds: [
      ...new Set([
        ...(selection ? axes : varyingAxes).flatMap(({ variable }) =>
          variable && (selection || isNumericVariable(variable)) ? [variable.id] : [],
        ),
        ...observables.map((observable) => observable.id),
        ...entitySliceVariables.map((variable) => variable.id),
      ]),
    ],
    heatmap,
  };
}

/** Pair aligned rank-one coordinates and observables for trace previews. */
export function measurementTraceQueryPlans(
  schema?: MeasurementDatasetSchema,
): MeasurementTraceQueryPlan[] {
  if (!schema) return [];
  const variables = variableDescriptors(schema);
  const coordinates = variables.filter(
    (variable) =>
      variable.role === "coordinate" && hasTraceShape(variable) && isRealNumericVariable(variable),
  );
  const observables = orderObservables(
    variables.filter(
      (variable) =>
        variable.role === "observable" && hasTraceShape(variable) && isNumericVariable(variable),
    ),
    schema,
  );
  return observables.flatMap((observable) => {
    const alignedCoordinates = coordinates.filter(
      (coordinate) =>
        coordinate.dims.every((dimension, index) => dimension === observable.dims[index]) &&
        coordinate.entityAxisId === observable.entityAxisId,
    );
    const compatibleCoordinates = alignedCoordinates.filter(
      (coordinate) => coordinate.recordingGroupId === observable.recordingGroupId,
    );
    const selectedCoordinates: Array<VariableDescriptor | undefined> =
      compatibleCoordinates.length > 0
        ? compatibleCoordinates
        : alignedCoordinates.length === 0
          ? [undefined]
          : [];
    const sampleDimensionId = observable.dims
      .slice(1)
      .find((dimension) => dimension !== observable.entityAxisId)!;
    return selectedCoordinates.flatMap((coordinate) =>
      valueModes(observable).map((valueMode) => ({
        id: `trace:${observable.id}:${coordinate?.id ?? sampleDimensionId}:${valueMode}`,
        label:
          compatibleCoordinates.length <= 1
            ? chartTitle(observable, valueMode)
            : `${chartTitle(observable, valueMode)} by ${coordinate!.label}`,
        observableId: observable.id,
        ...(coordinate ? { coordinateId: coordinate.id } : {}),
        valueMode,
        ...(observable.entityAxisId ? { entityAxisId: observable.entityAxisId } : {}),
      })),
    );
  });
}

function hasTraceShape(variable: VariableDescriptor): boolean {
  const localDimensions = variable.dims.slice(1);
  if (variable.entityAxisId === undefined) return localDimensions.length === 1;
  return localDimensions.length === 2 && localDimensions.includes(variable.entityAxisId);
}

export function measurementTraceChart(
  preview?: MeasurementTracePreview,
): MeasurementChartPlan | undefined {
  if (!preview || preview.series.length === 0) return undefined;
  const observable: VariableDescriptor = {
    id: preview.observable_id,
    role: "observable",
    dtype: preview.value_mode === "value" ? "float64" : "complex128",
    unit: preview.value_unit ?? preview.observable_unit ?? undefined,
    dims: ["point", preview.dimension_id],
    label: preview.observable_label ?? dimensionLabel(preview.observable_id),
  };
  const series = preview.series.map((candidate) => ({
    id: [candidate.logical_point_id ?? "point", candidate.point_index, candidate.entity_index].join(
      ":",
    ),
    label: candidate.label,
    points: candidate.x.map((x, index) => ({ x, y: candidate.y[index]! })),
    status:
      candidate.available_sample_count < candidate.source_sample_count
        ? `${candidate.available_sample_count}/${candidate.source_sample_count} available${candidate.unavailable_reasons.length > 0 ? ` · ${candidate.unavailable_reasons.join(", ")}` : ""}`
        : undefined,
  }));
  const monotonic = preview.series.every((candidate) => strictlyMonotonicValues(candidate.x));
  return {
    id: `trace:${preview.observable_id}:${preview.coordinate_id}:${preview.value_mode}`,
    kind: monotonic ? "line" : "scatter",
    layout: preview.layout === "small_multiples" ? "small-multiples" : "overlay",
    title: chartTitle(observable, preview.value_mode),
    xLabel: labelWithUnit(
      preview.coordinate_label ?? dimensionLabel(preview.coordinate_id),
      preview.coordinate_unit,
    ),
    yLabel: chartValueLabel(observable, preview.value_mode),
    note: chartNote(preview.value_mode),
    series,
  };
}

export function measurementTraceStatus(preview: MeasurementTracePreview): string {
  const series = `${preview.returned_series_count.toLocaleString()} plotted`;
  const failures =
    preview.failures.length > 0
      ? `${preview.failures.length.toLocaleString()} unavailable`
      : undefined;
  const pending =
    preview.inspected_series_count - preview.returned_series_count - preview.failures.length;
  const pendingStatus = pending > 0 ? `${pending.toLocaleString()} not durable` : undefined;
  const selection = `${preview.inspected_series_count.toLocaleString()} of ${preview.selected_series_count.toLocaleString()} selected examined`;
  const truncation = preview.truncated_series ? "series limit applied" : undefined;
  const samples = preview.samples_reduced
    ? `${preview.returned_sample_count.toLocaleString()} of ${preview.source_sample_count.toLocaleString()} source samples plotted · unavailable samples omitted or min/max downsampling applied`
    : `${preview.returned_sample_count.toLocaleString()} source samples plotted`;
  return [series, failures, pendingStatus, selection, truncation, samples]
    .filter((item) => item !== undefined)
    .join(" · ");
}

function sliceAxisDescriptor({ axis, variable }: ProductGridAxis): MeasurementSliceAxis {
  return {
    id: axis.id,
    label: variable?.label ?? dimensionLabel(axis.id),
    unit: variable?.unit,
    size: axis.size,
    source: axis.source,
  };
}

/** Read one compact product-grid coordinate without expanding the whole axis. */
export function measurementSliceAxisValue(
  axis: MeasurementSliceAxis,
  index: number,
): MeasurementScalar | null | undefined {
  const source = axis.source;
  if (source.kind === "values") return source.values[index];
  if (source.kind === "range") {
    return generatedScalar(
      source.start,
      interpolatedValue(
        source.start.value as number,
        source.stop.value as number,
        axis.size,
        index,
      ),
    );
  }
  const center = source.center.value as number;
  const span = source.span.value as number;
  const lastIndex = axis.size - 1;
  const value =
    index === 0
      ? center - span / 2
      : index === lastIndex
        ? center + span / 2
        : center + (span * (2 * index - lastIndex)) / (2 * lastIndex);
  return generatedScalar(source.center, value);
}

export function measurementSliceAxisValueIsDuplicated(
  axis: MeasurementSliceAxis,
  index: number,
): boolean {
  const scalar = measurementSliceAxisValue(axis, index);
  if (scalar === null || scalar === undefined) return false;
  const sameValue = (candidate: MeasurementScalar | null | undefined) =>
    candidate !== null &&
    candidate !== undefined &&
    JSON.stringify([candidate.dtype, candidate.unit, candidate.value]) ===
      JSON.stringify([scalar.dtype, scalar.unit, scalar.value]);
  if (axis.source.kind === "values") {
    return axis.source.values.some(
      (candidate, candidateIndex) => candidateIndex !== index && sameValue(candidate),
    );
  }
  return (
    (index > 0 && sameValue(measurementSliceAxisValue(axis, index - 1))) ||
    (index + 1 < axis.size && sameValue(measurementSliceAxisValue(axis, index + 1)))
  );
}

function generatedScalar(template: MeasurementScalar, value: number): MeasurementScalar {
  return { ...template, value };
}

function interpolatedValue(start: number, stop: number, size: number, index: number): number {
  if (index === 0) return start;
  if (index === size - 1) return stop;
  const difference = stop - start;
  if (Number.isFinite(difference)) return start + index * (difference / (size - 1));
  const weight = index / (size - 1);
  return start * (1 - weight) + stop * weight;
}

export function measurementTable(
  records: MeasurementRecord[],
  schema?: MeasurementDatasetSchema,
  entitySelection: MeasurementEntitySelection = {},
): MeasurementTableModel {
  const variables = schema ? variableDescriptors(schema) : [];
  const entityAxisById = new Map(measurementEntityAxes(schema).map((axis) => [axis.id, axis]));
  const columns: MeasurementTableColumn[] = [
    { id: "point", label: "Point", role: "point" },
    ...variables.map((variable) => ({
      id: variable.id,
      label: valueLabel(variable),
      role: variable.role,
    })),
  ];
  return {
    columns,
    rows: records.map((record, index) => ({
      id: `${record.logical_point_id ?? record.point_index}:${index}`,
      cells: [
        String(record.point_index),
        ...variables.map((variable) => {
          const axis = variable.entityAxisId
            ? entityAxisById.get(variable.entityAxisId)
            : undefined;
          return formatMeasurementValue(
            valueFor(record, variable),
            variable,
            axis,
            axis ? selectedEntityIndices(axis, entitySelection) : [],
          );
        }),
      ],
    })),
  };
}

function scalarChart(
  records: MeasurementRecord[],
  variables: VariableDescriptor[],
  observable: VariableDescriptor,
  schema: MeasurementDatasetSchema,
  coordinateId?: string,
): MeasurementChartPlan[] {
  const candidates = variables.filter(
    (variable) => variable.role === "coordinate" && variable.dims.length === 1,
  );
  const coordinate =
    coordinateId !== undefined
      ? candidates.find((candidate) => candidate.id === coordinateId)
      : orderCoordinates(candidates, schema).find((candidate) =>
          records.some((record) => numericScalar(valueFor(record, candidate)) !== undefined),
        );
  return valueModes(observable).flatMap((mode) => {
    const points = records.flatMap((record) => {
      const y = numericScalar(valueFor(record, observable), mode);
      const x = coordinate ? numericScalar(valueFor(record, coordinate)) : record.point_index;
      return x === undefined || y === undefined ? [] : [{ x, y }];
    });
    if (points.length === 0 && coordinateId === undefined) return [];
    return [
      {
        id: `scalar:${observable.id}:${coordinate?.id ?? "point"}:${mode}`,
        kind:
          schema.point_domain.kind === "point_cloud" || !strictlyMonotonic(points)
            ? "scatter"
            : "line",
        title: chartTitle(observable, mode),
        xLabel: coordinate ? valueLabel(coordinate) : "Point index",
        yLabel: chartValueLabel(observable, mode),
        note: chartNote(mode),
        series: [{ id: observable.id, label: observable.label, points }],
      },
    ];
  });
}

function entityScalarCharts(
  records: MeasurementRecord[],
  variables: VariableDescriptor[],
  observable: VariableDescriptor,
  axis: MeasurementEntityAxis,
  selected: number[],
  schema: MeasurementDatasetSchema,
  coordinateId?: string,
): MeasurementChartPlan[] {
  const coordinate = orderCoordinates(
    variables.filter((variable) => variable.role === "coordinate" && variable.dims.length === 1),
    schema,
  ).find((candidate) =>
    coordinateId !== undefined
      ? candidate.id === coordinateId
      : records.some((record) => numericScalar(valueFor(record, candidate)) !== undefined),
  );
  return valueModes(observable).flatMap((mode) => {
    const series = selected.map((entityIndex) => {
      const member = axis.members[entityIndex]!;
      const points = records.flatMap((record) => {
        const value = numericEntityScalar(
          valueFor(record, observable),
          observable,
          entityIndex,
          mode,
        );
        const x = coordinate ? numericScalar(valueFor(record, coordinate)) : record.point_index;
        return x === undefined || value === undefined ? [] : [{ x, y: value }];
      });
      return {
        id: `${observable.id}:${member.identity}`,
        label: member.label,
        points,
      };
    });
    const available = series.reduce((count, candidate) => count + candidate.points.length, 0);
    if (available === 0 && coordinateId === undefined) return [];
    const expected = records.length * selected.length;
    const monotonic = series
      .filter((candidate) => candidate.points.length > 0)
      .every((candidate) => strictlyMonotonic(candidate.points));
    const availabilityNote =
      available === expected
        ? `${selected.length} entity ${selected.length === 1 ? "series is" : "series are"} complete.`
        : `${expected - available} unavailable entity values are omitted.`;
    return [
      {
        id: `entity-scalar:${observable.id}:${axis.id}:${coordinate?.id ?? "point"}:${mode}`,
        kind: schema.point_domain.kind === "product_grid" && monotonic ? "line" : "scatter",
        title: `${chartTitle(observable, mode)} by ${axis.label}`,
        xLabel: coordinate ? valueLabel(coordinate) : "Point index",
        yLabel: chartValueLabel(observable, mode),
        note: [chartNote(mode), availabilityNote].filter(Boolean).join(" "),
        series,
      },
    ];
  });
}

interface EntityValuePart {
  shape: number[];
  valid: unknown;
  values: unknown;
}

function entityValuePart(
  value: MeasurementValue | undefined,
  variable: VariableDescriptor,
  entityIndex: number,
): EntityValuePart | undefined {
  const offset = variable.entityAxisOffset;
  if (offset === undefined || value === undefined || value.kind === "unavailable") return undefined;
  if (value.kind === "segmented_array") {
    if (offset !== 0) return undefined;
    const segment = value.segments[entityIndex];
    if (!segment || segment.kind === "unavailable") return undefined;
    return {
      shape: [...segment.shape],
      valid: segment.availability?.valid ?? true,
      values: segment.values,
    };
  }
  if (value.kind !== "array" && value.kind !== "partitioned_array") return undefined;
  if (value.kind === "partitioned_array") {
    return partitionedEntityValuePart(value, offset, entityIndex);
  }
  const values = treeAtAxis(value.values, offset, entityIndex);
  if (values === undefined) return undefined;
  return {
    shape: value.shape.filter((_extent, index) => index !== offset),
    valid:
      value.availability === null || value.availability === undefined
        ? true
        : treeAtAxis(value.availability.valid, offset, entityIndex),
    values,
  };
}

function partitionedEntityValuePart(
  value: MeasurementPartitionedArray,
  entityAxis: number,
  entityIndex: number,
): EntityValuePart | undefined {
  const logicalShape = measurementArrayShape(value);
  const localShape = logicalShape.filter((_extent, index) => index !== entityAxis);
  if (value.axis === entityAxis) {
    let offset = 0;
    for (const partition of value.partitions) {
      const extent = partition.shape[entityAxis]!;
      if (entityIndex < offset + extent) {
        const localIndex = entityIndex - offset;
        return {
          shape: localShape,
          values: treeAtAxis(partition.values, entityAxis, localIndex),
          valid: partition.availability
            ? treeAtAxis(partition.availability.valid, entityAxis, localIndex)
            : true,
        };
      }
      offset += extent;
    }
    return undefined;
  }
  const partitionAxis = value.axis - (entityAxis < value.axis ? 1 : 0);
  const values = value.partitions.map((partition) =>
    treeAtAxis(partition.values, entityAxis, entityIndex),
  );
  if (values.some((selected) => selected === undefined)) return undefined;
  const partial = value.partitions.some((partition) => partition.availability !== undefined);
  return {
    shape: localShape,
    values: concatenateTrees(values, partitionAxis),
    valid: partial
      ? concatenateTrees(
          value.partitions.map((partition) =>
            partition.availability
              ? treeAtAxis(partition.availability.valid, entityAxis, entityIndex)
              : trueTree(partition.shape.filter((_extent, index) => index !== entityAxis)),
          ),
          partitionAxis,
        )
      : true,
  };
}

function numericEntityScalar(
  value: MeasurementValue | undefined,
  variable: VariableDescriptor,
  entityIndex: number,
  mode: NumericValueMode,
): number | undefined {
  const part = entityValuePart(value, variable, entityIndex);
  if (!part || part.shape.length !== 0 || part.valid !== true) return undefined;
  return numericLeaf(part.values, mode);
}

function treeAtAxis(value: unknown, axis: number, index: number): unknown {
  if (!Array.isArray(value)) return undefined;
  if (axis === 0) return value[index];
  return value.map((child) => treeAtAxis(child, axis - 1, index));
}

function productGridHeatmaps(
  records: MeasurementRecord[],
  variables: VariableDescriptor[],
  observables: VariableDescriptor[],
  schema: MeasurementDatasetSchema,
  fixedAxisIndices?: Readonly<Record<string, number>>,
  selection?: MeasurementSliceSelection,
): MeasurementChartPlan[] {
  if (schema.point_domain.kind !== "product_grid") return [];
  const heatmap = measurementSlicePlan(schema, selection)?.heatmap;
  if (!heatmap) return [];
  const variablesById = new Map(variables.map((variable) => [variable.id, variable]));
  const axes: ProductGridAxis[] = schema.point_domain.axes.map((axis) => {
    const variable = variablesById.get(axis.id);
    return {
      axis,
      variable:
        variable && variable.role === "coordinate" && variable.dims.length === 1
          ? variable
          : undefined,
    };
  });
  const xAxis = axes.find(({ axis }) => axis.id === heatmap.xAxis.id);
  const yAxis = axes.find(({ axis }) => axis.id === heatmap.yAxis.id);
  if (!xAxis || !yAxis) return [];
  if (!hasRealNumericVariable(xAxis) || !hasRealNumericVariable(yAxis)) return [];
  const fixedAxes = axes.filter((axis) => axis !== xAxis && axis !== yAxis);
  const selectedCoordinates = selectedFixedCoordinates(fixedAxes, fixedAxisIndices ?? {});
  if (selectedCoordinates === undefined) return [];

  const heatmapObservableIds = new Set(heatmap.observableIds);
  return observables
    .filter((observable) => heatmapObservableIds.has(observable.id))
    .flatMap((observable) =>
      valueModes(observable).flatMap((mode) =>
        [{ records, fixedCoordinates: selectedCoordinates }].flatMap(
          (slice): MeasurementChartPlan[] => {
            const grid = completeGrid(
              slice.records,
              xAxis.variable,
              yAxis.variable,
              observable,
              mode,
              xAxis.axis.size,
              yAxis.axis.size,
            );
            if (!grid) {
              if (!selection) return [];
              const points = records.flatMap((record) => {
                const x = numericScalar(valueFor(record, xAxis.variable));
                const y = numericScalar(valueFor(record, yAxis.variable));
                const color = numericScalar(valueFor(record, observable), mode);
                return x === undefined || y === undefined || color === undefined
                  ? []
                  : [{ x, y, color }];
              });
              return [
                {
                  id: `slice:${observable.id}:${mode}`,
                  kind: "color-scatter" as const,
                  title: chartTitle(observable, mode),
                  xLabel: valueLabel(xAxis.variable),
                  yLabel: valueLabel(yAxis.variable),
                  colorLabel: chartValueLabel(observable, mode),
                  note: "Incomplete or repeated coordinates: available points only, without interpolation.",
                  series: [{ id: observable.id, label: observable.label, points }],
                },
              ];
            }
            const fixedCoordinates = slice.fixedCoordinates;
            return [
              {
                id: selection
                  ? `slice:${observable.id}:${mode}`
                  : heatmapId(observable, xAxis.variable, yAxis.variable, mode, fixedCoordinates),
                kind: "heatmap" as const,
                title: `${chartTitle(observable, mode)} heatmap`,
                xLabel: valueLabel(xAxis.variable),
                yLabel: valueLabel(yAxis.variable),
                colorLabel: chartValueLabel(observable, mode),
                fixedCoordinates,
                grid: { xValues: grid.xValues, yValues: grid.yValues },
                note: selection ? chartNote(mode) : productGridNote(mode),
                series: [{ id: observable.id, label: observable.label, points: grid.points }],
              },
            ];
          },
        ),
      ),
    );
}

interface ProductGridAxis {
  axis: ProductGridSchemaAxis;
  variable?: VariableDescriptor;
}

function selectedFixedCoordinates(
  axes: ProductGridAxis[],
  fixedAxisIndices: Readonly<Record<string, number>>,
): MeasurementChartFixedCoordinate[] | undefined {
  const selected: MeasurementChartFixedCoordinate[] = [];
  for (const { axis, variable } of axes) {
    const index = fixedAxisIndices[axis.id];
    if (index === undefined || index < 0 || index >= axis.size) return undefined;
    const descriptor = sliceAxisDescriptor({ axis, variable });
    const scalar = measurementSliceAxisValue(descriptor, index);
    selected.push(
      scalar
        ? {
            id: axis.id,
            label: variable?.label ?? dimensionLabel(axis.id),
            unit: scalar.unit ?? variable?.unit,
            value: scalar.value,
            index,
            disambiguateIndex: measurementSliceAxisValueIsDuplicated(descriptor, index),
          }
        : {
            id: axis.id,
            label: variable?.label ?? dimensionLabel(axis.id),
            index,
          },
    );
  }
  return selected;
}

function hasRealNumericVariable(
  axis: ProductGridAxis,
): axis is ProductGridAxis & { variable: VariableDescriptor } {
  return axis.variable !== undefined && isRealNumericVariable(axis.variable);
}

function heatmapId(
  observable: VariableDescriptor,
  xVariable: VariableDescriptor,
  yVariable: VariableDescriptor,
  mode: NumericValueMode,
  fixedCoordinates: MeasurementChartFixedCoordinate[],
): string {
  const base = `heatmap:${observable.id}:${xVariable.id}:${yVariable.id}:${mode}`;
  if (fixedCoordinates.length === 0) return base;
  const fixed = fixedCoordinates
    .map(
      (coordinate) =>
        `${encodeURIComponent(coordinate.id)}=${encodeURIComponent(
          [
            coordinate.value === undefined ? "opaque" : scalarValueId(coordinate.value),
            ...(coordinate.index === undefined ? [] : [`index:${coordinate.index}`]),
          ].join("@"),
        )}`,
    )
    .join("&");
  return `${base}:fixed:${fixed}`;
}

function completeGrid(
  records: MeasurementRecord[],
  xVariable: VariableDescriptor,
  yVariable: VariableDescriptor,
  observable: VariableDescriptor,
  mode: NumericValueMode,
  xSize: number,
  ySize: number,
): { points: NumericPoint[]; xValues: number[]; yValues: number[] } | undefined {
  const points: NumericPoint[] = [];
  const xValues = new Set<number>();
  const yValues = new Set<number>();
  const pairs = new Map<number, Set<number>>();
  for (const record of records) {
    const x = numericScalar(valueFor(record, xVariable));
    const y = numericScalar(valueFor(record, yVariable));
    const color = numericScalar(valueFor(record, observable), mode);
    if (x === undefined || y === undefined || color === undefined) return undefined;
    const yAtX = pairs.get(x) ?? new Set<number>();
    if (yAtX.has(y)) return undefined;
    yAtX.add(y);
    pairs.set(x, yAtX);
    xValues.add(x);
    yValues.add(y);
    points.push({ x, y, color });
  }
  if (xValues.size !== xSize || yValues.size !== ySize || points.length !== xSize * ySize) {
    return undefined;
  }
  return {
    points,
    xValues: [...xValues].sort((left, right) => left - right),
    yValues: [...yValues].sort((left, right) => left - right),
  };
}

function validGridSize(size: number): boolean {
  return Number.isSafeInteger(size) && size > 0;
}

function pointCloudColorCharts(
  records: MeasurementRecord[],
  variables: VariableDescriptor[],
  observables: VariableDescriptor[],
  schema: MeasurementDatasetSchema,
): MeasurementChartPlan[] {
  if (schema.point_domain.kind !== "point_cloud") return [];
  const variablesById = new Map(variables.map((variable) => [variable.id, variable]));
  const coordinates = schema.point_domain.columns.flatMap((column) => {
    const variable = variablesById.get(column.id);
    return variable &&
      variable.role === "coordinate" &&
      variable.dims.length === 1 &&
      isNumericVariable(variable)
      ? [variable]
      : [];
  });
  const xVariable = coordinates[0];
  const yVariable = coordinates[1];
  if (!xVariable || !yVariable) return [];
  return observables
    .filter((observable) => observable.dims.length === 1 && isNumericVariable(observable))
    .flatMap((observable) =>
      valueModes(observable).flatMap((mode) => {
        const points = records.flatMap((record) => {
          const x = numericScalar(valueFor(record, xVariable));
          const y = numericScalar(valueFor(record, yVariable));
          const color = numericScalar(valueFor(record, observable), mode);
          return x === undefined || y === undefined || color === undefined ? [] : [{ x, y, color }];
        });
        if (points.length === 0) return [];
        return [
          {
            id: `color:${observable.id}:${xVariable.id}:${yVariable.id}:${mode}`,
            kind: "color-scatter" as const,
            title: `${chartTitle(observable, mode)} map`,
            xLabel: valueLabel(xVariable),
            yLabel: valueLabel(yVariable),
            colorLabel: chartValueLabel(observable, mode),
            note: chartNote(mode),
            series: [{ id: observable.id, label: observable.label, points }],
          },
        ];
      }),
    );
}

export function measurementEntityAxes(schema?: MeasurementDatasetSchema): MeasurementEntityAxis[] {
  if (!schema) return [];
  return schema.dimensions.flatMap((dimension) => {
    if (dimension.kind !== "entity" || !dimension.index) return [];
    const values = dimension.index.values;
    const kinds = new Set(values.map((entity) => entity.kind ?? null));
    const mixedKinds = kinds.size > 1;
    return [
      {
        id: dimension.id,
        label: dimension.label ?? dimensionLabel(dimension.id),
        members: values.map((entity, index) => {
          const kind = entity.kind ?? undefined;
          const identity = JSON.stringify([kind ?? null, entity.id]);
          const metadataLabel = entityMetadataLabel(entity.metadata);
          return {
            id: entity.id,
            identity,
            index,
            kind,
            label: mixedKinds ? `${kind ?? "∅"}:${entity.id}` : (metadataLabel ?? entity.id),
          };
        }),
        size: values.length,
      },
    ];
  });
}

function variableDescriptors(schema: MeasurementDatasetSchema): VariableDescriptor[] {
  const entityDimensionIds = new Set(measurementEntityAxes(schema).map((axis) => axis.id));
  return (schema.variables ?? []).map((variable) => schemaVariable(variable, entityDimensionIds));
}

function schemaVariable(
  variable: SchemaVariable,
  entityDimensionIds: ReadonlySet<string>,
): VariableDescriptor {
  const entityAxisId =
    variable.source_entity_products?.dimension_id ??
    variable.dims.find((dimension) => entityDimensionIds.has(dimension));
  const entityAxisOffset =
    entityAxisId === undefined ? undefined : variable.dims.slice(1).indexOf(entityAxisId);
  return {
    id: variable.id,
    role: variable.role,
    dtype: variable.dtype,
    unit: variable.unit ?? undefined,
    dims: [...variable.dims],
    label: variable.label ?? dimensionLabel(variable.id),
    recordingGroupId: variable.recording_group_id ?? undefined,
    entityAxisId,
    entityAxisOffset:
      entityAxisOffset === undefined || entityAxisOffset < 0 ? undefined : entityAxisOffset,
  };
}

function selectedEntityIndices(
  axis: MeasurementEntityAxis,
  selection: MeasurementEntitySelection,
): number[] {
  const requested = selection[axis.id];
  if (!requested || requested.length === 0) {
    return axis.members.map((member) => member.index);
  }
  const selected = [...new Set(requested)].filter(
    (index) => Number.isSafeInteger(index) && index >= 0 && index < axis.size,
  );
  return selected.length > 0 ? selected : axis.members.map((member) => member.index);
}

function entityMetadataLabel(metadata: unknown): string | undefined {
  if (!isObject(metadata)) return undefined;
  const label = metadata.label;
  return typeof label === "string" && label.length > 0 ? label : undefined;
}

function orderObservables(
  variables: VariableDescriptor[],
  schema: MeasurementDatasetSchema,
): VariableDescriptor[] {
  const observables = variables.filter((variable) => variable.role === "observable");
  const primary = new Map((schema.primary_observables ?? []).map((id, index) => [id, index]));
  return [...observables].sort(
    (left, right) =>
      (primary.get(left.id) ?? Number.MAX_SAFE_INTEGER) -
      (primary.get(right.id) ?? Number.MAX_SAFE_INTEGER),
  );
}

function orderCoordinates(
  variables: VariableDescriptor[],
  schema: MeasurementDatasetSchema,
): VariableDescriptor[] {
  const domainIds =
    schema.point_domain.kind === "product_grid"
      ? schema.point_domain.axes.map((axis) => axis.id)
      : schema.point_domain.columns.map((column) => column.id);
  const orderedIds = [...new Set([...domainIds, ...(schema.primary_coordinates ?? [])])];
  const primary = new Map(orderedIds.map((id, index) => [id, index]));
  return [...variables].sort(
    (left, right) =>
      (primary.get(left.id) ?? Number.MAX_SAFE_INTEGER) -
      (primary.get(right.id) ?? Number.MAX_SAFE_INTEGER),
  );
}

function isNumericVariable(variable: VariableDescriptor): boolean {
  return (
    variable.dtype === "float64" || variable.dtype === "int64" || variable.dtype === "complex128"
  );
}

function isRealNumericVariable(variable: VariableDescriptor): boolean {
  return variable.dtype === "float64" || variable.dtype === "int64";
}

function valueFor(
  record: MeasurementRecord,
  variable: VariableDescriptor,
): MeasurementValue | undefined {
  return variable.role === "coordinate"
    ? record.coordinates[variable.id]
    : record.observables[variable.id];
}

function numericScalar(
  value: MeasurementValue | undefined,
  mode: NumericValueMode = "value",
): number | undefined {
  return value?.kind === "scalar" ? numericLeaf(value.value, mode) : undefined;
}

function scalarValueId(value: MeasurementScalarValue): string {
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value !== "object") return String(value);
  return `${value.real}${value.imag < 0 ? "" : "+"}${value.imag}i`;
}

function numericLeaf(value: unknown, mode: NumericValueMode): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) {
    if (mode === "magnitude") return Math.abs(value);
    if (mode === "phase") return Math.atan2(0, value);
    if (mode === "imag") return 0;
    return value;
  }
  const complex = complexComponents(value);
  if (!complex) return undefined;
  if (mode === "phase") return Math.atan2(complex.imag, complex.real);
  if (mode === "real") return complex.real;
  if (mode === "imag") return complex.imag;
  return Math.hypot(complex.real, complex.imag);
}

function complexComponents(value: unknown): ComplexComponents | undefined {
  if (
    !isObject(value) ||
    typeof value.real !== "number" ||
    !Number.isFinite(value.real) ||
    typeof value.imag !== "number" ||
    !Number.isFinite(value.imag)
  ) {
    return undefined;
  }
  return { real: value.real, imag: value.imag };
}

function formatMeasurementValue(
  value: MeasurementValue | undefined,
  variable: VariableDescriptor,
  entityAxis: MeasurementEntityAxis | undefined,
  selectedEntities: number[],
): string {
  if (!value) return "—";
  if (value.kind === "array_summary") {
    const shape = value.shape.map((extent) => extent ?? "?").join(" × ");
    const available =
      value.sample_count === null
        ? `${value.available_sample_count} available`
        : `${value.available_sample_count}/${value.sample_count} available`;
    return [
      `${shape} samples${unitSuffix(value.unit)}`,
      available,
      ...value.unavailable_reasons,
      "preview summary",
    ].join(" · ");
  }
  if (entityAxis && variable.entityAxisOffset !== undefined) {
    return formatEntityMeasurementValue(value, variable, entityAxis, selectedEntities);
  }
  if (value.kind === "unavailable") return `Unavailable · ${value.reason}`;
  if (value.kind === "array" || value.kind === "partitioned_array") {
    const shape = measurementArrayShape(value);
    const size = shape.length > 0 ? shape.join(" × ") : "?";
    const partitions =
      value.kind === "partitioned_array" ? ` · ${value.partitions.length} partitions` : "";
    return `${size} samples${partitions}${unitSuffix(value.unit)}`;
  }
  if (value.kind === "segmented_array") {
    return `${value.segments.length} entity segments${unitSuffix(value.unit)}`;
  }
  return `${formatScalar(value.value)}${unitSuffix(value.unit)}`;
}

function formatEntityMeasurementValue(
  value: MeasurementValue,
  variable: VariableDescriptor,
  axis: MeasurementEntityAxis,
  selected: number[],
): string {
  const count =
    selected.length === axis.size ? String(axis.size) : `${selected.length}/${axis.size}`;
  const complete = selected.filter((entityIndex) =>
    entityValueIsComplete(value, variable.entityAxisOffset!, entityIndex),
  ).length;
  const shapes = entityLocalShapes(value, variable, selected);
  const shape = commonEntityShape(shapes);
  const shapeSummary =
    shape === undefined
      ? "variable length"
      : shape.length === 0
        ? undefined
        : `${shape.map((extent) => extent ?? "?").join(" × ")} samples each`;
  const availability =
    complete === selected.length ? "All available" : `${complete}/${selected.length} complete`;
  const reasons = entityFailureReasons(value, variable.entityAxisOffset!, selected);
  return [
    `${count} entities`,
    shapeSummary,
    value.unit ?? undefined,
    availability,
    complete < selected.length && reasons.length > 0 ? reasons.join(", ") : undefined,
  ]
    .filter(Boolean)
    .join(" · ");
}

function entityFailureReasons(
  value: MeasurementValue,
  entityAxisOffset: number,
  selected: number[],
): string[] {
  if (value.kind === "unavailable") return [value.reason];
  if (value.kind === "array" || value.kind === "partitioned_array") {
    const shape = measurementArrayShape(value);
    const selectedSet = new Set(selected);
    const entityStride = shape
      .slice(entityAxisOffset + 1)
      .reduce((product, extent) => product * extent, 1);
    const entityExtent = shape[entityAxisOffset]!;
    return distinctReasons(
      measurementUnavailableGroups(value, shape).filter((group) =>
        group.flat_indices.some((flatIndex) =>
          selectedSet.has(Math.floor(flatIndex / entityStride) % entityExtent),
        ),
      ),
    );
  }
  if (value.kind !== "segmented_array") return [];
  return [
    ...new Set(
      selected.flatMap((index) => {
        const segment = value.segments[index];
        if (!segment) return ["missing"];
        if (segment.kind === "unavailable") return [segment.reason];
        return segment.availability ? distinctReasons(segment.availability.unavailable) : [];
      }),
    ),
  ];
}

function distinctReasons(groups: { reason: "invalid" | "missing" | "overload" }[]): string[] {
  return [...new Set(groups.map((group) => group.reason))];
}

function entityLocalShapes(
  value: MeasurementValue,
  variable: VariableDescriptor,
  selected: number[],
): (number | null)[][] {
  const offset = variable.entityAxisOffset;
  if (offset === undefined || value.kind === "scalar" || value.kind === "array_summary") return [];
  if (value.kind === "segmented_array") {
    return selected.flatMap((index) => {
      const segment = value.segments[index];
      return segment ? [[...segment.shape]] : [];
    });
  }
  if (value.kind === "unavailable") return selected.map(() => [...value.shape]);
  const shape = measurementArrayShape(value);
  shape.splice(offset, 1);
  return selected.map(() => shape);
}

function measurementArrayShape(value: MeasurementArray | MeasurementPartitionedArray): number[] {
  if (value.kind === "array") return [...value.shape];
  const shape = [...value.partitions[0]!.shape];
  shape[value.axis] = value.partitions.reduce(
    (total, partition) => total + partition.shape[value.axis]!,
    0,
  );
  return shape;
}

function measurementUnavailableGroups(
  value: MeasurementArray | MeasurementPartitionedArray,
  shape: number[],
): NonNullable<MeasurementArray["availability"]>["unavailable"] {
  if (value.kind === "array") return value.availability?.unavailable ?? [];
  let axisOffset = 0;
  return value.partitions.flatMap((partition) => {
    const groups = (partition.availability?.unavailable ?? []).map((group) => ({
      ...group,
      flat_indices: group.flat_indices.map((flatIndex) => {
        const coordinate = unravelIndex(flatIndex, partition.shape);
        coordinate[value.axis] = coordinate[value.axis]! + axisOffset;
        return ravelIndex(coordinate, shape);
      }),
    }));
    axisOffset += partition.shape[value.axis]!;
    return groups;
  });
}

function entityValueIsComplete(
  value: MeasurementValue,
  entityAxis: number,
  entityIndex: number,
): boolean {
  if (value.kind === "unavailable" || value.kind === "scalar" || value.kind === "array_summary")
    return false;
  if (value.kind === "segmented_array") {
    const segment = value.segments[entityIndex];
    return (
      segment?.kind === "array" &&
      (segment.availability == null || treeIsAllTrue(segment.availability.valid))
    );
  }
  if (value.kind === "array") {
    return (
      value.availability == null ||
      treeIsAllTrue(treeAtAxis(value.availability.valid, entityAxis, entityIndex))
    );
  }
  if (value.axis === entityAxis) {
    let offset = 0;
    for (const partition of value.partitions) {
      const extent = partition.shape[entityAxis]!;
      if (entityIndex < offset + extent) {
        return (
          partition.availability == null ||
          treeIsAllTrue(treeAtAxis(partition.availability.valid, entityAxis, entityIndex - offset))
        );
      }
      offset += extent;
    }
    return false;
  }
  return value.partitions.every(
    (partition) =>
      partition.availability == null ||
      treeIsAllTrue(treeAtAxis(partition.availability.valid, entityAxis, entityIndex)),
  );
}

function concatenateTrees(values: unknown[], axis: number): unknown {
  if (axis === 0) {
    if (!values.every(Array.isArray)) {
      throw new Error("measurement partition axis does not match its values");
    }
    return values.flatMap((value) => value as unknown[]);
  }
  if (!values.every(Array.isArray)) {
    throw new Error("measurement partition rank does not match its values");
  }
  const arrays = values as unknown[][];
  const extent = arrays[0]!.length;
  if (arrays.some((value) => value.length !== extent)) {
    throw new Error("measurement partitions disagree outside their split axis");
  }
  return Array.from({ length: extent }, (_unused, index) =>
    concatenateTrees(
      arrays.map((value) => value[index]),
      axis - 1,
    ),
  );
}

function trueTree(shape: number[]): unknown {
  if (shape.length === 0) return true;
  return Array.from({ length: shape[0]! }, () => trueTree(shape.slice(1)));
}

function unravelIndex(flatIndex: number, shape: number[]): number[] {
  const coordinate = Array<number>(shape.length);
  let remaining = flatIndex;
  for (let axis = shape.length - 1; axis >= 0; axis -= 1) {
    const extent = shape[axis]!;
    coordinate[axis] = remaining % extent;
    remaining = Math.floor(remaining / extent);
  }
  return coordinate;
}

function ravelIndex(coordinate: number[], shape: number[]): number {
  return coordinate.reduce((flatIndex, value, axis) => flatIndex * shape[axis]! + value, 0);
}

function commonEntityShape(shapes: (number | null)[][]): (number | null)[] | undefined {
  const first = shapes[0];
  if (!first) return undefined;
  return shapes.every(
    (shape) =>
      shape.length === first.length && shape.every((extent, index) => extent === first[index]),
  )
    ? first
    : undefined;
}

function treeIsAllTrue(value: unknown): boolean {
  if (value === true) return true;
  return Array.isArray(value) && value.every(treeIsAllTrue);
}

function formatScalar(value: unknown): string {
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "True" : "False";
  if (typeof value === "string") return value;
  const complex = complexComponents(value);
  if (complex) {
    const sign = complex.imag < 0 ? "−" : "+";
    return `${formatNumber(complex.real)} ${sign} ${formatNumber(Math.abs(complex.imag))}i`;
  }
  return "—";
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 6 }).format(value);
}

function valueLabel(variable: VariableDescriptor, magnitude = false): string {
  const label = magnitude ? `|${variable.label}|` : variable.label;
  return labelWithUnit(label, variable.unit);
}

function labelWithUnit(label: string, unit?: string | null): string {
  return `${label}${unit ? ` [${unit}]` : ""}`;
}

function valueModes(variable: VariableDescriptor): NumericValueMode[] {
  return variable.dtype === "complex128" ? COMPLEX_VALUE_MODES : ["value"];
}

function chartTitle(variable: VariableDescriptor, mode: NumericValueMode): string {
  if (mode === "value") return variable.label;
  return `${variable.label} ${mode === "imag" ? "imaginary" : mode}`;
}

function chartValueLabel(variable: VariableDescriptor, mode: NumericValueMode): string {
  if (mode === "magnitude") return valueLabel(variable, true);
  if (mode === "phase") return `phase(${variable.label}) [rad]`;
  if (mode === "real") return `Re(${variable.label})${variable.unit ? ` [${variable.unit}]` : ""}`;
  if (mode === "imag") return `Im(${variable.label})${variable.unit ? ` [${variable.unit}]` : ""}`;
  return valueLabel(variable);
}

function chartNote(mode: NumericValueMode): string | undefined {
  if (mode === "magnitude") return "Complex values are shown as magnitude.";
  if (mode === "phase") return "Complex values are shown as phase in radians.";
  if (mode === "real") return "Complex values are shown as the real component.";
  if (mode === "imag") return "Complex values are shown as the imaginary component.";
  return undefined;
}

function productGridNote(mode: NumericValueMode): string {
  const valueNote = chartNote(mode);
  const axisNote = "X and Y follow the authored product-grid axis order.";
  return valueNote ? `${valueNote} ${axisNote}` : axisNote;
}

function unitSuffix(unit?: string | null): string {
  return unit ? ` ${unit}` : "";
}

function dimensionLabel(id: string): string {
  return id.replaceAll(/[-_/]+/g, " ").replaceAll(/\b\w/g, (letter) => letter.toLocaleUpperCase());
}

function strictlyMonotonic(points: NumericPoint[]): boolean {
  return strictlyMonotonicValues(points.map((point) => point.x));
}

function strictlyMonotonicValues(values: number[]): boolean {
  if (values.length < 2) return false;
  let direction = 0;
  for (let index = 1; index < values.length; index += 1) {
    const previous = values[index - 1];
    const current = values[index];
    if (previous === undefined || current === undefined || current === previous) return false;
    const nextDirection = current > previous ? 1 : -1;
    if (direction !== 0 && direction !== nextDirection) return false;
    direction = nextDirection;
  }
  return true;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
