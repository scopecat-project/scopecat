import * as Controls from "../../ui/Controls";
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
  if (!value.trim() || !Number.isFinite(parsed)) throw new Error("控制值必须是有限数值。");
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
          <legend>{group || "控制项"}</legend>
          {controls
            .filter((control) => control.group === group)
            .map((control) => {
              const draft = drafts[control.id];
              if (control.ownership !== "editable" || !draft)
                return (
                  <div key={control.id}>
                    <strong>{control.title}</strong>
                    <p>
                      {control.ownership === "derived" ? "派生值" : "由配置提供"} ·{" "}
                      {control.provenance}。在预览中解析。
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
                <div
                  key={control.id}
                  className="grid min-w-0 gap-2 border-b border-line py-2 last:border-0 sm:grid-cols-[minmax(9rem,1fr)_minmax(0,2fr)] sm:items-center"
                >
                  <strong>
                    {control.title}
                    {control.default == null ? "（必填）" : ""}
                  </strong>
                  {error && <Notice role="alert">{error}</Notice>}
                  <p className="text-xs text-text-dim sm:col-start-1">
                    {control.provenance}
                    {control.minimum != null || control.maximum != null
                      ? ` · 允许范围： ${control.minimum ?? "−∞"}–${control.maximum ?? "∞"} ${control.unit ?? ""}`
                      : ""}
                  </p>
                  <div className="flex min-w-0 flex-wrap items-end gap-2 sm:col-start-2 sm:row-span-3 sm:row-start-1">
                    <Field>
                      取值来源{" "}
                      <Select
                        aria-label={`${control.title} 取值来源`}
                        value={draft.mode}
                        onChange={(event) =>
                          update(control.id, () =>
                            newSource(control, event.target.value as ControlDraft["mode"]),
                          )
                        }
                      >
                        <option value="fixed">固定值</option>
                        {control.default != null && <option value="default">声明默认值</option>}
                        {control.scannable && (
                          <>
                            <option value="values">扫描列表</option>
                            <option value="range">扫描范围</option>
                          </>
                        )}
                      </Select>
                    </Field>
                    {draft.mode === "default" ? (
                      <p>
                        {defaultNumber(control)} {control.unit} · 声明默认值
                      </p>
                    ) : (
                      <>
                        {draft.unit && (
                          <Field>
                            单位{" "}
                            <Select
                              aria-label={`${control.title} 单位`}
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
                            className="w-32 flex-1"
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
                          <Controls.Field>
                            数值列表{" "}
                            <Textarea
                              required
                              aria-label={`${control.title} 扫描列表`}
                              value={draft.values}
                              onBlur={() =>
                                update(control.id, () => convertDraft(draft, draft.unit))
                              }
                              onChange={(event) =>
                                onChange(control.id, { ...draft, values: event.target.value })
                              }
                              aria-invalid={Boolean(error)}
                            />
                            <span className="text-sm">
                              用逗号、空格或换行分隔数值。单位后缀紧接数值，不加空格。只有一个值也仍是扫描。
                            </span>
                          </Controls.Field>
                        )}
                        {draft.mode === "range" && (
                          <div className="flex w-full min-w-0 gap-2">
                            <Input
                              {...numeric}
                              aria-label={`${control.title} 起点`}
                              placeholder="起点"
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
                              aria-label={`${control.title} 终点`}
                              placeholder="终点"
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
                              aria-label={`${control.title} 点数`}
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
                  {draft.mode !== "default" && draft.unit && (
                    <p className="text-xs text-text-dim sm:col-start-2">
                      不带单位的数值使用 {draft.unit}；带单位后缀的输入在失去焦点时换算。
                    </p>
                  )}
                </div>
              );
            })}
        </fieldset>
      ))}
      {controls.some((control) => control.scannable) && (
        <details className="text-xs text-text-dim">
          <summary className="cursor-pointer">取值来源与校验说明</summary>
          <p>
            切换取值来源会替换其数值。固定值从声明默认值开始。预览与接纳前会检查项目约束。编辑输入不会发布参数版本。
          </p>
        </details>
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
const controlStateLabels: Record<Value["state"], string> = {
  fixed: "固定值",
  scanned: "扫描",
  derived: "派生值",
  configuration: "由配置提供",
};

export function ControlSummary({ fields, values }: { fields: Control[]; values: Value[] }) {
  if (!values.length) return null;
  return (
    <section>
      <h3 className="text-base font-semibold leading-6">已解析控制项</h3>
      <table className="w-full text-left">
        <thead>
          <tr>
            <th>控制项</th>
            <th>取值来源</th>
            <th>值 / 扫描轴</th>
            <th>来源说明</th>
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
                    ? `${display(source.start)}–${display(source.stop)} · ${source.points} 个点`
                    : "";
            return (
              <tr key={value.id}>
                <td>{fields.find((field) => field.id === value.id)?.title ?? value.id}</td>
                <td>{controlStateLabels[value.state] ?? value.state}</td>
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
