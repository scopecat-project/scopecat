import { navigateLink, useLocationUrl } from "../../lib/navigation";

/** Return to the editable draft without restoring historical inputs or submitting work. */
export function ReturnToPreparation() {
  const location = useLocationUrl();
  const query = new URLSearchParams({ preparation: "1" });
  const procedure = location.searchParams.get("procedure");
  if (procedure) query.set("procedure", procedure);
  return (
    <a
      className="underline"
      href={`?${query}#launch`}
      onClick={(event) => {
        const alreadyHere = event.currentTarget.href === window.location.href;
        navigateLink(event);
        if (alreadyHere && event.defaultPrevented) {
          const preparation = document.getElementById("experiment-preparation")?.closest("section");
          preparation?.focus({ preventScroll: true });
          preparation?.scrollIntoView?.({ block: "start", behavior: "smooth" });
        }
      }}
    >
      Return to preparation
    </a>
  );
}
