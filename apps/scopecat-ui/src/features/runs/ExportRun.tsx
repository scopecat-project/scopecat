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
        导出 Scopecat 文件…
      </button>
      {save.isPending && <p role="status">正在准备导出并保存文件…</p>}
      {save.data && <p role="status">已保存到 {save.data}</p>}
      {save.error && <p role="alert">{save.error.message}</p>}
    </div>
  );
}
