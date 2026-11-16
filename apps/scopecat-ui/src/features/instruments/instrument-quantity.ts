import registry from "../../unit-registry.json";
import { convertControlNumber } from "../launch/control-quantity";
const units: Record<string, { kind: string; scale: number | null }> = registry;

/** Engineering prefixes only: never automatically reinterpret logarithmic or angular units. */
export function quantityUnits(unit: string): string[] {
  const source = units[unit];
  if (!source || source.scale === null || source.kind === "phase") return [unit];
  return Object.keys(units)
    .filter((name) => units[name]!.kind === source.kind && units[name]!.scale !== null)
    .sort((a, b) => units[a]!.scale! - units[b]!.scale!);
}
export function engineeringUnit(value: number, unit: string): string {
  const candidates = quantityUnits(unit);
  if (!Number.isFinite(value) || value === 0 || candidates.length < 2) return unit;
  const magnitude = Math.abs(value * units[unit]!.scale!);
  let selected = candidates[0]!;
  for (const name of candidates) {
    if (magnitude >= units[name]!.scale!) selected = name;
  }
  return selected;
}
export function formatInstrumentQuantity(value: number, unit: string): string {
  const displayUnit = engineeringUnit(value, unit);
  try {
    return `${convertControlNumber(String(value), unit, displayUnit)} ${displayUnit}`;
  } catch {
    return `${value} ${unit}`;
  }
}
