import { DataType, tableFromIPC, type Field, type Table } from "apache-arrow";
import type { MeasurementRecord, MeasurementValue } from "../../api-contract";

type MeasurementArray = Extract<MeasurementValue, { kind: "array" }>;
type MeasurementPartitionedArray = Extract<MeasurementValue, { kind: "partitioned_array" }>;
type MeasurementSegmentedArray = Extract<MeasurementValue, { kind: "segmented_array" }>;
type MeasurementUnavailable = Extract<MeasurementValue, { kind: "unavailable" }>;
type MeasurementDType = MeasurementValue["dtype"];
type MeasurementLeaf = Extract<MeasurementValue, { kind: "scalar" }>["value"];
type ArrayItem = MeasurementArray["values"][number];
type ValidItem = NonNullable<MeasurementArray["availability"]>["valid"][number];

const textDecoder = new TextDecoder();

export function decodeMeasurementArrowRecord(content: ArrayBuffer): MeasurementRecord {
  const table = tableFromIPC(new Uint8Array(content));
  if (table.numRows !== 1) throw new Error("live measurement Arrow must contain one row");

  const coordinates: Record<string, MeasurementValue> = {};
  const observables: Record<string, MeasurementValue> = {};

  for (const field of table.schema.fields) {
    if (!field.name.startsWith("value:")) continue;
    const variableId = field.name.slice("value:".length);
    const role = requiredMetadata(field, "scopecat.variable_role");
    const dtype = requiredMetadata(field, "scopecat.variable_dtype");
    if (!isDType(dtype)) throw new Error("live measurement Arrow dtype is invalid");
    if (role !== "coordinate" && role !== "observable") {
      throw new Error("live measurement Arrow variable role is invalid");
    }
    const declaredKind = requiredMetadata(field, "scopecat.variable_kind");
    if (declaredKind !== "scalar" && declaredKind !== "array") {
      throw new Error("live measurement Arrow variable kind is invalid");
    }
    const unit = field.metadata.get("scopecat.variable_unit") ?? null;
    const metadata = decodeJsonColumn(table, `metadata:${variableId}`);
    const reason = columnValue(table, `unavailable_reason:${variableId}`);
    const shapeSidecar = decodeOptionalJsonColumn(table, `value_shape:${variableId}`);
    const availabilitySidecar = decodeJsonColumn(table, `availability:${variableId}`);
    const rawValue = plainValue(columnValue(table, field.name));
    let value: MeasurementValue;

    if (reason !== null) {
      if (!isReason(reason) || rawValue !== null) {
        throw new Error("live measurement Arrow unavailable value is invalid");
      }
      value = decodeUnavailable(reason, dtype, unit, shapeSidecar, metadata);
    } else if (declaredKind === "scalar") {
      value = {
        kind: "scalar",
        dtype,
        unit,
        value: measurementLeaf(rawValue, dtype),
        metadata,
      };
    } else if (shapeSidecar !== null && "segments" in shapeSidecar) {
      if (!isSegmentShapeSidecar(shapeSidecar)) {
        throw new Error("live measurement Arrow segmented shape is invalid");
      }
      value = decodeSegmentedArray(
        rawValue,
        dtype,
        unit,
        shapeSidecar.segments,
        availabilitySidecar,
        metadata,
      );
    } else if (hasPartitionShapeFields(shapeSidecar)) {
      if (!isPartitionShapeSidecar(shapeSidecar)) {
        throw new Error("live measurement Arrow partition shape is invalid");
      }
      value = decodePartitionedArray(
        rawValue,
        dtype,
        unit,
        shapeSidecar,
        availabilitySidecar,
        metadata,
      );
    } else {
      value = decodeArray(
        rawValue,
        dtype,
        unit,
        arrayShape(shapeSidecar, field),
        availabilitySidecar,
        metadata,
      );
    }

    (role === "coordinate" ? coordinates : observables)[variableId] = value;
  }

  return {
    run_id: requiredSchemaMetadata(table, "scopecat.run_id"),
    logical_point_id: logicalPointId(columnValue(table, "__scopecat.logical_point_id")),
    point_index: numericValue(columnValue(table, "__scopecat.point_index")),
    coordinates,
    observables,
    acquisition_evidence: decodeJsonColumn(table, "__scopecat.acquisition_evidence"),
    metadata: decodeJsonColumn(table, "__scopecat.record_metadata"),
  };
}

