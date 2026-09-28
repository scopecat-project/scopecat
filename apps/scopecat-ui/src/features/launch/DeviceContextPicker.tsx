import { useQuery } from "@tanstack/react-query";
import { getSetupRevisions } from "../config/setup-api";
import type { ScientificSelection } from "./scientific-selection";

type Parameters = Extract<ScientificSelection["configuration"], { kind: "parameters" }>;

export function DeviceContextPicker({
  value,
  projectId,
  disabled,
  onChange,
}: {
  value: Parameters;
  projectId?: string;
  disabled?: boolean;
  onChange: (choice: Parameters) => void;
}) {
  const revisions = useQuery({
    queryKey: ["setup-revisions", projectId],
    queryFn: ({ signal }) => getSetupRevisions(signal),
  });
  const items = revisions.data?.items ?? [];
  return (
    <div className="space-y-2">
      <label className="flex flex-col gap-1">
        Device context
        <select
          aria-label="Device context"
          className="border rounded p-2"
          value={value.setup?.revision_id ?? ""}
          disabled={disabled || revisions.isPending || revisions.isError}
          onChange={(event) => {
            const selected = items.find((item) => item.id === event.target.value);
            if (selected)
              onChange({
                ...value,
                setup: {
                  revision_id: selected.id,
                  content_hash: selected.content_hash,
                },
              });
          }}
        >
          <option value="" disabled>
            Choose devices for this page
          </option>
          {value.setup && !items.some((item) => item.id === value.setup?.revision_id) && (
            <option value={value.setup.revision_id}>
              {value.setup.revision_id} · retained selection
            </option>
          )}
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        disabled={disabled || revisions.isFetching}
        onClick={() => void revisions.refetch()}
      >
        Refresh device contexts
      </button>
      {revisions.error && <p role="alert">{revisions.error.message}</p>}
      {revisions.isSuccess && items.length === 0 && (
        <p>No device contexts saved yet. Save a setup in Configuration.</p>
      )}
      <p className="text-sm">
        Applies to this page. Selection does not connect devices or change submitted work.
      </p>
    </div>
  );
}
