import type { components } from "../../api-schema";
import { controlNumber } from "./control-quantity";
import registry from "../../unit-registry.json";
import type { LaunchDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";
import type { SubmissionRequest } from "./launch-submission";
import { controlEdits, type ControlDraft } from "./ControlFields";
import { canRenderField } from "./launch-fields";
import { normalizeSelection, subjectLabel } from "./scientific-selection";
import { formatCoordinateValue } from "../inspections/coordinate-codec";

interface Cell {
  text: string;
  value?: unknown;
  state?: "incomplete" | "unknown";
}
export interface ComparisonRow {
  label: string;
  submitted: string;
  current: string;
  status: "Changed" | "Same" | "Incomplete current input" | "Not compared";
}
const cell = (value: unknown, text = displayValue(value)): Cell => ({ value, text });
const unknown = (text: string): Cell => ({ text, state: "unknown" });
const incomplete = (text: string): Cell => ({
  text: `${text || "Empty"} · incomplete`,
  state: "incomplete",
});

// Object member order is irrelevant; scan/array order remains significant.
function canonical(value: unknown): string {
  if (
    value &&
    typeof value === "object" &&
    Object.keys(value).length === 2 &&
    "value" in value &&
    "unit" in value &&
    typeof value.value === "number" &&
    typeof value.unit === "string"
  ) {
    const units: Record<string, { kind: string; scale: number | null }> = registry;
    const unit = units[value.unit];
    if (unit?.scale != null) return `quantity:${unit.kind}:${value.value * unit.scale}`;
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.entries(value)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`)
      .join(",")}}`;
  return JSON.stringify(value) ?? "undefined";
}
function displayValue(value: unknown): string {
  if (value === undefined) return "Not specified";
  if (value === null) return "None";
  if (Array.isArray(value)) return value.length ? value.map(displayValue).join(", ") : "Empty list";
  if (typeof value === "object") return formatCoordinateValue(value) || "Structured value";
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : "Unsupported value";
}
function declaration(draft: LaunchDraft): LaunchCatalogEntry | undefined {
  try {
    return JSON.parse(draft.definition) as LaunchCatalogEntry;
  } catch {
    return undefined;
  }
}
function currentField(raw: string | undefined, name: string, entry?: LaunchCatalogEntry): Cell {
  const field = entry?.request?.properties?.[name];
  if (raw === undefined || raw === "")
    return entry?.request?.required?.includes(name)
      ? incomplete("")
      : unknown("Current default · preview required");
  if (!field || !canRenderField(field)) return unknown(`${raw} · field declaration unavailable`);
  let value: unknown = raw;
  if (field.type === "number" || field.type === "integer") {
    try {
      value = controlNumber(raw, null);
    } catch {
      return incomplete(raw);
    }
    if (
      !raw.trim() ||
      !Number.isFinite(value) ||
      (field.type === "integer" && !Number.isInteger(value))
    )
      return incomplete(raw);
    if (
      typeof value === "number" &&
      ((field.minimum != null && value < field.minimum) ||
        (field.maximum != null && value > field.maximum) ||
        (field.exclusiveMinimum != null && value <= field.exclusiveMinimum))
    )
      return incomplete(raw);
  } else if (field.type === "boolean") {
    if (raw !== "true" && raw !== "false") return incomplete(raw);
    value = raw === "true";
  } else if (field.type === "array") {
    value = raw.split("\n").filter(Boolean);
    if (Array.isArray(value) && value.some((item) => !field.items?.enum.includes(item)))
      return incomplete(raw);
  }
  if (field.enum && !field.enum.some((item) => item === value)) return incomplete(raw);
  return cell(value, raw);
}
function controlLabel(edit: NonNullable<SubmissionRequest["control_edits"]>[string]): string {
  if (edit.mode === "default") return "Declared default";
  if (edit.mode === "fixed") return `Fixed · ${displayValue(edit.value)}`;
  if (edit.axis?.kind === "values")
    return `Scan · ${edit.axis.values.map(displayValue).join(", ")}`;
  if (edit.axis?.kind === "range")
    return `Scan · ${displayValue(edit.axis.start)} → ${displayValue(edit.axis.stop)} · ${edit.axis.points} points`;
  if (edit.axis?.kind === "around")
    return `Scan around ${displayValue(edit.axis.center)} · span ${displayValue(edit.axis.span)} · ${edit.axis.points} points`;
  return "Retained scan";
}
function controlIdentity(edit: NonNullable<SubmissionRequest["control_edits"]>[string]) {
  return edit.mode === "fixed"
    ? { mode: edit.mode, value: edit.value }
    : { mode: edit.mode, axis: edit.axis };
}
function currentControl(control?: ControlDraft): Cell {
  if (!control) return unknown("Current default · preview required");
  if (control.mode === "default") return unknown("Current default · preview required");
  try {
    const edit = controlEdits({ control }).control!;
    if (
      edit.axis?.kind === "range" &&
      (!Number.isInteger(edit.axis.points) || edit.axis.points < 1)
    )
      throw new Error("Incomplete range");
    return cell(controlIdentity(edit), controlLabel(edit));
  } catch {
    const raw =
      control.mode === "fixed"
        ? control.value
        : control.mode === "values"
          ? control.values
          : `${control.start} → ${control.stop} · ${control.points} points`;
    return incomplete(raw);
  }
}
function configurationLabel(selection: SubmissionRequest["selection"]): string {
  const config = normalizeSelection(selection).configuration;
  if (config.kind === "parameters") return `Parameter version ${config.ref.revision_id}`;
  if (config.kind === "candidate")
    return `Candidate ${config.source.proposal_id} from run ${config.source.source_run_id}`;
  return "Not selected";
}

const parameterFields = (values: Record<string, unknown>) =>
  Object.entries(values)
    .map(([name, value]) => `${name} = ${displayValue(value)}`)
    .join(", ");

function parameterEditsLabel(edits: components["schemas"]["ParameterUpdate-Input"][]): string {
  if (!edits.length) return "None";
  return edits
    .map((edit) => {
      switch (edit.kind) {
        case "replace_parameter":
          return `${edit.value.id}: ${edit.value.shape === "scalar" ? displayValue(edit.value.value) : (edit.value.rows ?? []).map(parameterFields).join("; ")}`;
        case "update_parameter_rows":
          return `${edit.parameter_id} [${parameterFields(edit.key)}]: ${parameterFields(edit.values)}`;
        case "insert_parameter_rows":
          return `${edit.parameter_id} · add ${edit.rows.map(parameterFields).join("; ")}`;
        case "delete_parameter_rows":
          return `${edit.parameter_id} · delete [${parameterFields(edit.key)}]`;
      }
    })
    .join("\n");
}

/** Descriptive comparison only: never preview, freeze inputs, or authorize submission. */
export function compareSubmission(request: SubmissionRequest, draft: LaunchDraft): ComparisonRow[] {
  const rows: ComparisonRow[] = [];
  const add = (label: string, submitted: Cell, current: Cell) =>
    rows.push({
      label,
      submitted: submitted.text,
      current: current.text,
      status:
        current.state === "incomplete"
          ? "Incomplete current input"
          : submitted.state || current.state
            ? "Not compared"
            : canonical(submitted.value) === canonical(current.value)
              ? "Same"
              : "Changed",
    });
  const entry = declaration(draft);
  const previous = normalizeSelection(request.selection);
  const next = draft.selection;
  add("Workspace", cell(request.workspace_id), cell(draft.workspaceId));
  add("Experiment", cell(request.experiment), cell(draft.experiment));
  add(
    "Experiment version",
    cell(request.version),
    entry ? cell(entry.version) : unknown("Declaration unavailable"),
  );
  add(
    "Source revision",
    request.code_revision
      ? cell(request.code_revision.content_hash)
      : unknown("Default at submission"),
    (draft.codeRevision ?? draft.sourceBaseline)
      ? cell((draft.codeRevision ?? draft.sourceBaseline)!.content_hash)
      : unknown("Current source · preview required"),
  );
  const configurationIdentity = (selection: typeof previous) =>
    selection.configuration.kind === "parameters"
      ? selection.configuration.ref
      : selection.configuration;
  add(
    "Configuration",
    cell(configurationIdentity(previous), configurationLabel(previous)),
    cell(configurationIdentity(next), configurationLabel(next)),
  );
  const setup = (selection: typeof previous) =>
    selection.configuration.kind === "parameters"
      ? selection.configuration.setup
      : selection.configuration.kind === "candidate"
        ? selection.configuration.source.setup
        : undefined;
  add(
    "Setup version",
    setup(previous)
      ? cell(setup(previous), setup(previous)!.revision_id)
      : unknown("Default at submission"),
    setup(next) ? cell(setup(next), setup(next)!.revision_id) : unknown("Not selected"),
  );
  const overrides = (selection: typeof previous) =>
    selection.configuration.kind === "parameters" ? selection.configuration.overrides : [];
  add(
    "Parameter edits",
    cell(overrides(previous), parameterEditsLabel(overrides(previous))),
    cell(overrides(next), parameterEditsLabel(overrides(next))),
  );
  add(
    "Sample / target",
    cell(previous.subject, subjectLabel(previous)),
    cell(next.subject, subjectLabel(next)),
  );
  add(
    "Batch",
    cell(previous.batch, previous.batch.kind === "declared" ? previous.batch.id : "No batch"),
    cell(next.batch, next.batch.kind === "declared" ? next.batch.id : "No batch"),
  );
  add(
    "Operator",
    cell(request.actor),
    draft.actor.trim() ? cell(draft.actor) : incomplete(draft.actor),
  );
  add(
    "Collection",
    request.record_collection ? cell(request.record_collection) : unknown("Default at submission"),
    draft.collection ? cell(draft.collection) : unknown("Current default · preview required"),
  );
  const plan = draft.planDirty ? undefined : draft.plan?.ref;
  add(
    "Saved plan",
    cell(
      request.plan_ref ?? null,
      request.plan_ref
        ? `${request.plan_ref.plan_id} · revision ${request.plan_ref.revision}`
        : "None",
    ),
    cell(plan ?? null, plan ? `${plan.plan_id} · revision ${plan.revision}` : "None"),
  );
  if (request.manual_line || draft.manualLine)
    add(
      "Manual scan line",
      cell(request.manual_line ?? null, request.manual_line?.label ?? "None"),
      cell(draft.manualLine ?? null, draft.manualLine?.label ?? "None"),
    );
  if (draft.workingInput)
    add(
      "Working parameter table",
      request.reviewed
        ? cell(request.reviewed.binding.config_content_hash, "Frozen configuration retained")
        : unknown("Not recorded in this request"),
      unknown(
        `Working table revision ${draft.workingInput.revision} · preview and freeze required`,
      ),
    );
  const decision = draft.handoff
    ? {
        run_id: draft.handoff.source_run,
        analysis_id: draft.handoff.source_analysis,
        publication_hash: draft.handoff.source_hash,
      }
    : (draft.lineSource ?? draft.plan?.definition.source);
  if (request.decision_source || decision)
    add(
      "Analysis source",
      cell(
        request.decision_source ?? null,
        request.decision_source
          ? `${request.decision_source.analysis_id} · run ${request.decision_source.run_id}`
          : "None",
      ),
      cell(
        decision ?? null,
        decision ? `${decision.analysis_id} · run ${decision.run_id}` : "None",
      ),
    );
  add("Scan layout", cell(request.scan_mode), cell("cartesian"));
  if (request.parameter_sweeps.length)
    add(
      "Parameter sweeps",
      cell(request.parameter_sweeps, `${request.parameter_sweeps.length} retained sweeps`),
      unknown("Not represented by this editor"),
    );
  for (const name of new Set([
    ...Object.keys(request.inputs ?? {}),
    ...Object.keys(draft.values),
    ...(entry?.request?.required ?? []),
  ])) {
    add(
      `Input · ${name}`,
      Object.hasOwn(request.inputs ?? {}, name)
        ? cell(request.inputs![name])
        : unknown("Default at submission"),
      currentField(draft.values[name], name, entry),
    );
  }
  for (const name of new Set([
    ...Object.keys(request.control_edits ?? {}),
    ...Object.keys(draft.controls),
  ])) {
    const edit = request.control_edits?.[name];
    add(
      `Control · ${name}`,
      edit && edit.mode !== "default"
        ? cell(controlIdentity(edit), controlLabel(edit))
        : unknown("Default at submission"),
      currentControl(draft.controls[name]),
    );
  }
  return rows;
}

export function submittedInputRows(request: SubmissionRequest): { label: string; value: string }[] {
  return [
    ...Object.entries(request.inputs ?? {}).map(([name, value]) => ({
      label: `Input · ${name}`,
      value: displayValue(value),
    })),
    ...Object.entries(request.control_edits ?? {}).map(([name, edit]) => ({
      label: `Control · ${name}`,
      value: controlLabel(edit),
    })),
  ];
}
