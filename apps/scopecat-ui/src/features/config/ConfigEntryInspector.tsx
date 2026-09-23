import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, GitCompareArrows, LoaderCircle, SlidersHorizontal } from "lucide-react";
import { getRunAnalysis } from "../runs/run-api";
import type { ConfigProfileSnapshot, ConfigRegistryEntry } from "../../api-contract";
import { getRunParameterProposals } from "../../data/parameter-proposals/api";
import { parameterProposalKeys } from "../../data/parameter-proposals/query-keys";
import type { ParameterProposal } from "../../data/parameter-proposals/types";
import { errorMessage, formatDateTime, shorten } from "../../lib/presentation";
import { classes, primaryButton, secondaryButton } from "../../ui/styles";
import { ConfigParameters } from "./ConfigParameters";
import { ActionNote, ConfigFact, ConfigInlineEmpty } from "./ConfigUi";
import type { ConfigSnapshotSummary } from "./config-api";
import { type ConfigProvenanceSource, configSourceLabel } from "./config-utils";

export function ConfigEntryInspector({
  entry,
  active,
  latestActivation,
  snapshot,
  config,
  activeConfig,
  snapshotPending,
  snapshotError,
  note,
  pending,
  actionDisabled,
  onNoteChange,
  onSelectEntry,
  onOpenRun,
  onActivate,
  onEdit,
}: {
  entry: ConfigRegistryEntry;
  active: boolean;
  latestActivation?: number;
  snapshot?: ConfigSnapshotSummary;
  config?: ConfigProfileSnapshot;
  activeConfig?: ConfigProfileSnapshot;
  snapshotPending: boolean;
  snapshotError: Error | null;
  note: string;
  pending: boolean;
  actionDisabled: boolean;
  onNoteChange: (note: string) => void;
  onSelectEntry: (entryId: string) => void;
  onOpenRun?: (runId: string) => void;
  onActivate: () => void;
  onEdit?: () => void;
}) {
  const publication =
    entry.source.kind === "parameter_context" ? entry.source.publication : entry.source;
  const candidateSource = publication?.kind === "candidate_config" ? publication : undefined;
  const candidateRunId = candidateSource?.run_id;
  const candidateProposalsQuery = useQuery({
    queryKey: parameterProposalKeys.firstPage(candidateRunId!),
    queryFn: ({ signal }) => getRunParameterProposals(candidateRunId!, signal),
    enabled: candidateRunId !== undefined,
  });
  const candidateProposal = candidateProposalsQuery.data?.items.find(
    (proposal) => proposal.id === candidateSource?.proposal_id,
  );
  const candidateAnalysisId = candidateProposal?.analysisRecordId;
  const candidateAnalysisQuery = useQuery({
    queryKey: ["analysis", candidateRunId, candidateAnalysisId],
    queryFn: ({ signal }) => getRunAnalysis(candidateRunId!, candidateAnalysisId!, signal),
    enabled: candidateRunId !== undefined && candidateAnalysisId !== undefined,
  });

  return (
    <>
      <header className="flex min-h-[76px] items-start justify-between gap-5 border-b border-line pb-[15px] max-[680px]:grid">
        <div className="min-w-0">
          <span
            className={classes(
              "inline-flex rounded-md border border-line bg-panel-soft px-[7px] py-1 text-[0.55rem] font-extrabold tracking-[0.06em] text-text-dim uppercase",
              active && "border-[rgb(128_163_207_/_20%)] bg-accent-soft text-accent",
            )}
          >
            {active ? "Default" : "Saved"}
          </span>
          <h3
            className="mt-2 mb-[5px] text-[1.05rem] font-[650] tracking-[-0.03em] [overflow-wrap:anywhere]"
            title={entry.id}
          >
            {shorten(entry.id, 44)}
          </h3>
          <code
            className="block max-w-[min(58vw,680px)] overflow-hidden text-[0.61rem] text-ellipsis whitespace-nowrap text-text-dim max-[680px]:max-w-full"
            title={entry.content_hash}
          >
            {entry.content_hash}
          </code>
        </div>
        {active && onEdit && (
          <button
            className={classes(primaryButton, "max-[680px]:justify-self-start")}
            type="button"
            onClick={onEdit}
          >
            <SlidersHorizontal size={15} />
            Edit parameters
          </button>
        )}
        {!active && entry.source.kind !== "parameter_context" && (
          <button
            className={classes(primaryButton, "max-[680px]:justify-self-start")}
            type="button"
            disabled={actionDisabled}
            onClick={onActivate}
          >
            {pending ? (
              <LoaderCircle className="animate-spin" size={15} />
            ) : (
              <CheckCircle2 size={15} />
            )}
            {latestActivation !== undefined
              ? "Restore default"
              : entry.source.kind === "direct_config_profile" ||
                  entry.source.kind === "parameter_revision" ||
                  entry.source.kind === "bound_parameters"
                ? "Set as default"
                : "Accept as default"}
          </button>
        )}
      </header>
      <div className="my-[17px] grid grid-cols-4 gap-[9px] max-[1100px]:grid-cols-2 max-[460px]:grid-cols-1">
        <ConfigFact label="Source" value={configSourceLabel(entry)} />
        <ConfigFact label="Saved by" value={entry.actor || "Not reported"} />
        <ConfigFact
          label="Saved"
          value={entry.recorded_at ? formatDateTime(entry.recorded_at) : "Not reported"}
        />
        <ConfigFact label="Config ref" value={entry.config_ref || "Not reported"} code />
      </div>
      {latestActivation !== undefined && (
        <p className="mb-4 text-[0.68rem] text-text-dim">
          Previously selected at G{latestActivation}. Restoring selects the exact saved parameters;
          it does not renew calibration validity or run devices.
        </p>
      )}
      <EntryProvenance
        source={entry.source}
        onSelectEntry={onSelectEntry}
        onOpenRun={onOpenRun}
        candidateProposals={candidateProposalsQuery.data?.items}
        candidateProposalsPending={candidateProposalsQuery.isPending}
        candidateProposalsError={candidateProposalsQuery.error}
        candidateAnalysis={candidateAnalysisQuery.data}
        candidateAnalysisPending={candidateAnalysisQuery.isPending}
        candidateAnalysisError={candidateAnalysisQuery.error}
      />
      {snapshot ? (
        <>
          <SnapshotSummary snapshot={snapshot} />
          {config && <ConfigParameters config={config} activeConfig={activeConfig} />}
        </>
      ) : snapshotPending ? (
        <ConfigInlineEmpty
          title="Reading snapshot"
          detail="Loading this registry entry's immutable config snapshot."
        />
      ) : snapshotError ? (
        <ConfigInlineEmpty title="Snapshot unavailable" detail={errorMessage(snapshotError)} />
      ) : (
        <ConfigInlineEmpty
          title="Snapshot summary not included"
          detail="The registry projection can attach entry snapshots when detailed comparison is needed."
        />
      )}
      {entry.note && (
        <div className="mt-3.5 rounded-[8px] border border-line bg-[rgb(255_255_255_/_1%)] p-[11px]">
          <span className="text-[0.55rem] font-extrabold tracking-[0.07em] text-text-dim uppercase">
            Save note
          </span>
          <p className="mt-[5px] mb-0 text-[0.65rem] leading-normal text-text-soft">{entry.note}</p>
        </div>
      )}
      {!active && <ActionNote value={note} onChange={onNoteChange} />}
    </>
  );
}

