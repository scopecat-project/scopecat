import * as Controls from "../../ui/Controls";
import { useId } from "react";

type ScalarField = (
  | { type: "bool" | "string" | "float" | "int" }
  | { type: "literal"; values: unknown[] }
) & { nullable?: boolean };
type DecisionField = ScalarField | { type: "array"; items: ScalarField };

// Explicit contracts own legality. Older requests keep their presentation hints.
export function decisionMetadata(metadata: unknown, contract: unknown): Record<string, unknown> {
  const base = isRecord(metadata) ? metadata : {};
  return isRecord(contract) && contract.version === 1 && isRecord(contract.fields)
    ? { ...base, field_constraints: contract.fields }
    : base;
}

function scalarField(value: unknown): ScalarField | undefined {
  if (!isRecord(value)) return;
  if (value.type === "union" && Array.isArray(value.variants) && value.variants.length === 2) {
    const variants = value.variants.filter((item) => isRecord(item) && item.type !== "null");
    if (
      variants.length !== 1 ||
      !value.variants.some((item) => isRecord(item) && item.type === "null")
    )
      return;
    const inner = scalarField(variants[0]);
    if (inner && (inner.type === "float" || inner.type === "int"))
      return { ...inner, nullable: true };
    return;
  }
  if (["bool", "string", "float", "int"].includes(String(value.type)))
    return { type: value.type as "bool" | "string" | "float" | "int" };
  if (value.type === "literal" && Array.isArray(value.values))
    return { type: "literal", values: value.values };
  return undefined;
}

export function decisionFields(structure: unknown): Record<string, DecisionField> | undefined {
  if (!isRecord(structure) || structure.type !== "object" || !isRecord(structure.fields)) return;
  const fields: Record<string, DecisionField> = {};
  for (const [name, field] of Object.entries(structure.fields)) {
    const scalar = scalarField(field);
    if (scalar) fields[name] = scalar;
    else if (isRecord(field) && field.type === "array") {
      const items = scalarField(field.items);
      if (!items) return;
      fields[name] = { type: "array", items };
    } else return;
  }
  return fields;
}

const scalar = (field: ScalarField, item: unknown) => {
  if (field.nullable && typeof item === "string" && item.trim() === "") return null;
  if ((field.type === "float" || field.type === "int") && typeof item === "string") {
    if (!/^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/.test(item.trim()))
      return item;
    const number = Number(item);
    return Number.isFinite(number) ? number : item;
  }
  return item;
};

// Raw text remains editing data until explicit form validation/submission.
export function decisionFormValue(
  fields: Record<string, DecisionField>,
  value: unknown,
  metadata?: unknown,
): unknown {
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([name, item]) => {
      const field = fields[name];
      return [
        name,
        field && inactiveField(name, field, value, metadata, fields)
          ? null
          : !field
            ? item
            : field.type === "array"
              ? Array.isArray(item)
                ? item.map((entry) => scalar(field.items, entry))
                : item
              : scalar(field, item),
      ];
    }),
  );
}

// The existing conditional-empty contract only hides nullable fields. Draft values remain
// intact for switching back; command serialization explicitly clears inactive values.
export function inactiveField(
  name: string,
  field: DecisionField,
  value: Record<string, unknown>,
  metadata: unknown,
  fields: Record<string, DecisionField>,
): boolean {
  if (
    !("nullable" in field) ||
    !field.nullable ||
    !isRecord(metadata) ||
    !isRecord(metadata.field_constraints)
  )
    return false;
  const rule = metadata.field_constraints[name];
  if (!isRecord(rule) || rule.empty_otherwise !== true || !isRecord(rule.required_when))
    return false;
  const condition = rule.required_when;
  if (typeof condition.field !== "string" || !(condition.field in value)) return false;
  const controller = fields[condition.field];
  if (!controller || controller.type === "array") return false;
  const selected = scalar(controller, value[condition.field]);
  // Incomplete/invalid edits are not a different choice. Keep dependent input visible
  // and intact until the controlling value is valid in the same schema as submission.
  if (
    selected === null ||
    selected === undefined ||
    (typeof selected === "string" && !selected.trim()) ||
    !validScalar(controller, selected) ||
    !validScalar(controller, condition.value)
  )
    return false;
  return selected !== condition.value;
}

function validScalar(field: ScalarField, value: unknown): boolean {
  if (field.nullable && value === null) return true;
  if (field.type === "literal") return field.values.includes(value);
  if (field.type === "string") return typeof value === "string";
  if (field.type === "bool") return typeof value === "boolean";
  return (
    typeof value === "number" &&
    Number.isFinite(value) &&
    (field.type !== "int" || Number.isInteger(value))
  );
}

