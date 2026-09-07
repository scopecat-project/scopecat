import { isRecord } from "./DecisionFields";

export interface ValueChange {
  path: string;
  before: unknown;
  after: unknown;
}

/** Retain exact representation changes, including quantity units. */
export function parameterChanges(path: string, before: unknown, after: unknown): ValueChange[] {
  if (Object.is(before, after)) return [];
  if (Array.isArray(before) && Array.isArray(after)) {
    return Array.from({ length: Math.max(before.length, after.length) }, (_, index) => {
      const row: unknown = after[index] ?? before[index];
      const identities = isRecord(row)
        ? Object.values(row)
            .filter(isRecord)
            .filter((value) => typeof value.id === "string")
            .map((value) => value.id)
        : [];
      const label = identities.length ? ` (${identities.join(", ")})` : "";
      return parameterChanges(`${path}[${index}]${label}`, before[index], after[index]);
    }).flat();
  }
  if (isRecord(before) && isRecord(after)) {
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
    if (
      typeof before.unit === "string" &&
      typeof after.unit === "string" &&
      typeof before.value === "number" &&
      typeof after.value === "number"
    ) {
      if (keys.every((key) => Object.is(before[key], after[key]))) return [];
      return [{ path, before, after }];
    }
    return keys.flatMap((key) => parameterChanges(`${path}.${key}`, before[key], after[key]));
  }
  return [{ path, before, after }];
}

export function changeValue(value: unknown): string {
  if (value === undefined) return "—";
  if (isRecord(value) && typeof value.unit === "string" && typeof value.value === "number")
    return `${value.value} ${value.unit}`;
  return typeof value === "string" ? value : JSON.stringify(value);
}
