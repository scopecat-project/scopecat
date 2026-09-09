import { useRef, useState } from "react";
import { Menu } from "@base-ui/react/menu";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CircleDot,
  Database,
  FileUp,
  LoaderCircle,
  RefreshCw,
  SlidersHorizontal,
  UserRound,
  X,
  XCircle,
} from "lucide-react";
import { errorMessage } from "../../lib/presentation";
import { classes, secondaryButton } from "../../ui/styles";
import { ConfigStructureEditor } from "./ConfigStructureEditor";
import { ConfigContextEditor } from "./ConfigContextEditor";
import {
  getConfigRegistryEntry,
  resolveConfigContext,
  type ConfigContextRef,
  type ConfigContextResolution,
} from "./config-api";
import { ConfigDraftEditor, type ConfigDraftSeed } from "./ConfigDraftEditor";
import { ConfigEntryInspector } from "./ConfigEntryInspector";
import { ConfigImportDialog } from "./ConfigImportDialog";
import { ActivationHistory, ConfigSummary } from "./ConfigOverview";
import { ConfigRegistryPanel } from "./ConfigRegistryPanel";
import { ConfigBoundaryMessage } from "./ConfigUi";
import { useConfigMutationWorkflow } from "./useConfigMutationWorkflow";
import { configUndoTarget } from "./config-utils";
import { useConfigRegistry } from "./useConfigRegistry";

