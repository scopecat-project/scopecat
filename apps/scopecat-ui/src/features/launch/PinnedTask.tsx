import { navigate, useLocationUrl } from "../../lib/navigation";
import { useLaunchDraft } from "./LaunchDraft";
import { procedurePhase } from "./procedure-operator";
import { useProcedureOperator } from "./use-procedure-operator";
import { OpenRunWindow } from "../runs/OpenRunWindow";

export function PinnedTask() {
  const location = useLocationUrl();
  const { draft } = useLaunchDraft();
  const procedureId = location.searchParams.get("procedure") ?? draft?.admittedProcedureId ?? "";
  const query = useProcedureOperator(procedureId);
  if (!procedureId) return null;
  const view = query.data?.pages[0];
  return (
    <section className="workspace-task-strip" aria-label="Fixed task status">
      <span title={procedureId}>
        <strong>固定任务</strong> · …{procedureId.slice(-8)}
      </span>
      <span role={query.isError ? "alert" : "status"}>
        {query.isError
          ? `状态未更新 · 最后已知：${view ? procedurePhase(view) : "未知"}`
          : view
            ? procedurePhase(view)
            : "读取运行状态…"}
      </span>
      {draft && (
        <small className="workspace-draft-label">
          编辑：{draft.draftName ?? draft.experiment} · r{draft.revision}
        </small>
      )}
      <button
        onClick={() => {
          const url = new URL(window.location.href);
          url.searchParams.set("procedure", procedureId);
          url.searchParams.delete("preparation");
          url.searchParams.delete("workbench");
          url.hash = "launch";
          navigate(url);
        }}
        aria-label="View fixed monitor"
      >
        查看监控
      </button>
      {view?.current_child && (
        <OpenRunWindow
          runId={view.current_child.run.control.admission.run_id}
          label="独立监控窗口"
        />
      )}
    </section>
  );
}
