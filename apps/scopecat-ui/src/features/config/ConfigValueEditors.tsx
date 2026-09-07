import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { iconButton, secondaryButton } from "../../ui/styles";
import { defaultTableRow } from "./config-draft";
import type {
  ParameterAtom,
  ParameterDefinition,
  ParameterEntity,
  ParameterScalarType,
  StoredParameterValue,
  TableParameterType,
  TableParameterValue,
} from "../../api-contract";

export function ParameterEditor({
  definition,
  value,
  baseValue,
  entities,
  onChange,
  onFieldValidityChange,
  tableRowOrigins,
  onTableRowOriginsChange,
}: {
  definition: ParameterDefinition;
  value: StoredParameterValue;
  baseValue?: StoredParameterValue;
  entities: ParameterEntity[];
  onChange: (value: StoredParameterValue) => void;
  onFieldValidityChange: (field: string, valid: boolean) => void;
  tableRowOrigins?: Array<"base" | "new">;
  onTableRowOriginsChange: (origins: Array<"base" | "new">) => void;
}) {
  if (definition.value_type.shape === "scalar" && value.shape === "scalar") {
    return (
      <div className="grid min-h-[150px] place-items-center rounded-[8px] border border-line bg-bg">
        <AtomInput
          label={definition.id}
          type={definition.value_type.atom}
          value={value.value}
          entities={entities}
          onValidityChange={onFieldValidityChange}
          onChange={(next) =>
            onChange({
              id: value.id,
              shape: "scalar",
              value: next,
            })
          }
        />
      </div>
    );
  }
  if (definition.value_type.shape === "table" && value.shape === "table") {
    return (
      <TableEditor
        parameterId={definition.id}
        type={definition.value_type}
        value={value}
        baseValue={baseValue?.shape === "table" ? baseValue : undefined}
        entities={entities}
        onChange={onChange}
        onFieldValidityChange={onFieldValidityChange}
        rowOrigins={tableRowOrigins}
        onRowOriginsChange={onTableRowOriginsChange}
      />
    );
  }
  return <div className={draftEmpty}>The stored value does not match its catalog shape.</div>;
}

