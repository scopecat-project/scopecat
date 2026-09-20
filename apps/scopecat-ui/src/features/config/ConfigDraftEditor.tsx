import { problemLocationLabel } from "../../lib/problem-location";
import { useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Eye, LoaderCircle, Pencil, X } from "lucide-react";
import { ApiError } from "../../api-client";
import { classes, eyebrow, iconButton, primaryButton, secondaryButton } from "../../ui/styles";
import { createConfigOperationId, previewConfigDraft, publishConfig } from "./config-api";
import { deriveConfigDraftUpdates } from "./config-draft";
import { ConfigParameters } from "./ConfigParameters";
import { ParameterEditor } from "./ConfigValueEditors";
import type {
  ConfigDraftCommand,
  ConfigDraftPreview,
  ConfigProfileSnapshot,
  ConfigActivationRecord,
  ConfigRegistryEntry,
  ConfigPublishReceipt,
  ParameterUpdate,
  StoredParameterValue,
} from "../../api-contract";

export interface ConfigDraftSeed {
  entry: ConfigRegistryEntry;
  active: ConfigActivationRecord;
  config: ConfigProfileSnapshot;
}

type PendingConfigDraft = Omit<ConfigDraftCommand, "updates"> & {
  updates: ParameterUpdate[];
};

export function ConfigDraftEditor({
  seed,
  currentActive,
  operator,
  onCancel,
  onPublished,
}: {
  seed: ConfigDraftSeed;
  currentActive?: ConfigActivationRecord;
  operator: string;
  onCancel: () => void;
  onPublished: (receipt: ConfigPublishReceipt) => void | Promise<void>;
}) {
  const definitions = seed.config.system.parameter_catalog.definitions ?? [];
  const baseValues = useMemo(
    () => new Map((seed.config.parameter_snapshot.values ?? []).map((value) => [value.id, value])),
    [seed.config],
  );
  const [selectedId, setSelectedId] = useState(definitions[0]?.id);
  const [editedValues, setEditedValues] = useState<Record<string, StoredParameterValue>>({});
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<ConfigDraftPreview>();
  const [invalidFields, setInvalidFields] = useState<Set<string>>(() => new Set());
  const [tableRowOrigins, setTableRowOrigins] = useState<Record<string, Array<"base" | "new">>>({});
  const draftRevision = useRef(0);
  const updates = useMemo(
    () => deriveConfigDraftUpdates(seed.config, editedValues),
    [editedValues, seed.config],
  );
  const draft = useMemo<PendingConfigDraft>(
    () => ({
      base_entry_id: seed.entry.id,
      base_content_hash: seed.active.entry_content_hash,
      base_generation: seed.active.generation,
      candidate_id: `${seed.config.id}-edit`,
      updates,
    }),
    [seed.active, seed.config.id, seed.entry.id, updates],
  );
  const selectedDefinition = definitions.find((definition) => definition.id === selectedId);
  const selectedValue = selectedDefinition
    ? (editedValues[selectedDefinition.id] ?? baseValues.get(selectedDefinition.id))
    : undefined;
  const stale =
    !currentActive ||
    currentActive.entry_id !== seed.active.entry_id ||
    currentActive.entry_content_hash !== seed.active.entry_content_hash ||
    currentActive.generation !== seed.active.generation;

  const previewMutation = useMutation({
    mutationFn: ({ command }: { command: ConfigDraftCommand; revision: number }) =>
      previewConfigDraft(command),
    onSuccess: (result, variables) => {
      if (variables.revision === draftRevision.current) setPreview(result);
    },
  });
  const defaultMutation = useMutation({
    mutationFn: async ({
      command,
      reviewed,
      revision,
      operatorName,
      auditNote,
    }: {
      command: ConfigDraftCommand;
      reviewed?: ConfigDraftPreview;
      revision: number;
      operatorName: string;
      auditNote: string;
    }) => {
      const checked =
        reviewed?.valid && reviewed.result_content_hash
          ? reviewed
          : await previewConfigDraft(command);
      if (!checked.valid || !checked.result_content_hash) {
        return { preview: checked, revision };
      }
      const receipt = await publishConfig({
        operation_id: createConfigOperationId("draft"),
        source: {
          kind: "manual_parameter_updates",
          draft: command,
          expected_result_content_hash: checked.result_content_hash,
        },
        entry_id: defaultDraftEntryId(command.candidate_id, checked.result_content_hash),
        actor: operatorName,
        expected_generation: command.base_generation,
        note: auditNote,
      });
      return { preview: checked, receipt, revision };
    },
    onSuccess: async (result) => {
      if (result.revision === draftRevision.current) {
        setPreview(result.preview);
      }
      if (result.receipt) await onPublished(result.receipt);
    },
  });
  const saving = defaultMutation.isPending;

  const changeValue = (value: StoredParameterValue) => {
    draftRevision.current += 1;
    setEditedValues((current) => ({ ...current, [value.id]: value }));
    setPreview(undefined);
    previewMutation.reset();
    defaultMutation.reset();
  };
  const setFieldValidity = (field: string, valid: boolean) => {
    draftRevision.current += 1;
    setInvalidFields((current) => {
      const next = new Set(current);
      if (valid) next.delete(field);
      else next.add(field);
      return next;
    });
    setPreview(undefined);
    previewMutation.reset();
    defaultMutation.reset();
  };
  const resetParameter = (parameterId: string) => {
    draftRevision.current += 1;
    setEditedValues((current) => {
      const next = { ...current };
      delete next[parameterId];
      return next;
    });
    setPreview(undefined);
    setInvalidFields(new Set());
    setTableRowOrigins((current) => {
      const next = { ...current };
      delete next[parameterId];
      return next;
    });
    previewMutation.reset();
    defaultMutation.reset();
  };
  const runPreview = () => {
    const command = draftCommand(draft);
    setPreview(undefined);
    defaultMutation.reset();
    previewMutation.mutate({
      command,
      revision: draftRevision.current,
    });
  };
  const runSetDefault = () => {
    defaultMutation.mutate({
      command: draftCommand(draft),
      reviewed: preview,
      revision: draftRevision.current,
      operatorName: operator.trim(),
      auditNote: note.trim(),
    });
  };

  return (
    <section
      className="overflow-hidden rounded-lg border border-line bg-panel"
      aria-labelledby="draft-editor-title"
    >
      <header className="grid grid-cols-[34px_minmax(0,1fr)_auto] items-start gap-[11px] border-b border-line px-[19px] py-[17px]">
        <span
          className="grid size-[34px] place-items-center rounded-[8px] border border-[rgb(128_163_207_/_22%)] bg-accent-soft text-accent"
          aria-hidden="true"
        >
          <Pencil size={18} />
        </span>
        <div>
          <p className={eyebrow}>Transient browser draft</p>
          <h3 className="m-0 text-[0.9rem]" id="draft-editor-title">
            Edit default parameters
          </h3>
          <p className="mt-1 mb-0 text-[0.61rem] leading-[1.45] text-text-dim">
            Based on the current default, {seed.entry.id}. Preview changes when you want to inspect
            the complete candidate before saving it.
          </p>
        </div>
        <button
          className={iconButton}
          type="button"
          onClick={onCancel}
          aria-label="Discard parameter draft"
        >
          <X size={17} />
        </button>
      </header>

      {stale && (
        <div className={classes(configError, "mx-3.5 mt-3")} role="alert">
          <AlertTriangle className="flex-none text-red" size={17} aria-hidden="true" />
          <span className="flex-1">
            The default configuration changed. Discard this stale draft and start again from the new
            default.
          </span>
        </div>
      )}

      <div className="grid min-h-[330px] grid-cols-[minmax(190px,230px)_minmax(0,1fr)] border-b border-line max-[680px]:block">
        <aside
          className="grid max-h-[440px] content-start gap-1 overflow-auto border-r border-line bg-[rgb(255_255_255_/_1%)] px-[9px] py-2.5 max-[680px]:max-h-none max-[680px]:grid-flow-col max-[680px]:auto-cols-[minmax(165px,60vw)] max-[680px]:overflow-x-auto max-[680px]:border-r-0 max-[680px]:border-b"
          aria-label="Editable parameters"
        >
          {definitions.map((definition) => (
            <button
              key={definition.id}
              type="button"
              className={classes(
                "flex min-h-12 min-w-0 cursor-pointer items-center justify-between gap-2 rounded-[7px] border border-transparent bg-transparent p-2 text-left text-text hover:border-line hover:bg-panel-strong",
                selectedDefinition?.id === definition.id &&
                  "border-[rgb(128_163_207_/_22%)] bg-panel-strong",
              )}
              onClick={() => {
                if (definition.id === selectedDefinition?.id) return;
                setSelectedId(definition.id);
                setInvalidFields(new Set());
              }}
            >
              <span className="grid min-w-0 gap-[3px]">
                <strong className="overflow-hidden text-[0.64rem] text-ellipsis whitespace-nowrap">
                  {definition.id}
                </strong>
                <small className="overflow-hidden text-[0.52rem] text-ellipsis whitespace-nowrap text-text-dim">
                  {definition.value_type.shape}
                </small>
              </span>
              {editedValues[definition.id] && (
                <span
                  className="size-[7px] flex-none rounded-full bg-accent shadow-[0_0_8px_rgb(128_163_207_/_45%)]"
                  aria-label="Edited"
                />
              )}
            </button>
          ))}
        </aside>
        <div className="min-w-0 p-4">
          {selectedDefinition && selectedValue ? (
            <>
              <header className="mb-[13px] flex items-start justify-between gap-3 border-b border-line pb-3">
                <div>
                  <span className={parameterShape}>{selectedDefinition.value_type.shape}</span>
                  <h4 className="mt-[7px] mb-0 text-[0.85rem]">{selectedDefinition.id}</h4>
                  <p className="mt-1 mb-0 text-[0.61rem] leading-[1.45] text-text-dim">
                    {selectedDefinition.description ?? "No parameter description."}
                  </p>
                </div>
                {editedValues[selectedDefinition.id] && (
                  <button
                    className={secondaryButton}
                    type="button"
                    onClick={() => resetParameter(selectedDefinition.id)}
                  >
                    Reset
                  </button>
                )}
              </header>
              <ParameterEditor
                key={selectedDefinition.id}
                definition={selectedDefinition}
                value={selectedValue}
                baseValue={baseValues.get(selectedDefinition.id)}
                entities={seed.config.system.topology.entities ?? []}
                onChange={changeValue}
                onFieldValidityChange={setFieldValidity}
                tableRowOrigins={tableRowOrigins[selectedDefinition.id]}
                onTableRowOriginsChange={(origins) =>
                  setTableRowOrigins((current) => ({
                    ...current,
                    [selectedDefinition.id]: origins,
                  }))
                }
              />
            </>
          ) : (
            <p className={draftEmpty}>No parameter is available.</p>
          )}
        </div>
      </div>

      <div className="grid gap-[13px] px-[18px] py-[15px]">
        <label className="grid content-start gap-1.5">
          <span className="text-[0.54rem] font-extrabold tracking-[0.07em] text-text-dim uppercase">
            Audit note
          </span>
          <textarea
            className="min-h-[58px] w-full resize-y rounded-[8px] border border-line bg-bg px-2.5 py-[9px] text-[0.68rem] text-text outline-0 focus:border-[rgb(128_163_207_/_45%)]"
            rows={2}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="Why are these parameters changing?"
          />
        </label>
        <div className="col-span-full flex items-center justify-end gap-2 border-t border-line pt-[11px] max-[680px]:flex-wrap">
          <span className="mr-auto text-[0.59rem] text-text-dim max-[680px]:m-0 max-[680px]:w-full">
            {updates.length === 0
              ? "No changes"
              : `${updates.length} typed operation${updates.length === 1 ? "" : "s"}`}
          </span>
          <button
            className={secondaryButton}
            type="button"
            disabled={
              stale ||
              invalidFields.size > 0 ||
              updates.length === 0 ||
              previewMutation.isPending ||
              saving
            }
            onClick={runPreview}
          >
            {previewMutation.isPending ? (
              <LoaderCircle className="animate-spin" size={15} />
            ) : (
              <Eye size={15} />
            )}
            Preview changes
          </button>
          <button
            className={primaryButton}
            type="button"
            disabled={
              stale ||
              invalidFields.size > 0 ||
              updates.length === 0 ||
              !operator.trim() ||
              previewMutation.isPending ||
              saving
            }
            onClick={runSetDefault}
          >
            {defaultMutation.isPending ? (
              <LoaderCircle className="animate-spin" size={15} />
            ) : (
              <CheckCircle2 size={15} />
            )}
            Set as default
          </button>
        </div>
      </div>

      {(previewMutation.error || defaultMutation.error) && (
        <div className={classes(configError, "mx-3.5 mb-3")} role="alert">
          <AlertTriangle className="flex-none text-red" size={17} aria-hidden="true" />
          <span className="flex-1">
            {draftErrorMessage(defaultMutation.error ?? previewMutation.error)}
          </span>
        </div>
      )}

      {preview && <DraftPreview preview={preview} base={seed.config} />}
    </section>
  );
}

