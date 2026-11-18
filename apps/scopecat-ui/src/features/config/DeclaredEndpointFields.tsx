import { useState } from "react";
import type { components } from "../../api-schema";
import type { InstrumentDescription } from "../../api-contract";
import * as Controls from "../../ui/Controls";

export type MappingDeclaration = { description?: InstrumentDescription; pending?: boolean };
export type MappingDeclarations = Record<string, MappingDeclaration | undefined>;

// Mirrors the contract resolver: an unmounted interface starts at the root;
// mounted interfaces expose their declared component tree below each mount.
export function declaredEndpoints(description: InstrumentDescription | undefined) {
  const endpoints: { interface_id: string; component_path: string[] }[] = [];
  for (const capability of description?.interfaces ?? []) {
    const mounts = (description?.interface_mounts ?? []).filter(
      (mount) => mount.interface_id === capability.id,
    );
    const visit = (path: string[], children: components["schemas"]["ComponentSpec"][]) => {
      endpoints.push({ interface_id: capability.id, component_path: path });
      for (const child of children) visit([...path, child.id], child.components ?? []);
    };
    for (const mount of mounts.length ? mounts : [{ component_path: [] }])
      visit([...mount.component_path], capability.components ?? []);
  }
  return endpoints;
}

export function endpointProblem(
  declaration: MappingDeclaration | undefined,
  interfaceId: string,
  path: string[],
): string | undefined {
  if (!declaration?.description)
    return declaration?.pending
      ? "正在读取驱动声明…"
      : "声明未知：无法核对该设备绑定，可高级手填并保存待完成配置。";
  const mounts = declaredEndpoints(declaration.description);
  const matching = mounts.filter((mount) => mount.interface_id === interfaceId);
  if (!matching.length) {
    const family = interfaceId.replace(/\/v\d+$/, "");
    const versions = mounts.filter((mount) => mount.interface_id.replace(/\/v\d+$/, "") === family);
    return versions.length
      ? `能力版本不匹配：${interfaceId}；已声明 ${[...new Set(versions.map((mount) => mount.interface_id))].join("、")}`
      : `未声明能力：${interfaceId || "尚未选择"}`;
  }
  if (!matching.some((mount) => JSON.stringify(mount.component_path) === JSON.stringify(path)))
    return `组件路径不存在于该能力声明：${path.join("/") || "根组件"}；可用端点：${matching.map((mount) => mount.component_path.join("/") || "根组件").join("、")}`;
  return undefined;
}

export function DeclaredEndpointFields({
  declaration,
  interfaceId,
  path,
  label,
  interfaceLabel,
  pathLabel,
  onChange,
}: {
  declaration?: MappingDeclaration;
  interfaceId: string;
  path: string[];
  label: string;
  interfaceLabel: string;
  pathLabel: string;
  onChange: (interfaceId: string, path: string[]) => void;
}) {
  const mounts = declaredEndpoints(declaration?.description);
  const value = mounts.findIndex(
    (mount) =>
      mount.interface_id === interfaceId &&
      JSON.stringify(mount.component_path) === JSON.stringify(path),
  );
  const problem = endpointProblem(declaration, interfaceId, path);
  return (
    <>
      <Controls.Field>
        {label}
        <Controls.Select
          value={value < 0 ? "" : String(value)}
          onChange={(event) => {
            const mount = mounts[Number(event.target.value)];
            if (event.target.value && mount)
              onChange(mount.interface_id, [...mount.component_path]);
          }}
        >
          <option value="">
            {declaration?.description ? "选择已声明能力与端点" : "声明未知，使用高级手填"}
          </option>
          {mounts.map((mount, index) => (
            <option key={index} value={String(index)}>
              {mount.interface_id} · {mount.component_path.join("/") || "根组件"}
            </option>
          ))}
        </Controls.Select>
      </Controls.Field>
      {problem && <p role="status">{problem}</p>}
      <details open={!declaration?.description || undefined}>
        <summary>高级手填能力与组件路径</summary>
        <Controls.Field>
          {interfaceLabel}
          <Controls.Input
            value={interfaceId}
            onChange={(event) => onChange(event.target.value, path)}
          />
        </Controls.Field>
        <Controls.Field>
          {pathLabel}
          <ComponentPathInput
            value={path.join("/")}
            onChange={(raw) => onChange(interfaceId, raw.split("/").filter(Boolean))}
          />
        </Controls.Field>
      </details>
    </>
  );
}

// Keep unfinished separators while the draft retains canonical segments.
function ComponentPathInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (raw: string) => void;
}) {
  const [editing, setEditing] = useState<{ raw: string; canonical: string }>();
  return (
    <Controls.Input
      value={editing?.canonical === value ? editing.raw : value}
      onChange={(event) => {
        const raw = event.target.value;
        setEditing({ raw, canonical: raw.split("/").filter(Boolean).join("/") });
        onChange(raw);
      }}
      onBlur={() => setEditing(undefined)}
    />
  );
}
