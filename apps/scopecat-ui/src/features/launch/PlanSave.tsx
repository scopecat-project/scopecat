import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiClient, apiData } from "../../api-client";
import type { components } from "../../api-schema";
import { invalidateDraft, useLaunchDraft } from "./LaunchDraft";
import type { LaunchPreview } from "./launch-api";

export function PlanSave({
  request: getRequest,
  preview,
}: {
  request: () => components["schemas"]["LaunchRequest-Input"];
  preview?: LaunchPreview;
}) {
  const { projectId, draft, update, isCurrent } = useLaunchDraft();
  const cache = useQueryClient();
  const [name, setName] = useState(draft?.plan?.name ?? "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(copy: boolean) {
    if (!draft || !preview?.definition_hash) return;
    const revision = draft.revision;
    setSaving(true);
    setError("");
    try {
      const request = getRequest();
      const source = preview.config_source;
      if (source.kind === "analysis_candidate")
        throw new Error("Save plans from a named parameter context, not an unaccepted candidate.");
      const saved = await apiData(
        apiClient.POST("/api/v1/experiment-plans", {
          body: {
            name,
            saved_by: draft.actor,
            previous: copy ? undefined : draft.plan?.ref,
            copied_from: copy ? draft.plan?.ref : undefined,
            definition: {
              experiment: request.experiment ?? "",
              version: request.version ?? "",
              definition_hash: preview.definition_hash,
              code_revision: preview.code_revision,
              inputs: request.inputs ?? {},
              control_edits: request.control_edits ?? {},
              configuration:
                source.kind === "parameter_context"
                  ? null
                  : { entry_id: source.entry_id, content_hash: source.content_hash },
              context: source.kind === "parameter_context" ? source.context : null,
              overrides: source.kind === "parameter_context" ? source.overrides : [],
              sample: preview.sample_binding,
              source: draft.handoff
                ? {
                    run_id: draft.handoff.source_run,
                    analysis_id: draft.handoff.source_analysis,
                    publication_hash: draft.handoff.source_hash,
                  }
                : draft.plan?.definition.source,
            },
          },
        }),
      );
      if (isCurrent(revision))
        update((current) => ({
          ...invalidateDraft(
            current,
            `Saved ${saved.name}, revision ${saved.ref.revision}. Preview this exact plan before starting.`,
          ),
          plan: saved,
          planDirty: false,
          configuration: saved.definition.configuration,
          sampleBinding: saved.definition.sample,
          codeRevision: saved.definition.code_revision,
        }));
      await cache.invalidateQueries({ queryKey: ["experiment-plans", projectId] });
    } catch (caught) {
      if (isCurrent(revision)) setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }
  return (
    <section aria-label="Save experiment plan" className="border rounded p-3 space-y-2">
      <label>
        Plan name{" "}
        <input
          aria-label="Plan name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="border p-1"
        />
      </label>
      <p>
        {draft?.plan
          ? `${draft.plan.name}, revision ${draft.plan.ref.revision}${draft.planDirty ? " · unsaved input changes" : ""}`
          : "Save these inputs and exact configuration for later."}
      </p>
      <button
        className="border border-line rounded px-2 py-1 mr-2"
        type="button"
        disabled={!preview?.definition_hash || !name.trim() || saving}
        onClick={() => void save(false)}
      >
        {draft?.plan ? "Save new revision" : "Save plan"}
      </button>
      {draft?.plan && (
        <button
          className="border border-line rounded px-2 py-1 mr-2"
          type="button"
          disabled={!preview?.definition_hash || !name.trim() || saving}
          onClick={() => void save(true)}
        >
          Save as copy
        </button>
      )}
      {!preview && (
        <p>
          Preview the current inputs before saving. No request key or execution permission is saved.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
