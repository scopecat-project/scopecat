export function decisionFields(structure: unknown): Record<string, { type: string }> | undefined {
  if (!isRecord(structure) || structure.type !== "object" || !isRecord(structure.fields)) return;
  const fields: Record<string, { type: string }> = {};
  for (const [name, field] of Object.entries(structure.fields)) {
    if (
      !isRecord(field) ||
      typeof field.type !== "string" ||
      !["bool", "string", "float", "int"].includes(field.type)
    )
      return;
    fields[name] = { type: field.type };
  }
  return fields;
}

export function DecisionFields({
  fields,
  value,
  onChange,
}: {
  fields: Record<string, { type: string }>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  return (
    <div className="grid content-start gap-3">
      {Object.entries(fields).map(([name, field]) => (
        <label key={name} className="grid gap-1.5 text-[0.7rem] font-bold text-text-soft">
          {name.replaceAll("_", " ")}
          {field.type === "bool" ? (
            <select
              className="rounded-md border border-line bg-bg p-3"
              value={String(value[name])}
              onChange={(event) => onChange({ ...value, [name]: event.target.value === "true" })}
            >
              <option value="false">No</option>
              <option value="true">Yes</option>
            </select>
          ) : field.type === "string" ? (
            <textarea
              className="min-h-[100px] rounded-md border border-line bg-bg p-3"
              value={typeof value[name] === "string" ? value[name] : ""}
              onChange={(event) => onChange({ ...value, [name]: event.target.value })}
            />
          ) : (
            <input
              className="rounded-md border border-line bg-bg p-3"
              type="number"
              step={field.type === "int" ? 1 : "any"}
              value={typeof value[name] === "number" ? value[name] : ""}
              onChange={(event) =>
                onChange({
                  ...value,
                  [name]: event.target.value === "" ? null : event.target.valueAsNumber,
                })
              }
            />
          )}
        </label>
      ))}
    </div>
  );
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
