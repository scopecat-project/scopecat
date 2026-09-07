import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleOff, LoaderCircle, Radio, Send, Waves } from "lucide-react";
import type {
  PointCoordinateSpec,
  ProgramInspectionQuery,
  ReviewCompileCommand,
  ReviewSession,
} from "../../api-contract";
import { errorMessage } from "../../lib/presentation";
import { classes, primaryButton } from "../../ui/styles";
import { CompiledInspectionView } from "../inspections/CompiledInspectionView";
import {
  coordinateInputValue,
  formatCoordinateMapping,
  parseCoordinate,
} from "../inspections/coordinate-codec";
import { compileReviewPoint, getReview, getReviews } from "./review-api";

type PointMode = "exact" | "snap" | "free";
type CoordinateDraft = Record<string, string>;

export function ReviewWorkspace({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const [requestedId, setRequestedId] = useState(reviewIdFromLocation);
  const reviews = useQuery({
    queryKey: ["reviews"],
    queryFn: ({ signal }) => getReviews(signal),
    enabled: !daemonUnavailable,
    refetchInterval: 1_000,
  });
  const locationId = reviewIdFromLocation();
  const selectionId = requestedId === locationId ? requestedId : locationId;
  const selected =
    reviews.data?.items.find((session) => session.session_id === selectionId) ??
    reviews.data?.items.find((session) => session.active) ??
    reviews.data?.items[0];
  const selectedId = selected?.session_id;

  useEffect(() => {
    if (selectedId && reviewIdFromLocation() !== selectedId) {
      replaceReviewLocation(selectedId);
    }
  }, [selectedId]);

  if (reviews.isPending) return <WorkspaceMessage title="Loading reviews" pending />;
  if (reviews.isError) {
    return (
      <WorkspaceMessage title="Review sessions unavailable" detail={errorMessage(reviews.error)} />
    );
  }
  if (!selected) {
    return (
      <WorkspaceMessage
        title="No live experiment reviews"
        detail="Start one from Python with lab.prepare(experiment).review()."
      />
    );
  }

  return (
    <div className="grid min-h-[680px] grid-cols-[260px_minmax(0,1fr)] overflow-hidden rounded-lg border border-line bg-panel max-[900px]:grid-cols-1">
      <aside className="border-r border-line bg-panel-soft p-2.5 max-[900px]:border-r-0 max-[900px]:border-b">
        <div className="flex items-center justify-between px-2 py-2">
          <strong className="text-[0.68rem] tracking-[0.08em] text-text-dim uppercase">
            Review sessions
          </strong>
          <span className="text-[0.62rem] text-text-dim">{reviews.data.items.length}</span>
        </div>
        <div className="grid gap-1.5 max-[900px]:grid-cols-[repeat(auto-fit,minmax(210px,1fr))]">
          {reviews.data.items.map((session) => (
            <button
              className={classes(
                "grid cursor-pointer gap-1 rounded-md border border-transparent bg-transparent px-2.5 py-2.5 text-left text-text-soft hover:bg-panel",
                session.session_id === selected.session_id && "border-line-strong bg-panel",
              )}
              key={session.session_id}
              onClick={() => {
                setRequestedId(session.session_id);
                replaceReviewLocation(session.session_id);
              }}
              type="button"
            >
              <span className="flex items-center justify-between gap-2 text-[0.7rem] font-bold">
                <span className="truncate">{session.title}</span>
                <SessionState active={session.active} />
              </span>
              <span className="truncate font-mono text-[0.58rem] text-text-dim">
                {session.experiment_id}
              </span>
            </button>
          ))}
        </div>
      </aside>
      <ReviewDetail sessionId={selected.session_id} />
    </div>
  );
}

function ReviewDetail({ sessionId }: { sessionId: string }) {
  const queryClient = useQueryClient();
  const review = useQuery({
    queryKey: ["review", sessionId],
    queryFn: ({ signal }) => getReview(sessionId, signal),
    refetchInterval: 500,
  });
  const compile = useMutation({
    mutationFn: (command: ReviewCompileCommand) => compileReviewPoint(sessionId, command),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["review", sessionId] });
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
    },
  });

  if (review.isPending) return <WorkspaceMessage title="Loading compiler view" pending />;
  if (review.isError) {
    return <WorkspaceMessage title="Review unavailable" detail={errorMessage(review.error)} />;
  }
  const session = review.data;
  return (
    <section className="min-w-0 p-4 max-[680px]:p-2.5">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-line pb-3">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <SessionState active={session.active} />
            <span className="text-[0.6rem] font-bold tracking-[0.07em] text-text-dim uppercase">
              {session.experiment_kind}
            </span>
          </div>
          <h2 className="m-0 text-[1.05rem] tracking-[-0.02em]">{session.title}</h2>
          <code className="text-[0.62rem] text-text-dim">{session.experiment_id}</code>
        </div>
        <span className="inline-flex items-center gap-1.5 text-[0.63rem] text-text-dim">
          {session.pending_request_count > 0 && (
            <LoaderCircle className="animate-spin" size={13} aria-hidden="true" />
          )}
          {session.pending_request_count > 0
            ? `${session.pending_request_count} compilation pending`
            : session.active
              ? "Compiler ready"
              : "Compiler disconnected"}
        </span>
      </header>

      <PointCompiler
        key={pointCompilerKey(session)}
        compileError={compile.error}
        compiling={compile.isPending || session.pending_request_count > 0}
        onCompile={(command) => compile.mutate(command)}
        session={session}
      />
      <InspectionResult onCompile={(command) => compile.mutate(command)} session={session} />
    </section>
  );
}

