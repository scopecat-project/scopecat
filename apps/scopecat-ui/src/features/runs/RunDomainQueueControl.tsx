import * as Controls from "../../ui/Controls";
import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleDot, LoaderCircle, Plus } from "lucide-react";
import type {
  PointCoordinateSpec,
  RunDomainAxis,
  RunDomainEnqueueCommand,
  RunDomainQueue,
  RunDomainResolveCommand,
} from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { classes, primaryButton } from "../../ui/styles";
import type { ProjectRun } from "../../types";
import {
  coordinateInputValue,
  formatCoordinateMapping,
  formatCoordinateValue,
  isNumericCoordinate,
  parseCoordinate,
  parseLinearCoordinate,
} from "../inspections/coordinate-codec";
import { enqueueRunDomain, getRunDomainQueue, resolveRunDomain } from "./run-api";

type InputMode = "snap" | "free";
type SourceKind = "values" | "range" | "around";
type RegionScope = RunDomainResolveCommand["region_scope"];

interface AxisDraft {
  kind: SourceKind;
  values: string;
  start: string;
  stop: string;
  center: string;
  span: string;
  points: string;
}

type DomainDraft = Record<string, AxisDraft>;

export function RunDomainQueueControl({ run }: { run: ProjectRun }) {
  const queryClient = useQueryClient();
  const adaptive = run.plan.pointCount === undefined;
  const active = run.status === "running" && adaptive && !run.pointPlan.closed;
  const specs = useMemo(() => {
    const adaptiveIds = new Set(run.plan.adaptiveCoordinateIds);
    return run.plan.coordinateSpecs.filter((spec) => adaptiveIds.has(spec.id));
  }, [run.plan.adaptiveCoordinateIds, run.plan.coordinateSpecs]);
  const queue = useQuery({
    queryKey: ["run-domain-queue", run.runId],
    queryFn: ({ signal }) => getRunDomainQueue(run.runId, signal),
    enabled: adaptive,
    refetchInterval: active ? 250 : false,
  });
  const enqueue = useMutation({
    mutationFn: (command: RunDomainEnqueueCommand) => enqueueRunDomain(run.runId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["run-domain-queue", run.runId] });
      await queryClient.invalidateQueries({ queryKey: ["events", "run", run.runId] });
    },
  });
  const [mode, setMode] = useState<InputMode>("snap");
  const [scope, setScope] = useState<RegionScope>(
    run.plan.adaptiveScope === "global" ? "all" : "current",
  );
  const [selectedRegions, setSelectedRegions] = useState<string[]>([]);
  const [draft, setDraft] = useState<DomainDraft>({});
  const [inputError, setInputError] = useState<string>();
  const resolvedDraft = useMemo<DomainDraft>(
    () =>
      Object.fromEntries(specs.map((spec) => [spec.id, draft[spec.id] ?? initialAxisDraft(spec)])),
    [draft, specs],
  );

  const resolveCommand = useMemo<RunDomainResolveCommand | undefined>(() => {
    try {
      const regionIds = scope === "selected" ? selectedRegions : [];
      if (scope === "selected" && regionIds.length === 0) return undefined;
      return {
        coordinate_mode: mode,
        region_scope: scope,
        region_ids: regionIds,
        fragment: {
          layout: "grid",
          axes: specs.map((spec) => buildAxis(spec, resolvedDraft[spec.id])),
        },
      };
    } catch {
      return undefined;
    }
  }, [mode, resolvedDraft, scope, selectedRegions, specs]);
  const resolution = useQuery({
    queryKey: ["run-domain-resolution", run.runId, resolveCommand],
    queryFn: ({ signal }) => {
      if (!resolveCommand) throw new Error("scan domain is incomplete");
      return resolveRunDomain(run.runId, resolveCommand, signal);
    },
    enabled: active && resolveCommand !== undefined,
    retry: false,
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    try {
      if (!resolveCommand) throw new Error("complete every scan axis and region selection");
      setInputError(undefined);
      enqueue.mutate({
        request_id: `operator-domain.${globalThis.crypto.randomUUID()}`,
        ...resolveCommand,
      });
    } catch (error) {
      setInputError(errorMessage(error));
    }
  };

  if (!adaptive) return null;
  return (
    <div className="mb-3 rounded-md border border-line bg-panel-soft p-3">
      <div className="mb-2.5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <strong className="text-xs">Waveform &amp; adaptive scan</strong>
          <p className="mt-1 mb-0 text-xs leading-5 text-text-dim">
            Inspect compiled waveforms below, or extend the running scan with a compatible domain. A
            single value is simply a one-point scan.
          </p>
        </div>
        <div className="flex gap-1 rounded-md border border-line bg-panel p-1" role="group">
          {(["snap", "free"] as const).map((item) => (
            <button
              className={classes(
                "cursor-pointer rounded px-2.5 py-1.5 text-xs font-bold text-text-dim",
                mode === item && "bg-panel-strong text-text",
              )}
              key={item}
              onClick={() => {
                setMode(item);
                setInputError(undefined);
              }}
              type="button"
            >
              {`${capitalize(item)} scan`}
            </button>
          ))}
        </div>
      </div>

      <form onSubmit={submit}>
        <div className="mb-3 flex flex-wrap items-end gap-2.5">
          <Controls.Field className="grid gap-1 text-xs font-bold text-text-dim">
            Apply to
            <Controls.Select
              onChange={(event) => setScope(event.target.value as RegionScope)}
              value={scope}
            >
              {run.plan.adaptiveScope !== "global" && (
                <option value="current">Current region</option>
              )}
              <option value="all">All regions</option>
              {!run.plan.adaptiveRegionsTruncated && (
                <option value="selected">Selected regions</option>
              )}
            </Controls.Select>
          </Controls.Field>
          <span className="pb-2 text-xs text-text-dim">
            {scope === "current"
              ? "The executor binds this scan to its current outer static point."
              : scope === "all"
                ? `Targets all ${run.plan.adaptiveRegionCount} admitted regions.`
                : "Choose explicit outer static regions below."}
          </span>
          {run.plan.adaptiveRegionsTruncated && (
            <span className="w-full text-xs text-text-dim">
              The region catalog is sampled, so explicit selection is unavailable. Use the current
              region or all {run.plan.adaptiveRegionCount} regions.
            </span>
          )}
        </div>

        {scope === "selected" && (
          <RegionSelector run={run} selected={selectedRegions} setSelected={setSelectedRegions} />
        )}

        <div className="grid gap-2.5">
          {specs.map((spec) => (
            <AxisEditor
              draft={resolvedDraft[spec.id]!}
              key={spec.id}
              onChange={(next) => setDraft((current) => ({ ...current, [spec.id]: next }))}
              runId={run.runId}
              spec={spec}
            />
          ))}
        </div>

        {resolution.data && (
          <p className="mt-2.5 mb-0 rounded border border-line bg-panel px-2.5 py-2 text-xs text-text-dim">
            <strong className="text-text-soft">
              {resolution.data.total_point_count.toLocaleString()} points
            </strong>{" "}
            across {resolution.data.region_count.toLocaleString()} region
            {resolution.data.region_count === 1 ? "" : "s"} ·{" "}
            {fragmentSummary(resolution.data.fragment)}
            {mode === "snap" &&
              resolution.data.fragment.fragment_fingerprint !==
                resolution.data.requested_fragment.fragment_fingerprint && (
                <> · values will be snapped explicitly</>
              )}
          </p>
        )}

        <div className="mt-2.5 flex flex-wrap items-center gap-2">
          <button
            className={primaryButton}
            disabled={!active || enqueue.isPending || specs.length === 0}
            type="submit"
          >
            {enqueue.isPending ? (
              <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
            ) : (
              <Plus size={14} aria-hidden="true" />
            )}
            Add scan
          </button>
          <span className="text-xs text-text-dim">
            {!active
              ? run.pointPlan.closed
                ? "The adaptive point plan is closed."
                : "Adding scans becomes available while the executor owns this run."
              : mode === "snap"
                ? "Previewed values are snapped to admitted samples before queueing."
                : "Free values are compiled as entered, including off-grid coordinates."}
          </span>
          {(inputError || resolution.error || enqueue.error) && (
            <span className="w-full text-xs text-red" role="alert">
              {inputError ?? errorMessage(resolution.error ?? enqueue.error)}
            </span>
          )}
        </div>
      </form>

      {queue.data && queue.data.items.length > 0 && <DomainQueue queue={queue.data} />}
    </div>
  );
}

