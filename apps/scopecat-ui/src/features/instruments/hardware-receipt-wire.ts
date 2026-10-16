import type { InstrumentCollectReceipt, MeasurementAcquisitionValue } from "../../api-contract";

export const HARDWARE_RECEIPT_MEDIA_TYPE = "application/vnd.scopecat.hardware-receipt.v1";

const MAGIC = new TextEncoder().encode("SCRCPT01");
const PREFIX_SIZE = MAGIC.byteLength + 8;
const MAX_HEADER_BYTES = 8 * 1024 * 1024;
const textDecoder = new TextDecoder("utf-8", { fatal: true });

type MeasurementDType = "float64" | "int64" | "complex128" | "bool" | "string";
type ArrayValue = Extract<MeasurementAcquisitionValue, { kind: "array" }>;
type ArrayLeaf = Extract<MeasurementAcquisitionValue, { kind: "scalar" }>["value"];
type ArrayTree = ArrayValue["values"][number];
type Problem = InstrumentCollectReceipt["problems"][number];
type ProblemLocation = NonNullable<Problem["location"]>;

interface ArrayReference {
  kind: "array_attachment";
  index: number;
  size_bytes: number;
  dtype: MeasurementDType;
  unit: string | null;
  shape: number[];
  metadata: Record<string, unknown>;
}

/** Decode one direct-collection receipt without materializing a JSON number tree on the server. */
export function decodeCollectReceipt(content: ArrayBuffer): InstrumentCollectReceipt {
  const bytes = new Uint8Array(content);
  if (bytes.byteLength < PREFIX_SIZE || !MAGIC.every((byte, index) => bytes[index] === byte)) {
    throw invalidReceipt("prefix");
  }

  const headerSizeBigInt = new DataView(content).getBigUint64(MAGIC.byteLength, true);
  if (headerSizeBigInt > BigInt(MAX_HEADER_BYTES)) throw invalidReceipt("header size");
  const headerSize = Number(headerSizeBigInt);
  const bodyOffset = PREFIX_SIZE + headerSize;
  if (bodyOffset > bytes.byteLength) throw invalidReceipt("header size");

  let parsed: unknown;
  try {
    parsed = JSON.parse(textDecoder.decode(bytes.subarray(PREFIX_SIZE, bodyOffset)));
  } catch {
    throw invalidReceipt("header");
  }
  const header = record(parsed, "header");
  if (header.format_id !== "scopecat.collect_receipt.v1") throw invalidReceipt("format");
  if (!isCollectStatus(header.status)) throw invalidReceipt("status");
  if (!Array.isArray(header.problems)) throw invalidReceipt("problems");

  const readback = header.readback;
  if (readback !== null && readback !== undefined && !isRecord(readback)) {
    throw invalidReceipt("readback");
  }
  const wireValues =
    readback === null || readback === undefined ? {} : record(readback.values, "values");
  const references = Object.values(wireValues)
    .filter(isArrayReference)
    .sort((left, right) => left.index - right.index);
  if (references.some((reference, index) => reference.index !== index)) {
    throw invalidReceipt("attachment indexes");
  }

  let attachmentOffset = bodyOffset;
  const attachments = references.map((reference) => {
    const end = attachmentOffset + reference.size_bytes;
    if (end > bytes.byteLength) throw invalidReceipt("truncated attachment");
    const attachment = bytes.subarray(attachmentOffset, end);
    attachmentOffset = end;
    return attachment;
  });
  if (attachmentOffset !== bytes.byteLength) throw invalidReceipt("trailing attachment bytes");

  const values = Object.fromEntries(
    Object.entries(wireValues).map(([id, value]) => [
      id,
      isArrayReference(value)
        ? decodeArray(value, attachments[value.index]!)
        : measurementValue(value),
    ]),
  );
  return {
    metadata: optionalRecord(header.metadata, "metadata"),
    problems: header.problems.map(problem),
    readback:
      readback === null || readback === undefined
        ? null
        : {
            metadata: optionalRecord(readback.metadata, "readback metadata"),
            values,
          },
    status: header.status,
  };
}