function decodePartitionedArray(
  rawValue: unknown,
  dtype: MeasurementDType,
  unit: string | null,
  shapeSidecar: PartitionShapeSidecar,
  availabilitySidecar: Record<string, unknown>,
  metadata: Record<string, unknown>,
): MeasurementPartitionedArray {
  validateShape(rawValue, shapeSidecar.shape);
  const diagnostics = availabilitySidecar.partitions;
  if (!Array.isArray(diagnostics) || diagnostics.length !== shapeSidecar.partition_sizes.length) {
    throw new Error("live measurement Arrow partition diagnostics are invalid");
  }
  let offset = 0;
  const partitions = shapeSidecar.partition_sizes.map((size, index) => {
    const rawDiagnostics = diagnostics[index];
    if (!Array.isArray(rawDiagnostics)) {
      throw new Error("live measurement Arrow partition diagnostics are invalid");
    }
    const shape = [...shapeSidecar.shape];
    shape[shapeSidecar.partition_axis] = size;
    const partition = decodeArray(
      sliceNestedAxis(rawValue, shapeSidecar.partition_axis, offset, size),
      dtype,
      unit,
      shape,
      { unavailable: rawDiagnostics },
      {},
    );
    offset += size;
    return partition;
  });
  return {
    kind: "partitioned_array",
    dtype,
    unit,
    axis: shapeSidecar.partition_axis,
    partitions,
    metadata,
  };
}

function sliceNestedAxis(value: unknown, axis: number, offset: number, size: number): unknown {
  if (!Array.isArray(value)) {
    throw new Error("live measurement Arrow partitioned value is invalid");
  }
  if (axis === 0) return value.slice(offset, offset + size);
  return value.map((child) => sliceNestedAxis(child, axis - 1, offset, size));
}

function decodeUnavailable(
  reason: MeasurementUnavailable["reason"],
  dtype: MeasurementDType,
  unit: string | null,
  shapeSidecar: Record<string, unknown> | null,
  metadata: Record<string, unknown>,
): MeasurementUnavailable {
  const shape = shapeSidecar?.shape;
  if (!isShape(shape)) throw new Error("live measurement Arrow unavailable shape is invalid");
  return {
    kind: "unavailable",
    reason,
    dtype,
    unit,
    shape,
    metadata,
  };
}

function decodeArray(
  rawValue: unknown,
  dtype: MeasurementDType,
  unit: string | null,
  shape: number[],
  availabilitySidecar: Record<string, unknown>,
  metadata: Record<string, unknown>,
): MeasurementArray {
  validateShape(rawValue, shape);
  const unavailable = unavailableGroups(availabilitySidecar);
  const missing: number[] = [];
  let leafIndex = 0;
  const collectMissing = (item: unknown): void => {
    if (Array.isArray(item)) item.forEach(collectMissing);
    else {
      if (item === null) missing.push(leafIndex);
      leafIndex += 1;
    }
  };
  collectMissing(rawValue);
  const indices = unavailable.flatMap((group) => group.flat_indices).sort((a, b) => a - b);
  if (indices.length !== missing.length || indices.some((index, i) => index !== missing[i])) {
    throw new Error("live measurement Arrow availability does not match null leaves");
  }
  const decoded = decodeNullableArray(rawValue, dtype);
  if (!Array.isArray(decoded.values) || !Array.isArray(decoded.valid)) {
    throw new Error("live measurement Arrow array value is not nested");
  }
  const value: MeasurementArray = {
    kind: "array",
    dtype,
    unit,
    shape,
    values: decoded.values,
    metadata,
  };
  if (unavailable.length > 0) {
    value.availability = {
      valid: decoded.valid,
      unavailable,
    };
  }
  return value;
}

function decodeSegmentedArray(
  rawValue: unknown,
  dtype: MeasurementDType,
  unit: string | null,
  shapeSpecs: unknown[],
  availabilitySidecar: Record<string, unknown>,
  metadata: Record<string, unknown>,
): MeasurementSegmentedArray {
  if (!Array.isArray(rawValue)) {
    throw new Error("live measurement Arrow segmented value is invalid");
  }
  const diagnostics = availabilitySidecar.segments;
  if (!Array.isArray(diagnostics) || diagnostics.length !== shapeSpecs.length) {
    throw new Error("live measurement Arrow segmented diagnostics are invalid");
  }
  if (rawValue.length !== shapeSpecs.length) {
    throw new Error("live measurement Arrow segmented cardinality is invalid");
  }

  const segments = diagnostics.map((rawDiagnostic, index) => {
    if (!isJsonObject(rawDiagnostic)) {
      throw new Error("live measurement Arrow segmented diagnostics are invalid");
    }
    const segmentMetadata = rawDiagnostic.metadata;
    if (!isJsonObject(segmentMetadata)) {
      throw new Error("live measurement Arrow segmented metadata is invalid");
    }
    const shapeSpec = segmentShape(shapeSpecs[index]);
    if (rawDiagnostic.kind === "unavailable") {
      if (!isReason(rawDiagnostic.reason) || shapeSpec === null || rawValue[index] !== null) {
        throw new Error("live measurement Arrow unavailable segment is invalid");
      }
      return {
        kind: "unavailable" as const,
        reason: rawDiagnostic.reason,
        dtype,
        unit,
        shape: shapeSpec,
        metadata: segmentMetadata,
      };
    }
    if (rawDiagnostic.kind !== "array" || rawValue[index] === null) {
      throw new Error("live measurement Arrow available segment is invalid");
    }
    if (shapeSpec !== null && !isConcreteShape(shapeSpec)) {
      throw new Error("live measurement Arrow available segment shape is not concrete");
    }
    return decodeArray(
      rawValue[index],
      dtype,
      unit,
      shapeSpec ?? nestedShape(rawValue[index]),
      rawDiagnostic,
      segmentMetadata,
    );
  });

  return { kind: "segmented_array", dtype, unit, segments, metadata };
}

