import type { ParameterAtom, ParameterEntity, ParameterScalarType } from "../../api-contract";
import { secondaryButton } from "../../ui/styles";

export function ParameterValueField({
  label,
  origin,
  type,
  value,
  entities,
  disabled,
  onChange,
}: {
  label: string;
  origin?: string;
  type: ParameterScalarType;
  value?: ParameterAtom;
  entities: ParameterEntity[];
  disabled?: boolean;
  onChange: (value?: ParameterAtom) => void;
}) {
  const quantity =
    value != null && typeof value === "object" && "unit" in value ? value : undefined;
  const entity = value != null && typeof value === "object" && "id" in value ? value : undefined;
  return (
    <label className="flex flex-wrap items-center gap-2">
      {label}
      {type.type === "bool" ? (
        <select
          aria-label={label}
          value={typeof value === "boolean" ? String(value) : ""}
          disabled={disabled}
          onChange={(event) =>
            onChange(event.target.value === "" ? undefined : event.target.value === "true")
          }
        >
          <option value="">Unknown</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : type.type === "entity" ? (
        <select
          aria-label={label}
          value={entity?.id ?? ""}
          disabled={disabled}
          onChange={(event) => onChange(entities.find((item) => item.id === event.target.value))}
        >
          <option value="">Unknown</option>
          {entities
            .filter((item) => !type.entity_kind || item.kind === type.entity_kind)
            .map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
        </select>
      ) : (
        <input
          aria-label={label}
          placeholder="Unknown"
          type={type.type === "string" ? "text" : "number"}
          step="any"
          disabled={disabled}
          value={
            quantity?.value ?? (typeof value === "string" || typeof value === "number" ? value : "")
          }
          onChange={(event) => {
            const text = event.target.value;
            if (type.type === "string") onChange(text);
            else if (text === "") onChange(undefined);
            else if (type.type === "quantity")
              onChange({ value: Number(text), unit: quantity?.unit ?? type.unit ?? "" });
            else onChange(Number(text));
          }}
        />
      )}
      {type.type === "quantity" && (
        <input
          aria-label={`${label} unit`}
          value={quantity?.unit ?? type.unit ?? ""}
          disabled={disabled || quantity === undefined}
          onChange={(event) => {
            if (quantity) onChange({ ...quantity, unit: event.target.value });
          }}
        />
      )}
      <small>
        {origin ?? (value === undefined ? "Unknown" : "Value set")}
        {type.type === "quantity" ? ` · ${quantity?.unit ?? type.unit ?? ""}` : ""}
      </small>
      {!disabled && (
        <button type="button" className={secondaryButton} onClick={() => onChange(undefined)}>
          Mark unknown
        </button>
      )}
    </label>
  );
}
