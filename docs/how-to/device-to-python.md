# 从登记设备到 Python 实验

先用公开的 `scopecat.virtual.vna`（Virtual VNA）完成这条旅程。它只生成合成响应，
不代表真实样品。需要已启用提供该驱动的来源；如果驱动列表没有它，先完成
[驱动来源配置](control-instruments.md#update-driver-source-while-the-application-is-running)，
不要用真实设备替代。多来源管理与空白应用模拟设备入口由 #1434/#565 跟踪。

## 从设备页复制声明

在 **设备与驱动** 登记 Virtual VNA，使用 `virtual` 连接。选中设备，展开
**在 Python 中使用此设备**。复制文本或查看能力不连接设备；无需先测试连接。
本批提供 `scopecat.network_sweep/v1` 的经过验证示例，其他接口如实显示未覆盖提示。

两份代码的区别：

- **立即调试**：按登记 ID 打开设备；运行 `with` 块会连接并按当前设置扫描。
  需要修改设置时显式 `apply(...)`，与实验共用占用管理。
- **实验声明**：将代码保存为作者目录的 `src/scopecat_lab/authored/capture.py`。
  `ensure(...)` 记录期望状态，`sweep()` 声明采集。这里只定义实验，没有提交。
  示例返回完整 `NetworkSweepProducts`，包含 frequency 坐标和复数 s_parameter。

作者目录和内核先按[入门实验](../tutorials/starter-authoring.md)创建并跑通三点扫描。
在同一目录、同一应用中继续，保留原参数分支。`scopecat-instruments` 在客户端和执行环境
均须可导入，选择作者 `.venv` 后才有对应类型提示。没有补全时先检查解释器和安装包，
不要在 Notebook 临时生成 client 或切到应用自用 Python。

## 明确实验用途和映射

本教学示例有意选择 `demo-readout` 角色，表示一次无实体的模拟扫描；它不是根据设备名称
推断出的用途。实际实验由作者决定角色、实体及物理端点。设备页列出所选能力的组件路径。

| 名称 | 本例含义 |
| --- | --- |
| 登记 ID | 设备页显示的设备身份，下面手动填入 `device_id` |
| setup 逻辑别名 `demo-vna` | 引用登记设备的实验配置名称 |
| 角色 `demo-readout` | Python 声明和路由共同选择的教学用途 |
| 路由 `demo-vna-route` | 将该角色连接到逻辑别名和能力端点 |
| 组件路径 `()` | 公开虚拟 VNA 的整机挂载；其他设备应核对设备页路径 |

在原作者 Notebook 中连接，复制设备页的**登记 ID**，从现有 starter setup 派生新版本。
先用 `author.setup.list()` 查看已保存名称，显式填写 `base_setup_name`，不要自动选第一项。
以下代码只保存配置，不连接设备，也不选择实验之外的全局默认值。

```python
import scopecat as sc

author = sc.notebook(".")
print(author.setup.list())
```

```python
from scopecat.records.config import (
    ResourceRoleSpec,
    ResourceRoute,
    RoutingEndpoint,
    RoutingGraph,
)
from scopecat.records.setup import SetupInstrumentBinding
from scopecat.records.execution_scenario import SoftwareExecutionScenario

base_setup_name = "initial"  # replace with the starter setup you inspected
# Replace with the registered Virtual VNA ID shown on the device page.
device_id = "paste-registered-virtual-vna-id"
base = author.setup.definition(base_setup_name).definition
binding = SetupInstrumentBinding(
    id="demo-vna",
    device_id=device_id,
    run_start="preserve",
    success_action="release",
    failure_action="abort_and_release",
)
# These lifecycle choices apply to this virtual-only exercise, not physical equipment.
routing = RoutingGraph(
    roles=[ResourceRoleSpec(id="demo-readout", description="Synthetic VNA exercise")],
    routes=[
        ResourceRoute(
            id="demo-vna-route",
            instrument_id="demo-vna",
            role_id="demo-readout",
            endpoints=[RoutingEndpoint(interface_id="scopecat.network_sweep/v1")],
        )
    ],
)
scenario = SoftwareExecutionScenario(
    id="virtual-vna-demo",
    label="Virtual VNA sweep",
    model_id="scopecat.virtual.vna",
    model_version="v1",
    capabilities=("Synthetic complex S-parameter sweeps",),
    limitations=(
        "Synthetic response, not a physical sample or instrument measurement.",
    ),
)
definition = base.model_copy(
    update={"instruments": (binding,), "routing": routing, "scenario": scenario}
)
setup = author.setup.save(
    definition, name="virtual-vna-demo", note="Virtual-only exercise"
)
```

场景信息会保存在 setup 和运行来源中，不只是界面标题。这里显式替换 starter 的
Analytic signal experiment，声明公开虚拟 VNA v1 的合成扫描；不填未知的随机种子或模型设置。
已有教程 setup 请另存新名称并重新准备，不改写旧运行的场景记录。

此练习有意只保留一个虚拟仪器绑定和一条路由，不应对日常实验 setup 直接照抄。
在 **参数与绑定 → 实验 setup** 查看保存的新版本和绑定。拓扑及路由编辑仍使用 Python。
修改后的配置请另存新名称，原 setup 与运行历史继续保留。

没有匹配路由：检查选中的 setup、角色、能力版本和组件路径，再保存新版本并重新准备。
存在歧义：查看候选路由，明确用途或实体使匹配唯一；不要删除未知路由或选择第一个设备。
设备正被占用：等待运行清理或结束原手动会话，再重试；查看示例不会取得控制权。

## 修改、准备与显式运行

沿用入门目录的参数函数，并刷新刚保存的 `capture.py`，重新导入声明。
只改请求参数时无需刷新源码。下面的准备会检查本次精确输入，但不会开始采集。

```python
author.refresh()
from scopecat_lab.authored.capture import capture
from scopecat_lab.authored.parameters import open_parameters

params = open_parameters(author)
author.use(setup=setup.ref)
request = capture(points=51)
prepared = author.prepare(request, parameters=params)
print(prepared.preview)
```

准备后把 `request.values["points"]` 改为 `101` 不会修改已有 `prepared`。
需要新采集参数时重新 `author.prepare(request, parameters=params)`。扫描的频率轴由 VNA 返回，
不是把 `points` 当成主机扫描轴。确认预览后，单独执行提交单元格：

```python
from scopecat.application.author_project import AuthorSubmissionUncertain

try:
    job = prepared.run()
except AuthorSubmissionUncertain as error:
    print("查询原提交，不要再次采集：", error.job.receipt)
    raise
receipt = job.receipt
print("保留 receipt：", receipt)
run = job.wait(timeout=120).result()
print(run.id, run.measurements()["frequency"].require_magnitudes("GHz"))
```

结果自动保存在应用的数据空间，在 GUI **数据** 中按运行 ID 查看。
改成 101 点并重新准备、提交后得到新运行；原 51 点结果保持不变。
等待超时只结束等待，可继续等待同一个 job；再次执行 `prepared.run()` 会新增采集。

## 退出后重开原结果

保存 receipt 路径与运行 ID，`author.close()` 后正常退出应用。重开原应用与 Notebook，
只执行连接单元格，再执行以下代码；不要 Run All，也不要重跑提交：

```python
from pathlib import Path

receipt = Path("paste-the-printed-receipt-path")
reopened = author.reopen(receipt).wait(timeout=120).result()
print(reopened.id, reopened.measurements()["frequency"].require_magnitudes("GHz"))
```

若原提交结果不确定，使用 `author.reopen(receipt).recover()` 查询接纳状态。
原数据、请求和来源身份保留；重开不需要当前作者模块仍有同名字段。

## 谁负责更新 client

| 修改 | 需要做的工作 |
| --- | --- |
| 实验输入或扫描 | 修改请求并重新准备；不用生成 client |
| 实验 Python 源码 | 保存，`author.refresh()`，重新 import 并创建新请求 |
| 驱动内部实现，能力契约未变 | 驱动维护者更新驱动来源；相关设备空闲后切换，重新准备实验 |
| 能力字段、参数、结果或公开名称 | 维护者修改声明/manifest，重新生成、检查并交付匹配的包；作者显式更新客户端与执行环境后重新 import、准备 |

第一方包提交生成产物，普通作者安装匹配的 `scopecat-instruments` 即可。
维护者使用 `uv run --locked python scripts/generate_instrument_clients.py`，
再执行同命令的 `--check`；现有 CI 已检查生成漂移。外部驱动包使用
[已安装生成器命令](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-instruments/README.md#typed-client-source-generation)。
当前明确的手工负担是维护者重新生成并交付、作者更新两个环境和重选内核；本批不增加运行时生成或任意热更新。

改名或删除字段后，旧 import 别名和旧请求仍指向旧定义。按具体错误修正新代码，
不要静默重写 prepared request 或历史结果。输入默认值、能力接口和结果字段的变化需要
分别检查，生成成功不代表行为兼容。依赖变化使用设置中的环境准备入口，Notebook 刷新不安装依赖。