function TableEditor({
  parameterId,
  type,
  value,
  baseValue,
  entities,
  onChange,
  onFieldValidityChange,
  rowOrigins,
  onRowOriginsChange,
}: {
  parameterId: string;
  type: TableParameterType;
  value: TableParameterValue;
  baseValue?: TableParameterValue;
  entities: ParameterEntity[];
  onChange: (value: StoredParameterValue) => void;
  onFieldValidityChange: (field: string, valid: boolean) => void;
  rowOrigins?: Array<"base" | "new">;
  onRowOriginsChange: (origins: Array<"base" | "new">) => void;
}) {
  const rows = value.rows ?? [];
  const baseRows = baseValue?.rows ?? [];
  const primaryKey = type.primary_key ?? [];
  const origins = rowOrigins ?? rows.map((_, index) => (index < baseRows.length ? "base" : "new"));
  const setRows = (nextRows: Array<Record<string, ParameterAtom>>) =>
    onChange({
      id: value.id,
      shape: "table",
      rows: nextRows,
    });
  const updateCell = (rowIndex: number, columnId: string, next: ParameterAtom) => {
    setRows(rows.map((row, index) => (index === rowIndex ? { ...row, [columnId]: next } : row)));
  };
  const addRow = () => {
    setRows([...rows, defaultTableRow(type, entities)]);
    onRowOriginsChange([...origins, "new"]);
  };
  const deleteRow = (rowIndex: number) => {
    setRows(rows.filter((_, index) => index !== rowIndex));
    onRowOriginsChange(origins.filter((_, index) => index !== rowIndex));
  };
  return (
    <div className="grid justify-items-start gap-[9px]">
      {primaryKey.length === 0 && (
        <div className="rounded-[7px] border border-line bg-panel-soft px-2.5 py-2 text-[0.57rem] leading-[1.45] text-text-dim">
          This table has no primary key. The daemon will validate it as one complete replacement.
        </div>
      )}
      <div className="max-w-full w-full overflow-auto rounded-[8px] border border-line [scrollbar-color:var(--color-line-strong)_transparent]">
        <table className="w-full min-w-max border-collapse bg-bg text-[0.58rem] [&_th]:min-w-[110px] [&_th]:border-r [&_th]:border-b [&_th]:border-line [&_th]:px-2.5 [&_th]:py-2 [&_th]:text-left [&_td]:min-w-[110px] [&_td]:border-r [&_td]:border-b [&_td]:border-line [&_td]:px-2.5 [&_td]:py-2 [&_td]:text-left [&_td]:align-middle [&_tr:last-child>*]:border-b-0 [&_tr>*:last-child]:border-r-0 [&_tr>*:last-child]:min-w-[55px]">
          <thead>
            <tr>
              {type.columns.map((column) => (
                <th
                  className="sticky top-0 z-[1] bg-panel-strong text-[0.56rem] font-extrabold text-text-soft"
                  key={column.id}
                >
                  <span className="block">{column.id}</span>
                  <small className="mt-0.5 block text-[0.48rem] font-normal text-text-dim">
                    {column.value_type.type}
                    {primaryKey.includes(column.id) ? " · key" : ""}
                  </small>
                </th>
              ))}
              <th className="sticky top-0 z-[1] bg-panel-strong text-[0.56rem] font-extrabold text-text-soft">
                <span className="block">Row</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => {
              const existing = origins[rowIndex] === "base";
              return (
                <tr key={rowIndex}>
                  {type.columns.map((column) => (
                    <td key={column.id}>
                      <AtomInput
                        label={`${parameterId} row ${rowIndex + 1} ${column.id}`}
                        type={column.value_type}
                        value={row[column.id]!}
                        entities={entities}
                        disabled={existing && primaryKey.includes(column.id)}
                        onValidityChange={onFieldValidityChange}
                        onChange={(next) => updateCell(rowIndex, column.id, next)}
                      />
                    </td>
                  ))}
                  <td>
                    <button
                      className={iconButton}
                      type="button"
                      onClick={() => deleteRow(rowIndex)}
                      aria-label={`Delete ${parameterId} row ${rowIndex + 1}`}
                    >
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button className={secondaryButton} type="button" onClick={addRow}>
        <Plus size={15} />
        Add row
      </button>
    </div>
  );
}

function AtomInput({
  label,
  type,
  value,
  entities,
  disabled = false,
  onChange,
  onValidityChange,
}: {
  label: string;
  type: ParameterScalarType;
  value: ParameterAtom;
  entities: ParameterEntity[];
  disabled?: boolean;
  onChange: (value: ParameterAtom) => void;
  onValidityChange: (field: string, valid: boolean) => void;
}) {
  return (
    <div className={atomInput}>
      <ConcreteAtomInput
        label={label}
        type={type}
        value={value}
        entities={entities}
        disabled={disabled}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    </div>
  );
}

function ConcreteAtomInput({
  label,
  type,
  value,
  entities,
  disabled,
  onChange,
  onValidityChange,
}: {
  label: string;
  type: ParameterScalarType;
  value: ParameterAtom;
  entities: ParameterEntity[];
  disabled: boolean;
  onChange: (value: ParameterAtom) => void;
  onValidityChange: (field: string, valid: boolean) => void;
}) {
  if (type.type === "bool") {
    return (
      <select
        aria-label={label}
        value={value === true ? "true" : "false"}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value === "true")}
      >
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (type.type === "int" || type.type === "float") {
    return (
      <NumericInput
        label={label}
        value={typeof value === "number" ? value : 0}
        integer={type.type === "int"}
        minimum={type.minimum ?? undefined}
        maximum={type.maximum ?? undefined}
        disabled={disabled}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    );
  }
  if (type.type === "string") {
    if (type.choices) {
      return (
        <select
          aria-label={label}
          value={typeof value === "string" ? value : ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        >
          {type.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      );
    }
    return (
      <input
        aria-label={label}
        type="text"
        value={typeof value === "string" ? value : ""}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  if (type.type === "quantity") {
    const quantity =
      typeof value === "object" && "value" in value ? value : { value: 0, unit: type.unit ?? "" };
    return (
      <span className="grid grid-cols-[minmax(80px,1fr)_minmax(65px,0.6fr)] gap-[5px]">
        <NumericInput
          label={`${label} value`}
          value={quantity.value}
          minimum={type.minimum ?? undefined}
          maximum={type.maximum ?? undefined}
          disabled={disabled}
          onChange={(next) => onChange({ value: next, unit: quantity.unit })}
          onValidityChange={onValidityChange}
        />
        <input
          aria-label={`${label} unit`}
          type="text"
          value={quantity.unit}
          disabled={disabled}
          onChange={(event) => onChange({ value: quantity.value, unit: event.target.value })}
        />
      </span>
    );
  }
  const choices = entities.filter(
    (entity) => !type.entity_kind || entity.kind === type.entity_kind,
  );
  const entity = typeof value === "object" && "id" in value ? value : { id: "", metadata: {} };
  const selected = entityKey(entity);
  return (
    <select
      aria-label={label}
      value={selected}
      disabled={disabled}
      onChange={(event) => {
        const next = choices.find((candidate) => entityKey(candidate) === event.target.value);
        if (next) onChange(next);
      }}
    >
      {!choices.some((choice) => entityKey(choice) === selected) && (
        <option value={selected}>{entity.id || "Choose entity"}</option>
      )}
      {choices.map((choice) => (
        <option key={entityKey(choice)} value={entityKey(choice)}>
          {choice.id}
          {choice.kind ? ` · ${choice.kind}` : ""}
        </option>
      ))}
    </select>
  );
}

function NumericInput({
  label,
  value,
  integer = false,
  minimum,
  maximum,
  disabled,
  onChange,
  onValidityChange,
}: {
  label: string;
  value: number;
  integer?: boolean;
  minimum?: number;
  maximum?: number;
  disabled: boolean;
  onChange: (value: number) => void;
  onValidityChange: (field: string, valid: boolean) => void;
}) {
  const [draft, setDraft] = useState(() => ({ source: value, text: String(value) }));
  const text = Object.is(draft.source, value) ? draft.text : String(value);
  const valid = numericTextIsValid(text, {
    integer,
    minimum,
    maximum,
  });
  return (
    <input
      aria-label={label}
      aria-invalid={!valid}
      type="number"
      value={text}
      step={integer ? 1 : "any"}
      min={minimum}
      max={maximum}
      required
      disabled={disabled}
      onChange={(event) => {
        const nextText = event.target.value;
        setDraft({ source: value, text: nextText });
        const next = Number(nextText);
        const nextValid = numericTextIsValid(nextText, {
          integer,
          minimum,
          maximum,
        });
        onValidityChange(label, nextValid);
        if (nextValid) {
          onChange(next);
        }
      }}
    />
  );
}

function numericTextIsValid(
  text: string,
  {
    integer,
    minimum,
    maximum,
  }: {
    integer: boolean;
    minimum?: number;
    maximum?: number;
  },
): boolean {
  const value = Number(text);
  return (
    text !== "" &&
    Number.isFinite(value) &&
    (!integer || Number.isInteger(value)) &&
    (minimum === undefined || value >= minimum) &&
    (maximum === undefined || value <= maximum)
  );
}

function entityKey(entity: ParameterEntity): string {
  return JSON.stringify([entity.kind ?? null, entity.id]);
}

const draftEmpty =
  "rounded-[8px] border border-dashed border-line-strong bg-bg p-3.5 text-[0.64rem] leading-normal text-text-dim";
const atomInput =
  "grid min-w-[120px] gap-[5px] [&_input:not([type=checkbox])]:min-h-[34px] [&_input:not([type=checkbox])]:rounded-[7px] [&_input:not([type=checkbox])]:border [&_input:not([type=checkbox])]:border-line-strong [&_input:not([type=checkbox])]:bg-panel [&_input:not([type=checkbox])]:px-2 [&_input:not([type=checkbox])]:text-[0.64rem] [&_input:not([type=checkbox])]:text-text [&_input:not([type=checkbox])]:outline-0 [&_input:not([type=checkbox]):focus]:border-[rgb(128_163_207_/_45%)] [&_input[aria-invalid=true]]:border-[rgb(255_140_136_/_52%)] [&_input[aria-invalid=true]]:bg-red-soft [&_input:disabled]:opacity-70 [&_select]:min-h-[34px] [&_select]:rounded-[7px] [&_select]:border [&_select]:border-line-strong [&_select]:bg-panel [&_select]:px-2 [&_select]:text-[0.64rem] [&_select]:text-text [&_select]:outline-0 [&_select:focus]:border-[rgb(128_163_207_/_45%)] [&_select:disabled]:opacity-70";
