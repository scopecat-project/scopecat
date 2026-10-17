import { Field, Select, Notice } from "../../ui/Controls";
import { secondaryButton } from "../../ui/styles";
import { useQuery, useMutation } from "@tanstack/react-query";
import { getSetupDefinitions, getSetupRevision, resolveSetupDefinition } from "../config/setup-api";
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
    queryFn: ({ signal }) => getSetupDefinitions(signal),
  });
  const resolution = useMutation({
    mutationFn: resolveSetupDefinition,
    onSuccess: (selected) => {
      // Resolving the same retained setup does not change the launch input.
      // In particular, it must not cancel a preview waiting on its working table.
      if (
        value.setup?.revision_id === selected.id &&
        value.setup.content_hash === selected.content_hash
      )
        return;
      onChange({
        ...value,
        setup: { revision_id: selected.id, content_hash: selected.content_hash },
      });
    },
  });
  const retained = useQuery({
    queryKey: ["setup-revision", value.setup?.revision_id],
    queryFn: ({ signal }) => getSetupRevision(value.setup!.revision_id, signal),
    enabled: !!value.setup,
    staleTime: Infinity,
  });
  const selectedDefinition = retained.data?.resolution.definition_id;
  const items = revisions.data?.items ?? [];
  return (
    <div className="space-y-2">
      <Field className="flex-col">
        Experiment setup
        <Select
          aria-label="Experiment setup"
          value={selectedDefinition ?? ""}
          disabled={disabled || revisions.isPending || revisions.isError || resolution.isPending}
          onChange={(event) => {
            const selected = items.find((item) => item.id === event.target.value);
            if (selected) resolution.mutate(selected.id);
          }}
        >
          <option value="" disabled>
            Choose experiment roles and devices
          </option>
          {items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id}
            </option>
          ))}
        </Select>
      </Field>
      <button
        className={`${secondaryButton} mr-2`}
        type="button"
        disabled={disabled || revisions.isFetching}
        onClick={() => void revisions.refetch()}
      >
        Refresh setups
      </button>
      {revisions.error && <Notice role="alert">{revisions.error.message}</Notice>}
      {resolution.error && <Notice role="alert">{resolution.error.message}</Notice>}
      {retained.error && <Notice role="alert">{retained.error.message}</Notice>}
      {selectedDefinition && (
        <button
          className={`${secondaryButton} mr-2`}
          type="button"
          disabled={disabled || resolution.isPending}
          onClick={() => resolution.mutate(selectedDefinition)}
        >
          Recheck device connections
        </button>
      )}
      {revisions.isSuccess && items.length === 0 && (
        <p>No setups saved yet. Create one in Configuration.</p>
      )}
      <p className="text-sm">
        Applies to this page. Selection does not connect devices or change submitted work.
      </p>
    </div>
  );
}
