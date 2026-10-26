import type { PointCoordinateSpec, ReviewCompileCommand } from "../../api-contract";

export type PointCoordinateValue = NonNullable<ReviewCompileCommand["coordinates"]>[string];

export function parseCoordinate(spec: PointCoordinateSpec, encoded: string): PointCoordinateValue {
  const input = encoded.trim();
  if (!input) throw new Error(`${spec.id} 需要填写值`);
  if (spec.kind === "bool") {
    if (input !== "true" && input !== "false") {
      throw new Error(`${spec.id} 必须为布尔值`);
    }
    return input === "true";
  }
  if (spec.kind === "int" || spec.kind === "float") {
    const value = Number(input);
    if (!Number.isFinite(value)) throw new Error(`${spec.id} 必须为数值`);
    if (spec.kind === "int" && !Number.isInteger(value)) {
      throw new Error(`${spec.id} 必须为整数`);
    }
    return value;
  }
  if (spec.kind === "quantity") {
    const value = Number(input);
    if (!Number.isFinite(value)) throw new Error(`${spec.id} 必须为数值`);
    return { value, unit: spec.unit ?? "" };
  }
  if (spec.kind === "entity") return { id: input, metadata: {} };
  return input;
}

export function parseLinearCoordinate(
  spec: PointCoordinateSpec,
  encoded: string,
): number | Extract<PointCoordinateValue, { value: number }> {
  const value = parseCoordinate(spec, encoded);
  if (typeof value === "number") return value;
  if (typeof value === "object" && value !== null && "value" in value) return value;
  throw new Error(`${spec.id} does not support a linear source`);
}

export function coordinateInputValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "object") {
    if ("value" in value && typeof value.value === "number") return String(value.value);
    if ("id" in value && typeof value.id === "string") return value.id;
    return "";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "";
}

export function formatCoordinateValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "object" && "value" in value && typeof value.value === "number") {
    const unit = "unit" in value && typeof value.unit === "string" ? ` ${value.unit}` : "";
    return `${value.value}${unit}`;
  }
  return coordinateInputValue(value);
}

export function formatCoordinateMapping(coordinates: Record<string, unknown>): string {
  return Object.entries(coordinates)
    .map(([id, value]) => `${id}=${formatCoordinateValue(value)}`)
    .join(" · ");
}

export function isNumericCoordinate(spec: PointCoordinateSpec): boolean {
  return spec.kind === "int" || spec.kind === "float" || spec.kind === "quantity";
}
