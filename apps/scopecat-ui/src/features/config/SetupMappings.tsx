import { useState } from "react";
import * as Controls from "../../ui/Controls";
import type { SetupDefinition } from "./setup-api";

export function mappingProblems(draft: SetupDefinition): string[] {
  const entities = new Set((draft.topology.entities ?? []).map((item) => item.id));
  const roles = new Set((draft.routing.roles ?? []).map((item) => item.id));
  const instruments = new Set(draft.instruments.map((item) => item.id));
  const problems: string[] = [];
  const claims = new Map<string, string>();
  for (const route of draft.routing.routes ?? []) {
    if (!instruments.has(route.instrument_id))
      problems.push(`${route.id}：缺少设备绑定 ${route.instrument_id}`);
    if (route.role_id && !roles.has(route.role_id))
      problems.push(`${route.id}：缺少用途 ${route.role_id}`);
    for (const entity of route.entity_ids ?? [])
      if (!entities.has(entity)) problems.push(`${route.id}：缺少对象 ${entity}`);
    for (const endpoint of route.endpoints) {
      for (const entity of endpoint.entity_id ? [endpoint.entity_id] : (route.entity_ids ?? [])) {
        const key = JSON.stringify([
          entity,
          route.role_id,
          endpoint.interface_id,
          endpoint.channel_id,
        ]);
        const previous = claims.get(key);
        if (previous && previous !== route.id)
          problems.push(
            `${entity} / ${route.role_id ?? "未指定用途"} 有多个候选：${previous}、${route.id}。准备实验时必须显式消除歧义。`,
          );
        claims.set(key, route.id);
      }
    }
  }
  for (const entity of entities)
    if (!(draft.routing.routes ?? []).some((route) => route.entity_ids?.includes(entity)))
      problems.push(`${entity}：尚未分配用途与设备通道`);
  return [...new Set(problems)];
}

