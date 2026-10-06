import type { ParameterDefinition, ParameterEntity } from "../../api-contract";
import type { ParameterDraftValue } from "./parameter-draft-api";
import { objectRows, rawEntity, updateObjectCell } from "./object-parameters";
import { ParameterValueField } from "./ParameterValueField";

export function ObjectParameterFields({
  definitions,
  values,
  entity,
  onChange,
}: {
  definitions: ParameterDefinition[];
  values: ParameterDraftValue[];
  entity?: ParameterEntity;
  onChange: (id: string, value: ParameterDraftValue) => void;
}) {
  const matches = entity ? objectRows(definitions, values, entity) : [];
  return (
    <section
      aria-label="Object parameter values"
      className="grid gap-3 [&_input]:min-w-0 [&_input]:max-w-full [&_input]:w-24 [&_input]:rounded [&_input]:border [&_input]:border-line [&_input]:bg-bg [&_input]:px-2 [&_input]:py-1 [&_label]:text-xs"
    >
      <header>
        <h3 className="text-lg font-semibold">
          {entity ? `${entity.id} · Parameters` : "Select an object"}
        </h3>
        <p className="text-sm text-text-dim">
          Direct entity references in this working table. Other dependencies and their full impact
          are not inferred.
        </p>
      </header>
      {entity && !matches.length && (
        <p>
          No rows directly reference this object. Incomplete entity references and other parameters
          remain in All parameters.
        </p>
      )}
      {matches.map(({ definition, table, value, row, rowIndex, references }) => (
        <fieldset
          key={`${definition.id}:${rowIndex}`}
          className="grid gap-3 rounded-lg border border-line bg-panel-soft p-3 min-w-0"
        >
          <legend className="font-semibold px-1">
            {definition.id} · row {rowIndex + 1}
          </legend>
          {definition.description && (
            <p className="text-sm text-text-dim">{definition.description}</p>
          )}
          <p className="text-xs break-words">
            {(table.primary_key ?? [])
              .map((key) => {
                const atom = row[key];
                const type = table.columns.find((column) => column.id === key)?.value_type;
                const ref = type?.type === "entity" ? rawEntity(atom) : undefined;
                return `${key}: ${ref?.id ?? atom?.text ?? "Unknown"}${!ref && atom?.unit ? ` ${atom.unit}` : ""}`;
              })
              .join(" · ") || "No primary key"}
          </p>
          {references.length > 1 && (
            <p className="rounded border border-line p-2 text-sm">
              Shared row · directly references{" "}
              {references.map((ref) => `${ref.kind ?? "entity"}/${ref.id}`).join(", ")}. Editing
              here changes this same row for each referenced object; the full impact is not
              declared.
            </p>
          )}
          {table.columns
            .filter(
              (column) =>
                !(table.primary_key ?? []).includes(column.id) &&
                column.value_type.type !== "entity",
            )
            .map((column) => (
              <ParameterValueField
                key={column.id}
                label={`${definition.id}[${rowIndex + 1}].${column.id}`}
                displayLabel={column.id.replaceAll("_", " ")}
                type={column.value_type}
                value={row[column.id]}
                entities={[]}
                onChange={(atom) =>
                  onChange(definition.id, updateObjectCell(value, rowIndex, column.id, atom))
                }
              />
            ))}
          <small className="text-text-dim">
            Keys, references and row structure are edited in All parameters.
          </small>
        </fieldset>
      ))}
    </section>
  );
}