function AxisEditor({
  draft,
  onChange,
  runId,
  spec,
}: {
  draft: AxisDraft;
  onChange: (next: AxisDraft) => void;
  runId: string;
  spec: PointCoordinateSpec;
}) {
  const linear = isNumericCoordinate(spec);
  return (
    <fieldset className="rounded-md border border-line bg-panel p-2.5">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <legend className="text-xs font-bold text-text-soft">
          {spec.id} {spec.unit && <span className="font-medium text-text-dim">({spec.unit})</span>}
        </legend>
        <Controls.Select
          aria-label={`${spec.id} source`}
          className="bg-panel-soft"
          onChange={(event) => onChange({ ...draft, kind: event.target.value as SourceKind })}
          value={draft.kind}
        >
          <option value="values">Values</option>
          {linear && <option value="range">Range</option>}
          {linear && <option value="around">Around</option>}
        </Controls.Select>
      </div>
      {draft.kind === "values" ? (
        <Controls.Field className="grid gap-1 text-xs font-bold text-text-dim">
          Comma-separated values
          <Controls.Input
            aria-label={`${spec.id} values`}
            className="bg-panel-soft"
            list={spec.sampled_values.length > 0 ? `run-domain-${runId}-${spec.id}` : undefined}
            onChange={(event) => onChange({ ...draft, values: event.target.value })}
            placeholder="5.0, 5.1, 5.2"
            value={draft.values}
          />
          {spec.sampled_values.length > 0 && (
            <datalist id={`run-domain-${runId}-${spec.id}`}>
              {spec.sampled_values.map((value, index) => (
                <option key={index} value={coordinateInputValue(value)}>
                  {formatCoordinateValue(value)}
                </option>
              ))}
            </datalist>
          )}
        </Controls.Field>
      ) : draft.kind === "range" ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2">
          <AxisInput
            label="Start"
            value={draft.start}
            onChange={(start) => onChange({ ...draft, start })}
          />
          <AxisInput
            label="Stop"
            value={draft.stop}
            onChange={(stop) => onChange({ ...draft, stop })}
          />
          <AxisInput
            label="Points"
            value={draft.points}
            onChange={(points) => onChange({ ...draft, points })}
          />
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-2">
          <AxisInput
            label="Center"
            value={draft.center}
            onChange={(center) => onChange({ ...draft, center })}
          />
          <AxisInput
            label="Span"
            value={draft.span}
            onChange={(span) => onChange({ ...draft, span })}
          />
          <AxisInput
            label="Points"
            value={draft.points}
            onChange={(points) => onChange({ ...draft, points })}
          />
        </div>
      )}
    </fieldset>
  );
}