function EntryProvenance({
  source,
  onSelectEntry,
  onOpenRun,
  candidateProposals,
  candidateProposalsPending,
  candidateProposalsError,
  candidateAnalysis,
  candidateAnalysisPending,
  candidateAnalysisError,
}: {
  source: ConfigProvenanceSource;
  onSelectEntry: (entryId: string) => void;
  onOpenRun?: (runId: string) => void;
  candidateProposals?: ParameterProposal[];
  candidateProposalsPending: boolean;
  candidateProposalsError: Error | null;
  candidateAnalysis?: { id: string; title: string };
  candidateAnalysisPending: boolean;
  candidateAnalysisError: Error | null;
}) {
  if (source.kind === "parameter_context") {
    return (
      <div className={provenance}>
        <div>
          <strong>{source.context.label}</strong>
          <p className={provenanceCopy}>
            Sample {source.context.sample.sample_id} · revision {source.context.sample.revision} ·
            working point {source.context.working_point_id}.
            {source.publication
              ? " Accepted calibration evidence is recorded below."
              : " Saved trial parameters; calibration validity is not implied."}
          </p>
          <button
            className={secondaryButton}
            onClick={() => onSelectEntry(source.context.base.entry_id)}
          >
            Open source {source.context.base.entry_id}
          </button>
          <details>
            <summary>Detailed cell origins ({source.context.value_origins?.length ?? 0})</summary>
            <dl className="max-h-80 overflow-auto">
              {(source.context.value_origins ?? []).map((origin, index) => (
                <div key={index}>
                  <dt>
                    {origin.parameter_id}
                    {origin.row_index != null
                      ? `[${origin.row_index}]`
                      : Object.keys(origin.key ?? {}).length
                        ? ` ${JSON.stringify(origin.key)}`
                        : ""}
                    {origin.field_id ? `.${origin.field_id}` : ""}
                  </dt>
                  <dd>
                    {origin.layer} · {origin.entry.entry_id}
                    {origin.evidence && (
                      <p>
                        {origin.evidence.origin} · {origin.evidence.note}
                      </p>
                    )}
                    {origin.source_cell && (
                      <p>
                        Source: {origin.source_cell.entry.entry_id} /{" "}
                        {origin.source_cell.parameter_id}
                        {origin.source_cell.row_index != null
                          ? `[${origin.source_cell.row_index}]`
                          : ` ${JSON.stringify(origin.source_cell.key)}`}
                        {origin.source_cell.field_id ? `.${origin.source_cell.field_id}` : ""}
                      </p>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
          {source.rebind && (
            <p>
              Explicitly rebound to setup {source.rebind.setup.revision_id}; calibration acceptance
              is not carried over.
            </p>
          )}
          {source.publication && (
            <EntryProvenance
              source={source.publication}
              onSelectEntry={onSelectEntry}
              onOpenRun={onOpenRun}
              candidateProposals={candidateProposals}
              candidateProposalsPending={candidateProposalsPending}
              candidateProposalsError={candidateProposalsError}
              candidateAnalysis={candidateAnalysis}
              candidateAnalysisPending={candidateAnalysisPending}
              candidateAnalysisError={candidateAnalysisError}
            />
          )}
        </div>
      </div>
    );
  }
  if (source.kind === "setup_rebind") {
    return (
      <div className={provenance}>
        <p>
          Explicitly rebound to setup {source.setup.revision_id}; original parameters retained
          without carrying calibration acceptance.
        </p>
        <button className={secondaryButton} onClick={() => onSelectEntry(source.base.entry_id)}>
          Open source {source.base.entry_id}
        </button>
      </div>
    );
  }
  if (source.kind === "direct_config_profile") {
    return (
      <div className={provenance}>
        <GitCompareArrows className={provenanceIcon} size={16} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <strong className="text-[0.68rem]">Direct configuration profile</strong>
          <p className={provenanceCopy}>Saved from one complete config snapshot.</p>
        </div>
      </div>
    );
  }
  if (source.kind === "parameter_revision") {
    return (
      <div className={provenance}>
        <p>
          Parameters published against saved setup {source.setup.revision_id}. Setup selection was
          unchanged.
        </p>
      </div>
    );
  }
  if (source.kind === "bound_parameters") {
    return (
      <div className={provenance}>
        <p>
          Parameter revision {source.parameters.revision_id} combined with setup{" "}
          {source.setup.revision_id}. Preparing these inputs does not select a default or establish
          calibration validity.
        </p>
      </div>
    );
  }
  if (source.kind === "manual_parameter_updates") {
    return (
      <div className={provenance}>
        <GitCompareArrows className={provenanceIcon} size={16} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <strong className="text-[0.68rem]">Manual parameter update</strong>
          <p className={provenanceCopy}>
            Derived from{" "}
            {source.base_entry_id ? (
              <button
                className="cursor-pointer border-0 bg-transparent p-0 text-purple hover:[&_code]:underline"
                type="button"
                aria-label={`Open base version ${source.base_entry_id}`}
                onClick={() => onSelectEntry(source.base_entry_id)}
              >
                <code>{source.base_entry_id}</code>
              </button>
            ) : (
              "an unreported base version"
            )}
            {source.base_registry_generation
              ? ` at registry generation ${source.base_registry_generation}.`
              : "."}
          </p>
        </div>
      </div>
    );
  }
  const runId = source.run_id;
  const proposalId = source.proposal_id;
  const proposalsById = new Map(
    (candidateProposals ?? []).map((proposal) => [proposal.id, proposal]),
  );
  const proposal = proposalsById.get(proposalId);
  const analysis =
    proposal && candidateAnalysis?.id === proposal.analysisRecordId ? candidateAnalysis : undefined;
  const approval = proposal?.approval;
  return (
    <div className={provenance}>
      <GitCompareArrows className={provenanceIcon} size={16} aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-3">
          <div>
            <strong className="text-[0.68rem]">Analysis candidate</strong>
            <p className={provenanceCopy}>
              Produced by run <code>{runId ?? "unreported producing run"}</code>.
            </p>
          </div>
          {runId && onOpenRun && (
            <button
              className={classes(secondaryButton, "min-h-[31px] flex-none")}
              type="button"
              onClick={() => onOpenRun(runId)}
            >
              Open producing run
            </button>
          )}
        </div>

        {candidateProposalsError && (
          <p className="rounded-md border border-[rgb(255_140_136_/_18%)] bg-red-soft px-[9px] py-[7px]">
            Proposal evidence unavailable: {errorMessage(candidateProposalsError)}
          </p>
        )}
        {candidateAnalysisError && (
          <p className="rounded-md border border-[rgb(255_140_136_/_18%)] bg-red-soft px-[9px] py-[7px]">
            Analysis details unavailable: {errorMessage(candidateAnalysisError)}
          </p>
        )}

        <div className="mt-2.5 grid gap-[7px]">
          <article
            className="rounded-[7px] border border-[rgb(182_156_255_/_14%)] bg-[rgb(0_0_0_/_12%)] p-[9px]"
            aria-label={`Proposal ${proposalId}`}
          >
            <dl className="m-0 grid grid-cols-2 gap-x-3 gap-y-2 [&>div]:grid [&>div]:min-w-0 [&>div]:gap-0.5 [&_dt]:text-[0.52rem] [&_dt]:font-extrabold [&_dt]:tracking-[0.06em] [&_dt]:text-text-dim [&_dt]:uppercase [&_dd]:m-0 [&_dd]:min-w-0 [&_dd]:text-[0.61rem] [&_dd]:leading-[1.4] [&_dd]:text-text-soft [&_dd_code]:[overflow-wrap:anywhere] [&_dd_span]:mt-0.5 [&_dd_span]:block [&_dd_span]:text-text-dim">
              <div>
                <dt>Proposal</dt>
                <dd>
                  <code>{proposalId}</code>
                </dd>
              </div>
              <div>
                <dt>Analysis</dt>
                <dd>
                  {proposal ? (
                    <>
                      <code>{proposal.analysisRecordId}</code>
                      {analysis && <span>{analysis.title}</span>}
                      {!analysis && candidateAnalysisPending && " · Loading details"}
                    </>
                  ) : candidateProposalsPending ? (
                    "Loading proposal"
                  ) : (
                    "Proposal details unavailable"
                  )}
                </dd>
              </div>
              <div>
                <dt>Operator approval</dt>
                <dd>
                  {approval
                    ? `Approved · ${approval.actor}`
                    : candidateProposalsPending
                      ? "Loading approval"
                      : "Approval unavailable"}
                </dd>
              </div>
              <div>
                <dt>Acceptance</dt>
                <dd>
                  {source.acceptance.kind === "manual_review"
                    ? "Manual review"
                    : "Cross-run verification"}
                </dd>
              </div>
              {source.acceptance.kind === "cross_run_verification" && (
                <>
                  <div>
                    <dt>Cross-run verification</dt>
                    <dd>
                      <code>{source.acceptance.decision.analysis_record_id}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Decision output</dt>
                    <dd>
                      <code>{source.acceptance.decision.output_id}</code>
                    </dd>
                  </div>
                </>
              )}
              {approval && (
                <>
                  <div>
                    <dt>Note</dt>
                    <dd>{approval.note || "No approval note"}</dd>
                  </div>
                  <div>
                    <dt>Approved</dt>
                    <dd>
                      {approval.approvedAt ? (
                        <time dateTime={approval.approvedAt}>
                          {formatDateTime(approval.approvedAt)}
                        </time>
                      ) : (
                        "Approval time unavailable"
                      )}
                    </dd>
                  </div>
                </>
              )}
            </dl>
          </article>
        </div>
      </div>
    </div>
  );
}

function SnapshotSummary({ snapshot }: { snapshot: ConfigSnapshotSummary }) {
  return (
    <section
      className="mt-[13px] rounded-[9px] border border-line bg-panel-soft p-3.5"
      aria-label="Snapshot summary"
    >
      <div className="flex items-center gap-[9px]">
        <SlidersHorizontal className="text-blue" size={17} aria-hidden="true" />
        <span className="grid gap-0.5">
          <strong className="text-[0.72rem]">{snapshot.id}</strong>
          <small className="text-[0.58rem] text-text-dim">Immutable config snapshot</small>
        </span>
      </div>
      <div className="mt-[13px] grid grid-cols-4 gap-[9px] pt-3 max-[1100px]:grid-cols-2 max-[460px]:grid-cols-1">
        <ConfigFact label="Parameters" value={String(snapshot.parameterCount)} />
        <ConfigFact label="Instruments" value={String(snapshot.instrumentCount)} />
      </div>
    </section>
  );
}

const provenance =
  "mb-[13px] flex items-start gap-2.5 rounded-[8px] border border-[rgb(182_156_255_/_18%)] bg-[rgb(182_156_255_/_5%)] p-[11px]";
const provenanceIcon = "flex-none text-purple";
const provenanceCopy = "mt-1 mb-0 text-[0.61rem] leading-[1.45] text-text-dim";
