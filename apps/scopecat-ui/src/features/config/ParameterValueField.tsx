import type { ParameterEntity, ParameterScalarType } from "../../api-contract";
import type { ParameterDraftAtom } from "./parameter-draft-api";
import { secondaryButton } from "../../ui/styles";

const identity = (item: { id: string; kind?: string | null }) =>
  JSON.stringify([item.kind ?? null, item.id]);

export function ParameterValueField({
  label,
  displayLabel,
  origin,
  type,
  value,
  entities,
  disabled,
  onChange,
}: {
  label: string;
  displayLabel?: string;
  origin?: string;
  type: ParameterScalarType;
  value?: ParameterDraftAtom;
  entities: ParameterEntity[];
  disabled?: boolean;
  onChange: (value?: ParameterDraftAtom) => void;
}) {
  let retained: { id: string; kind?: string | null } | undefined;
  if (type.type === "entity" && value) {
    try {
      const parsed: unknown = JSON.parse(value.text);
      if (
        parsed &&
        typeof parsed === "object" &&
        "id" in parsed &&
        typeof parsed.id === "string" &&
        (!("kind" in parsed) || parsed.kind === null || typeof parsed.kind === "string")
      )
        retained = {
          id: parsed.id,
          kind: "kind" in parsed ? (parsed.kind as string | null) : null,
        };
    } catch {
      /* Raw, incomplete input remains recoverable until explicit validation. */
    }
  }
  const choices = entities.filter(
    (item) => type.type === "entity" && (!type.entity_kind || item.kind === type.entity_kind),
  );
  const selected = retained ? identity(retained) : value ? "retained-invalid" : "";
  return (
    <label className="flex flex-wrap items-center gap-2">
      {displayLabel ? (
        <span className="w-full font-medium text-text-soft">{displayLabel}</span>
      ) : (
        label
      )}
      {type.type === "bool" ? (
        <select
          aria-label={label}
          value={value?.text ?? ""}
          disabled={disabled}
          onChange={(event) =>
            onChange(event.target.value === "" ? undefined : { text: event.target.value, unit: "" })
          }
        >
          <option value="">Unknown</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : type.type === "entity" ? (
        <select
          aria-label={label}
          value={selected}
          disabled={disabled}
          onChange={(event) =>
            onChange(
              event.target.value
                ? {
                    text: JSON.stringify(
                      choices.find((item) => identity(item) === event.target.value),
                    ),
                    unit: "",
                  }
                : undefined,
            )
          }
        >
          <option value="">Unknown</option>
          {value && !choices.some((item) => identity(item) === selected) && (
            <option value={selected}>
              {retained
                ? `Retained ${retained.kind}: ${retained.id}`
                : "Incomplete retained entity"}
            </option>
          )}
          {choices.map((item) => (
            <option key={identity(item)} value={identity(item)}>
              {item.id}
            </option>
          ))}
        </select>
      ) : (
        <input
          aria-label={label}
          placeholder="Unknown"
          type="text"
          inputMode={type.type === "string" ? undefined : "decimal"}
          disabled={disabled}
          value={value?.text ?? ""}
          onChange={(event) => {
            const text = event.target.value;
            onChange({
              text,
              unit: value?.unit ?? (type.type === "quantity" ? (type.unit ?? "") : ""),
            });
          }}
        />
      )}
      {type.type === "quantity" && (
        <input
          aria-label={`${label} unit`}
          value={value?.unit ?? type.unit ?? ""}
          disabled={disabled || value === undefined}
          onChange={(event) => {
            if (value) onChange({ ...value, unit: event.target.value });
          }}
        />
      )}
      <small>
        {origin ?? (value === undefined ? "Unknown" : "Value set")}
        {type.type === "quantity" ? ` · ${value?.unit ?? type.unit ?? ""}` : ""}
      </small>
      {!disabled && (
        <button type="button" className={secondaryButton} onClick={() => onChange(undefined)}>
          Mark unknown
        </button>
      )}
    </label>
  );
}
