import * as Controls from "../../ui/Controls";
import { useEffect, useState, useSyncExternalStore } from "react";
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
const modeLabels = { exact: "精确匹配", snap: "就近选点", free: "自由坐标" };

export function ReviewWorkspace({ daemonUnavailable }: { daemonUnavailable: boolean }) {
  const requestedId = useSyncExternalStore(subscribeReviewLocation, reviewIdFromLocation);
  const reviews = useQuery({
    queryKey: ["reviews"],
    queryFn: ({ signal }) => getReviews(signal),
    enabled: !daemonUnavailable,
    refetchInterval: 1_000,
  });
  const selected =
    requestedId !== undefined
      ? reviews.data?.items.find((session) => session.session_id === requestedId)
      : (reviews.data?.items.find((session) => session.active) ?? reviews.data?.items[0]);
  const selectedId = selected?.session_id;

  useEffect(() => {
    if (requestedId === undefined && selectedId) {
      replaceReviewLocation(selectedId);
    }
  }, [requestedId, selectedId]);

  if (daemonUnavailable)
    return <WorkspaceMessage title="本地服务暂不可用" detail="恢复连接后可继续查看检查会话。" />;
  if (reviews.isPending) return <WorkspaceMessage title="正在加载检查会话" pending />;
  if (reviews.isError) {
    return <WorkspaceMessage title="无法加载检查会话" detail={errorMessage(reviews.error)} />;
  }
  if (!selected && requestedId === undefined) {
    return (
      <WorkspaceMessage
        title="暂无实验检查会话"
        detail="在 Python 中使用 lab.prepare(experiment).review() 创建会话。"
      />
    );
  }

  return (
    <div className="grid min-h-[680px] grid-cols-[260px_minmax(0,1fr)] overflow-hidden rounded-lg border border-line bg-panel max-[900px]:grid-cols-1">
      <aside className="border-r border-line bg-panel-soft p-2.5 max-[900px]:border-r-0 max-[900px]:border-b">
        <div className="flex items-center justify-between px-2 py-2">
          <strong className="text-xs tracking-[0.08em] text-text-dim uppercase">检查会话</strong>
          <span className="text-xs text-text-dim">{reviews.data.items.length}</span>
        </div>
        <div className="grid gap-1.5 max-[900px]:grid-cols-[repeat(auto-fit,minmax(210px,1fr))]">
          {reviews.data.items.map((session) => (
            <button
              aria-pressed={session.session_id === selected?.session_id}
              className={classes(
                "grid cursor-pointer gap-1 rounded-md border border-transparent bg-transparent px-2.5 py-2.5 text-left text-text-soft hover:bg-panel",
                session.session_id === selected?.session_id && "border-line-strong bg-panel",
              )}
              key={session.session_id}
              onClick={() => {
                window.location.hash = `reviews/${encodeURIComponent(session.session_id)}`;
              }}
              type="button"
            >
              <span className="flex items-center justify-between gap-2 text-xs font-bold">
                <span className="truncate">{session.title}</span>
                <SessionState active={session.active} />
              </span>
              <span className="truncate font-mono text-xs text-text-dim">
                {session.experiment_id}
              </span>
            </button>
          ))}
        </div>
      </aside>
      {selected ? (
        <ReviewDetail key={selected.session_id} sessionId={selected.session_id} />
      ) : (
        <WorkspaceMessage
          title="指定的检查会话不存在"
          detail={`会话 ${requestedId} 已不可用。地址已保留；请从列表主动选择其他会话，或从 Notebook 创建新会话。`}
        />
      )}
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

  if (review.isPending) return <WorkspaceMessage title="正在加载编译检查" pending />;
  if (review.isError) {
    return <WorkspaceMessage title="无法加载检查会话" detail={errorMessage(review.error)} />;
  }
  const session = review.data;
  return (
    <section className="min-w-0 p-4 max-[680px]:p-2.5">
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-line pb-3">
        <div className="min-w-0 break-words">
          <div className="mb-1 flex items-center gap-2">
            <SessionState active={session.active} />
            <span className="text-xs font-bold tracking-[0.07em] text-text-dim uppercase">
              {session.experiment_kind}
            </span>
          </div>
          <h2 className="m-0 text-[1.05rem] tracking-[-0.02em]">{session.title}</h2>
          <code className="text-xs text-text-dim">{session.experiment_id}</code>
          <div className="break-all text-xs text-text-dim">会话：{session.session_id}</div>
        </div>
        <span className="inline-flex items-center gap-1.5 text-xs text-text-dim">
          {session.pending_request_count > 0 && (
            <LoaderCircle className="animate-spin" size={13} aria-hidden="true" />
          )}
          {session.pending_request_count > 0
            ? `${session.pending_request_count} 个编译请求等待完成`
            : session.active
              ? "编译器就绪"
              : "编译器已断开"}
        </span>
      </header>

      <p className="mb-3 text-xs leading-5 text-text-dim">
        Notebook 编译检查 ·
        同一会话的所有窗口共享请求与结果，最新请求优先；已开始的旧编译不保证立即停止。
        关闭窗口不会关闭 Notebook handle。检查结果不代表正式执行的代码版本或环境。
      </p>
      {session.latest_request && (
        <div className="mb-3 break-all rounded-md border border-line p-3 text-xs" role="status">
          <strong>
            最新请求：
            {session.pending_request_count > 0
              ? "等待完成"
              : session.latest_result?.request_id === session.latest_request.request_id
                ? "已完成"
                : "未完成（会话已断开）"}
          </strong>
          <div>请求 {session.latest_request.request_id}</div>
          <div>
            {session.latest_request.point_index != null
              ? `计划点 #${session.latest_request.point_index + 1}`
              : formatCoordinateMapping(session.latest_request.coordinates ?? {})}{" "}
            · {modeLabels[session.latest_request.coordinate_mode]}
          </div>
          {session.latest_request.inspection_query && (
            <div>程序层：{session.latest_request.inspection_query.layer_id}</div>
          )}
        </div>
      )}
      <PointCompiler
        key={pointCompilerKey(session)}
        compileError={compile.error}
        compiling={compile.isPending}
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

  const [inputError, setInputError] = useState<string>();
  const submit = () => {
    try {
      setInputError(undefined);
      onCompile({
        coordinates: Object.fromEntries(
          session.coordinates.map((spec) => [
            spec.id,
            parseCoordinate(spec, coordinates[spec.id] ?? ""),
          ]),
        ),
        coordinate_mode: mode,
      });
    } catch (error) {
      setInputError(errorMessage(error));
    }
  };

  return (
    <div className="mb-4 rounded-md border border-line bg-panel-soft p-3.5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <strong className="text-xs">编译选点</strong>
          <p className="mt-1 mb-0 text-xs leading-5 text-text-dim">
            选择计划点或输入坐标，编译后查看程序与波形；此操作不执行采集。
          </p>
        </div>
        <div className="flex gap-1 rounded-md border border-line bg-panel p-1" role="group">
          {(["exact", "snap", "free"] as const).map((item) => (
            <button
              className={classes(
                "cursor-pointer rounded px-2.5 py-1.5 text-xs font-bold capitalize text-text-dim",
                mode === item && "bg-panel-strong text-text",
              )}
              aria-pressed={mode === item}
              key={item}
              onClick={() => setMode(item)}
              type="button"
            >
              {modeLabels[item]}
            </button>
          ))}
        </div>
      </div>

      {session.planned_points.length > 0 && (
        <Controls.Field className="mb-2.5 grid max-w-[420px] gap-1.5 text-xs font-bold text-text-dim">
          从计划点填入
          <Controls.Select
            className="font-medium"
            onChange={(event) => {
              const point = session.planned_points[Number(event.target.value)];
              if (point) {
                setCoordinates(coordinateDraft(session.coordinates, point.coordinates));
              }
            }}
            defaultValue=""
          >
            <option value="" disabled>
              请选择…
            </option>
            {session.planned_points.map((point, index) => (
              <option key={point.point_index ?? "candidate"} value={index}>
                #{(point.point_index ?? 0) + 1} · {formatCoordinateMapping(point.coordinates)}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
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
          {compiling ? "正在提交请求…" : "编译波形"}
        </button>
        <span className="text-xs text-text-dim">
          {mode === "exact"
            ? "拒绝扫描范围以外的值。"
            : mode === "snap"
              ? "将各坐标匹配到最近的计划扫描值。"
              : "允许扫描范围以外的有效坐标。"}
        </span>
        {(inputError || compileError) && (
          <span className="w-full text-xs text-red" role="alert">
            提交失败：{inputError ?? errorMessage(compileError)}
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
      <Controls.Field className="grid gap-1.5 text-xs font-bold text-text-dim">
        {spec.id}
        <Controls.Select onChange={(event) => onChange(event.target.value)} value={value}>
          <option value="true">true</option>
          <option value="false">false</option>
        </Controls.Select>
      </Controls.Field>
    );
  }
  return (
    <Controls.Field className="grid gap-1.5 text-xs font-bold text-text-dim">
      <span className="flex justify-between gap-2">
        {spec.id}
        {spec.unit && <span className="font-medium text-text-dim">{spec.unit}</span>}
      </span>
      <Controls.Input
        className="font-medium"
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
              aria-label={`${spec.id} 计划值 ${index + 1}`}
              key={index}
              value={coordinateInputValue(planned)}
            />
          ))}
        </datalist>
      )}
    </Controls.Field>
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
  if (!result) return <WorkspaceMessage title="尚无编译结果" />;
  const point = result.point;
  return (
    <div className="grid gap-3">
      <div
        className="break-all rounded-md border border-line bg-panel-soft p-3 text-xs"
        role="status"
      >
        <strong>
          {session.pending_request_count > 0 ? "上一结果（新请求尚未完成）" : "最近完成的结果"} ·{" "}
          {result.error ? "编译失败" : "编译完成"}
        </strong>
        <div>结果请求：{result.request_id}</div>
        <div>
          完成时间：<time dateTime={result.completed_at}>{result.completed_at}</time>
        </div>
      </div>
      {result.error ? (
        <WorkspaceMessage title="编译失败" detail={result.error} />
      ) : !point ? (
        <WorkspaceMessage title="结果没有选点信息" />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-panel-soft px-3.5 py-2.5">
            <div>
              <span className="text-xs font-bold tracking-[0.06em] text-text-dim uppercase">
                {point.point_index == null ? "非计划候选点" : `计划点 #${point.point_index + 1}`}
              </span>
              <div className="mt-1 text-xs font-semibold text-text-soft">
                {formatCoordinateMapping(point.coordinates)}
              </div>
            </div>
            {point.proposal_fingerprint && (
              <code className="max-w-[350px] truncate text-xs text-text-dim">
                {point.proposal_fingerprint}
              </code>
            )}
          </div>
          <CompiledInspectionView
            inspections={result.inspections}
            onProgramQuery={
              !session.active
                ? undefined
                : (inspectionQuery) => onCompile(inspectionQueryCommand(point, inspectionQuery))
            }
          />
        </>
      )}
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
  return JSON.stringify([session.session_id, session.coordinates]);
}

function SessionState({ active }: { active: boolean }) {
  return (
    <span
      className={classes(
        "inline-flex items-center gap-1 text-xs font-bold uppercase",
        active ? "text-accent" : "text-text-dim",
      )}
    >
      {active ? <Radio size={10} aria-hidden="true" /> : <CircleOff size={10} aria-hidden="true" />}
      {active ? "在线" : "已断开"}
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
    <div className="break-words grid min-h-[420px] place-content-center justify-items-center p-8 text-center">
      <span className="mb-3 grid size-12 place-items-center rounded-xl border border-line bg-panel-soft text-text-dim">
        {pending ? <LoaderCircle className="animate-spin" /> : <Waves />}
      </span>
      <h2 className="text-lg font-semibold text-text">{title}</h2>
      {detail && <p className="mt-2 max-w-[460px] text-xs leading-5 text-text-dim">{detail}</p>}
    </div>
  );
}

function reviewIdFromLocation(): string | undefined {
  const match = window.location.hash.match(/^#reviews\/(.+)$/);
  const encoded = match?.[1];
  if (!encoded) return undefined;
  try {
    return decodeURIComponent(encoded);
  } catch {
    return encoded;
  }
}

function replaceReviewLocation(sessionId: string): void {
  const location = new URL(window.location.href);
  location.hash = `reviews/${encodeURIComponent(sessionId)}`;
  window.history.replaceState(
    window.history.state,
    "",
    `${location.pathname}${location.search}${location.hash}`,
  );
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function subscribeReviewLocation(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  window.addEventListener("popstate", onChange);
  return () => {
    window.removeEventListener("hashchange", onChange);
    window.removeEventListener("popstate", onChange);
  };
}
