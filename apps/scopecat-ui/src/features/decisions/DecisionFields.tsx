import { useId } from "react";

type ScalarField =
  | { type: "bool" | "string" | "float" | "int" }
  | { type: "literal"; values: unknown[] };
type DecisionField = ScalarField | { type: "array"; items: ScalarField };

function scalarField(value: unknown): ScalarField | undefined {
  if (!isRecord(value)) return;
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

function validScalar(field: ScalarField, value: unknown): boolean {
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
  if (!isRecord(value)) return "Complete the decision fields before recording.";
  for (const [name, field] of Object.entries(fields)) {
    const item = value[name];
    const valid =
      field.type === "array"
        ? Array.isArray(item) && item.every((entry) => validScalar(field.items, entry))
        : validScalar(field, item);
    if (!valid) return `Complete ${name.replaceAll("_", " ")} before recording.`;
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
  const style = "rounded-md border border-line bg-bg p-3";
  if (field.type === "literal" || field.type === "bool") {
    const choices: unknown[] = field.type === "literal" ? field.values : [false, true];
    return (
      <select
        id={id}
        className={style}
        value={choices.includes(value) ? JSON.stringify(value) : ""}
        onChange={(event) =>
          onChange(event.target.value === "" ? null : JSON.parse(event.target.value))
        }
      >
        <option value="">Choose…</option>
        {choices.map((choice, index) => (
          <option key={index} value={JSON.stringify(choice)}>
            {field.type === "bool" ? (choice ? "Yes" : "No") : String(choice)}
          </option>
        ))}
      </select>
    );
  }
  if (field.type === "string")
    return (
      <textarea
        id={id}
        className={style}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  return (
    <input
      id={id}
      className={style}
      type="number"
      step={field.type === "int" ? 1 : "any"}
      value={typeof value === "number" ? value : ""}
      onChange={(event) => onChange(event.target.value === "" ? null : event.target.valueAsNumber)}
    />
  );
}

export function DecisionFields({
  fields,
  value,
  onChange,
  labels = {},
}: {
  fields: Record<string, DecisionField>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  labels?: Record<string, unknown>;
}) {
  const prefix = useId();
  return (
    <div className="grid content-start gap-3">
      {Object.entries(fields).map(([name, field]) => {
        const label = typeof labels[name] === "string" ? labels[name] : name.replaceAll("_", " ");
        const id = `${prefix}-${name}`;
        if (field.type !== "array")
          return (
            <div key={name} className="grid gap-1.5 text-[0.7rem]">
              <label htmlFor={id}>{label}</label>
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
                <label htmlFor={`${id}-${index}`}>
                  {label} {index + 1}
                </label>
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
                  <button
                    type="button"
                    aria-label={`Remove ${label} ${index + 1}`}
                    onClick={() =>
                      onChange({ ...value, [name]: items.filter((_, i) => i !== index) })
                    }
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
            <button
              type="button"
              className="text-left text-accent"
              onClick={() => onChange({ ...value, [name]: [...items, null] })}
            >
              Add {label}
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
