import registry from "../../unit-registry.json";
interface Quantity {
  value: number;
  unit: string;
}
export type ScientificChange = "physical" | "representation";
function quantity(value: unknown): value is Quantity {
  return (
    typeof value === "object" &&
    value !== null &&
    "value" in value &&
    typeof value.value === "number" &&
    "unit" in value &&
    typeof value.unit === "string"
  );
}
/** Same dimensions and floating-point tolerance as the core unit registry. */
export function scientificChange(before: unknown, after: unknown): ScientificChange {
  if (!quantity(before) || !quantity(after)) return "physical";
  const units: Record<string, { kind: string; scale: number | null }> = registry;
  const left = units[before.unit],
    right = units[after.unit];
  if (!left || !right || left.kind !== right.kind) return "physical";
  let a = before.value,
    b = after.value;
  if (left.scale !== null && right.scale !== null) {
    a *= left.scale;
    b *= right.scale;
  } else if (before.unit !== after.unit) return "physical";
  return Math.abs(a - b) <= 1e-12 * Math.max(Math.abs(a), Math.abs(b))
    ? "representation"
    : "physical";
}
export function scientificChangeLabel(kind: ScientificChange): string {
  return kind === "representation"
    ? "Equivalent representation · same physical value"
    : "Physical value change";
}
