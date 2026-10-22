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
      aria-label="Submitted work inspection"
      className="grid min-w-0 items-start gap-4 lg:grid-cols-[minmax(16rem,1fr)_minmax(0,2fr)]"
    >
      <ProcedureHistory mode="submitted" selectedId={inspectedId} onSelect={inspect} />
      <div className="min-w-0 space-y-3">
        {inspectedId ? (
          <>
            <header className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="min-w-0 break-all font-semibold">Submitted task {inspectedId}</h3>
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => onOpenMonitor(inspectedId)}>Open in monitor</Button>
                <Button onClick={() => inspect("")}>Close inspection</Button>
              </div>
            </header>
            <ProcedureProgress key={inspectedId} procedureId={inspectedId} projectId={projectId} />
          </>
        ) : (
          <p className="rounded-md border border-line p-4 text-text-dim">
            Select submitted work to inspect its progress, resource waits and actions here. Your
            monitor keeps its selected task. Closing this inspection leaves background work running.
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