function decodeArray(reference: ArrayReference, content: Uint8Array): ArrayValue {
  const count = arrayElementCount(reference.shape);
  const view = new DataView(content.buffer, content.byteOffset, content.byteLength);
  let leaves: ArrayLeaf[];
  switch (reference.dtype) {
    case "float64":
      requireByteLength(content, count * 8);
      leaves = Array.from({ length: count }, (_, index) => view.getFloat64(index * 8, true));
      break;
    case "int64":
      requireByteLength(content, count * 8);
      leaves = Array.from({ length: count }, (_, index) =>
        Number(view.getBigInt64(index * 8, true)),
      );
      break;
    case "complex128":
      requireByteLength(content, count * 16);
      leaves = Array.from({ length: count }, (_, index) => ({
        real: view.getFloat64(index * 16, true),
        imag: view.getFloat64(index * 16 + 8, true),
      }));
      break;
    case "bool":
      requireByteLength(content, count);
      leaves = Array.from(content, (value) => {
        if (value > 1) throw invalidReceipt("boolean attachment");
        return value === 1;
      });
      break;
    case "string":
      leaves = decodeStrings(content, count);
      break;
  }
  return {
    kind: "array",
    dtype: reference.dtype,
    unit: reference.unit,
    shape: reference.shape,
    metadata: reference.metadata,
    values: reshape(leaves, reference.shape),
  };
}

function decodeStrings(content: Uint8Array, count: number): string[] {
  const offsetBytes = (count + 1) * 8;
  if (content.byteLength < offsetBytes) throw invalidReceipt("string attachment size");
  const view = new DataView(content.buffer, content.byteOffset, content.byteLength);
  const offsets = Array.from({ length: count + 1 }, (_, index) =>
    safeOffset(view.getBigUint64(index * 8, true)),
  );
  const encoded = content.subarray(offsetBytes);
  if (
    offsets[0] !== 0 ||
    offsets.at(-1) !== encoded.byteLength ||
    offsets.some((offset, index) => index < count && offset > offsets[index + 1]!)
  ) {
    throw invalidReceipt("string attachment offsets");
  }
  try {
    return Array.from({ length: count }, (_, index) =>
      textDecoder.decode(encoded.subarray(offsets[index], offsets[index + 1])),
    );
  } catch {
    throw invalidReceipt("string attachment encoding");
  }
}

function reshape(leaves: ArrayLeaf[], shape: number[]): ArrayTree[] {
  let cursor = 0;
  const build = (dimension: number): ArrayTree[] =>
    Array.from({ length: shape[dimension]! }, () =>
      dimension === shape.length - 1 ? leaves[cursor++]! : build(dimension + 1),
    );
  const result = build(0);
  if (cursor !== leaves.length) throw invalidReceipt("array shape");
  return result;
}

function arrayElementCount(shape: number[]): number {
  if (shape.length === 0 || shape.some((size) => !Number.isSafeInteger(size) || size < 0)) {
    throw invalidReceipt("array shape");
  }
  const count = shape.reduce((total, size) => total * size, 1);
  if (!Number.isSafeInteger(count)) throw invalidReceipt("array shape");
  return count;
}

function isArrayReference(value: unknown): value is ArrayReference {
  if (!isRecord(value) || value.kind !== "array_attachment") return false;
  if (
    !isNonnegativeInteger(value.index) ||
    !isNonnegativeInteger(value.size_bytes) ||
    !isMeasurementDType(value.dtype) ||
    !(value.unit === null || typeof value.unit === "string") ||
    !isShape(value.shape) ||
    !isRecord(value.metadata)
  ) {
    throw invalidReceipt("array descriptor");
  }
  arrayElementCount(value.shape);
  return true;
}

function measurementValue(value: unknown): MeasurementAcquisitionValue {
  if (
    !isRecord(value) ||
    !isMeasurementDType(value.dtype) ||
    !(value.unit === undefined || value.unit === null || typeof value.unit === "string") ||
    !(value.metadata === undefined || isRecord(value.metadata))
  ) {
    throw invalidReceipt("measurement value");
  }
  if (value.kind === "scalar" && isLeaf(value.value, value.dtype)) {
    return {
      ...value,
      kind: "scalar",
      dtype: value.dtype,
      value: value.value,
      ...(value.unit === undefined ? {} : { unit: value.unit }),
      ...(value.metadata === undefined ? {} : { metadata: value.metadata }),
    };
  }
  if (
    value.kind === "unavailable" &&
    isReason(value.reason) &&
    isUnavailableShape(value.shape) &&
    (value.unit === null || typeof value.unit === "string") &&
    isRecord(value.metadata)
  ) {
    return {
      ...value,
      kind: "unavailable",
      dtype: value.dtype,
      reason: value.reason,
      shape: value.shape,
      unit: value.unit,
      metadata: value.metadata,
    };
  }
  throw invalidReceipt("measurement value");
}

