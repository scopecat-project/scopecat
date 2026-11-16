import { useState } from "react";
import { convertControlNumber } from "../launch/control-quantity";
import { engineeringUnit, quantityUnits } from "./instrument-quantity";
import * as Controls from "../../ui/Controls";
import type { InstrumentProperty, InstrumentStateValue } from "../../api-contract";

export interface InstrumentPropertyDraft {
  raw: string | boolean;
  unit?: string;
  value?: InstrumentStateValue;
}

export function InstrumentPropertyInput({
  property,
  currentValue,
  draft,
  editable,
  ariaLabel,
  onChange,
}: {
  property: InstrumentProperty;
  currentValue?: InstrumentStateValue;
  draft?: InstrumentPropertyDraft;
  editable: boolean;
  ariaLabel?: string;
  onChange: (draft: InstrumentPropertyDraft) => void;
}) {
  const type = property.value_type;
  if (type.type === "quantity") {
    return (
      <QuantityInput
        key={`${property.id}:${type.unit}`}
        property={property}
        currentValue={currentValue}
        draft={draft}
        editable={editable}
        ariaLabel={ariaLabel}
        onChange={onChange}
      />
    );
  }
  const value = draft?.raw ?? inputValue(currentValue, type.type);
  if (type.type === "bool") {
    return (
      <span className="flex items-center gap-2 text-xs text-text-soft">
        <input
          className="size-[15px] accent-accent"
          type="checkbox"
          checked={typeof value === "boolean" ? value : false}
          disabled={!editable}
          aria-label={ariaLabel}
          onChange={(event) => onChange({ raw: event.target.checked, value: event.target.checked })}
        />
        {typeof value === "boolean" ? (value ? "开启" : "关闭") : "未知"}
      </span>
    );
  }
  if (type.type === "string" && type.choices) {
    return (
      <Controls.Select
        className={propertyInput}
        value={typeof value === "string" ? value : ""}
        disabled={!editable}
        aria-label={ariaLabel}
        onChange={(event) => onChange({ raw: event.target.value, value: event.target.value })}
      >
        <option value="" disabled>
          请选择…
        </option>
        {type.choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </Controls.Select>
    );
  }
  if (type.type === "int" || type.type === "float") {
    return (
      <span className="grid grid-cols-[minmax(0,1fr)_auto] items-stretch">
        <Controls.Input
          className={`${propertyInput} rounded-r-none!`}
          type="number"
          step={type.type === "int" ? 1 : "any"}
          min={type.minimum ?? undefined}
          max={type.maximum ?? undefined}
          value={typeof value === "string" || typeof value === "number" ? value : ""}
          placeholder={editable ? "输入数值" : "—"}
          disabled={!editable}
          aria-label={ariaLabel}
          aria-invalid={draft !== undefined && draft.value === undefined}
          onChange={(event) => {
            const raw = event.target.value;
            const number = Number(raw);
            const valid =
              raw.length > 0 &&
              Number.isFinite(number) &&
              (type.type !== "int" || Number.isInteger(number)) &&
              (type.minimum === null || type.minimum === undefined || number >= type.minimum) &&
              (type.maximum === null || type.maximum === undefined || number <= type.maximum);
            onChange({ raw, value: valid ? number : undefined });
          }}
        />
      </span>
    );
  }
  return (
    <Controls.Input
      className={propertyInput}
      type="text"
      value={typeof value === "string" ? value : ""}
      placeholder={editable ? "输入数值" : "—"}
      disabled={!editable}
      aria-label={ariaLabel}
      onChange={(event) => onChange({ raw: event.target.value, value: event.target.value })}
    />
  );
}

const propertyInput = "w-full";

function inputValue(
  value: InstrumentStateValue | undefined,
  type: InstrumentProperty["value_type"]["type"],
): string | number | boolean {
  if (value === undefined) return "";
  if (type === "quantity") return quantityValue(value)?.value ?? "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return value;
  }
  return "";
}

function quantityValue(
  value: InstrumentStateValue | undefined,
): { value: number; unit: string } | undefined {
  if (
    typeof value === "object" &&
    value !== null &&
    "value" in value &&
    "unit" in value &&
    typeof value.value === "number" &&
    typeof value.unit === "string"
  ) {
    return value;
  }
  return undefined;
}

/** Unit selection is presentation state: only editing the number creates a device draft. */
function QuantityInput({
  property,
  currentValue,
  draft,
  editable,
  ariaLabel,
  onChange,
}: Parameters<typeof InstrumentPropertyInput>[0]) {
  const type = property.value_type;
  if (type.type !== "quantity") throw new Error("Expected quantity property");
  const current = quantityValue(currentValue);
  const canonical = type.unit ?? current?.unit ?? "";
  const [selection, setSelection] = useState<string | null>(() =>
    current ? engineeringUnit(current.value, current.unit) : null,
  );
  // A property can mount before its first read. Adopt the first observation's scale once,
  // then retain the user's reading scale across polling and edits.
  if (selection === null && current) setSelection(engineeringUnit(current.value, current.unit));
  const unit = selection ?? canonical;
  const options = quantityUnits(canonical);
  let original = "";
  try {
    if (current) original = convertControlNumber(String(current.value), current.unit, unit);
  } catch {
    /* incompatible observation */
  }
  let raw = draft ? String(draft.raw) : original;
  if (draft?.unit && draft.unit !== unit) {
    try {
      raw = convertControlNumber(raw, draft.unit, unit);
    } catch {
      /* retain invalid input */
    }
  }
  return (
    <span className="grid grid-cols-[minmax(0,1fr)_auto] items-stretch">
      <Controls.Input
        className={`${propertyInput} rounded-r-none!`}
        type="text"
        inputMode="decimal"
        value={raw}
        placeholder={editable ? "输入数值" : "—"}
        disabled={!editable}
        aria-label={ariaLabel}
        aria-invalid={draft !== undefined && draft.value === undefined}
        onChange={(event) => {
          const edited = event.target.value;
          let value: InstrumentStateValue | undefined;
          try {
            let number = Number(convertControlNumber(edited, unit, canonical));
            // Do not round-trip an unchanged observation through scaled floating-point arithmetic.
            if (current && edited === original && current.unit === canonical)
              number = current.value;
            if (
              canonical &&
              Number.isFinite(number) &&
              (type.minimum == null || number >= type.minimum) &&
              (type.maximum == null || number <= type.maximum)
            )
              value = { value: number, unit: canonical };
          } catch {
            /* invalid drafts remain visible, but cannot be applied */
          }
          onChange({ raw: edited, unit, value });
        }}
      />
      {options.length > 1 ? (
        <Controls.Select
          className="rounded-l-none! border-l-0!"
          aria-label={`${ariaLabel ?? property.label ?? property.id} 单位`}
          value={unit}
          disabled={draft !== undefined && draft.value === undefined}
          onChange={(event) => setSelection(event.target.value)}
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </Controls.Select>
      ) : (
        <span className="grid min-w-[42px] place-items-center rounded-r-sm border border-l-0 border-line bg-panel-strong px-2 text-xs text-text-dim">
          {canonical || "需要单位"}
        </span>
      )}
    </span>
  );
}
