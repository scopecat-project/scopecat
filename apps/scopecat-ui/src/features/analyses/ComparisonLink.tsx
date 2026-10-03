export function ComparisonLink({ runId }: { runId?: string }) {
  const target = `?${runId ? `compare=${encodeURIComponent(runId)}` : ""}#analyses`;
  return (
    <a
      className="text-blue underline"
      href={target}
      onClick={(event) => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        event.preventDefault();
        navigate(target);
      }}
    >
      Compare retained runs
    </a>
  );
}
import { navigate } from "../../lib/navigation";
