import { Notice, Field, Select, Input, Textarea } from "../../ui/Controls";
import { useState } from "react";
import { controlNumber, convertControlNumber } from "./control-quantity";
import type { components } from "../../api-schema";
import registry from "../../unit-registry.json";

type Control = components["schemas"]["LaunchControl"];
type Edit = components["schemas"]["ControlEdit-Input"];
type Value = components["schemas"]["LaunchControlValue"];
export type ControlDraft =
  | { mode: "default" }
  | { mode: "fixed"; value: string; unit: string | null }
  | { mode: "values"; values: string; unit: string | null }
  | { mode: "range"; start: string; stop: string; points: string; unit: string | null };
export type ControlDrafts = Record<string, ControlDraft>;
const units: Record<string, { kind: string; scale: number | null }> = registry;

function defaultNumber(control: Control): string {
  return String(
    typeof control.default === "object" ? (control.default?.value ?? "") : (control.default ?? ""),
  );
}
function newSource(control: Control, mode: ControlDraft["mode"]): ControlDraft {
  if (mode === "default") return { mode };
  if (mode === "fixed") return { mode, value: defaultNumber(control), unit: control.unit };
  if (mode === "values") return { mode, values: defaultNumber(control), unit: control.unit };
  return { mode, start: defaultNumber(control), stop: "", points: "2", unit: control.unit };
}
export function initialControlDrafts(controls: Control[]): ControlDrafts {
  return Object.fromEntries(
    controls
      .filter((control) => control.ownership === "editable")
      .map((control) => [control.id, newSource(control, "fixed")]),
  );
}
function number(value: string): number {
  const parsed = Number(value);
  if (!value.trim() || !Number.isFinite(parsed))
    throw new Error("Control values must be finite numbers.");
  return parsed;
}
function quantity(value: string, unit: string | null) {
  return unit ? { value: controlNumber(value, unit), unit } : controlNumber(value, null);
}
export function controlEdits(drafts: ControlDrafts): Record<string, Edit> {
  return Object.fromEntries(
    Object.entries(drafts).map(([id, draft]): [string, Edit] => {
      if (draft.mode === "default") return [id, { mode: "default" }];
      if (draft.mode === "fixed")
        return [id, { mode: "fixed", value: quantity(draft.value, draft.unit) }];
      if (draft.mode === "values")
        return [
          id,
          {
            mode: "scan",
            axis: {
              kind: "values",
              values: draft.values
                .trim()
                .split(/[\s,]+/)
                .map((value) => quantity(value, draft.unit)),
            },
          },
        ];
      return [
        id,
        {
          mode: "scan",
          axis: {
            kind: "range",
            start: quantity(draft.start, draft.unit),
            stop: quantity(draft.stop, draft.unit),
            points: number(draft.points),
          },
        },
      ];
    }),
  );
}
function convertDraft(
  draft: Exclude<ControlDraft, { mode: "default" }>,
  unit: string | null,
): ControlDraft {
  const convert = (value: string) =>
    value.trim() ? convertControlNumber(value, draft.unit, unit) : value;
  if (draft.mode === "fixed") return { ...draft, value: convert(draft.value), unit };
  if (draft.mode === "values")
    return {
      ...draft,
      values: draft.values
        .split(/([\s,]+)/)
        .map((value) => (/^[\s,]*$/.test(value) ? value : convert(value)))
        .join(""),
      unit,
    };
  return { ...draft, start: convert(draft.start), stop: convert(draft.stop), unit };
}
export function ControlFields({
  controls,
  drafts,
  onChange,
}: {
  controls: Control[];
  drafts: ControlDrafts;
  onChange: (id: string, draft: ControlDraft) => void;
}) {
  const [errors, setErrors] = useState<
    Record<string, { draft: ControlDraft | undefined; message: string } | undefined>
  >({});
  function update(id: string, action: () => ControlDraft) {
    try {
      const next = action();
      if (JSON.stringify(next) !== JSON.stringify(drafts[id])) onChange(id, next);
      setErrors((previous) => ({ ...previous, [id]: undefined }));
    } catch (error) {
      setErrors((previous) => ({
        ...previous,
        [id]: { draft: drafts[id], message: (error as Error).message },
      }));
    }
  }
  const groups = [...new Set(controls.map((control) => control.group))];
  return (
    <div className="space-y-3">
      {groups.map((group) => (
        <fieldset key={group} className="rounded-md border border-line p-3 space-y-3">
          <legend>{group || "Controls"}</legend>
          {controls
            .filter((control) => control.group === group)
            .map((control) => {
              const draft = drafts[control.id];
              if (control.ownership !== "editable" || !draft)
                return (
                  <div key={control.id}>
                    <strong>{control.title}</strong>
                    <p>
                      {control.ownership === "derived" ? "Derived" : "Configuration-owned"} ·{" "}
                      {control.provenance}. Resolved in preview.
                    </p>
                  </div>
                );
              // LaunchForm reuses this component when opening another plan. Errors belong
              // to the exact validated draft, not an id shared by multiple plans.
              const error =
                errors[control.id]?.draft === draft ? errors[control.id]?.message : undefined;
              const declaredUnit = control.unit;
              const declared = declaredUnit ? units[declaredUnit] : undefined;
              const choices = declaredUnit
                ? declared?.scale != null
                  ? Object.keys(units).filter(
                      (unit) => units[unit]?.kind === declared.kind && units[unit]?.scale != null,
                    )
                  : [declaredUnit]
                : [];
              const scale =
                draft.mode !== "default" && draft.unit
                  ? (units[control.unit ?? ""]?.scale ?? 1) / (units[draft.unit]?.scale ?? 1)
                  : 1;
              const numeric = {
                type: "text",
                inputMode: "decimal" as const,
                step: "any",
                required: true,
                min: control.minimum == null ? undefined : control.minimum * scale,
                max: control.maximum == null ? undefined : control.maximum * scale,
                "aria-invalid": Boolean(error),
              };
              return (
                <div key={control.id} className="space-y-2">
                  <strong>
                    {control.title}
                    {control.default == null ? " (required)" : ""}
                  </strong>
                  {error && <Notice role="alert">{error}</Notice>}
                  <p className="text-sm">
                    {control.provenance}
                    {control.minimum != null || control.maximum != null
                      ? ` · Allowed: ${control.minimum ?? "−∞"}–${control.maximum ?? "∞"} ${control.unit ?? ""}`
                      : ""}
                  </p>
                  <Field>
                    Source{" "}
                    <Select
                      aria-label={`${control.title} source`}
                      value={draft.mode}
                      onChange={(event) =>
                        update(control.id, () =>
                          newSource(control, event.target.value as ControlDraft["mode"]),
                        )
                      }
                    >
                      <option value="fixed">Fixed value</option>
                      {control.default != null && <option value="default">Declared default</option>}
                      {control.scannable && (
                        <>
                          <option value="values">Scan values</option>
                          <option value="range">Scan range</option>
                        </>
                      )}
                    </Select>
                  </Field>
                  {draft.mode === "default" ? (
                    <p>
                      {defaultNumber(control)} {control.unit} · Declared default
                    </p>
                  ) : (
                    <>
                      {draft.unit && (
                        <p className="text-sm">裸数按 {draft.unit}；单位简写失焦后换算。</p>
                      )}
                      {draft.unit && (
                        <Field>
                          Unit{" "}
                          <Select
                            aria-label={`${control.title} unit`}
                            value={draft.unit}
                            onChange={(event) =>
                              update(control.id, () => convertDraft(draft, event.target.value))
                            }
                          >
                            {choices.map((unit) => (
                              <option key={unit}>{unit}</option>
                            ))}
                          </Select>
                        </Field>
                      )}
                      {draft.mode === "fixed" && (
                        <Input
                          {...numeric}
                          aria-label={control.title}
                          value={draft.value}
                          onBlur={() =>
                            update(control.id, () => ({
                              ...draft,
                              value: String(controlNumber(draft.value, draft.unit)),
                            }))
                          }
                          onChange={(event) =>
                            onChange(control.id, { ...draft, value: event.target.value })
                          }
                        />
                      )}
                      {draft.mode === "values" && (
                        <label>
                          Values{" "}
                          <Textarea
                            required
                            aria-label={`${control.title} scan values`}
                            value={draft.values}
                            onBlur={() => update(control.id, () => convertDraft(draft, draft.unit))}
                            onChange={(event) =>
                              onChange(control.id, { ...draft, values: event.target.value })
                            }
                            aria-invalid={Boolean(error)}
                          />
                          <span className="text-sm">
                            逗号、空白或换行分隔；单位后缀紧接数字，不加空格。单值仍为扫描。
                          </span>
                        </label>
                      )}
                      {draft.mode === "range" && (
                        <div className="flex gap-2">
                          <Input
                            {...numeric}
                            aria-label={`${control.title} start`}
                            placeholder="Start"
                            value={draft.start}
                            onBlur={() =>
                              update(control.id, () => ({
                                ...draft,
                                start: String(controlNumber(draft.start, draft.unit)),
                              }))
                            }
                            onChange={(event) =>
                              onChange(control.id, { ...draft, start: event.target.value })
                            }
                          />
                          <Input
                            {...numeric}
                            aria-label={`${control.title} stop`}
                            placeholder="Stop"
                            value={draft.stop}
                            onBlur={() =>
                              update(control.id, () => ({
                                ...draft,
                                stop: String(controlNumber(draft.stop, draft.unit)),
                              }))
                            }
                            onChange={(event) =>
                              onChange(control.id, { ...draft, stop: event.target.value })
                            }
                          />
                          <Input
                            type="text"
                            inputMode="decimal"
                            min={2}
                            step={1}
                            required
                            aria-label={`${control.title} points`}
                            value={draft.points}
                            onChange={(event) =>
                              onChange(control.id, { ...draft, points: event.target.value })
                            }
                            className="w-24"
                          />
                        </div>
                      )}
                    </>
                  )}
                </div>
              );
            })}
        </fieldset>
      ))}
      {controls.some((control) => control.scannable) && (
        <p className="text-sm">
          Changing source replaces its values. Fixed value starts from the declared default. Project
          constraints are checked during preview and before admission.
        </p>
      )}
    </div>
  );
}
function display(value: unknown): string {
  if (typeof value === "number") return String(value);
  if (value && typeof value === "object" && "value" in value && "unit" in value)
    return `${String(value.value)} ${String(value.unit)}`;
  return JSON.stringify(value);
}
export function ControlSummary({ fields, values }: { fields: Control[]; values: Value[] }) {
  if (!values.length) return null;
  return (
    <section>
      <h3>Resolved controls</h3>
      <table className="w-full text-left">
        <thead>
          <tr>
            <th>Control</th>
            <th>Source</th>
            <th>Value / axis</th>
            <th>Provenance</th>
          </tr>
        </thead>
        <tbody>
          {values.map((value) => {
            const source = value.axis?.source;
            const text =
              value.value != null
                ? display(value.value)
                : source?.kind === "values"
                  ? source.values.map(display).join(", ")
                  : source?.kind === "range"
                    ? `${display(source.start)}–${display(source.stop)} · ${source.points} points`
                    : "";
            return (
              <tr key={value.id}>
                <td>{fields.find((field) => field.id === value.id)?.title ?? value.id}</td>
                <td>{value.state}</td>
                <td>{text}</td>
                <td>{value.provenance}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
