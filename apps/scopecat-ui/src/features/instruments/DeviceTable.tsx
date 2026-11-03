import * as Controls from "../../ui/Controls";
import type { DeviceView } from "./device-api";
import { connectionSummary } from "./instrument-api";

export function DeviceTable({
  devices,
  selectedId,
  disabled,
  driverLabels,
  onSelect,
  onControl,
}: {
  devices: DeviceView[];
  selectedId?: string;
  disabled: boolean;
  driverLabels: Map<string, string>;
  onSelect: (id: string) => void;
  onControl?: (id: string) => void;
}) {
  return (
    <table className="w-full table-fixed text-left max-[700px]:block" aria-label="已登记设备">
      <thead className="border-b border-line text-sm text-text-dim max-[700px]:hidden">
        <tr>
          <th className="p-2">设备 / 驱动</th>
          <th className="p-2">连接</th>
          <th className="w-28 p-2">状态</th>
          <th className="w-28 p-2">最近测试</th>
          {onControl && <th className="w-36 p-2">控制</th>}
        </tr>
      </thead>
      <tbody className="max-[700px]:block">
        {devices.map((item) => {
          const retired = item.device.state === "retired";
          const status = retired
            ? "已停用"
            : item.availability === "quarantined"
              ? "需要处理"
              : item.availability === "active"
                ? item.owner_kind === "run"
                  ? "运行中"
                  : "手动占用"
                : "空闲";
          const test = item.last_connection_test;
          return (
            <tr
              key={item.device.id}
              className="border-b border-line last:border-0 max-[700px]:block max-[700px]:py-2"
            >
              <td className="break-words p-2 align-top max-[700px]:block">
                <button
                  title={`查看仪器 ${item.device.id}`}
                  aria-pressed={item.device.id === selectedId}
                  disabled={disabled}
                  className="text-left font-semibold underline decoration-line underline-offset-4 aria-pressed:text-accent disabled:opacity-60"
                  onClick={() => onSelect(item.device.id)}
                >
                  {item.device.label}
                </button>
                <div className="text-sm text-text-dim">
                  {driverLabels.get(item.revision.content.driver.driver_id) ?? "驱动当前不可用"}
                </div>
              </td>
              <td className="break-words p-2 align-top text-sm max-[700px]:block">
                {connectionSummary(item.revision.content.connection)}
              </td>
              <td className="p-2 align-top text-sm max-[700px]:inline-block">{status}</td>
              <td className="p-2 align-top text-sm max-[700px]:inline-block">
                {test ? (test.error ? "测试失败" : "测试通过") : "尚未测试"}
                {retired && test && <span className="block text-text-dim">停用前记录</span>}
              </td>
              {onControl && (
                <td className="p-2 align-top max-[700px]:block">
                  {!retired && (
                    <Controls.Button disabled={disabled} onClick={() => onControl(item.device.id)}>
                      打开控制窗口
                    </Controls.Button>
                  )}
                </td>
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