function decodeNullableArray(
  value: unknown,
  dtype: MeasurementDType,
): { values: ArrayItem; valid: ValidItem } {
  if (!Array.isArray(value)) {
    return {
      values: value === null ? unavailableFill(dtype) : measurementLeaf(value, dtype),
      valid: value !== null,
    };
  }
  const children = value.map((item) => decodeNullableArray(item, dtype));
  return {
    values: children.map((child) => child.values),
    valid: children.map((child) => child.valid),
  };
}

function unavailableFill(dtype: MeasurementDType): MeasurementLeaf {
  if (dtype === "string") return "";
  if (dtype === "bool") return false;
  if (dtype === "complex128") return { real: 0, imag: 0 };
  return 0;
}

function arrayShape(shapeSidecar: Record<string, unknown> | null, field: Field): number[] {
  if (shapeSidecar !== null) {
    if (!isConcreteShape(shapeSidecar.shape)) {
      throw new Error("live measurement Arrow array shape is invalid");
    }
    return shapeSidecar.shape;
  }
  return shapeFromType(field.type);
}

function segmentShape(value: unknown): (number | null)[] | null {
  if (value === null) return null;
  if (!isJsonObject(value) || !isShape(value.shape)) {
    throw new Error("live measurement Arrow segmented shape is invalid");
  }
  return value.shape;
}

type PartitionShapeSidecar = {
  shape: number[];
  partition_axis: number;
  partition_sizes: number[];
};

function isPartitionShapeSidecar(
  value: Record<string, unknown> | null,
): value is PartitionShapeSidecar {
  if (value === null) return false;
  const shape = value.shape;
  const axis = value.partition_axis;
  const sizes = value.partition_sizes;
  return (
    isConcreteShape(shape) &&
    typeof axis === "number" &&
    Number.isInteger(axis) &&
    axis >= 0 &&
    axis < shape.length &&
    Array.isArray(sizes) &&
    sizes.length > 0 &&
    sizes.every((size) => typeof size === "number" && Number.isInteger(size) && size > 0) &&
    sizes.reduce((total, size) => total + size, 0) === shape[axis]
  );
}

function hasPartitionShapeFields(value: Record<string, unknown> | null): boolean {
  return value !== null && ("partition_axis" in value || "partition_sizes" in value);
}

function nestedShape(value: unknown): number[] {
  if (!Array.isArray(value)) {
    throw new Error("live measurement Arrow array value is not nested");
  }
  if (value.length === 0) return [0];
  const childShapes = value.map((item) => (Array.isArray(item) ? nestedShape(item) : []));
  const first = childShapes[0]!;
  const rest = childShapes.slice(1);
  if (rest.some((shape) => shape.length !== first.length || shape.some((x, i) => x !== first[i]))) {
    throw new Error("live measurement Arrow array value is not rectangular");
  }
  return [value.length, ...first];
}

function unavailableGroups(
  value: Record<string, unknown>,
): NonNullable<MeasurementArray["availability"]>["unavailable"] {
  const groups = value.unavailable ?? [];
  if (!Array.isArray(groups)) {
    throw new Error("live measurement Arrow array availability is invalid");
  }
  return groups.map((group: unknown) => {
    if (
      !isJsonObject(group) ||
      !isReason(group.reason) ||
      !isConcreteShape(group.flat_indices) ||
      group.flat_indices.length === 0 ||
      (group.metadata !== undefined && !isJsonObject(group.metadata))
    ) {
      throw new Error("live measurement Arrow array availability group is invalid");
    }
    return {
      reason: group.reason,
      flat_indices: group.flat_indices,
      ...(group.metadata === undefined ? {} : { metadata: group.metadata }),
    };
  });
}