export function ConfigWorkspace({
  daemonUnavailable,
  onOpenRun,
  onSelectContext,
}: {
  daemonUnavailable: boolean;
  onOpenRun?: (runId: string) => void;
  onSelectContext?: (context: ConfigContextResolution) => void;
}) {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [configDraft, setConfigDraft] = useState<ConfigDraftSeed>();
  const [editingContext, setEditingContext] = useState(false);
  const [editingStructure, setEditingStructure] = useState(false);
  const [comparisonId, setComparisonId] = useState("");
  const comparison = useQuery({
    queryKey: ["config", "comparison", comparisonId],
    queryFn: ({ signal }) => getConfigRegistryEntry(comparisonId, signal),
    enabled: !!comparisonId,
  });
  const contextSelection = useMutation({
    mutationFn: (ref: ConfigContextRef) => resolveConfigContext(ref),
    onSuccess: (resolution) => onSelectContext?.(resolution),
  });
  const registry = useConfigRegistry(daemonUnavailable);
  const workflow = useConfigMutationWorkflow(registry.overview);
  const undoTarget = registry.overview ? configUndoTarget(registry.overview) : undefined;

  const selectEntry = (entryId: string) => {
    registry.selectEntry(entryId);
    contextSelection.reset();
    workflow.mutation.reset();
  };

  if (daemonUnavailable) {
    return (
      <ConfigBoundaryMessage
        icon={<Database />}
        title="Connect to the local daemon"
        detail="Configuration is owned by the daemon. This console does not keep an editable browser copy."
      />
    );
  }
  if (registry.registryQuery.isPending) {
    return (
      <ConfigBoundaryMessage
        icon={<LoaderCircle className="animate-spin" />}
        title="Reading saved configurations"
        detail="Loading the default snapshot, saved versions, and change history."
      />
    );
  }
  if (registry.registryQuery.isError) {
    return (
      <ConfigBoundaryMessage
        icon={<XCircle />}
        title="Configuration registry unavailable"
        detail={errorMessage(registry.registryQuery.error)}
        warning
        action={
          <button
            className={secondaryButton}
            type="button"
            onClick={() => void registry.registryQuery.refetch()}
          >
            <RefreshCw size={15} aria-hidden="true" />
            Retry
          </button>
        }
      />
    );
  }
  const overview = registry.overview;
  if (!overview) {
    return (
      <ConfigBoundaryMessage
        icon={<XCircle />}
        title="Configuration registry unavailable"
        detail="The daemon returned no registry projection."
        warning
      />
    );
  }

  const selectedEntry = registry.selectedEntry;
  const commandDisabled = workflow.commandDisabled;
  const latestActivation = registry.entryDetailQuery.data?.latestActivation;
  const restoring = latestActivation != null;
  const accepting = selectedEntry?.source.kind !== "direct_config_profile";
  const editableDraftSeed =
    selectedEntry &&
    overview.activation &&
    registry.entryDetailQuery.data?.config &&
    overview.activation.entry_id === selectedEntry.id
      ? {
          entry: selectedEntry,
          active: overview.activation,
          config: registry.entryDetailQuery.data.config,
        }
      : undefined;
  return (
    <section className="grid gap-3.5" aria-labelledby="config-heading">
      <header className="flex min-h-[50px] items-center justify-between gap-5 rounded-lg border border-line bg-panel py-1.5 pr-2.5 pl-3.5 max-[880px]:items-start max-[680px]:grid max-[680px]:gap-[17px]">
        <div>
          <h2 className="m-0 text-base font-[650] tracking-[-0.025em]" id="config-heading">
            Default configuration
          </h2>
        </div>
        <div className="flex items-center gap-2 max-[680px]:flex-wrap">
          <label className="flex min-h-9 items-center gap-2 rounded-[8px] border border-line bg-bg px-2.5 text-text-dim focus-within:border-[rgb(128_163_207_/_45%)] max-[680px]:flex-1">
            <UserRound size={15} aria-hidden="true" />
            <span className="sr-only">Operator name</span>
            <input
              className="w-[145px] min-w-0 border-0 bg-transparent p-0 text-[0.72rem] text-text outline-0 max-[680px]:w-full"
              value={workflow.operator}
              onChange={(event) => workflow.setOperator(event.target.value)}
              placeholder="Operator"
              autoComplete="name"
            />
          </label>
          <input
            ref={fileInput}
            className="sr-only"
            type="file"
            accept=".json,application/json"
            onChange={(event) => void workflow.readImport(event)}
          />
          <Menu.Root>
            <Menu.Trigger
              className={classes(
                secondaryButton,
                "data-[popup-open]:bg-panel-strong data-[popup-open]:text-text",
              )}
            >
              <SlidersHorizontal size={15} aria-hidden="true" />
              Advanced
            </Menu.Trigger>
            <Menu.Portal>
              <Menu.Positioner className="z-60 outline-0" sideOffset={6} align="end">
                <Menu.Popup className="grid w-[270px] rounded-md border border-line-strong bg-panel-strong p-1.5 shadow-panel outline-0">
                  <Menu.Item
                    className="grid w-full cursor-pointer grid-cols-[18px_minmax(0,1fr)] items-start gap-[9px] rounded-sm p-[9px] text-[0.67rem] text-text-soft outline-0 data-[disabled]:cursor-not-allowed data-[disabled]:opacity-45 data-[highlighted]:bg-accent-soft data-[highlighted]:text-text"
                    onClick={() => fileInput.current?.click()}
                  >
                    <FileUp size={15} aria-hidden="true" />
                    <span className="grid gap-[3px]">
                      <strong className="text-[0.68rem]">Import raw snapshot</strong>
                      <small className="text-[0.59rem] leading-[1.45] text-text-dim">
                        Bypass the typed parameter editor.
                      </small>
                    </span>
                  </Menu.Item>
                </Menu.Popup>
              </Menu.Positioner>
            </Menu.Portal>
          </Menu.Root>
        </div>
      </header>

      {(workflow.importError || workflow.mutation.error) && (
        <div
          className="flex min-h-[42px] items-center gap-[9px] rounded-[9px] border border-[rgb(255_140_136_/_25%)] bg-red-soft px-3 text-[0.7rem] text-[#edb5b2]"
          role="status"
        >
          <AlertTriangle className="flex-none text-red" size={17} aria-hidden="true" />
          <span className="flex-1">
            {workflow.importError ?? errorMessage(workflow.mutation.error)}
          </span>
          <button
            className="grid size-[27px] cursor-pointer place-items-center rounded-md border-0 bg-transparent p-0 text-text-dim"
            type="button"
            aria-label="Dismiss error"
            onClick={workflow.dismissError}
          >
            <X size={15} />
          </button>
        </div>
      )}

      <ConfigSummary
        overview={overview}
        activeEntry={registry.activeDetailQuery.data?.entry}
        undoEntryId={undoTarget?.entryId}
        undoDisabled={commandDisabled || undoTarget === undefined}
        undoPending={workflow.mutation.isPending && workflow.mutation.variables?.kind === "undo"}
        onUndo={() => {
          if (undoTarget === undefined) return;
          workflow.runAction(
            {
              kind: "undo",
              entryId: undoTarget.entryId,
              expectedGeneration: undoTarget.expectedGeneration,
            },
            `Restore ${undoTarget.entryId} as the default configuration? Calibration validity is not renewed and no devices are run.`,
          );
        }}
      />

      <div className="grid min-h-[630px] grid-cols-[minmax(290px,340px)_minmax(0,1fr)] overflow-hidden rounded-lg border border-line bg-panel max-[1100px]:grid-cols-[minmax(260px,300px)_minmax(0,1fr)] max-[880px]:block max-[880px]:min-h-0 max-[880px]:overflow-visible max-[880px]:border-0 max-[880px]:bg-transparent">
        <ConfigRegistryPanel
          overview={overview}
          entries={registry.filteredEntries}
          selectedId={registry.selectedId}
          search={registry.registrySearch}
          refreshing={registry.registryQuery.isFetching}
          hasOlder={overview.entries_next_cursor !== undefined}
          loadingOlder={registry.olderEntriesMutation.isPending}
          olderError={registry.olderEntriesMutation.error}
          onSearchChange={registry.setRegistrySearch}
          onSelectEntry={selectEntry}
          onLoadOlder={registry.loadOlderEntries}
        />

        <section
          className="min-w-0 bg-panel p-[clamp(18px,2vw,26px)] max-[880px]:rounded-lg max-[880px]:border max-[880px]:border-line max-[680px]:min-h-[520px] max-[680px]:px-3.5 max-[680px]:py-5"
          aria-live="polite"
        >
          {selectedEntry ? (
            <>
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <button
                  className={secondaryButton}
                  disabled={!registry.entryDetailQuery.data}
                  onClick={() => setEditingContext(true)}
                >
                  Save working point copy
                </button>
                {selectedEntry.source.kind === "parameter_context" && (
                  <button
                    className={secondaryButton}
                    disabled={!registry.entryDetailQuery.data?.structureVersion}
                    onClick={() => setEditingStructure(true)}
                  >
                    Change table structure
                  </button>
                )}
                {selectedEntry.source.kind === "parameter_context" && (
                  <button
                    className={secondaryButton}
                    disabled={contextSelection.isPending}
                    onClick={() =>
                      contextSelection.mutate({
                        entry_id: selectedEntry.id,
                        content_hash: selectedEntry.content_hash,
                      })
                    }
                  >
                    Use for next experiment
                  </button>
                )}
                <label>
                  Compare with
                  <select
                    aria-label="Compare configuration with"
                    value={comparisonId}
                    onChange={(event) => setComparisonId(event.target.value)}
                  >
                    <option value="">Lab default</option>
                    {overview.entries.map((entry) => (
                      <option key={entry.id} value={entry.id}>
                        {entry.source.kind === "parameter_context"
                          ? entry.source.context.label
                          : entry.id}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {comparison.error && <p role="alert">{errorMessage(comparison.error)}</p>}
              {contextSelection.error && <p role="alert">{errorMessage(contextSelection.error)}</p>}
              {contextSelection.data && (
                <p>
                  Selected {contextSelection.data.config_source.sample.sample_id} /{" "}
                  {contextSelection.data.config_source.sample.context_id}. Unknown:{" "}
                  {contextSelection.data.missing_values?.join(", ") || "none"}. Lab default
                  unchanged.
                </p>
              )}
              <ConfigEntryInspector
                entry={selectedEntry}
                active={overview.activation?.entry_id === selectedEntry.id}
                latestActivation={latestActivation?.generation}
                snapshot={registry.entryDetailQuery.data?.summary}
                config={registry.entryDetailQuery.data?.config}
                activeConfig={
                  comparisonId ? comparison.data?.config : registry.activeDetailQuery.data?.config
                }
                snapshotPending={registry.entryDetailQuery.isPending}
                snapshotError={registry.entryDetailQuery.error}
                note={workflow.note}
                pending={
                  workflow.mutation.isPending &&
                  workflow.mutation.variables?.kind === "activate-entry"
                }
                actionDisabled={commandDisabled || !registry.entryDetailQuery.isSuccess}
                onNoteChange={workflow.setNote}
                onSelectEntry={selectEntry}
                onOpenRun={onOpenRun}
                onActivate={() =>
                  workflow.runAction(
                    {
                      kind: "activate-entry",
                      entryId: selectedEntry.id,
                      expectedGeneration: overview.activation?.generation ?? 0,
                    },
                    restoring
                      ? `Restore ${selectedEntry.id} as the default configuration? This selects the exact saved parameters from G${latestActivation.generation}; calibration validity is not renewed.`
                      : `${accepting ? "Accept" : "Set"} ${selectedEntry.id} as the default configuration?`,
                    restoring
                      ? "Restore default"
                      : accepting
                        ? "Accept as default"
                        : "Set as default",
                  )
                }
                onEdit={editableDraftSeed ? () => setConfigDraft(editableDraftSeed) : undefined}
              />
              {editingStructure && registry.entryDetailQuery.data && (
                <ConfigStructureEditor
                  key={selectedEntry.id}
                  detail={registry.entryDetailQuery.data}
                  operator={workflow.operator}
                  onCancel={() => setEditingStructure(false)}
                  onSaved={(entryId) => {
                    setEditingStructure(false);
                    void queryClient.invalidateQueries({ queryKey: ["config"] });
                    registry.selectEntry(entryId);
                  }}
                />
              )}
              {editingContext && registry.entryDetailQuery.data && (
                <ConfigContextEditor
                  key={selectedEntry.id}
                  entry={selectedEntry}
                  config={registry.entryDetailQuery.data.config}
                  operator={workflow.operator}
                  onCancel={() => setEditingContext(false)}
                  onSaved={(entryId) => {
                    setEditingContext(false);
                    void queryClient.invalidateQueries({ queryKey: ["config"] });
                    registry.selectEntry(entryId);
                  }}
                />
              )}
            </>
          ) : (
            <ConfigBoundaryMessage
              icon={<CircleDot />}
              title="Nothing selected"
              detail="Choose a saved version to inspect its immutable snapshot."
              compact
            />
          )}
        </section>
      </div>

      {configDraft && (
        <ConfigDraftEditor
          seed={configDraft}
          currentActive={overview.activation ?? undefined}
          operator={workflow.operator}
          onCancel={() => setConfigDraft(undefined)}
          onPublished={async (receipt) => {
            setConfigDraft(undefined);
            await Promise.all([
              queryClient.invalidateQueries({ queryKey: ["config"] }),
              queryClient.invalidateQueries({ queryKey: ["events"] }),
            ]);
            registry.selectEntry(receipt.entry.id);
          }}
        />
      )}

      <ActivationHistory
        history={overview.activation_history}
        hasOlder={overview.activation_history_next_cursor !== undefined}
        loadingOlder={registry.olderActivationsMutation.isPending}
        olderError={registry.olderActivationsMutation.error}
        onLoadOlder={registry.loadOlderActivations}
      />

      {workflow.importDraft && (
        <ConfigImportDialog
          draft={workflow.importDraft}
          note={workflow.note}
          pending={workflow.mutation.isPending && workflow.mutation.variables?.kind === "import"}
          disabled={workflow.mutation.isPending || !workflow.operator.trim()}
          onChange={workflow.setImportDraft}
          onNoteChange={workflow.setNote}
          onCancel={() => workflow.setImportDraft(undefined)}
          onSubmit={() =>
            workflow.mutation.mutate({
              kind: "import",
              draft: workflow.importDraft!,
            })
          }
        />
      )}
      {workflow.confirmationDialog}
    </section>
  );
}
