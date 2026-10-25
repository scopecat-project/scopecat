import { withDraftContext } from "../launch/draft-location";

export function ComparisonLink({ runId }: { runId?: string }) {
  const query = new URLSearchParams();
  if (runId) query.set("compare", runId);
  const procedure = new URL(window.location.href).searchParams.get("procedure");
  if (procedure) query.set("procedure", procedure);
  const target = withDraftContext(`?${query}#analyses`);
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