function columnValue(table: Table, name: string): unknown {
  const column = table.getChild(name);
  if (column === null) throw new Error(`live measurement Arrow is missing ${name}`);
  return column.get(0);
}

function decodeJsonColumn(table: Table, name: string): Record<string, unknown> {
  const value = columnValue(table, name);
  if (!(value instanceof Uint8Array)) throw new Error(`${name} is not binary JSON metadata`);
  const decoded: unknown = JSON.parse(textDecoder.decode(value));
  if (!isJsonObject(decoded)) throw new Error(`${name} JSON metadata is not an object`);
  return decoded;
}

function decodeOptionalJsonColumn(table: Table, name: string): Record<string, unknown> | null {
  const value = columnValue(table, name);
  if (value === null) return null;
  if (!(value instanceof Uint8Array)) throw new Error(`${name} is not binary JSON metadata`);
  const decoded: unknown = JSON.parse(textDecoder.decode(value));
  if (!isJsonObject(decoded)) throw new Error(`${name} JSON metadata is not an object`);
  return decoded;
}

function plainValue(value: unknown): unknown {
  if (typeof value === "bigint") return Number(value);
  if (value === null || typeof value !== "object") return value;
  if (value instanceof Uint8Array) return value;
  if ("toJSON" in value && typeof value.toJSON === "function") {
    return plainValue(value.toJSON());
  }
  if (isIterable(value)) {
    return Array.from(value, plainValue);
  }
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, plainValue(item)]));
}

function shapeFromType(type: DataType): number[] {
  const shape: number[] = [];
  let current = type;
  while (DataType.isFixedSizeList(current)) {
    shape.push(current.listSize);
    current = current.valueType;
  }
  return shape;
}

function isSegmentShapeSidecar(
  value: Record<string, unknown> | null,
): value is { segments: unknown[] } {
  return value !== null && Array.isArray(value.segments);
}

function isShape(value: unknown): value is (number | null)[] {
  return (
    Array.isArray(value) &&
    value.every((extent) => extent === null || (Number.isInteger(extent) && extent >= 0))
  );
}

function isConcreteShape(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((extent) => Number.isInteger(extent) && extent >= 0);
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function requiredMetadata(field: Field, name: string): string {
  const value = field.metadata.get(name);
  if (value === undefined) throw new Error(`live measurement Arrow field is missing ${name}`);
  return value;
}

function requiredSchemaMetadata(table: Table, name: string): string {
  const value = table.schema.metadata.get(name);
  if (value === undefined) throw new Error(`live measurement Arrow schema is missing ${name}`);
  return value;
}

function numericValue(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "bigint") return Number(value);
  throw new Error("live measurement Arrow point index is not numeric");
}

function isIterable(value: object): value is Iterable<unknown> {
  return Symbol.iterator in value && typeof value[Symbol.iterator] === "function";
}

function isDType(value: unknown): value is MeasurementDType {
  return (
    value === "float64" ||
    value === "int64" ||
    value === "complex128" ||
    value === "bool" ||
    value === "string"
  );
}

function isReason(value: unknown): value is MeasurementUnavailable["reason"] {
  return value === "missing" || value === "invalid" || value === "overload";
}

function measurementLeaf(value: unknown, dtype: MeasurementDType): MeasurementLeaf {
  if (dtype === "string" && typeof value === "string") return value;
  if (dtype === "bool" && typeof value === "boolean") return value;
  if (
    (dtype === "float64" || dtype === "int64") &&
    typeof value === "number" &&
    Number.isFinite(value) &&
    (dtype !== "int64" || Number.isInteger(value))
  )
    return value;
  if (
    dtype === "complex128" &&
    isJsonObject(value) &&
    typeof value.real === "number" &&
    typeof value.imag === "number" &&
    Number.isFinite(value.real) &&
    Number.isFinite(value.imag)
  ) {
    return { real: value.real, imag: value.imag };
  }
  throw new Error("live measurement Arrow value does not match dtype");
}

// Empty axes retain the remaining extents from the validated sidecar.
function validateShape(value: unknown, shape: number[], dimension = 0): void {
  if (dimension === shape.length) {
    if (Array.isArray(value)) throw new Error("live measurement Arrow array shape is invalid");
    return;
  }
  if (!Array.isArray(value) || value.length !== shape[dimension]) {
    throw new Error("live measurement Arrow array shape is invalid");
  }
  value.forEach((child: unknown) => validateShape(child, shape, dimension + 1));
}

function logicalPointId(value: unknown): string | null {
  if (value === null || typeof value === "string") return value;
  throw new Error("live measurement Arrow logical point id is invalid");
}
