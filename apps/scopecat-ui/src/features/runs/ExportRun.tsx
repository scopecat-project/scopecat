import { useMutation } from "@tanstack/react-query";
import { useDesktopAvailable } from "../application/DesktopSession";
import { secondaryButton } from "../../ui/styles";

export function ExportRun({ runId }: { runId: string }) {
  const desktop = useDesktopAvailable();
  const save = useMutation({ mutationFn: () => window.pywebview!.api.export_run(runId) });
  if (!desktop) return null;
  return (
    <div>
      <button className={secondaryButton} disabled={save.isPending} onClick={() => save.mutate()}>
        Export Scopecat file…
      </button>
      {save.isPending && <p role="status">Preparing export and saving file…</p>}
      {save.data && <p role="status">Saved to {save.data}</p>}
      {save.error && <p role="alert">{save.error.message}</p>}
    </div>
  );
}