function isLeaf(value: unknown, dtype: MeasurementDType): value is ArrayLeaf {
  switch (dtype) {
    case "string":
      return typeof value === "string";
    case "bool":
      return typeof value === "boolean";
    case "float64":
      return typeof value === "number" && Number.isFinite(value);
    case "int64":
      return typeof value === "number" && Number.isInteger(value);
    case "complex128":
      return (
        isRecord(value) &&
        typeof value.real === "number" &&
        Number.isFinite(value.real) &&
        typeof value.imag === "number" &&
        Number.isFinite(value.imag)
      );
  }
}

function isReason(value: unknown): value is "missing" | "invalid" | "overload" {
  return value === "missing" || value === "invalid" || value === "overload";
}

function isNonnegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isUnavailableShape(value: unknown): value is (number | null)[] {
  return (
    Array.isArray(value) &&
    value.every((size: unknown) => size === null || isNonnegativeInteger(size))
  );
}

function isShape(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((size: unknown) => typeof size === "number");
}

function problem(value: unknown): Problem {
  if (
    !isRecord(value) ||
    typeof value.code !== "string" ||
    typeof value.message !== "string" ||
    !isProblemPhase(value.phase) ||
    !Array.isArray(value.related_locations) ||
    !value.related_locations.every(isProblemLocation) ||
    !(
      value.location === undefined ||
      value.location === null ||
      isProblemLocation(value.location)
    ) ||
    !isOptionalString(value.occurrence_id)
  )
    throw invalidReceipt("problem");
  return {
    ...value,
    code: value.code,
    message: value.message,
    phase: value.phase,
    related_locations: value.related_locations,
    ...(value.location === undefined ? {} : { location: value.location }),
    ...(value.occurrence_id === undefined ? {} : { occurrence_id: value.occurrence_id }),
  };
}

function isProblemPhase(value: unknown): value is Problem["phase"] {
  return (
    value === "definition" ||
    value === "authoring" ||
    value === "configuration" ||
    value === "planning" ||
    value === "provider_preflight" ||
    value === "execution" ||
    value === "persistence" ||
    value === "analysis"
  );
}

function isOptionalString(value: unknown): value is string | null | undefined {
  return value === undefined || value === null || typeof value === "string";
}

function isOptionalNumber(value: unknown): value is number | null | undefined {
  return (
    value === undefined || value === null || (typeof value === "number" && Number.isFinite(value))
  );
}

function isProblemLocation(value: unknown): value is ProblemLocation {
  if (!isRecord(value)) return false;
  if (value.kind === "runtime")
    return (
      isOptionalString(value.run_id) &&
      isOptionalString(value.instrument_id) &&
      isOptionalString(value.operation_id) &&
      isOptionalNumber(value.point_index)
    );
  if (
    !Array.isArray(value.path) ||
    !value.path.every(
      (item: unknown) =>
        typeof item === "string" || (typeof item === "number" && Number.isInteger(item)),
    )
  )
    return false;
  switch (value.kind) {
    case "model":
      return typeof value.root === "string";
    case "storage":
      return isOptionalString(value.ref) && isOptionalString(value.run_id);
    case "external":
      return (
        typeof value.uri === "string" &&
        isOptionalString(value.sheet) &&
        isOptionalNumber(value.row) &&
        (isOptionalString(value.column) || isOptionalNumber(value.column))
      );
    default:
      return false;
  }
}

function optionalRecord(value: unknown, field: string): Record<string, unknown> {
  return value === undefined ? {} : record(value, field);
}

function record(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) throw invalidReceipt(field);
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCollectStatus(value: unknown): value is InstrumentCollectReceipt["status"] {
  return value === "collected" || value === "not_collected" || value === "unknown";
}

function isMeasurementDType(value: unknown): value is MeasurementDType {
  return (
    value === "float64" ||
    value === "int64" ||
    value === "complex128" ||
    value === "bool" ||
    value === "string"
  );
}

function safeOffset(value: bigint): number {
  if (value > BigInt(Number.MAX_SAFE_INTEGER)) throw invalidReceipt("string attachment offsets");
  return Number(value);
}

function requireByteLength(content: Uint8Array, expected: number): void {
  if (content.byteLength !== expected) throw invalidReceipt("attachment size");
}

function invalidReceipt(field: string): Error {
  return new Error(`Invalid hardware receipt ${field}.`);
}