export function SetupMappings({
  draft,
  onChange,
}: {
  draft: SetupDefinition;
  onChange: (value: SetupDefinition) => void;
}) {
  const [entity, setEntity] = useState("");
  const [role, setRole] = useState("");
  const [purpose, setPurpose] = useState("");
  const [routeId, setRouteId] = useState("");
  const [selectedEntity, setSelectedEntity] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const [instrument, setInstrument] = useState("");
  const [interfaceId, setInterfaceId] = useState("");
  const [channel, setChannel] = useState("");
  const [path, setPath] = useState("");
  const entities = draft.topology.entities ?? [];
  const roles = draft.routing.roles ?? [];
  const routes = draft.routing.routes ?? [];
  const problems = mappingProblems(draft);
  return (
    <section aria-label="对象与用途映射" className="grid gap-3 rounded border border-line p-3">
      <h4>对象 → 用途 → 设备通道</h4>
      <p>
        这是实验的逻辑能力绑定，不是物理接线图，也不声明同步或触发关系。设备端口和驱动能力必须与实际配置相符；替换后保存新修订并重新准备实验，旧运行保持原来的配置。
      </p>
      <fieldset className="flex flex-wrap gap-2">
        <legend>添加对象</legend>
        <Controls.Field>
          对象 ID（如 q0）
          <Controls.Input value={entity} onChange={(event) => setEntity(event.target.value)} />
        </Controls.Field>
        <Controls.Button
          disabled={!entity.trim() || entities.some((item) => item.id === entity.trim())}
          onClick={() => {
            onChange({
              ...draft,
              topology: {
                ...draft.topology,
                entities: [...entities, { id: entity.trim(), kind: "qubit" }],
              },
            });
            setSelectedEntity(entity.trim());
            setEntity("");
          }}
        >
          添加比特对象
        </Controls.Button>
      </fieldset>
      <p>当前对象：{entities.map((item) => item.id).join("、") || "尚无对象"}</p>
      <fieldset className="flex flex-wrap gap-2">
        <legend>添加用途</legend>
        <Controls.Field>
          用途 ID（如 drive）
          <Controls.Input value={role} onChange={(event) => setRole(event.target.value)} />
        </Controls.Field>
        <Controls.Field>
          用途说明
          <Controls.Input value={purpose} onChange={(event) => setPurpose(event.target.value)} />
        </Controls.Field>
        <Controls.Button
          disabled={!role.trim() || roles.some((item) => item.id === role.trim())}
          onClick={() => {
            onChange({
              ...draft,
              routing: {
                ...draft.routing,
                roles: [...roles, { id: role.trim(), description: purpose.trim() || null }],
              },
            });
            setSelectedRole(role.trim());
            setRole("");
            setPurpose("");
          }}
        >
          添加用途
        </Controls.Button>
      </fieldset>
      <fieldset className="grid gap-2">
        <legend>添加逻辑映射</legend>
        <Controls.Field>
          映射 ID
          <Controls.Input value={routeId} onChange={(event) => setRouteId(event.target.value)} />
        </Controls.Field>
        <Controls.Field>
          测量对象
          <Controls.Select
            value={selectedEntity}
            onChange={(event) => setSelectedEntity(event.target.value)}
          >
            <option value="">选择对象</option>
            {entities.map((item) => (
              <option key={item.id}>{item.id}</option>
            ))}
          </Controls.Select>
        </Controls.Field>
        <Controls.Field>
          逻辑用途
          <Controls.Select
            value={selectedRole}
            onChange={(event) => setSelectedRole(event.target.value)}
          >
            <option value="">选择用途</option>
            {roles.map((item) => (
              <option value={item.id} key={item.id}>
                {item.description ?? item.id}
              </option>
            ))}
          </Controls.Select>
        </Controls.Field>
        <Controls.Field>
          设备绑定
          <Controls.Select
            value={instrument}
            onChange={(event) => setInstrument(event.target.value)}
          >
            <option value="">选择已有绑定</option>
            {draft.instruments.map((item) => (
              <option key={item.id}>{item.id}</option>
            ))}
          </Controls.Select>
        </Controls.Field>
        <Controls.Field>
          能力接口 ID（从设备声明复制）
          <Controls.Input
            value={interfaceId}
            onChange={(event) => setInterfaceId(event.target.value)}
            placeholder="scopecat.rf_output/v2"
          />
        </Controls.Field>
        <Controls.Field>
          逻辑通道（可留空）
          <Controls.Input value={channel} onChange={(event) => setChannel(event.target.value)} />
        </Controls.Field>
        <Controls.Field>
          设备组件路径（/ 分隔，可留空）
          <Controls.Input value={path} onChange={(event) => setPath(event.target.value)} />
        </Controls.Field>
        <Controls.Button
          disabled={
            !routeId.trim() ||
            routes.some((item) => item.id === routeId.trim()) ||
            !selectedEntity ||
            !selectedRole ||
            !instrument ||
            !interfaceId.trim()
          }
          onClick={() => {
            onChange({
              ...draft,
              routing: {
                ...draft.routing,
                routes: [
                  ...routes,
                  {
                    id: routeId.trim(),
                    instrument_id: instrument,
                    role_id: selectedRole,
                    entity_ids: [selectedEntity],
                    endpoints: [
                      {
                        interface_id: interfaceId.trim(),
                        entity_id: selectedEntity,
                        channel_id: channel.trim() || null,
                        component_path: path
                          .split("/")
                          .map((item) => item.trim())
                          .filter(Boolean),
                      },
                    ],
                  },
                ],
              },
            });
            setRouteId("");
          }}
        >
          添加映射
        </Controls.Button>
      </fieldset>
      {routes.map((route, index) => (
        <fieldset className="grid gap-2 border-t border-line pt-2" key={route.id}>
          <legend>
            {route.entity_ids?.join("、") || "未指定对象"} / {route.role_id ?? "未指定用途"} ·{" "}
            {route.id}
          </legend>
          <Controls.Field>
            替换设备绑定 {route.id}
            <Controls.Select
              value={route.instrument_id}
              onChange={(event) =>
                onChange({
                  ...draft,
                  routing: {
                    ...draft.routing,
                    routes: routes.map((item, i) =>
                      i === index ? { ...item, instrument_id: event.target.value } : item,
                    ),
                  },
                })
              }
            >
              {!draft.instruments.some((item) => item.id === route.instrument_id) && (
                <option>{route.instrument_id}</option>
              )}
              {draft.instruments.map((item) => (
                <option key={item.id}>{item.id}</option>
              ))}
            </Controls.Select>
          </Controls.Field>
          {route.endpoints.map((endpoint, endpointIndex) => (
            <div key={endpointIndex} className="grid gap-2">
              <p>
                {endpoint.interface_id} · 逻辑通道 {endpoint.channel_id ?? "未指定"}
              </p>
              <Controls.Field>
                替换组件路径 {route.id} / {endpointIndex + 1}
                <Controls.Input
                  value={endpoint.component_path?.join("/") ?? ""}
                  onChange={(event) =>
                    onChange({
                      ...draft,
                      routing: {
                        ...draft.routing,
                        routes: routes.map((item, i) =>
                          i === index
                            ? {
                                ...item,
                                endpoints: item.endpoints.map((point, j) =>
                                  j === endpointIndex
                                    ? {
                                        ...point,
                                        component_path: event.target.value
                                          .split("/")
                                          .filter(Boolean),
                                      }
                                    : point,
                                ),
                              }
                            : item,
                        ),
                      },
                    })
                  }
                />
              </Controls.Field>
            </div>
          ))}
          <Controls.Button
            onClick={() =>
              onChange({
                ...draft,
                routing: { ...draft.routing, routes: routes.filter((_, i) => i !== index) },
              })
            }
          >
            移除草稿映射 {route.id}
          </Controls.Button>
        </fieldset>
      ))}
      <div role="status">
        <strong>草稿检查（不访问硬件）</strong>
        {problems.length ? (
          <ul>
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : (
          <p>未发现本地引用缺失或重复候选；这不替代保存时的驱动契约检查和实验准备检查。</p>
        )}
      </div>
    </section>
  );
}
