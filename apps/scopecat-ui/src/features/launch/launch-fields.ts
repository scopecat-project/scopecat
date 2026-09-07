import type { components } from "../../api-schema";

type LaunchField = components["schemas"]["LaunchField"];
interface FormArrayItem extends LaunchField {
  type: "string";
  enum: string[];
}
export interface FormField extends LaunchField {
  type: string;
  enum?: string[] | null;
  items?: FormArrayItem | null;
}

// This console intentionally renders a small subset of project JSON Schema.
// Unsupported schemas remain intact in the catalog for other project clients.
export function canRenderField(field: LaunchField | boolean): field is FormField {
  if (typeof field === "boolean" || typeof field.type !== "string") return false;
  if (field.enum?.some((value) => typeof value !== "string")) return false;
  if (["string", "number", "integer", "boolean"].includes(field.type)) return true;
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
