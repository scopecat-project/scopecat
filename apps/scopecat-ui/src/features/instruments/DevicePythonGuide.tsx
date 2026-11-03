import type { InstrumentDescription } from "../../api-contract";
import { interfaceMountPaths } from "./InstrumentInterfaceControls";

const guide = "https://scopecat-project.github.io/scopecat/how-to/device-to-python/";
const networkSweep = "scopecat.network_sweep/v1";

/** Curated, versioned examples: never infer Python imports from driver identifiers. */
export function networkSweepExamples(deviceId: string, mountPath: string[]) {
  const mount = mountPath.length ? `, component_path=tuple(${JSON.stringify(mountPath)})` : "";
  return {
    live: `import scopecat as sc
from scopecat_instruments import network_sweep

DEVICE_ID = ${JSON.stringify(deviceId)}
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
  description,
}: {
  deviceId: string;
  description: InstrumentDescription;
}) {
  const supported = description.interfaces?.some((item) => item.id === networkSweep);
  const mounts = supported ? interfaceMountPaths(description, networkSweep) : [];
  return (
    <details className="rounded-md border border-line p-3 text-xs leading-normal">
      <summary className="cursor-pointer font-semibold">在 Python 中使用此设备</summary>
      <p>查看或复制以下文本不会连接设备、发出命令或提交实验。</p>
      <p>
        登记 ID：<code>{deviceId}</code>。先结束手动会话并等待运行清理，再显式运行 Python。
      </p>
      {mounts.length === 0 ? (
        <p>
          此能力尚无经过验证的内置 Python
          示例。请查驱动包的类型化客户端文档和版本；不要从接口名称猜测 import，也不必连接设备来发现
          API。
        </p>
      ) : (
        mounts.map((mountPath) => {
          const examples = networkSweepExamples(deviceId, mountPath);
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
                label={`即时调试 Python ${mountPath.join("/") || "整机"}`}
                value={examples.live}
              />
              <h5>在实验中声明能力</h5>
              <p>
                保存到作者模块。ensure 只声明期望设置，定义和创建请求不采集；需选择
                setup、准备检查，再显式提交。
              </p>
              <Code
                label={`实验声明 Python ${mountPath.join("/") || "整机"}`}
                value={examples.experiment}
              />
              <p>
                示例角色 demo-readout 是教学用途名称，不是此设备已有角色。请在“参数与绑定 → 实验
                setup”核对逻辑别名绑定到登记 ID <code>{deviceId}</code>；路由端点使用{" "}
                <code>{networkSweep}</code>，组件路径 <code>{JSON.stringify(mountPath)}</code>。
              </p>
            </section>
          );
        })
      )}
      <p>
        本页不选择实验
        setup，也不判断接线。没有兼容路由时，请补充经核对的绑定、角色和端点；有多条候选时，明确实验用途并选择唯一匹配路由，不要靠连接顺序消除歧义。路由的
        instrument_id 是 setup 逻辑别名，不是登记 ID。
      </p>
      <a className="text-accent underline" href={guide} target="_blank" rel="noreferrer">
        模拟 VNA 完整旅程：setup、修改参数、准备、运行与重开
      </a>
    </details>
  );
}

function Code({ label, value }: { label: string; value: string }) {
  return (
    <textarea
      aria-label={label}
      readOnly
      value={value}
      rows={Math.min(value.split("\n").length, 18)}
      className="w-full rounded border border-line bg-panel p-2 font-mono text-xs"
      onFocus={(event) => event.currentTarget.select()}
    />
  );
}
