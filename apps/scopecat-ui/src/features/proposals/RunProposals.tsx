import { useMemo, useState, type ReactNode } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronDown,
  CircleDot,
  GitCompareArrows,
  History,
  LoaderCircle,
  XCircle,
} from "lucide-react";
import {
  getOlderRunParameterProposals,
  getRunParameterProposals,
} from "../../data/parameter-proposals/api";
import type { ParameterProposal } from "../../data/parameter-proposals/types";
import { parameterProposalKeys } from "../../data/parameter-proposals/query-keys";
import { errorMessage, formatDateTime, shorten } from "../../lib/presentation";
import { classes, countBadge, detailCard } from "../../ui/styles";

export function RunProposals({ runId }: { runId: string }) {
  const proposalsQuery = useInfiniteQuery({
    queryKey: parameterProposalKeys.infinite(runId),
    queryFn: ({ pageParam, signal }) =>
      pageParam === undefined
        ? getRunParameterProposals(runId, signal)
        : getOlderRunParameterProposals(runId, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => page.nextCursor,
  });
  const proposals = useMemo(() => {
    const items = new Map<string, ParameterProposal>();
    for (const page of proposalsQuery.data?.pages ?? []) {
      for (const proposal of page.items) items.set(proposal.id, proposal);
    }
    return [...items.values()];
  }, [proposalsQuery.data]);
  return (
    <article
      className={classes(
        detailCard,
        "col-span-full overflow-hidden p-0 max-[680px]:col-auto max-[680px]:row-auto",
      )}
      data-testid="run-proposals-card"
    >
      <header className="grid grid-cols-[30px_minmax(0,1fr)_auto_auto] items-center gap-2.5 border-b border-line px-[17px] py-4 max-[680px]:grid-cols-[30px_minmax(0,1fr)_auto]">
        <span
          className="grid size-[30px] place-items-center rounded-[8px] border border-line bg-panel text-accent"
          aria-hidden="true"
        >
          <GitCompareArrows size={17} />
        </span>
        <div>
          <h3 className="m-0 text-[0.78rem]">Parameter proposals</h3>
          <p className="mt-[3px] mb-0 text-[0.59rem] text-text-dim">
            Review proposed changes and test a candidate before publishing it to a parameter branch.
          </p>
        </div>
        <span className={countBadge}>
          {proposals.length}
          {proposalsQuery.hasNextPage ? "+" : ""}
        </span>
      </header>

      {proposalsQuery.isPending ? (
        <ProposalMessage
          icon={<LoaderCircle className="animate-spin" />}
          title="Reading proposals"
          detail="Loading retained parameter changes and approval records."
        />
      ) : proposalsQuery.isError && proposalsQuery.data === undefined ? (
        <ProposalMessage
          icon={<XCircle />}
          title="Proposals unavailable"
          detail={errorMessage(proposalsQuery.error)}
          warning
        />
      ) : proposals.length === 0 ? (
        <ProposalMessage
          icon={<CircleDot />}
          title="No parameter proposals"
          detail="Analysis-generated changes for this run will appear here."
        />
      ) : (
        <div className="grid gap-2.5 p-3">
          {proposals.map((proposal) => {
            return (
              <section
                className="overflow-hidden rounded-[10px] border border-line bg-[rgb(6_10_14_/_32%)]"
                key={proposal.id}
              >
                <header className="grid grid-cols-[minmax(0,1fr)_auto] gap-3.5 px-3.5 py-[13px] max-[680px]:grid-cols-[minmax(0,1fr)]">
                  <div>
                    <span
                      data-testid="proposal-state"
                      className={classes(
                        "mb-[7px] inline-flex min-h-5 items-center rounded-full border px-[7px] text-[0.54rem] font-extrabold tracking-[0.05em] uppercase",
                        proposal.approval
                          ? "border-[rgb(128_163_207_/_25%)] bg-accent-soft text-accent"
                          : "border-line bg-yellow-soft text-yellow",
                      )}
                    >
                      {proposal.approval ? "Approval recorded" : "Candidate"}
                    </span>
                    <h4 className="m-0 overflow-hidden font-mono text-[0.72rem] text-ellipsis whitespace-nowrap">
                      {proposal.id}
                    </h4>
                    <p className="mt-[5px] mb-0 text-[0.66rem] leading-[1.45] text-text-soft">
                      {proposal.reason}
                    </p>
                    <a
                      className="mt-2 inline-block text-[0.65rem] text-accent underline"
                      href={`?run=${encodeURIComponent(proposal.sourceRunId)}&run-analysis=${encodeURIComponent(proposal.analysisRecordId)}#runs`}
                    >
                      View source analysis
                    </a>
                    {proposal.evidenceOutputIds.length > 0 && (
                      <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[0.56rem] text-text-dim">
                        <span className="font-extrabold tracking-[0.05em] uppercase">Evidence</span>
                        {proposal.evidenceOutputIds.map((outputId) => (
                          <code
                            className="rounded border border-line bg-bg px-1.5 py-0.5 text-text-soft"
                            key={outputId}
                          >
                            {outputId}
                          </code>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="grid content-start justify-items-end gap-[5px] text-[0.57rem] text-text-dim max-[680px]:grid-cols-[auto_minmax(0,1fr)] max-[680px]:justify-items-start">
                    {proposal.confidence !== undefined && (
                      <span>{Math.round(proposal.confidence * 100)}% confidence</span>
                    )}
                    <code
                      className="max-w-[190px] overflow-hidden text-ellipsis whitespace-nowrap"
                      title={proposal.baseContentHash}
                    >
                      Base {shorten(proposal.baseConfigId, 18)}
                    </code>
                  </div>
                </header>

                <ProposalDiff proposal={proposal} />
                <ProposalApproval proposal={proposal} />

                <CandidateNextSteps proposal={proposal} />
              </section>
            );
          })}
          {proposalsQuery.isFetchNextPageError ? (
            <p className="mx-1 my-0 text-[0.62rem] text-red" role="status">
              {errorMessage(proposalsQuery.error)}
            </p>
          ) : null}
          {proposalsQuery.hasNextPage ? (
            <button
              className={classes(
                "inline-flex min-h-8 cursor-pointer items-center justify-center gap-1.5 rounded-[7px] border border-line bg-panel px-2.5 text-[0.62rem] font-[750] text-text-soft hover:bg-panel-soft disabled:cursor-not-allowed disabled:opacity-42",
              )}
              disabled={proposalsQuery.isFetchingNextPage}
              onClick={() => void proposalsQuery.fetchNextPage()}
              type="button"
            >
              {proposalsQuery.isFetchingNextPage ? (
                <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
              ) : (
                <ChevronDown size={14} aria-hidden="true" />
              )}
              {proposalsQuery.isFetchingNextPage
                ? "Loading older proposals…"
                : "Load older proposals"}
            </button>
          ) : null}
        </div>
      )}
    </article>
  );
}

function CandidateNextSteps({ proposal }: { proposal: ParameterProposal }) {
  return (
    <section className="px-3.5 py-3 text-[0.7rem] leading-relaxed" aria-label="Candidate adoption">
      <p>
        Adoption currently requires an author session; this page does not publish parameters.
        Collect independent verification using your laboratory's policy, then explicitly publish to
        the reviewed parameter branch. Only that branch advances; existing runs and prepared
        experiments keep their original inputs.
      </p>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-accent underline">
        <a
          href="https://scopecat-project.github.io/scopecat/how-to/verify-parameter-candidates/"
          target="_blank"
          rel="noreferrer"
        >
          Verify a candidate
        </a>
        <a
          href="https://scopecat-project.github.io/scopecat/how-to/publish-working-point-calibration/"
          target="_blank"
          rel="noreferrer"
        >
          Publish to a parameter branch
        </a>
      </div>
      <details className="mt-3">
        <summary className="cursor-pointer text-accent">For authors: reopen this candidate</summary>
        <p className="mt-2">
          In a connected author session named <code>session</code>, reopen this saved proposal. The
          guides above explain how to prepare an independent verification experiment, supply its
          retained policy result and capture the destination branch.
        </p>
        <pre className="mt-2 max-h-40 overflow-auto rounded border border-line bg-bg p-3">
          <code>{`candidate = session.config.candidate(${JSON.stringify(proposal.sourceRunId)}, ${JSON.stringify(proposal.id)})`}</code>
        </pre>
      </details>
    </section>
  );
}

function ProposalValue({ value }: { value: unknown }) {
  const [expanded, setExpanded] = useState(false);
  const text = formatParameterValue(value);
  if (text.length <= 120) return <>{text}</>;
  return (
    <div className="min-w-0">
      <span className="block max-h-16 overflow-hidden">{text.slice(0, 120)}…</span>
      <details onToggle={(event) => setExpanded(event.currentTarget.open)}>
        <summary className="mt-1 cursor-pointer font-sans text-accent">View full value</summary>
        {expanded && (
          <pre
            className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap [overflow-wrap:anywhere]"
            aria-label="Full parameter value"
          >
            {text}
          </pre>
        )}
      </details>
    </div>
  );
}

function ProposalDiff({ proposal }: { proposal: ParameterProposal }) {
  return (
    <div
      className="mx-3.5 max-h-96 overflow-auto rounded-[9px] border border-line bg-panel-soft"
      role="table"
    >
      <div
        className="grid grid-cols-[minmax(130px,0.8fr)_minmax(130px,1fr)_22px_minmax(130px,1fr)] items-center gap-2 border-b border-line bg-panel px-[11px] py-[9px] text-[0.54rem] font-extrabold tracking-[0.07em] text-text-dim uppercase max-[680px]:min-w-[650px]"
        role="row"
      >
        <span role="columnheader">Parameter</span>
        <span role="columnheader">Before</span>
        <span aria-hidden="true" />
        <span role="columnheader">Proposed</span>
      </div>
      {proposal.deltas
        .flatMap<{
          parameterId: string;
          before: unknown;
          after: unknown;
          changeKind?: string;
        }>((delta) =>
          delta.cells?.length
            ? delta.cells.map((cell) => ({
                parameterId: `${delta.parameterId}[${Object.entries(cell.key)
                  .map(([key, value]) => `${key}=${formatParameterValue(value)}`)
                  .join(", ")}].${cell.field}`,
                before: cell.before,
                after: cell.after,
                changeKind: cell.change_kind,
              }))
            : [{ ...delta, changeKind: delta.cells ? "No changed keyed cells" : undefined }],
        )
        .map((delta) => (
          <div
            className="grid grid-cols-[minmax(130px,0.8fr)_minmax(130px,1fr)_22px_minmax(130px,1fr)] items-center gap-2 border-b border-line px-[11px] py-[9px] last:border-b-0 max-[680px]:min-w-[650px] [&>svg]:text-text-dim"
            role="row"
            key={delta.parameterId}
          >
            <div
              className="font-mono text-[0.61rem] text-text-soft [overflow-wrap:anywhere]"
              role="cell"
            >
              <ProposalValue value={delta.parameterId} />
              {delta.changeKind && (
                <span className="mt-1 block font-sans text-text-dim">{delta.changeKind}</span>
              )}
            </div>
            <div
              className="min-w-0 rounded-md bg-[rgb(255_140_136_/_6%)] px-2 py-[7px] font-mono text-[0.61rem] text-[#c7a6a4] [overflow-wrap:anywhere]"
              role="cell"
            >
              <ProposalValue value={delta.before} />
            </div>
            <ArrowRight size={14} aria-hidden="true" />
            <div
              className="min-w-0 rounded-md bg-accent-soft px-2 py-[7px] font-mono text-[0.61rem] text-accent [overflow-wrap:anywhere]"
              role="cell"
            >
              <ProposalValue value={delta.after} />
            </div>
          </div>
        ))}
    </div>
  );
}

function ProposalApproval({ proposal }: { proposal: ParameterProposal }) {
  const approval = proposal.approval;
  if (!approval) return null;
  return (
    <div className="mx-3.5 mt-3 grid grid-cols-[120px_minmax(0,1fr)] gap-2.5 rounded-[8px] border border-line bg-panel-soft p-2.5 max-[680px]:grid-cols-[minmax(0,1fr)]">
      <div className="flex items-center gap-1.5 text-[0.58rem] font-[750] text-text-dim">
        <History size={14} aria-hidden="true" />
        <span>Operator approval</span>
      </div>
      <ol className="m-0 grid list-none gap-1.5 p-0">
        <li className="grid grid-cols-[7px_auto_minmax(0,1fr)_auto] items-baseline gap-[7px] text-[0.58rem] text-text-dim">
          <span className="size-[7px] rounded-full bg-accent" />
          <strong className="text-[0.6rem] text-text-soft">Approved</strong>
          <span>by {approval.actor}</span>
          {approval.note && (
            <p className="col-[2/-1] m-0 leading-[1.45] text-text-soft">{approval.note}</p>
          )}
          {approval.approvedAt && (
            <time className="text-[0.54rem]" dateTime={approval.approvedAt}>
              {formatDateTime(approval.approvedAt)}
            </time>
          )}
        </li>
      </ol>
    </div>
  );
}

function ProposalMessage({
  icon,
  title,
  detail,
  warning = false,
}: {
  icon: ReactNode;
  title: string;
  detail: string;
  warning?: boolean;
}) {
  return (
    <div className="flex min-h-[100px] items-center gap-[11px] p-5 text-text-dim">
      <span
        className={classes(
          "grid size-[34px] flex-none place-items-center rounded-[9px] [&>svg]:w-[17px]",
          warning ? "bg-red-soft text-red" : "bg-accent-soft text-accent",
        )}
        aria-hidden="true"
      >
        {icon}
      </span>
      <div>
        <strong className="text-[0.7rem] text-text-soft">{title}</strong>
        <p className="mt-1 mb-0 text-[0.62rem] leading-normal">{detail}</p>
      </div>
    </div>
  );
}

function formatParameterValue(value: unknown): string {
  if (value === null) return "null";
  if (value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (typeof value === "object" && !Array.isArray(value)) {
    const object = value as Record<string, unknown>;
    if (
      (typeof object.value === "number" || typeof object.value === "string") &&
      typeof object.unit === "string"
    ) {
      return `${object.value} ${object.unit}`;
    }
  }
  const serialized = JSON.stringify(value);
  if (serialized === undefined) return "—";
  return serialized;
}
