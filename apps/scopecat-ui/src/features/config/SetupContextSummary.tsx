import { useQuery } from "@tanstack/react-query";
import type { components } from "../../api-schema";
import { getSetupRevision } from "./setup-api";
import { Button } from "../../ui/Controls";

/** Read an exact resolution; never resolve a definition against current devices. */
export function SetupContextSummary({
  revision,
}: {
  revision: components["schemas"]["SetupRevisionRef"];
}) {
  const retained = useQuery({
    queryKey: ["setup-revision", revision.revision_id],
    queryFn: ({ signal }) => getSetupRevision(revision.revision_id, signal),
    staleTime: Infinity,
  });
  const data = retained.data;
  if (retained.isPending) return <p>正在读取配置摘要…</p>;
  if (retained.error || !data?.setup || data.content_hash !== revision.content_hash)
    return (
      <div role="alert">
        <p>
          {retained.error
            ? `配置摘要不可用：${retained.error.message}`
            : "配置摘要与所选版本不符，请重新读取。"}
        </p>
        <Button
          type="button"
          disabled={retained.isFetching}
          onClick={() => void retained.refetch()}
        >
          重新读取配置摘要
        </Button>
      </div>
    );
  const { setup } = data;
  const routes = setup.routing.routes ?? [];
  return (
    <section
      aria-label="设备与逻辑用途"
      className="min-w-0 space-y-2 rounded border border-line p-3 text-sm"
    >
      <p>
        <strong>实验配置：</strong>
        {data.resolution.definition_id}
      </p>
      <p className="break-all">固定版本：{data.id}</p>
      <p>以下是所选配置的记录，不代表当前样品已接线或设备正在连接。</p>
      <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="实验设备表格">
        <table className="w-full min-w-[40rem] text-left [&_th]:px-2 [&_td]:px-2 [&_td]:align-top">
          <caption className="text-left font-semibold">实验设备与逻辑用途</caption>
          <thead>
            <tr>
              <th>实验设备标识</th>
              <th>驱动</th>
              <th>用途与对象</th>
            </tr>
          </thead>
          <tbody>
            {setup.instrument_registry.instruments.map((instrument) => {
              const assigned = routes.filter((route) => route.instrument_id === instrument.id);
              return (
                <tr key={instrument.id}>
                  <td className="whitespace-nowrap">{instrument.id}</td>
                  <td className="whitespace-nowrap font-mono text-xs">{instrument.driver_id}</td>
                  <td>
                    {assigned.length ? (
                      <ul>
                        {assigned.map((route) => {
                          const role = setup.routing.roles?.find(
                            (item) => item.id === route.role_id,
                          );
                          return (
                            <li key={route.id}>
                              {role?.description || route.role_id || "未指定用途"} · 路由 {route.id}
                              {route.entity_ids?.length
                                ? ` · 对象 ${route.entity_ids.join("、")}`
                                : ""}
                              <ul>
                                {route.endpoints.map((endpoint, index) => (
                                  <li key={index}>
                                    {endpoint.interface_id}
                                    {endpoint.component_path.length
                                      ? ` · ${endpoint.component_path.join(" / ")}`
                                      : ""}
                                    {endpoint.channel_id ? ` · 通道 ${endpoint.channel_id}` : ""}
                                    {endpoint.entity_id ? ` · 对象 ${endpoint.entity_id}` : ""}
                                  </li>
                                ))}
                              </ul>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      "未分配通用路由"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {setup.instrument_registry.instruments.length === 0 && <p>此配置没有实验设备。</p>}
      {routes.length === 0 && (
        <p>此配置没有通用路由。设备登记和独立调试可先进行；实验所需映射在准备时检查。</p>
      )}
      {setup.domain_target && (
        <p>
          专用实验目标：{setup.domain_target.id} · {setup.domain_target.kind}
          {setup.domain_target.instrument_ids?.length
            ? ` · 实验设备 ${setup.domain_target.instrument_ids.join("、")}`
            : ""}
          。专用目标的内部映射不在通用路由表中展开。
        </p>
      )}
      <details>
        <summary>登记设备的固定版本</summary>
        <ul>
          {data.resolution.devices.map((device) => (
            <li key={device.device_id} className="break-all">
              {device.device_id} · {device.revision_id}
            </li>
          ))}
        </ul>
        {data.resolution.devices.length === 0 && <p>未引用登记设备。</p>}
      </details>
    </section>
  );
}
