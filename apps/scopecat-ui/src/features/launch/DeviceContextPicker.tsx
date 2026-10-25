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
        实验配置
        <Select
          aria-label="实验配置"
          value={selectedDefinition ?? ""}
          disabled={disabled || revisions.isPending || revisions.isError || resolution.isPending}
          onChange={(event) => {
            const selected = items.find((item) => item.id === event.target.value);
            if (selected) resolution.mutate(selected.id);
          }}
        >
          <option value="" disabled>
            选择实验角色与设备
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
        刷新实验配置
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
          重新检查设备连接
        </button>
      )}
      {revisions.isSuccess && items.length === 0 && <p>暂无已保存配置，请在参数与绑定中创建。</p>}
      <p className="text-sm">仅应用于本页。选择不会连接设备，也不会改变已提交任务。</p>
    </div>
  );
}
