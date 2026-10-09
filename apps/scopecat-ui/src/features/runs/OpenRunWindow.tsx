import { useMutation } from "@tanstack/react-query";
import { useDesktopAvailable } from "../application/DesktopSession";
import { secondaryButton } from "../../ui/styles";

export function OpenRunWindow({ runId }: { runId: string }) {
  const desktop = useDesktopAvailable();
  const open = useMutation({ mutationFn: () => window.pywebview!.api.open_run_window(runId) });
  // Only the exact retained run crosses the window boundary, never drafts or selection state.
  const href = `/?${new URLSearchParams({ run: runId })}`;
  return (
    <div title="View this exact run independently. Each window keeps its own selection.">
      {desktop ? (
        <button className={secondaryButton} disabled={open.isPending} onClick={() => open.mutate()}>
          Open result in new window
        </button>
      ) : (
        <a className={secondaryButton} href={href} target="_blank" rel="noopener noreferrer">
          Open result in new tab or window
        </a>
      )}
      {open.error && <p role="alert">{open.error.message}</p>}
    </div>
  );
}
