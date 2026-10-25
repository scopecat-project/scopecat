import { useLocationUrl, navigate } from "../../lib/navigation";
import { Button } from "../../ui/Controls";
import { ProcedureHistory } from "./ProcedureHistory";
import { ProcedureProgress } from "./ProcedureProgress";

/** Inspection has its own identity; only an explicit monitor action rebinds it. */
export function SubmittedWork({
  projectId,
  onOpenMonitor,
}: {
  projectId?: string;
  onOpenMonitor: (id: string) => void;
}) {
  const location = useLocationUrl();
  const inspectedId = location.searchParams.get("submitted_task") ?? "";
  return (
    <section
      aria-label="已提交任务检查"
      className="grid min-w-0 items-start gap-4 lg:grid-cols-[minmax(16rem,1fr)_minmax(0,2fr)]"
    >
      <ProcedureHistory mode="submitted" selectedId={inspectedId} onSelect={inspect} />
      <div className="min-w-0 space-y-3">
        {inspectedId ? (
          <>
            <header className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="min-w-0 break-all font-semibold">已提交任务 {inspectedId}</h3>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => onOpenMonitor(inspectedId)}>在监看中打开</Button>
                <Button onClick={() => inspect("")}>关闭检查</Button>
              </div>
            </header>
            <ProcedureProgress key={inspectedId} procedureId={inspectedId} projectId={projectId} />
          </>
        ) : (
          <p className="rounded-md border border-line p-4 text-text-dim">
            选择要检查的任务。监看仍固定在原任务；关闭检查不会停止执行。
          </p>
        )}
      </div>
    </section>
  );
}

function inspect(id: string) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("submitted_task", id);
  else url.searchParams.delete("submitted_task");
  navigate(url);
}
