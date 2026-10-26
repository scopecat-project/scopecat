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
    <section aria-label="对象参数值" className="grid gap-3">
      <header>
        <h3 className="text-lg font-semibold">{entity ? `${entity.id} · 参数` : "选择对象"}</h3>
        <p className="text-sm text-text-dim">
          显示工作表中的直接实体引用。不推断其他依赖及其完整影响。
        </p>
      </header>
      {entity && !matches.length && (
        <p>没有直接引用此对象的行。不完整实体引用及其他参数仍在“全部参数”中。</p>
      )}
      {matches.map(({ definition, table, value, row, rowIndex, references }) => (
        <fieldset
          key={`${definition.id}:${rowIndex}`}
          className="grid gap-3 rounded-lg border border-line bg-panel-soft p-3 min-w-0"
        >
          <legend className="font-semibold px-1">
            {definition.id} · 行 {rowIndex + 1}
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
                return `${key}: ${ref?.id ?? atom?.text ?? "未知"}${!ref && atom?.unit ? ` ${atom.unit}` : ""}`;
              })
              .join(" · ") || "无主键"}
          </p>
          {references.length > 1 && (
            <p className="rounded border border-line p-2 text-sm">
              共享行 · 直接引用{" "}
              {references.map((ref) => `${ref.kind ?? "entity"}/${ref.id}`).join(", ")}
              。在此编辑会改变所有引用对象共享的这一行；完整影响未声明。
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
          <small className="text-text-dim">键、引用和行结构请在“全部参数”中编辑。</small>
        </fieldset>
      ))}
    </section>
  );
}
