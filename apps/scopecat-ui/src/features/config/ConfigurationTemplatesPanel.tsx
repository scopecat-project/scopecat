import * as Controls from "../../ui/Controls";
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { ExecutionScenario } from "../../ui/ExecutionScenario";
import { errorMessage } from "../../lib/presentation";
import { secondaryButton } from "../../ui/styles";
import { createConfigOperationId } from "./config-api";
import {
  getConfigurationTemplates,
  importConfigurationTemplate,
  type ConfigurationTemplateImportCommand,
  type ConfigurationTemplateImportResult,
} from "./setup-api";

export function ConfigurationTemplatesPanel({
  actor,
  resolveOperator,
  onImported,
  onSelectConfiguration,
}: {
  actor: string;
  resolveOperator?: () => Promise<string>;
  onImported: (result: ConfigurationTemplateImportResult) => Promise<void>;
  onSelectConfiguration?: (choice: components["schemas"]["ConfigurationChoice-Input"]) => void;
}) {
  const templates = useQuery({
    queryKey: ["setup", "templates"],
    queryFn: ({ signal }) => getConfigurationTemplates(signal),
  });
  const [selected, setSelected] = useState("");
  const [command, setCommand] = useState<ConfigurationTemplateImportCommand>();
  const imported = useMutation({
    mutationFn: async (input: ConfigurationTemplateImportCommand) => {
      const next = command ?? {
        ...input,
        actor: resolveOperator ? await resolveOperator() : input.actor,
      };
      setCommand(next);
      return importConfigurationTemplate(next);
    },
    onSuccess: (result) => onImported(result),
  });
  const candidate = templates.data?.items.find((item) => item.id === selected);
  const result = imported.data;
  const error = templates.error ?? imported.error;
  return (
    <section aria-label="配置模板" className="grid gap-2 rounded border border-line p-3">
      <h4>配置模板</h4>
      <p>从已安装模板导入 setup 和独立参数。复用已登记设备连接，模板不会覆盖它们。</p>
      <Controls.Field>
        可用模板{" "}
        <Controls.Select
          aria-label="可用模板"
          value={selected}
          disabled={imported.isPending}
          onChange={(event) => {
            setSelected(event.target.value);
            setCommand(undefined);
            imported.reset();
          }}
        >
          <option value="">选择模板</option>
          {templates.data?.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
        </Controls.Select>
      </Controls.Field>
      {templates.isPending && <p>正在加载模板…</p>}
      {templates.data?.items.length === 0 && <p>此 adapter 未声明配置模板。</p>}
      {candidate && (
        <>
          <p>{candidate.description}</p>
          <ExecutionScenario scenario={candidate.setup.scenario} label="模板场景" />
          <button
            className={secondaryButton}
            disabled={!actor.trim() || imported.isPending || Boolean(result)}
            onClick={() => {
              const next = command ?? {
                template_id: candidate.id,
                content_hash: candidate.content_hash,
                revision_id: createConfigOperationId("template"),
                actor: actor.trim(),
                note: "",
              };
              imported.mutate(next);
            }}
          >
            {imported.isPending
              ? "正在导入模板…"
              : command && imported.isError
                ? "重试导入模板"
                : "导入配置模板"}
          </button>
        </>
      )}
      {result && (
        <div role="status">
          <p>
            已导入 setup <code>{result.setup.resolution.definition_id}</code> 及参数{" "}
            <code>{result.parameters.id}</code>.
          </p>
          <p>可直接用于下次实验，也可先编辑。其他页面和已提交任务保留原先选择的输入。</p>
          {onSelectConfiguration && (
            <button
              className={secondaryButton}
              onClick={() =>
                onSelectConfiguration({
                  kind: "parameters",
                  overrides: [],
                  ref: {
                    revision_id: result.parameters.id,
                    content_hash: result.parameters.content_hash,
                  },
                  setup: { revision_id: result.setup.id, content_hash: result.setup.content_hash },
                })
              }
            >
              将导入配置用于下次实验
            </button>
          )}
        </div>
      )}
      {error && <p role="alert">{errorMessage(error)}</p>}
    </section>
  );
}