export function decisionValueError(
  fields: Record<string, DecisionField>,
  value: unknown,
): string | undefined {
  if (!isRecord(value)) return "请填写完整后再提交判断。";
  for (const [name, field] of Object.entries(fields)) {
    const item = value[name];
    const valid =
      field.type === "array"
        ? Array.isArray(item) && item.every((entry) => validScalar(field.items, entry))
        : validScalar(field, item);
    if (!valid) return `请填写 ${name.replaceAll("_", " ")} 后再提交判断。`;
  }
  return undefined;
}

// Presentation constraints give immediate feedback; procedures still validate scientific meaning.
export function decisionConstraintError(value: unknown, metadata: unknown): string | undefined {
  if (!isRecord(value) || !isRecord(metadata) || !isRecord(metadata.field_constraints)) return;
  const labels = isRecord(metadata.field_labels) ? metadata.field_labels : {};
  for (const [name, rule] of Object.entries(metadata.field_constraints)) {
    if (!isRecord(rule)) continue;
    const label = typeof labels[name] === "string" ? labels[name] : name.replaceAll("_", " ");
    const item = value[name];
    if (isRecord(rule.required_when) && typeof rule.required_when.field === "string") {
      const choice = rule.required_when;
      const selected = value[choice.field as string] === choice.value;
      if (selected && item == null) return `选择 ${String(choice.value)} 时，请填写 ${label}。`;
      if (!selected && rule.empty_otherwise === true && item != null)
        return `仅在选择 ${String(choice.value)} 时填写 ${label}，其他情况请留空。`;
    }
    if (typeof item !== "number") continue;
    if (
      (typeof rule.minimum === "number" && item < rule.minimum) ||
      (typeof rule.maximum === "number" && item > rule.maximum)
    )
      return `${label} 必须在 ${typeof rule.minimum === "number" ? rule.minimum : "−∞"} 至 ${typeof rule.maximum === "number" ? rule.maximum : "∞"} 之间。`;
  }
  return undefined;
}

function ScalarInput({
  field,
  value,
  onChange,
  id,
}: {
  field: ScalarField;
  value: unknown;
  onChange: (value: unknown) => void;
  id: string;
}) {
  if (field.type === "literal" || field.type === "bool") {
    const choices: unknown[] = field.type === "literal" ? field.values : [false, true];
    return (
      <Controls.Select
        id={id}
        value={choices.includes(value) ? JSON.stringify(value) : ""}
        onChange={(event) =>
          onChange(event.target.value === "" ? null : JSON.parse(event.target.value))
        }
      >
        <option value="">请选择…</option>
        {choices.map((choice, index) => (
          <option key={index} value={JSON.stringify(choice)}>
            {field.type === "bool" ? (choice ? "是" : "否") : String(choice)}
          </option>
        ))}
      </Controls.Select>
    );
  }
  if (field.type === "string")
    return (
      <Controls.Textarea
        id={id}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  return (
    <Controls.Input
      id={id}
      type="text"
      inputMode="decimal"
      value={typeof value === "number" || typeof value === "string" ? value : ""}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function DecisionFields({
  fields,
  value,
  onChange,
  labels = {},
  metadata,
}: {
  fields: Record<string, DecisionField>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  labels?: Record<string, unknown>;
  metadata?: unknown;
}) {
  const prefix = useId();
  return (
    <div className="grid content-start gap-3">
      {Object.entries(fields).map(([name, field]) => {
        if (inactiveField(name, field, value, metadata, fields)) return null;
        const label = typeof labels[name] === "string" ? labels[name] : name.replaceAll("_", " ");
        const id = `${prefix}-${name}`;
        if (field.type !== "array")
          return (
            <div key={name} className="grid gap-1.5 text-xs">
              <Controls.Field htmlFor={id}>{label}</Controls.Field>
              <ScalarInput
                id={id}
                field={field}
                value={value[name]}
                onChange={(item) => onChange({ ...value, [name]: item })}
              />
            </div>
          );
        const items: unknown[] = Array.isArray(value[name]) ? value[name] : [];
        return (
          <fieldset key={name} className="grid gap-2 rounded-md border border-line p-3">
            <legend>{label}</legend>
            {items.map((item, index) => (
              <div key={index} className="grid gap-1">
                <Controls.Field htmlFor={`${id}-${index}`}>
                  {label} {index + 1}
                </Controls.Field>
                <div className="flex gap-2">
                  <ScalarInput
                    id={`${id}-${index}`}
                    field={field.items}
                    value={item}
                    onChange={(next) =>
                      onChange({
                        ...value,
                        [name]: items.map((old, i) => (i === index ? next : old)),
                      })
                    }
                  />
                  <Controls.Button
                    type="button"
                    aria-label={`移除 ${label} 第 ${index + 1} 项`}
                    onClick={() =>
                      onChange({ ...value, [name]: items.filter((_, i) => i !== index) })
                    }
                  >
                    移除
                  </Controls.Button>
                </div>
              </div>
            ))}
            <button
              type="button"
              className="text-left text-accent"
              onClick={() => onChange({ ...value, [name]: [...items, null] })}
            >
              添加 {label}
            </button>
          </fieldset>
        );
      })}
    </div>
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