function AxisInput({
  label,
  onChange,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <Controls.Field className="grid gap-1 text-xs font-bold text-text-dim">
      {label}
      <Controls.Input
        className="bg-panel-soft"
        inputMode="decimal"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      />
    </Controls.Field>
  );
}

function RegionSelector({
  run,
  selected,
  setSelected,
}: {
  run: ProjectRun;
  selected: string[];
  setSelected: (ids: string[]) => void;
}) {
  return (
    <div className="mb-3 grid gap-1.5 rounded-md border border-line bg-panel p-2.5">
      {run.plan.adaptiveRegions.map((region) => (
        <Controls.Field className="flex items-center gap-2 text-xs text-text-soft" key={region.id}>
          <input
            aria-label={`Select ${region.id}`}
            checked={selected.includes(region.id)}
            onChange={(event) =>
              setSelected(
                event.target.checked
                  ? [...selected, region.id]
                  : selected.filter((id) => id !== region.id),
              )
            }
            type="checkbox"
          />
          <span className="font-bold">{region.id}</span>
          <span className="text-text-dim">{formatCoordinateMapping(region.coordinates)}</span>
        </Controls.Field>
      ))}
      {run.plan.adaptiveRegionsTruncated && (
        <span className="text-xs text-text-dim">
          Showing {run.plan.adaptiveRegions.length} of {run.plan.adaptiveRegionCount} regions.
        </span>
      )}
    </div>
  );
}

