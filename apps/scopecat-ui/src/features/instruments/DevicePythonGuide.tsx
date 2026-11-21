import { useEffect, useRef, useState } from "react";
import * as Controls from "../../ui/Controls";
import type { InstrumentDescription } from "../../api-contract";
import { interfaceMountPaths } from "./InstrumentInterfaceControls";

const guide = "https://scopecat-project.github.io/scopecat/how-to/progressive-instrument-bindings/";
const networkSweep = "scopecat.network_sweep/v1";

/** Curated, versioned examples: never infer Python imports from driver identifiers. */
export function networkSweepExamples(deviceId: string, mountPath: string[], deviceLabel?: string) {
  // Display-only context: keep arbitrary labels on one comment line, never in lookup code.
  const labelComment =
    deviceLabel === undefined
      ? ""
      : `# Device name (display only): ${JSON.stringify(deviceLabel).replace(
          /[\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]/g,
          (character) => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`,
        )}\n`;

  const mount = mountPath.length ? `, component_path=tuple(${JSON.stringify(mountPath)})` : "";
  return {
    live: `import scopecat as sc
from scopecat_instruments import network_sweep

${labelComment}DEVICE_ID = ${JSON.stringify(deviceId)}
VNA = network_sweep(DEVICE_ID${mount})

# Running this block connects and performs a sweep. Review settings first.
with sc.open_project(".").connect(operator="your-name") as lab:
    with lab.devices.open(DEVICE_ID) as devices:
        trace = devices[VNA].sweep()
        print(trace)
`,
    experiment: `import scopecat as sc
from scopecat_instruments import NetworkSweepProducts, network_sweep


@sc.experiment
def capture(
    experiment: sc.ExperimentContext,
    points: sc.Input[int] = 51,
) -> NetworkSweepProducts:
    # Example values for a virtual VNA; review before using physical equipment.
    vna = network_sweep(experiment, role="demo-readout")
    vna.ensure(
        start_frequency=sc.Quantity(4.9, "GHz"),
        stop_frequency=sc.Quantity(5.1, "GHz"),
        points=points,
        if_bandwidth=sc.Quantity(1, "kHz"),
        source_power=sc.Quantity(-40, "dBm"),
        s_parameter="S21",
    )
    return vna.sweep()
`,
  };
}

export function DevicePythonGuide({
  deviceId,
  deviceLabel,
  description,
}: {
  deviceId: string;
  deviceLabel?: string;
  description: InstrumentDescription;
}) {
  const supported = description.interfaces?.some((item) => item.id === networkSweep);
  const mounts = supported ? interfaceMountPaths(description, networkSweep) : [];
  return (
    <section aria-label="在 Python 中使用此设备" className="grid gap-2 text-xs leading-normal">
      {deviceLabel !== undefined && <p>设备名称：{deviceLabel}</p>}
      <p>
        稳定登记 ID：<code>{deviceId}</code>
      </p>
      <p>名称仅供辨认；代码仍使用完整登记 ID。请在原数据空间运行并核对当前连接设置。</p>
      {mounts.length === 0 ? (
        <p>此能力暂无内置 Python 示例，请参阅驱动的客户端文档。</p>
      ) : (
        mounts.map((mountPath) => {
          const examples = networkSweepExamples(deviceId, mountPath, deviceLabel);
          return (
            <section key={JSON.stringify(mountPath)} className="grid gap-2">
              <h4>Network sweep · {mountPath.join(" / ") || "整机"}</h4>
              <p>
                <code>from scopecat_instruments import network_sweep</code>{" "}
                提供类型化补全；采集返回频率坐标和复数 S 参数。其他能力请查对应驱动文档。
              </p>
              <h5>立即调试此设备</h5>
              <p>
                按登记 ID 使用当前设备设置；运行下面的 with 块会连接并触发扫描。修改设置用
                apply，不需要实验 setup。
              </p>
              <Code
                key={examples.live}
                label={`即时调试 Python ${mountPath.join("/") || "整机"}`}
                value={examples.live}
              />
              <h5>在实验中声明能力</h5>
              <p>
                保存到作者模块。ensure
                只声明期望设置，定义和创建请求不采集。此普通仪器示例可在实验准备时查看需求，明确选择登记设备、端点和生命周期策略，检查绑定并准备，再显式运行。无需先创建具名
                setup；需要复用时可另存命名配置。
              </p>
              <Code
                key={examples.experiment}
                label={`实验声明 Python ${mountPath.join("/") || "整机"}`}
                value={examples.experiment}
              />
              <p>
                示例角色 demo-readout
                是实验声明的用途，不要求设备预先拥有此角色。准备时核对所选登记设备 ID{" "}
                <code>{deviceId}</code>、能力 <code>{networkSweep}</code> 和端点{" "}
                <code>{JSON.stringify(mountPath)}</code>（空路径为整机）。也可选择已有的兼容 setup。
              </p>
            </section>
          );
        })
      )}
      <p>
        本页仅提供示例，不选择设备绑定或判断接线。多条候选必须明确选择；检查通过仍需准备和显式运行。依赖实体、复杂拓扑、领域执行或嵌套模块等不支持渐进绑定的需求，请按准备诊断核对原因，再提供明确的
        setup；可在“参数与绑定 → 实验 setup”配置。
      </p>
      <a className="text-accent underline" href={guide} target="_blank" rel="noreferrer">
        Python 渐进绑定：查看需求、选端点、准备与运行
      </a>
    </section>
  );
}

function Code({ label, value }: { label: string; value: string }) {
  const [status, setStatus] = useState<"idle" | "copying" | "copied" | "manual">("idle");
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  async function copy() {
    setStatus("copying");
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(value);
      if (mounted.current) setStatus("copied");
    } catch {
      if (mounted.current) setStatus("manual");
    }
  }
  return (
    <div className="grid gap-2">
      <Controls.Button aria-label={`复制${label}`} disabled={status === "copying"} onClick={copy}>
        {status === "copying" ? "正在复制…" : "复制 Python 示例"}
      </Controls.Button>
      {status === "copied" && <p role="status">示例已复制，未执行代码。</p>}
      {status === "manual" && <p role="status">无法访问剪贴板，请选中下面的完整示例手动复制。</p>}
      <textarea
        aria-label={label}
        readOnly
        value={value}
        rows={Math.min(value.split("\n").length, 18)}
        className="w-full rounded border border-line bg-panel p-2 font-mono text-xs"
        onFocus={(event) => event.currentTarget.select()}
      />
    </div>
  );
}