function defaultDraftEntryId(candidateId: string, contentHash: string): string {
  const normalized =
    candidateId
      .trim()
      .replace(/[^A-Za-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "config";
  const digest = contentHash.includes(":")
    ? contentHash.slice(contentHash.indexOf(":") + 1)
    : contentHash;
  return `${normalized}-${digest.slice(0, 12)}`;
}

function DraftPreview({
  preview,
  base,
}: {
  preview: ConfigDraftPreview;
  base: ConfigProfileSnapshot;
}) {
  return (
    <section
      className="mx-[18px] mb-[18px] border-t border-line pt-[15px]"
      aria-labelledby="draft-preview-title"
    >
      <header className="flex items-start gap-[9px]">
        <span
          className={classes(
            "grid size-[29px] flex-none place-items-center rounded-[7px]",
            preview.valid ? "bg-accent-soft text-accent" : "bg-red-soft text-red",
          )}
          aria-hidden="true"
        >
          {preview.valid ? <CheckCircle2 size={17} /> : <AlertTriangle size={17} />}
        </span>
        <div>
          <h4 className="m-0" id="draft-preview-title">
            {preview.valid ? "Candidate is valid" : "Candidate needs changes"}
          </h4>
          <p className="mt-1 mb-0 text-[0.61rem] leading-[1.45] text-text-dim">
            {preview.valid
              ? `${preview.deltas.length} parameter delta${
                  preview.deltas.length === 1 ? "" : "s"
                } passed daemon validation.`
              : "The daemon did not produce a registerable candidate."}
          </p>
        </div>
      </header>
      {preview.problems.length > 0 && (
        <ul className="mt-3 grid list-none gap-1.5 p-0">
          {preview.problems.map((problem, index) => (
            <li
              className="grid gap-[3px] rounded-[7px] border border-[rgb(255_140_136_/_18%)] bg-red-soft px-2.5 py-[9px]"
              key={`${problem.code}-${index}`}
            >
              <strong className="text-[0.62rem] text-[#edb5b2]">{problem.message}</strong>
              <small className="text-[0.5rem] text-text-dim">
                {problem.code}
                {problem.location ? ` · ${problemLocationLabel(problem.location)}` : ""}
              </small>
            </li>
          ))}
        </ul>
      )}
      {preview.valid && preview.config && (
        <ConfigParameters
          config={preview.config}
          activeConfig={base}
          headingId="draft-preview-parameters"
        />
      )}
    </section>
  );
}

const configError =
  "flex min-h-[42px] items-center gap-[9px] rounded-[9px] border border-[rgb(255_140_136_/_25%)] bg-red-soft px-3 text-[0.7rem] text-[#edb5b2]";
const parameterShape =
  "inline-flex rounded-[5px] border border-[rgb(120_184_255_/_18%)] bg-blue-soft px-[5px] py-[3px] text-[0.5rem] font-extrabold text-blue uppercase";
const draftEmpty =
  "rounded-[8px] border border-dashed border-line-strong bg-bg p-3.5 text-[0.64rem] leading-normal text-text-dim";

function draftCommand(draft: PendingConfigDraft): ConfigDraftCommand {
  const [first, ...rest] = draft.updates;
  if (!first) throw new Error("A config draft requires at least one update.");
  return { ...draft, updates: [first, ...rest] };
}

function draftErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "The default configuration changed before the daemon could apply this draft. Discard it and start again.";
  }
  return error instanceof Error ? error.message : "The request failed.";
}
