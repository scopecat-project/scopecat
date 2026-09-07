import type { LaunchCatalogEntry, LaunchPreview } from "./launch-api";

export function PreflightSummary({
  entry,
  preview,
}: {
  entry: LaunchCatalogEntry;
  preview: LaunchPreview;
}) {
  return (
    <div role="status" className="space-y-2">
      <h3 className="font-semibold">Preview ready</h3>
      <p>{preview.summary}</p>
      <p>
        {preview.point_count} previewed experiment points · Configuration{" "}
        {preview.config_source.entry_id}
      </p>
      <p>
        {entry.configuration_effect === "none"
          ? "This procedure does not change the default configuration."
          : entry.configuration_effect === "candidate"
            ? "Produces a candidate. Accepting it as default is a separate operator action."
            : "Changes the default configuration only after review."}
      </p>
      {entry.review && <p>{entry.review.instructions}</p>}
      <details>
        <summary>Resolved inputs and configuration</summary>
        <pre className="overflow-auto text-xs p-3">
          {JSON.stringify(
            { inputs: preview.resolved_inputs, config_source: preview.config_source },
            null,
            2,
          )}
        </pre>
      </details>
    </div>
  );
}
