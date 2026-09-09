import { ChevronDown, GitCompareArrows, History, LoaderCircle, RotateCcw } from "lucide-react";
import type {
  ConfigActivationRecord,
  ConfigRegistryEntry,
  ConfigRegistryOverview,
} from "../../api-contract";
import { errorMessage, formatDateTime, shorten } from "../../lib/presentation";
import { classes, secondaryButton } from "../../ui/styles";
import { ConfigInlineEmpty } from "./ConfigUi";

export function ConfigSummary({
  overview,
  activeEntry,
  undoEntryId,
  undoDisabled,
  undoPending,
  onUndo,
}: {
  overview: ConfigRegistryOverview;
  activeEntry?: ConfigRegistryEntry;
  undoEntryId?: string;
  undoDisabled: boolean;
  undoPending: boolean;
  onUndo: () => void;
}) {
  const active = overview.activation;
  const history = overview.activation_history;
  const defaultEntry =
    activeEntry ?? overview.entries.find((entry) => entry.id === active?.entry_id);
  // Browser state cannot prove whether runtime changes were copied back to Git.
  const runtimeDerived =
    defaultEntry?.source.kind === "manual_parameter_updates" ||
    defaultEntry?.source.kind === "candidate_config" ||
    defaultEntry?.source.kind === "calibration_cohort_merge";
  return (
    <div
      className="grid grid-cols-[minmax(260px,1.5fr)_minmax(230px,1fr)_minmax(280px,1.2fr)] items-stretch overflow-hidden rounded-lg border border-line bg-panel max-[1100px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] max-[460px]:grid-cols-[minmax(0,1fr)]"
      aria-label="Configuration summary"
    >
      <div className="grid min-h-[52px] min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-[9px] border-r border-line px-3 py-2 max-[460px]:border-r-0 max-[460px]:border-b">
        <span className={summaryLabel}>Default</span>
        <strong
          className="overflow-hidden text-[0.72rem] text-ellipsis whitespace-nowrap"
          data-testid="active-config-entry"
          title={active?.entry_id}
        >
          {active?.entry_id ?? "Not configured"}
        </strong>
        <code
          className="overflow-hidden text-[0.59rem] text-ellipsis whitespace-nowrap text-text-dim"
          title={active?.entry_content_hash}
        >
          {active ? shorten(active.entry_content_hash, 16) : "No content hash"}
        </code>
      </div>
      <dl className="m-0 grid min-w-0 grid-cols-2 items-center border-r border-line px-3 py-[7px] max-[1100px]:border-r-0 max-[460px]:border-b">
        <div className="grid min-w-0 grid-cols-[1fr_auto] items-baseline gap-2 border-r border-line px-[9px]">
          <dt className={summaryLabel}>Saved versions</dt>
          <dd className="m-0 text-[0.75rem] font-bold text-text-soft">
            {overview.entries.length}
            {overview.entries_next_cursor !== undefined ? "+" : ""}
          </dd>
        </div>
        <div className="grid min-w-0 grid-cols-[1fr_auto] items-baseline gap-2 px-[9px]">
          <dt className={summaryLabel}>Default changes</dt>
          <dd className="m-0 text-[0.75rem] font-bold text-text-soft">
            {history.length}
            {overview.activation_history_next_cursor !== undefined ? "+" : ""}
          </dd>
        </div>
      </dl>
      <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-[9px] py-[7px] pr-[9px] pl-3 max-[1100px]:col-span-full max-[1100px]:border-t max-[1100px]:border-line max-[460px]:col-auto max-[460px]:border-t-0">
        <span className={summaryLabel}>Previous</span>
        <strong
          className="overflow-hidden text-[0.66rem] text-ellipsis whitespace-nowrap text-text-soft"
          title={undoEntryId}
        >
          {undoEntryId ?? "Nothing to undo"}
        </strong>
        <button
          className={classes(secondaryButton, "min-h-[31px]")}
          type="button"
          disabled={undoDisabled}
          onClick={onUndo}
        >
          {undoPending ? (
            <LoaderCircle className="animate-spin" size={15} />
          ) : (
            <RotateCcw size={15} />
          )}
          Undo
        </button>
      </div>
      {runtimeDerived && (
        <aside
          className="col-span-full flex items-start gap-2.5 border-t border-[rgb(120_184_255_/_22%)] bg-blue-soft px-[13px] py-[11px] text-blue"
          role="note"
        >
          <GitCompareArrows className="mt-px flex-none" size={17} aria-hidden="true" />
          <div>
            <strong className="block text-[0.68rem] text-text-soft">Runtime-derived default</strong>
            <p className="mt-[3px] mb-0 text-[0.62rem] leading-normal text-text-dim">
              This console cannot tell whether the project&apos;s Git/Python configuration source is
              synchronized. Run <code className="text-blue">scopecat config diff .</code> to check.
            </p>
          </div>
        </aside>
      )}
    </div>
  );
}

