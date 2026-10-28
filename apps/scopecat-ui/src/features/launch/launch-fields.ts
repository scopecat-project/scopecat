import registry from "../../unit-registry.json";
import { controlNumber } from "./control-quantity";
import type { components } from "../../api-schema";

type LaunchField = components["schemas"]["LaunchField-Output"];
interface FormArrayItem extends LaunchField {
  type: "string";
  enum: string[];
}
export interface FormField extends LaunchField {
  type: string;
  enum?: (string | number | boolean)[] | null;
  items?: FormArrayItem | null;
}

// This console intentionally renders a small subset of project JSON Schema.
// Unsupported schemas remain intact in the catalog for other project clients.
export function canRenderField(field: LaunchField | boolean): field is FormField {
  if (typeof field === "boolean" || typeof field.type !== "string") return false;
  const enumType = ["integer", "number"].includes(field.type) ? "number" : field.type;
  if (field.enum?.some((value) => typeof value !== enumType)) return false;
  if (["string", "number", "integer", "boolean"].includes(field.type)) return true;
  if (isQuantityField(field)) return true;
  const items = field.items;
  return (
    field.type === "array" &&
    typeof items === "object" &&
    items !== null &&
    !Array.isArray(items) &&
    items.type === "string" &&
    Array.isArray(items.enum) &&
    items.enum.every((value) => typeof value === "string")
  );
}

export function isQuantityField(field: LaunchField | boolean): boolean {
  if (typeof field === "boolean" || field.type !== "object") return false;
  const marker = field["x-scopecat-quantity"];
  const properties = field.properties as Record<string, LaunchField> | undefined;
  return (
    !!marker &&
    typeof marker === "object" &&
    properties?.value?.type === "number" &&
    properties?.unit?.type === "string" &&
    Object.keys(properties).length === 2
  );
}
export function quantityDraft(raw: string): { value: string; unit: string } {
  try {
    const parsed = JSON.parse(raw);
    return { value: String(parsed.value ?? ""), unit: String(parsed.unit ?? "") };
  } catch {
    return { value: "", unit: "" };
  }
}
export function quantityInput(raw: string, field: FormField): { value: number; unit: string } {
  const draft = quantityDraft(raw);
  const value = controlNumber(draft.value, null);
  const units: Record<string, { kind: string; scale: number | null }> = registry;
  const unit = draft.unit.trim();
  const marker = field["x-scopecat-quantity"] as {
    dimension?: string;
    unit?: string;
    minimum?: number;
    maximum?: number;
  };
  const expected = marker.dimension ?? (marker.unit ? units[marker.unit]?.kind : undefined);
  if (!units[unit] || (expected && units[unit].kind !== expected))
    throw new Error("请输入兼容的显式单位。");
  // Converted bounds are checked by the backend during preview/start. Its
  // Decimal conversion is authoritative; binary float conversion here can
  // incorrectly reject exact boundaries (for example 1 us == 1000 ns).
  return { value, unit };
}
