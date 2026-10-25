import * as Controls from "../../ui/Controls";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { errorMessage } from "../../lib/presentation";
import { getDevices } from "../instruments/device-api";
import { ConfigurationTemplatesPanel } from "./ConfigurationTemplatesPanel";
import { getSetupDefinitions, saveSetupDefinition, type SetupDefinition } from "./setup-api";

export function SetupPanel({
  operator,
  resolveOperator,
  operatorOverride,
  onSelectConfiguration,
}: {
  operator: string;
  resolveOperator?: () => Promise<string>;
  operatorOverride?: string;
  onSelectConfiguration?: (choice: components["schemas"]["ConfigurationChoice-Input"]) => void;
}) {
  const cache = useQueryClient();
  const definitions = useQuery({
    queryKey: ["setup-definitions"],
    queryFn: ({ signal }) => getSetupDefinitions(signal),
  });
  const devices = useQuery({ queryKey: ["devices"], queryFn: ({ signal }) => getDevices(signal) });
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [draft, setDraft] = useState<SetupDefinition>();
  const [alias, setAlias] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const refresh = async () => {
    await cache.invalidateQueries({ queryKey: ["setup-definitions"] });
    await cache.invalidateQueries({ queryKey: ["setup-revisions"] });
  };
  const attempt = useRef<{ key: string; actor: string } | undefined>(undefined);
  const save = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("请选择要编辑的 setup。");
      const key = JSON.stringify({
        draft,
        name: name.trim(),
        actor: resolveOperator ? (operatorOverride ?? null) : operator,
      });
      if (attempt.current?.key !== key)
        attempt.current = { key, actor: resolveOperator ? await resolveOperator() : operator };
      return saveSetupDefinition(draft, name.trim(), attempt.current.actor);
    },
    onSuccess: async (saved) => {
      attempt.current = undefined;
      setSelected(saved.resolution.definition_id);
      setDraft(undefined);
      setName("");
      await refresh();
    },
  });
  const choose = (id: string) => {
    setSelected(id);
    save.reset();
    const definition = definitions.data?.items.find((item) => item.id === id)?.definition;
    setDraft(
      definition
        ? structuredClone(definition)
        : { topology: {}, instruments: [], routing: {}, domain_target: null },
    );
    setName(id ? `${id} (revised)` : "");
  };
  return (
    <section
      aria-label="实验 setup"
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
    >
      <h3 className="text-base font-semibold leading-6">实验 setup</h3>
      <p>setup 将实验角色和通道映射到已登记设备。地址和设备安全设置请在“设备与驱动”中编辑。</p>
      <ConfigurationTemplatesPanel
        actor={operator}
        resolveOperator={resolveOperator}
        onSelectConfiguration={onSelectConfiguration}
        onImported={async (result) => {
          setSelected(result.setup.resolution.definition_id);
          setDraft(undefined);
          await refresh();
          await cache.invalidateQueries({ queryKey: ["devices"] });
        }}
      />
      <Controls.Field>
        已保存的 setup
        <Controls.Select value={selected} onChange={(event) => choose(event.target.value)}>
          <option value="">选择 setup</option>
          {definitions.data?.items.map((item) => (
            <option key={item.id} value={item.id}>
              {item.id}
            </option>
          ))}
        </Controls.Select>
      </Controls.Field>
      <Controls.Button onClick={() => choose("")}>新建 setup</Controls.Button>
      {draft && (
        <>
          <Controls.Field>
            另存为
            <Controls.Input value={name} onChange={(event) => setName(event.target.value)} />
          </Controls.Field>
          {draft.instruments.map((binding, index) => (
            <fieldset className="grid gap-2 rounded border border-line p-3" key={binding.id}>
              <legend>{binding.id}</legend>
              <Controls.Field>
                已登记设备
                <Controls.Select
                  value={binding.device_id}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      instruments: draft.instruments.map((item, position) =>
                        position === index ? { ...item, device_id: event.target.value } : item,
                      ),
                    })
                  }
                >
                  {devices.data?.items.map((item) => (
                    <option
                      key={item.device.id}
                      value={item.device.id}
                      disabled={item.device.state === "retired"}
                    >
                      {item.device.label}
                      {item.device.state === "retired" ? " · 已退役" : ""}
                    </option>
                  ))}
                </Controls.Select>
              </Controls.Field>
              <Controls.Field>
                任务成功后
                <Controls.Select
                  value={binding.success_action}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      instruments: draft.instruments.map((item, position) =>
                        position === index
                          ? {
                              ...item,
                              success_action: event.target.value as typeof binding.success_action,
                            }
                          : item,
                      ),
                    })
                  }
                >
                  <option value="release">保留最终状态</option>
                  <option value="restore_baseline">恢复初始状态</option>
                  <option value="apply_safe_state">应用设备安全状态</option>
                </Controls.Select>
              </Controls.Field>
              <Controls.Field>
                失败后
                <Controls.Select
                  value={binding.failure_action}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      instruments: draft.instruments.map((item, position) =>
                        position === index
                          ? {
                              ...item,
                              failure_action: event.target.value as typeof binding.failure_action,
                            }
                          : item,
                      ),
                    })
                  }
                >
                  <option value="abort_and_release">中止并释放</option>
                  <option value="abort_then_safe_state">中止并应用设备安全状态</option>
                </Controls.Select>
              </Controls.Field>
              <p>
                启动时： {binding.run_start === "preserve" ? "保留观测状态" : "应用实验默认值"}
                。设备安全要求仍必须遵守。
              </p>
              {(draft.routing.routes ?? [])
                .filter((route) => route.instrument_id === binding.id)
                .map((route) => (
                  <p key={route.id}>
                    路由 {route.id}:{" "}
                    {route.endpoints
                      .map((endpoint) => endpoint.channel_id ?? endpoint.interface_id)
                      .join(", ")}
                  </p>
                ))}
            </fieldset>
          ))}
          <fieldset className="flex flex-wrap gap-2 border border-line p-3">
            <legend>添加设备绑定</legend>
            <Controls.Field>
              实验别名
              <Controls.Input value={alias} onChange={(event) => setAlias(event.target.value)} />
            </Controls.Field>
            <Controls.Field>
              设备
              <Controls.Select
                value={deviceId}
                onChange={(event) => setDeviceId(event.target.value)}
              >
                <option value="">选择设备</option>
                {devices.data?.items
                  .filter((item) => item.device.state === "available")
                  .map((item) => (
                    <option key={item.device.id} value={item.device.id}>
                      {item.device.label}
                    </option>
                  ))}
              </Controls.Select>
            </Controls.Field>
            <Controls.Button
              disabled={
                !alias.trim() ||
                !deviceId ||
                draft.instruments.some(
                  (item) => item.id === alias.trim() || item.device_id === deviceId,
                )
              }
              onClick={() => {
                const safety = devices.data?.items.find((item) => item.device.id === deviceId)
                  ?.revision.content.safety;
                setDraft({
                  ...draft,
                  instruments: [
                    ...draft.instruments,
                    {
                      id: alias.trim(),
                      device_id: deviceId,
                      default_state: [],
                      run_start: "preserve",
                      success_action: safety?.require_safe_success ? "apply_safe_state" : "release",
                      failure_action: safety?.require_safe_failure
                        ? "abort_then_safe_state"
                        : "abort_and_release",
                    },
                  ],
                });
                setAlias("");
              }}
            >
              添加绑定
            </Controls.Button>
          </fieldset>
          <Controls.Button
            disabled={!name.trim() || !operator.trim() || save.isPending}
            onClick={() => save.mutate()}
          >
            保存 setup
          </Controls.Button>
          <Controls.Button onClick={() => setDraft(undefined)}>取消编辑</Controls.Button>
          <p>保存会创建命名定义。准备实验时解析当前设备连接；已提交任务保留其精确快照。</p>
        </>
      )}
      {(definitions.error || devices.error || save.error) && (
        <p role="alert">{errorMessage(definitions.error ?? devices.error ?? save.error)}</p>
      )}
    </section>
  );
}
