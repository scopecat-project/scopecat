import { useMemo, useState } from "react";
import { CheckCircle2, CircleDot, XCircle } from "lucide-react";
import type { RunDomainDecisionPage } from "../../api-contract";
import { errorMessage, formatDateTime, titleCase } from "../../lib/presentation";
import { classes, countBadge, detailCard } from "../../ui/styles";
import type { ProjectRun } from "../../types";
import { formatCoordinateValue } from "../inspections/coordinate-codec";
import { RunDomainQueueControl } from "./RunDomainQueueControl";

type DomainDecision = RunDomainDecisionPage["items"][number];

export function RunDomainDecisionCard({
  page,
  error,
  pending,
  completedPointCount,
  run,
}: {
  page?: RunDomainDecisionPage;
  error: Error | null;
  pending: boolean;
  completedPointCount: number;
  run: ProjectRun;
}) {
  const items = useMemo(() => page?.items ?? [], [page?.items]);
  const [requestedProposal, setRequestedProposal] = useState<number>();
  const selected = items.find((item) => item.proposal_index === requestedProposal) ?? items.at(-1);

  return (
    <article
      className={classes(detailCard, "col-span-full")}
      data-testid="run-domain-decision-card"
    >
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-text-soft">
          <CircleDot size={17} aria-hidden="true" />
          <strong className="text-sm">Adaptive domain decisions</strong>
        </div>
        <span className={countBadge}>{run.pointPlan?.decisionCount ?? items.length}</span>
      </div>
      <RunDomainQueueControl
        key={`${run.runId}:${run.plan.adaptiveScope ?? "static"}:${run.plan.adaptiveRegionsTruncated}`}
        run={run}
      />
      {error ? (
        <EmptyDecision title="Decision history unavailable" detail={errorMessage(error)} warning />
      ) : pending && !page ? (
        <EmptyDecision title="Reading domain decisions" />
      ) : items.length === 0 ? (
        <EmptyDecision
          title="No adaptive domains decided"
          detail="Optimizer and operator requests appear here after admission accepts or rejects them."
        />
      ) : (
        <div className="grid grid-cols-[230px_minmax(0,1fr)] gap-3 max-[760px]:grid-cols-1">
          <div className="grid content-start gap-1.5">
            {page?.next_cursor != null && (
              <span className="px-2 py-1 text-xs text-text-dim">
                Showing the latest {items.length} decisions
              </span>
            )}
            {items.map((item) => (
              <button
                className={classes(
                  "grid cursor-pointer gap-1 rounded-md border border-line bg-panel px-2.5 py-2 text-left",
                  item.proposal_index === selected?.proposal_index &&
                    "border-line-strong bg-panel-strong",
                )}
                key={item.proposal_index}
                onClick={() => setRequestedProposal(item.proposal_index)}
                type="button"
              >
                <span className="flex items-center justify-between gap-2 text-xs font-bold">
                  Decision #{item.proposal_index + 1}
                  <DecisionStatus decision={item} completedPointCount={completedPointCount} />
                </span>
                <span className="truncate text-xs text-text-dim">{formatDomainDecision(item)}</span>
              </button>
            ))}
          </div>
          {selected && <DecisionDetail decision={selected} />}
        </div>
      )}
    </article>
  );
}

function DecisionDetail({ decision }: { decision: DomainDecision }) {
  return (
    <div className="min-w-0 rounded-md border border-line bg-panel p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <strong className="text-xs">
            {decision.outcome === "accepted" ? acceptedPointLabel(decision) : "Rejected domain"}
          </strong>
          <div className="mt-1 text-xs text-text-dim">{formatDomainDecision(decision)}</div>
        </div>
        <time className="text-xs text-text-dim" dateTime={decision.occurred_at}>
          {formatDateTime(decision.occurred_at)}
        </time>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 text-xs max-[620px]:grid-cols-1">
        <DecisionFact label="Source" value={titleCase(decision.proposal.source)} />
        <DecisionFact
          label="Regions"
          value={
            decision.proposal.region_ids.length === 1
              ? (decision.proposal.region_ids[0] ?? "Unknown region")
              : `${decision.proposal.region_ids.length} regions`
          }
        />
        <DecisionFact label="Layout" value={titleCase(decision.proposal.fragment.layout)} />
        <DecisionFact
          label="Requested points"
          value={String(
            decision.proposal.fragment.point_count * decision.proposal.region_ids.length,
          )}
        />
      </dl>
      {decision.reason && <p className="mt-3 mb-0 text-xs text-red">{decision.reason}</p>}
    </div>
  );
}

function DecisionFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-bold text-text-dim">{label}</dt>
      <dd className="mt-0.5 text-text-soft">{value}</dd>
    </div>
  );
}

function DecisionStatus({
  decision,
  completedPointCount,
}: {
  decision: DomainDecision;
  completedPointCount: number;
}) {
  if (decision.outcome === "rejected") {
    return (
      <span className="inline-flex items-center gap-1 text-red">
        <XCircle size={11} /> Rejected
      </span>
    );
  }
  const completed =
    decision.accepted_point_start != null &&
    decision.accepted_point_start + decision.accepted_point_count <= completedPointCount;
  return completed ? (
    <span className="inline-flex items-center gap-1 text-accent">
      <CheckCircle2 size={11} /> Complete
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-blue">
      <CircleDot size={11} /> Accepted
    </span>
  );
}

function acceptedPointLabel(decision: DomainDecision): string {
  if (decision.accepted_point_start == null || decision.accepted_point_count === 0) {
    return "Accepted domain";
  }
  const first = decision.accepted_point_start;
  if (decision.accepted_point_count === 1) return `Run point #${first + 1}`;
  return `${decision.accepted_point_count} run points #${first + 1}–${first + decision.accepted_point_count}`;
}

function formatDomainDecision(decision: DomainDecision): string {
  const axes = decision.proposal.fragment.axes
    .map((axis) => {
      const source = axis.source;
      if (source.kind === "values") {
        return `${axis.axis_id} [${source.values.map(formatCoordinateValue).join(", ")}]`;
      }
      if (source.kind === "range") {
        return `${axis.axis_id} ${formatCoordinateValue(source.start)}→${formatCoordinateValue(source.stop)} (${source.points})`;
      }
      return `${axis.axis_id} around ${formatCoordinateValue(source.center)} span ${formatCoordinateValue(source.span)} (${source.points})`;
    })
    .join(" × ");
  const regions =
    decision.proposal.region_ids.length === 1
      ? (decision.proposal.region_ids[0] ?? "Unknown region")
      : `${decision.proposal.region_ids.length} regions`;
  return `${axes} · ${regions}`;
}

function EmptyDecision({
  title,
  detail,
  warning = false,
}: {
  title: string;
  detail?: string;
  warning?: boolean;
}) {
  return (
    <div className="rounded-md border border-dashed border-line p-4 text-center">
      <strong className={classes("text-xs text-text-soft", warning && "text-red")}>{title}</strong>
      {detail && <p className="mt-1.5 mb-0 text-xs text-text-dim">{detail}</p>}
    </div>
  );
}