export function ActivationHistory({
  history,
  hasOlder,
  loadingOlder,
  olderError,
  onLoadOlder,
}: {
  history: ConfigActivationRecord[];
  hasOlder: boolean;
  loadingOlder: boolean;
  olderError: Error | null;
  onLoadOlder: () => void;
}) {
  return (
    <section
      className="rounded-lg border border-line bg-panel px-5 py-[18px]"
      aria-labelledby="history-heading"
    >
      <header className="mb-3.5 grid grid-cols-[30px_minmax(0,1fr)_auto] items-center gap-[9px]">
        <span
          className="grid size-[30px] place-items-center rounded-[8px] border border-line bg-panel-soft text-accent"
          aria-hidden="true"
        >
          <History size={17} />
        </span>
        <div>
          <h3 className="m-0 text-[0.76rem]" id="history-heading">
            Default history
          </h3>
          <p className="mt-[3px] mb-0 text-[0.59rem] text-text-dim">
            Every default change is retained and can be revisited.
          </p>
        </div>
        <span className="rounded-md border border-line px-[7px] py-1 text-[0.61rem] text-text-dim">
          {history.length}
          {hasOlder ? "+" : ""}
        </span>
      </header>
      {history.length === 0 ? (
        <ConfigInlineEmpty
          title="No default history"
          detail="The first default configuration will appear here."
        />
      ) : (
        <ol className="m-0 grid max-h-80 list-none gap-[5px] overflow-auto p-0 [scrollbar-color:#344252_transparent] [scrollbar-width:thin]">
          {history.map((record, index) => (
            <li
              className="grid min-h-[55px] grid-cols-[52px_18px_minmax(0,1fr)_auto] items-center gap-2 rounded-[8px] border border-line bg-panel-soft px-2.5 py-2 max-[680px]:grid-cols-[44px_12px_minmax(0,1fr)]"
              key={record.generation}
            >
              <span className="grid gap-0.5">
                <strong className="text-[0.68rem] text-accent">G{record.generation}</strong>
                <small className="text-[0.51rem] text-text-dim capitalize">
                  {index === 0 ? "Current" : activationActionLabel(record.action)}
                </small>
              </span>
              <span
                className="relative h-px w-[18px] bg-line-strong after:absolute after:top-[-3px] after:right-0 after:size-[7px] after:rounded-full after:bg-line-strong after:content-['']"
                aria-hidden="true"
              />
              <div className="grid min-w-0 gap-0.5">
                <strong className="overflow-hidden text-[0.66rem] text-ellipsis whitespace-nowrap">
                  {record.entry_id}
                </strong>
                <small className="text-[0.55rem] text-text-dim">
                  {record.actor || "Unknown actor"}
                  {record.recorded_at ? ` · ${formatDateTime(record.recorded_at)}` : ""}
                </small>
                {record.restored_from_generation != null && (
                  <small className="text-[0.55rem] text-text-dim">
                    Restored from G{record.restored_from_generation} · calibration validity not
                    renewed
                  </small>
                )}
                {record.note && (
                  <p className="mt-[3px] mb-0 text-[0.58rem] text-text-soft">{record.note}</p>
                )}
              </div>
              <code className="text-[0.55rem] text-text-dim max-[680px]:hidden">
                {shorten(record.entry_content_hash, 18)}
              </code>
            </li>
          ))}
          {olderError && (
            <li className="px-2 py-1 text-[0.61rem] text-red" role="status">
              {errorMessage(olderError)}
            </li>
          )}
          {hasOlder && (
            <li>
              <button
                className={classes(secondaryButton, "w-full")}
                disabled={loadingOlder}
                onClick={onLoadOlder}
                type="button"
              >
                {loadingOlder ? (
                  <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
                ) : (
                  <ChevronDown size={14} aria-hidden="true" />
                )}
                {loadingOlder ? "Loading older changes…" : "Load older changes"}
              </button>
            </li>
          )}
        </ol>
      )}
    </section>
  );
}

const summaryLabel = "text-[0.58rem] font-extrabold tracking-[0.08em] text-text-dim uppercase";

function activationActionLabel(action: ConfigActivationRecord["action"]) {
  switch (action) {
    case "activation":
      return "Activated";
    case "inventory_migration":
      return "Inventory migration";
    default:
      return action;
  }
}
