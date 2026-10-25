import { Field, Input, Button, Notice } from "../../ui/Controls";
import { normalizeSelection, subjectSample, reviewedForRequest } from "./scientific-selection";
import type { PlanRevision } from "./experiment-plans";
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
      const source = preview.reviewed.config_source;
      const selection = normalizeSelection(request.selection);
      const sample = subjectSample(preview.reviewed.binding);
      if (source.kind === "analysis_candidate")
        throw new Error("保存计划s from a named parameter context, not an unaccepted candidate.");
      const saved = await apiData<PlanRevision>(
        apiClient.POST("/api/v1/experiment-plans", {
          headers: { "X-Scopecat-Workspace": preview.workspace_id },
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
              workspace_id: preview.workspace_id,
              inputs: request.inputs ?? {},
              scan_mode: request.scan_mode,
              parameter_sweeps: request.parameter_sweeps,
              control_edits: request.control_edits ?? {},
              selection: {
                ...selection,
                subject:
                  selection.subject.kind === "sample" && sample
                    ? { kind: "sample", sample_id: sample.sample_id, revision: sample.revision }
                    : selection.subject,
                configuration: selection.configuration,
              },
              scientific_binding: reviewedForRequest(preview.reviewed).binding,
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
          selection: normalizeSelection(saved.definition.selection),
          codeRevision: saved.definition.code_revision,
          workspaceId: saved.definition.workspace_id,
        }));
      await cache.invalidateQueries({ queryKey: ["experiment-plans", projectId] });
    } catch (caught) {
      if (isCurrent(revision)) setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }
  return (
    <section aria-label="保存实验计划" className="border border-line rounded p-3 space-y-2">
      <Field>
        计划名称{" "}
        <Input
          aria-label="计划名称"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </Field>
      <p>
        {draft?.plan
          ? `${draft.plan.name}, revision ${draft.plan.ref.revision}${draft.planDirty ? " · unsaved input changes" : ""}`
          : "保存这些输入与精确配置，供之后复用。"}
      </p>
      <Button
        type="button"
        disabled={!preview?.definition_hash || !name.trim() || saving}
        onClick={() => void save(false)}
      >
        {draft?.plan ? "保存新 revision" : "保存计划"}
      </Button>
      {draft?.plan && (
        <Button
          type="button"
          disabled={!preview?.definition_hash || !name.trim() || saving}
          onClick={() => void save(true)}
        >
          另存为副本
        </Button>
      )}
      {!preview && <p>保存前请预览当前输入。不会保存请求 key 或执行权限。</p>}
      {error && <Notice role="alert">{error}</Notice>}
    </section>
  );
}
