import { useMutation } from "@tanstack/react-query";
import { useDesktopAvailable } from "../application/DesktopSession";
import { secondaryButton } from "../../ui/styles";

export function OpenRunWindow({ runId, label }: { runId: string; label?: string }) {
  const desktop = useDesktopAvailable();
  const open = useMutation({ mutationFn: () => window.pywebview!.api.open_run_window(runId) });
  // Only the exact retained run crosses the window boundary, never drafts or selection state.
  const href = `/?${new URLSearchParams({ run: runId, focus: "run" })}#runs`;
  return (
    <div title="独立查看此精确 run，每个窗口保留自己的选择。">
      {desktop ? (
        <button className={secondaryButton} disabled={open.isPending} onClick={() => open.mutate()}>
          {label ?? "在新窗口打开结果"}
        </button>
      ) : (
        <a className={secondaryButton} href={href} target="_blank" rel="noopener noreferrer">
          {label ?? "在新标签页或窗口打开结果"}
        </a>
      )}
      {open.error && <p role="alert">{open.error.message}</p>}
    </div>
  );
}