function DomainQueue({ queue }: { queue: RunDomainQueue }) {
  return (
    <div className="mt-3 grid gap-1.5 border-t border-line pt-2.5">
      {queue.items.map((item) => (
        <div
          className="flex flex-wrap items-center justify-between gap-2 rounded border border-line bg-panel px-2.5 py-2 text-xs"
          key={item.request.request_id}
        >
          <span className="truncate text-text-soft">
            {fragmentSummary(item.request.fragment)} · {regionSummary(item.request)}
          </span>
          {item.request.coordinate_mode === "snap" &&
            item.request.requested_fragment.fragment_fingerprint !==
              item.request.fragment.fragment_fingerprint && (
              <span className="w-full text-text-dim">
                Requested {fragmentSummary(item.request.requested_fragment)}
              </span>
            )}
          <span
            className={classes(
              "inline-flex items-center gap-1 font-bold capitalize",
              item.status === "accepted"
                ? "text-accent"
                : item.status === "rejected" || item.status === "cancelled"
                  ? "text-red"
                  : "text-blue",
            )}
          >
            <CircleDot size={10} aria-hidden="true" />
            {item.status}
            {item.accepted_point_start != null && acceptedPointRange(item)}
          </span>
          {item.reason && <span className="w-full text-text-dim">{item.reason}</span>}
        </div>
      ))}
    </div>
  );
}

function acceptedPointRange(item: RunDomainQueue["items"][number]): string {
  const start = item.accepted_point_start! + 1;
  if (item.accepted_point_count === 1) return ` · point #${start}`;
  return ` · points #${start}–${start + item.accepted_point_count - 1}`;
}

function buildAxis(spec: PointCoordinateSpec, draft: AxisDraft | undefined): RunDomainAxis {
  if (!draft) throw new Error(`${spec.id} requires a source`);
  if (draft.kind === "values") {
    const encoded = draft.values
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    if (encoded.length === 0) throw new Error(`${spec.id} requires at least one value`);
    return {
      axis_id: spec.id,
      source: { kind: "values", values: encoded.map((value) => parseCoordinate(spec, value)) },
    };
  }
  const points = Number(draft.points);
  if (!Number.isInteger(points) || points < 2) {
    throw new Error(`${spec.id} requires at least two points`);
  }
  if (draft.kind === "range") {
    return {
      axis_id: spec.id,
      source: {
        kind: "range",
        start: parseLinearCoordinate(spec, draft.start),
        stop: parseLinearCoordinate(spec, draft.stop),
        points,
      },
    };
  }
  return {
    axis_id: spec.id,
    source: {
      kind: "around",
      center: parseLinearCoordinate(spec, draft.center),
      span: parseLinearCoordinate(spec, draft.span),
      points,
    },
  };
}

function initialAxisDraft(spec: PointCoordinateSpec): AxisDraft {
  const values = spec.sampled_values.slice(0, 3).map(coordinateInputValue).filter(Boolean);
  const first = values[0] ?? "";
  const last = values.at(-1) ?? first;
  return {
    kind: "values",
    values: values.join(", "),
    start: first,
    stop: last,
    center: first,
    span: "0",
    points: String(Math.max(2, values.length)),
  };
}

function fragmentSummary(fragment: RunDomainQueue["items"][number]["request"]["fragment"]): string {
  return fragment.axes
    .map((axis) => {
      const source = axis.source;
      if (source.kind === "values") {
        return `${axis.axis_id} [${source.values.map(formatCoordinateValue).join(", ")}]`;
      }
      if (source.kind === "range") {
        return `${axis.axis_id} ${formatCoordinateValue(source.start)}→${formatCoordinateValue(source.stop)} (${source.points})`;
      }
      return `${axis.axis_id} around ${formatCoordinateValue(source.center)} ± ${formatCoordinateValue(source.span)} (${source.points})`;
    })
    .join(" × ");
}

function regionSummary(request: RunDomainQueue["items"][number]["request"]): string {
  if (request.region_scope === "current") return "current region";
  if (request.region_scope === "all") return `${request.region_count} regions`;
  return `${request.region_ids.length} selected regions`;
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
