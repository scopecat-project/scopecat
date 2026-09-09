import type { ConfigContextResolution } from "../config/config-api";
import type { ComparisonHandoff } from "../analyses/RunComparison";
import { initialControlDrafts, type ControlDraft } from "./ControlFields";
import { definitionKey, invalidateDraft, type LaunchDraft } from "./LaunchDraft";
import type { LaunchCatalogEntry } from "./launch-api";

function scalar(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return { value, unit: null };
  if (
    typeof value === "object" &&
    value !== null &&
    "value" in value &&
    "unit" in value &&
    typeof value.value === "number" &&
    Number.isFinite(value.value) &&
    typeof value.unit === "string"
  )
    return { value: value.value, unit: value.unit };
  throw new Error("Suggested scans must contain explicit finite numeric values.");
}

export function importLaunchHandoff(
  current: LaunchDraft,
  entry: LaunchCatalogEntry,
  handoff: ComparisonHandoff,
  selectedSource?: ConfigContextResolution["config_source"],
): LaunchDraft {
  const request = handoff.request;
  if (request.experiment !== entry.id || request.version !== entry.version)
    throw new Error(
      "The suggested experiment definition changed. Reopen the source analysis and review its inputs.",
    );
  if (
    request.context &&
    (selectedSource?.context.entry_id !== request.context.entry_id ||
      selectedSource.context.content_hash !== request.context.content_hash ||
      JSON.stringify(selectedSource.overrides) !== JSON.stringify(request.overrides))
  )
    throw new Error(
      "Select and resolve the suggested exact parameter context and overrides in Configuration first; context instructions cannot be silently dropped.",
    );
  if (request.config_source || request.action !== "preview")
    throw new Error(
      "Suggested inputs require a fresh preview in the selected configuration; a submitted or frozen request cannot be imported as a draft.",
    );
  const unknown = Object.keys(request.inputs ?? {}).filter(
    (name) => !(name in (entry.request.properties ?? {})),
  );
  if (unknown.length) throw new Error(`Unknown suggested input: ${unknown.join(", ")}`);
  const controls = initialControlDrafts(entry.controls);
  for (const [name, edit] of Object.entries(request.control_edits ?? {})) {
    if (!entry.controls.some((control) => control.id === name && control.ownership === "editable"))
      throw new Error(`Suggested control is unavailable or configuration-owned: ${name}`);
    let draft: ControlDraft;
    if (edit.mode === "default") draft = { mode: "default" };
    else if (edit.mode === "fixed" && edit.value !== null && edit.value !== undefined) {
      draft = {
        mode: "fixed",
        value: String(typeof edit.value === "object" ? edit.value.value : edit.value),
        unit: typeof edit.value === "object" ? edit.value.unit : null,
      };
    } else if (edit.mode === "scan" && edit.axis) {
      const axis = edit.axis;
      if (axis.kind === "around")
        throw new Error(
          "Relative scans require project configuration resolution before draft import.",
        );
      const first = scalar(axis.kind === "range" ? axis.start : axis.values[0]);
      const render = (value: unknown) => {
        const next = scalar(value);
        if (next.unit !== first.unit)
          throw new Error("Suggested scan values must use one explicit unit before import.");
        return String(next.value);
      };
      draft =
        axis.kind === "range"
          ? {
              mode: "range",
              start: render(axis.start),
              stop: render(axis.stop),
              points: String(axis.points),
              unit: first.unit,
            }
          : { mode: "values", values: axis.values.map(render).join("\n"), unit: first.unit };
    } else throw new Error(`Invalid suggested control: ${name}`);
    controls[name] = draft;
  }
  return {
    ...invalidateDraft(
      current,
      "Suggested inputs imported from saved analysis. Preview in the selected configuration before starting. Source provenance is retained only in this console draft.",
    ),
    experiment: entry.id,
    definition: definitionKey(entry),
    values: {
      ...current.values,
      ...Object.fromEntries(
        Object.entries(request.inputs ?? {}).map(([name, value]) => [
          name,
          Array.isArray(value)
            ? value.join("\n")
            : typeof value === "string"
              ? value
              : JSON.stringify(value),
        ]),
      ),
    },
    controls,
    sample: request.sample ?? "",
    actor: request.actor ?? "operator",
    handoff,
  };
}
