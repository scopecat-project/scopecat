import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Button, Field, Input, Select, Notice, Textarea } from "../../ui/Controls";
import { errorMessage } from "../../lib/presentation";
import { saveSetupDefinition } from "../config/setup-api";
import {
  inspectBindings,
  bindingOptions,
  checkBindings,
  type BindingRequest,
  type BindingOptions,
  type BindingChoice,
  type BindingPlan,
  type BindingCheck,
} from "./instrument-bindings-api";

type Policies = Pick<BindingChoice, "run_start" | "success_action" | "failure_action">;
type Selection = { candidate: string } & Partial<Policies>;

// The parent keys this editor by the exact draft inputs/source. No candidate or
// in-flight response may carry over to another experiment or input revision.
export function ProgressiveBindings({
  request,
  disabled,
  actor,
  onChecked,
  onEdit,
  onSaved,
}: {
  request: () => BindingRequest;
  disabled: boolean;
  actor: string;
  onChecked: (plan: BindingPlan) => void;
  onEdit: () => void;
  onSaved?: () => void;
}) {
  const [options, setOptions] = useState<BindingOptions>();
  const [selections, setSelections] = useState<Record<string, Selection>>({});
  const [check, setCheck] = useState<BindingCheck>();
  const [busy, setBusy] = useState<false | "check" | "save">(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [saved, setSaved] = useState("");
  const [software, setSoftware] = useState(false);
  const [model, setModel] = useState("");
  const [version, setVersion] = useState("");
  const [label, setLabel] = useState("");
  const [limits, setLimits] = useState("");
  const [capabilities, setCapabilities] = useState("");
  const generation = useRef(0);
  const enabled = useRef(!disabled);
  useLayoutEffect(() => {
    enabled.current = !disabled;
    if (disabled) generation.current++;
  }, [disabled]);
  if (disabled && busy) setBusy(false);
  useEffect(
    () => () => {
      generation.current++;
    },
    [],
  );
  const edit = () => {
    generation.current++;
    setCheck(undefined);
    setSaved("");
    setError("");
    onEdit();
  };
  const update = (port: string, patch: Partial<Selection>) => {
    edit();
    setSelections((current) => ({
      ...current,
      [port]: { ...current[port], candidate: current[port]?.candidate ?? "", ...patch },
    }));
  };
  const perform = async (
    work: (current: () => boolean) => Promise<void>,
    kind: "check" | "save" = "check",
  ) => {
    const token = ++generation.current;
    const current = () => token === generation.current && enabled.current;
    setBusy(kind);
    setError("");
    try {
      await work(current);
    } catch (failure) {
      if (current()) setError(errorMessage(failure));
    } finally {
      if (current()) setBusy(false);
    }
  };
  const inspect = () => {
    edit();
    setOptions(undefined);
    setSelections({});
    void perform(async (current) => {
      const requirements = await inspectBindings(request());
      if (!current()) return;
      if (!requirements.supported) {
        setOptions({ requirements, candidates: [], diagnostics: requirements.diagnostics });
        return;
      }
      const result = await bindingOptions(requirements);
      if (current()) setOptions(result);
    });
  };
  const complete =
    options?.requirements.supported &&
    options.requirements.requirements.length > 0 &&
    options.requirements.requirements.every((port) => {
      const choice = selections[port.port_id];
      return (
        choice?.candidate && choice.run_start && choice.success_action && choice.failure_action
      );
    }) &&
    (!software ||
      (model.trim() && version.trim() && label.trim() && capabilities.trim() && limits.trim()));
  const plan = (): BindingPlan => ({
    requirements: options!.requirements,
    choices: options!.requirements.requirements.map((port) => {
      const selected = selections[port.port_id]!;
      const candidate = options!.candidates[Number(selected.candidate)];
      if (
        !candidate ||
        candidate.port_id !== port.port_id ||
        !selected.run_start ||
        !selected.success_action ||
        !selected.failure_action
      )
        throw new Error("请为每项需求明确选择设备、端点及结束策略。");
      return {
        port_id: port.port_id,
        device_id: candidate.device_id,
        connection_revision_id: candidate.connection_revision_id,
        component_path: candidate.component_path,
        run_start: selected.run_start,
        success_action: selected.success_action,
        failure_action: selected.failure_action,
      };
    }),
    scenario: software
      ? {
          kind: "software",
          id: model.trim(),
          model_id: model.trim(),
          model_version: version.trim(),
          label: label.trim(),
          capabilities: capabilities
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
          limitations: limits
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
        }
      : undefined,
  });
  return (
    <section aria-label="为本次实验选择设备" className="space-y-3 rounded border border-line p-3">
      <h4 className="font-semibold">为本次实验选择设备</h4>
      <p>还没有 setup？先查看这次实验需要什么，再选择设备和端点。不连接设备，也不会开始采集。</p>
      <fieldset disabled={disabled || Boolean(busy)} className="space-y-3">
        <Button type="button" onClick={inspect}>
          {options ? "重新检查需求与设备" : "检查本实验所需设备"}
        </Button>
        {options && !options.requirements.supported && (
          <Notice>
            这类实验暂不支持逐步绑定。请使用明确配置的 setup；不会推断对象、拓扑或嵌套模块。
          </Notice>
        )}
        {options?.requirements.requirements.map((port) => (
          <fieldset key={port.port_id} className="grid gap-2 border-t border-line pt-2">
            <legend>
              {port.role_id || "默认用途"} · {port.interface_id}
            </legend>
            <p className="text-sm">实验端口：{port.port_id}</p>
            <Field>
              设备与端点 · {port.port_id}
              <Select
                value={selections[port.port_id]?.candidate ?? ""}
                onChange={(event) =>
                  update(port.port_id, {
                    candidate: event.target.value,
                    run_start: undefined,
                    success_action: undefined,
                    failure_action: undefined,
                  })
                }
              >
                <option value="">请选择，不自动匹配</option>
                {options.candidates.map(
                  (candidate, index) =>
                    candidate.port_id === port.port_id && (
                      <option key={index} value={String(index)}>
                        {candidate.device_label} · {candidate.device_id} ·{" "}
                        {candidate.component_path?.join("/") || "整机端点"}
                      </option>
                    ),
                )}
              </Select>
            </Field>
            <Field>
              开始时 · {port.port_id}
              <Select
                value={selections[port.port_id]?.run_start ?? ""}
                onChange={(event) =>
                  update(port.port_id, { run_start: event.target.value as Policies["run_start"] })
                }
              >
                <option value="">选择开始策略</option>
                <option value="preserve">保留观测状态</option>
                <option value="apply_default_state">应用本绑定的默认状态（当前为空）</option>
              </Select>
            </Field>
            <Field>
              成功后 · {port.port_id}
              <Select
                value={selections[port.port_id]?.success_action ?? ""}
                onChange={(event) =>
                  update(port.port_id, {
                    success_action: event.target.value as Policies["success_action"],
                  })
                }
              >
                <option value="">选择成功策略</option>
                <option value="release">保留最终状态</option>
                <option value="restore_baseline">恢复初始状态</option>
                <option value="apply_safe_state">应用设备安全状态</option>
              </Select>
            </Field>
            <Field>
              失败后 · {port.port_id}
              <Select
                value={selections[port.port_id]?.failure_action ?? ""}
                onChange={(event) =>
                  update(port.port_id, {
                    failure_action: event.target.value as Policies["failure_action"],
                  })
                }
              >
                <option value="">选择失败策略</option>
                <option value="abort_and_release">中止并释放</option>
                <option value="abort_then_safe_state">中止并应用设备安全状态</option>
              </Select>
            </Field>
          </fieldset>
        ))}
        {options?.requirements.supported && (
          <>
            <Field>
              <Input
                type="checkbox"
                checked={software}
                onChange={(event) => {
                  edit();
                  setSoftware(event.target.checked);
                }}
              />
              这是软件模拟，需要记录模型及局限
            </Field>
            {software && (
              <div className="grid gap-2">
                <Field>
                  模拟名称
                  <Input
                    value={label}
                    onChange={(event) => {
                      edit();
                      setLabel(event.target.value);
                    }}
                  />
                </Field>
                <Field>
                  模型 ID
                  <Input
                    value={model}
                    onChange={(event) => {
                      edit();
                      setModel(event.target.value);
                    }}
                  />
                </Field>
                <Field>
                  模型版本
                  <Input
                    value={version}
                    onChange={(event) => {
                      edit();
                      setVersion(event.target.value);
                    }}
                  />
                </Field>
                <Field>
                  模拟能力（每行一项）
                  <Textarea
                    value={capabilities}
                    onChange={(event) => {
                      edit();
                      setCapabilities(event.target.value);
                    }}
                  />
                </Field>
                <Field>
                  模拟局限（每行一项）
                  <Textarea
                    value={limits}
                    onChange={(event) => {
                      edit();
                      setLimits(event.target.value);
                    }}
                  />
                </Field>
              </div>
            )}
            <Button
              type="button"
              disabled={!complete}
              onClick={() =>
                void perform(async (current) => {
                  setCheck(undefined);
                  onEdit();
                  const selected = plan();
                  const result = await checkBindings(selected);
                  if (current()) {
                    setCheck(result);
                    if (result.definition) onChecked(selected);
                  }
                })
              }
            >
              检查并用于本次准备
            </Button>
          </>
        )}
        {check?.definition && (
          <>
            <Notice>
              绑定检查通过。请预览或开始时再做完整准备，尚未提交采集。此选择只保留在当前打开的编辑器中。
            </Notice>
            <details>
              <summary>另存为可复用 setup（可选）</summary>
              <Field>
                复用名称
                <Input
                  value={name}
                  onChange={(event) => {
                    setName(event.target.value);
                    setSaved("");
                  }}
                />
              </Field>
              <Button
                type="button"
                disabled={!name.trim() || !actor.trim() || !!saved}
                onClick={() =>
                  void perform(async (current) => {
                    const result = await saveSetupDefinition(check.definition!, name.trim(), actor);
                    onSaved?.();
                    if (current()) setSaved(result.resolution.definition_id);
                  }, "save")
                }
              >
                保存为可复用 setup
              </Button>
              {saved && <Notice>已保存 {saved}。保存不会切换其他页面或开始实验。</Notice>}
            </details>
          </>
        )}
      </fieldset>
      {busy && (
        <Notice>
          {busy === "save"
            ? "正在保存 setup，请等待确认。保存不会开始采集。"
            : "正在读取声明或检查选择，不连接设备…"}
        </Notice>
      )}
      {busy === "check" && (
        <Button
          type="button"
          onClick={() => {
            generation.current++;
            setBusy(false);
          }}
        >
          取消检查
        </Button>
      )}
      {[...(options?.diagnostics ?? []), ...(check?.diagnostics ?? [])].map((item, index) => (
        <Notice key={index}>
          {item.port_id ? `${item.port_id}：` : ""}
          {item.device_id ? `${item.device_id}：` : ""}
          {item.message}
        </Notice>
      ))}
      {error && <Notice role="alert">{error}</Notice>}
    </section>
  );
}