function PointCompiler({
  session,
  compiling,
  compileError,
  onCompile,
}: {
  session: ReviewSession;
  compiling: boolean;
  compileError: Error | null;
  onCompile: (command: ReviewCompileCommand) => void;
}) {
  const selectedPoint = session.latest_result?.point;
  const [mode, setMode] = useState<PointMode>(
    selectedPoint?.point_index == null ? "free" : "exact",
  );
  const [coordinates, setCoordinates] = useState<CoordinateDraft>(() =>
    coordinateDraft(session.coordinates, selectedPoint?.coordinates),
  );

  const submit = () => {
    onCompile({
      coordinates: Object.fromEntries(
        session.coordinates.map((spec) => [
          spec.id,
          parseCoordinate(spec, coordinates[spec.id] ?? ""),
        ]),
      ),
      coordinate_mode: mode,
    });
  };

  return (
    <div className="mb-4 rounded-md border border-line bg-panel-soft p-3.5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <strong className="text-[0.72rem]">Compile point</strong>
          <p className="mt-1 mb-0 text-[0.62rem] leading-5 text-text-dim">
            Exact matching, optional snapping, and free coordinates use the same pure compiler and
            waveform viewer.
          </p>
        </div>
        <div className="flex gap-1 rounded-md border border-line bg-panel p-1" role="group">
          {(["exact", "snap", "free"] as const).map((item) => (
            <button
              className={classes(
                "cursor-pointer rounded px-2.5 py-1.5 text-[0.62rem] font-bold capitalize text-text-dim",
                mode === item && "bg-panel-strong text-text",
              )}
              key={item}
              onClick={() => setMode(item)}
              type="button"
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      {session.planned_points.length > 0 && (
        <label className="mb-2.5 grid max-w-[420px] gap-1.5 text-[0.61rem] font-bold text-text-dim">
          Fill from a planned point
          <select
            className="rounded-md border border-line bg-panel px-2.5 py-2 text-[0.68rem] font-medium text-text-soft"
            onChange={(event) => {
              const point = session.planned_points[Number(event.target.value)];
              if (point) {
                setCoordinates(coordinateDraft(session.coordinates, point.coordinates));
              }
            }}
            defaultValue=""
          >
            <option value="" disabled>
              Select…
            </option>
            {session.planned_points.map((point, index) => (
              <option key={point.point_index ?? "candidate"} value={index}>
                #{(point.point_index ?? 0) + 1} · {formatCoordinateMapping(point.coordinates)}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2.5">
        {session.coordinates.map((spec) => (
          <CoordinateInput
            key={spec.id}
            spec={spec}
            value={coordinates[spec.id] ?? ""}
            onChange={(value) => setCoordinates((current) => ({ ...current, [spec.id]: value }))}
          />
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          className={primaryButton}
          disabled={compiling || !session.active}
          onClick={submit}
          type="button"
        >
          {compiling ? (
            <LoaderCircle className="animate-spin" size={14} aria-hidden="true" />
          ) : (
            <Send size={14} aria-hidden="true" />
          )}
          Compile waveform
        </button>
        <span className="text-[0.61rem] text-text-dim">
          {mode === "exact"
            ? "Reject values outside the authored scan."
            : mode === "snap"
              ? "Resolve each value to the nearest authored scan point."
              : "Allow valid coordinates outside the authored scan."}
        </span>
        {compileError && (
          <span className="w-full text-[0.62rem] text-red" role="alert">
            {errorMessage(compileError)}
          </span>
        )}
      </div>
    </div>
  );
}

function CoordinateInput({
  spec,
  value,
  onChange,
}: {
  spec: PointCoordinateSpec;
  value: string;
  onChange: (value: string) => void;
}) {
  const datalistId = `review-coordinate-${spec.id}`;
  if (spec.kind === "bool") {
    return (
      <label className="grid gap-1.5 text-[0.61rem] font-bold text-text-dim">
        {spec.id}
        <select
          className="rounded-md border border-line bg-panel px-2.5 py-2 text-[0.68rem] text-text-soft"
          onChange={(event) => onChange(event.target.value)}
          value={value}
        >
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
    );
  }
  return (
    <label className="grid gap-1.5 text-[0.61rem] font-bold text-text-dim">
      <span className="flex justify-between gap-2">
        {spec.id}
        {spec.unit && <span className="font-medium text-text-dim">{spec.unit}</span>}
      </span>
      <input
        className="rounded-md border border-line bg-panel px-2.5 py-2 text-[0.68rem] font-medium text-text-soft"
        list={spec.sampled_values.length > 0 ? datalistId : undefined}
        max={spec.maximum ?? undefined}
        min={spec.minimum ?? undefined}
        onChange={(event) => onChange(event.target.value)}
        step={spec.kind === "int" ? 1 : "any"}
        type={["int", "float", "quantity"].includes(spec.kind) ? "number" : "text"}
        value={value}
      />
      {spec.sampled_values.length > 0 && (
        <datalist id={datalistId}>
          {spec.sampled_values.map((planned, index) => (
            <option
              aria-label={`${spec.id} planned value ${index + 1}`}
              key={index}
              value={coordinateInputValue(planned)}
            />
          ))}
        </datalist>
      )}
    </label>
  );
}

function InspectionResult({
  session,
  onCompile,
}: {
  session: ReviewSession;
  onCompile: (command: ReviewCompileCommand) => void;
}) {
  const result = session.latest_result;
  if (!result) return <WorkspaceMessage title="No point compiled yet" />;
  if (result.error) return <WorkspaceMessage title="Compilation failed" detail={result.error} />;
  if (!result.point) return <WorkspaceMessage title="No selected point" />;
  const point = result.point;
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-panel-soft px-3.5 py-2.5">
        <div>
          <span className="text-[0.59rem] font-bold tracking-[0.06em] text-text-dim uppercase">
            {point.point_index == null
              ? "Off-grid candidate"
              : `Planned point #${point.point_index + 1}`}
          </span>
          <div className="mt-1 text-[0.7rem] font-semibold text-text-soft">
            {formatCoordinateMapping(point.coordinates)}
          </div>
        </div>
        {point.proposal_fingerprint && (
          <code className="max-w-[350px] truncate text-[0.56rem] text-text-dim">
            {point.proposal_fingerprint}
          </code>
        )}
      </div>
      <CompiledInspectionView
        inspections={result.inspections}
        onProgramQuery={(inspectionQuery) =>
          onCompile(inspectionQueryCommand(point, inspectionQuery))
        }
      />
    </div>
  );
}

function inspectionQueryCommand(
  point: NonNullable<NonNullable<ReviewSession["latest_result"]>["point"]>,
  inspectionQuery: ProgramInspectionQuery,
): ReviewCompileCommand {
  if (point.point_index != null) {
    return {
      point_index: point.point_index,
      coordinate_mode: "exact",
      inspection_query: inspectionQuery,
    };
  }
  return {
    coordinates: point.coordinates,
    coordinate_mode: "free",
    inspection_query: inspectionQuery,
  };
}

function coordinateDraft(
  specs: PointCoordinateSpec[],
  current?: Record<string, unknown>,
): CoordinateDraft {
  return Object.fromEntries(
    specs.map((spec) => [
      spec.id,
      coordinateInputValue(current?.[spec.id] ?? spec.sampled_values[0] ?? ""),
    ]),
  );
}

function pointCompilerKey(session: ReviewSession): string {
  return JSON.stringify([
    session.session_id,
    session.coordinates,
    session.latest_result?.point ?? null,
  ]);
}

function SessionState({ active }: { active: boolean }) {
  return (
    <span
      className={classes(
        "inline-flex items-center gap-1 text-[0.56rem] font-bold uppercase",
        active ? "text-accent" : "text-text-dim",
      )}
    >
      {active ? <Radio size={10} aria-hidden="true" /> : <CircleOff size={10} aria-hidden="true" />}
      {active ? "Live" : "Closed"}
    </span>
  );
}

function WorkspaceMessage({
  title,
  detail,
  pending = false,
}: {
  title: string;
  detail?: string;
  pending?: boolean;
}) {
  return (
    <div className="grid min-h-[420px] place-content-center justify-items-center p-8 text-center">
      <span className="mb-3 grid size-12 place-items-center rounded-xl border border-line bg-panel-soft text-text-dim">
        {pending ? <LoaderCircle className="animate-spin" /> : <Waves />}
      </span>
      <strong className="text-[0.8rem] text-text-soft">{title}</strong>
      {detail && (
        <p className="mt-2 max-w-[460px] text-[0.66rem] leading-5 text-text-dim">{detail}</p>
      )}
    </div>
  );
}

function reviewIdFromLocation(): string | undefined {
  const match = window.location.hash.match(/^#reviews\/(.+)$/);
  const encoded = match?.[1];
  return encoded ? decodeURIComponent(encoded) : undefined;
}

function replaceReviewLocation(sessionId: string): void {
  const location = new URL(window.location.href);
  location.hash = `reviews/${encodeURIComponent(sessionId)}`;
  window.history.replaceState(null, "", `${location.pathname}${location.search}${location.hash}`);
}
