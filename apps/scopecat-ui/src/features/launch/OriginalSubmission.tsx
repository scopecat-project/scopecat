import { SubmissionComparison } from "./SubmissionComparison";
import { useEffect, useRef } from "react";
import { secondaryButton } from "../../ui/styles";
import { subjectLabel } from "./scientific-selection";
import { useLaunchDraft } from "./LaunchDraft";

export function OriginalSubmission({
  onOpen,
  onPrepare,
  catalogReady: _catalogReady,
}: {
  onOpen: (id: string) => void;
  onPrepare?: () => void;
  catalogReady: boolean;
}) {
  const { attempt, draft, checkSubmission, rerun } = useLaunchDraft();
  const panel = useRef<HTMLElement>(null);
  useEffect(() => {
    if (attempt?.status === "unknown" || attempt?.status === "rejected") {
      panel.current?.focus({ preventScroll: true });
      panel.current?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    }
  }, [attempt?.request.request_key, attempt?.status]);
  if (!attempt) return null;
  return (
    <section
      ref={panel}
      tabIndex={-1}
      className={`scroll-mt-24 border-b py-2 space-y-2 break-words ${attempt.status === "confirmed" ? "border-line" : "border-accent/40 bg-panel-soft p-3"}`}
      aria-label="原始提交"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">
          {attempt.status === "confirmed"
            ? "提交已确认"
            : attempt.status === "rejected"
              ? "提交被拒绝"
              : "原始提交待确认"}
        </h3>
        <div className="flex flex-wrap gap-2">
          {attempt.procedureId && (
            <button
              className={secondaryButton}
              type="button"
              onClick={() => onOpen(attempt.procedureId!)}
            >
              查看已提交任务
            </button>
          )}
          <button
            className={secondaryButton}
            type="button"
            disabled={attempt.status === "pending"}
            onClick={() => {
              rerun();
              onPrepare?.();
            }}
          >
            准备新的 run（独立采集）
          </button>
        </div>
      </div>
      {attempt.error && <p role="alert">{attempt.error}</p>}
      {attempt.status === "unknown" && (
        <>
          <p>原始请求与可编辑草稿分别保留。再次采集前，请先核实原始请求是否已被接纳。</p>
          <button
            className={secondaryButton}
            type="button"
            disabled={attempt.checking}
            onClick={() => {
              void checkSubmission();
            }}
          >
            核实原始提交
          </button>
        </>
      )}
      {attempt.status === "pending" && <p role="status">正在等待原始提交响应…</p>}
      <details className="text-sm">
        <summary className="cursor-pointer text-text-dim">提交来源与请求详情</summary>
        <div className="space-y-2 py-2 select-text [overflow-wrap:anywhere]">
          <p>
            Workspace {attempt.request.workspace_id} · {attempt.request.experiment} · version{" "}
            {attempt.request.version} · {attempt.request.actor} ·
            {subjectLabel(attempt.request.selection)}
          </p>
          <p>
            请求 key： <code>{attempt.request.request_key}</code>
          </p>
        </div>
      </details>
      <SubmissionComparison request={attempt.request} draft={draft} />
    </section>
  );
}
