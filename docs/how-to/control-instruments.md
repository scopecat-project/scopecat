# 仪器控制与实验编写 {#instrument-control-and-authoring}

Scopecat 在两种时间模型中使用同一套类型化能力词汇：即时（live）客户端现在执行操作，
符号（symbolic）客户端在实验中记录相同的设备动作。驱动实现这些能力，不接收运行、实体、
实验点、产物或数据集等上层概念。

后台服务的占用管理与故障处理见[实验室后台服务架构](../development/architecture/daemon.md)。
执行顺序与物理控制权见[执行语义](../development/architecture/execution.md)。
提供方注册、生成、配置和驱动测试见[仪器提供方 README](https://github.com/scopecat-project/scopecat/blob/main/packages/scopecat-instruments/README.md)。

## 立即使用已配置设备 {#use-a-configured-device-now}

在 Python 中选择已保存的设备配置上下文，按仪器 ID 创建类型化引用，再显式连接。
列出或选择上下文不会连接设备。

```python
import scopecat as sc
from scopecat_instruments import network_sweep


READOUT_VNA = network_sweep("readout-vna")

with sc.open_project(".").connect(operator="alice") as lab:
    setup = lab.setup.get("bench-a").ref
    with lab.instruments.open(READOUT_VNA, setup=setup) as devices:
        vna = devices[READOUT_VNA]
        vna.apply(
            start_frequency=sc.Quantity(4.9, "GHz"),
            stop_frequency=sc.Quantity(5.1, "GHz"),
            points=751,
            s_parameter="S21",
        )
        trace = vna.sweep()
```

`apply(...)` 在调用期间执行稀疏状态变更，只指定需要修改的字段。此处的 `sweep()`
触发硬件扫描，并返回带回执证据的类型化读回结果。会话负责同步，并使用与实验运行相同的排他占用；
它不会创建一个单点实验。

使用 `lab.setup.list()` 查找已保存的上下文，用 `lab.instruments.list(setup=setup)` 查看其中的设备。
会话固定使用精确的 setup 引用，其他 Notebook 或浏览器页面不能替它切换配置。

当前 GUI 使用 **设备与驱动** 页面：选择登记设备、查看能力，再点击 **连接**。
左侧可按名称、地址搜索或按占用状态筛选，右侧分为 **操作**、**连接与环境**、**诊断记录**；
每台设备会记住本浏览器会话中最近使用的入口。长期编辑留在右侧工作区，添加设备使用短弹窗。
无需在该页选择旧版 **Device context**。仅选中设备不会连接；切换设备前先结束当前交互会话。
**添加设备** 或编辑设备用于保存登记连接的版本，不是激活一套全局实验配置；实验仍使用自己的精确配置引用。

设备编辑器先保存连接版本，再在设备详情显式 **测试连接**，测试结果只属于该版本。
测试遵守与手动会话相同的占用检查；若其他会话或实验正占用设备，应先结束相应工作再试。
测试成功会释放测试占用，连接可保留供后续工作复用；不代表仪器属性已读回或输出已停止。

编辑时可查看具体差异。保存会退役旧空闲连接，可能执行驱动的断开动作；新版本不会自动测试。
测试失败后可继续编辑，或 **使用上一连接版本生成草稿**，审阅后保存为新版本，再测试。
此入口要求前后驱动实现相同，不回退驱动安装。旧记录保留，相关实验准备需重新检查。
退役失败会保留旧连接 head 和待处理状态，不代表旧硬件状态已恢复。
保存响应丢失时，不改草稿直接重试可检查同一次提交；不要把未确认结果当作未保存。

会话通过租约（lease）维持占用，Python 句柄和页面会续租。
关闭会话会结束占用、执行配置的收尾策略，并可保留连接供后续工作。
若续租失败或页面失联，不要把本地“已断开”当作硬件已停止的证据；重新连接前先检查设备与服务端占用状态。
若要在所有运行和会话结束后把已配置设备交给另一个程序，显式释放物理连接：

```python
with sc.open_project(".").connect(operator="alice") as lab:
    setup = lab.setup.get("bench-a").ref
    lab.instruments.release(READOUT_VNA, setup=setup)
```

`release` 拒绝仍被未结束运行或会话保留的设备。它不停止后台服务，会丢弃连接及其缓存状态；
下一次会话或实验会重新连接。即使传入组件引用，释放也作用于其所属的整台物理仪器。
这不会替另一个程序预留设备；该程序工作期间应暂停新的提交。
释放本身不应用新的硬件设置，最终状态取决于先前运行或会话的收尾策略。
页面的 **释放控制** 结束手动会话。**连接与环境 → 释放物理连接** 执行上述空闲连接释放，
占用中不可用；失败时保留具体错误，不显示成功。两者都不承诺输出归零或物理停止。
外部程序结束后重新连接，再用 **刷新状态** 或驱动实际提供的操作核对设备。
驱动未声明状态属性时不提供状态刷新；无法读回的项目仍须通过前面板或其他支持途径确认。

**连接与环境** 直接展示驱动声明的已保存字段和地址表，敏感字段遮蔽，未声明的额外字段不自动展开。
连接选项按驱动声明显示字段、枚举和名称/值表格，可选路径可留空。桌面版对标注的文件/目录字段提供原生选择器，浏览器仍可手工输入服务端路径。地址表的未填项或重复名称会阻止保存。
编辑旧连接不补入新驱动默认值；如驱动提供设置来源选择，需显式选择再填写该模式要求的字段。
表格只管理显式连接数据，不探测机箱、板卡或样品接线；设备连接配置与实验路由仍然分开。
未支持的复杂对象仍使用高级 JSON 字段，保存时继续由服务端检查驱动契约。

当一个清单条目拥有同一接口的多个实现时，在即时引用上选择物理挂载位置：

```python
from scopecat_instruments import rf_output


PUMP_2 = rf_output("pump-source", component_path=("channels", "2"))

with sc.open_project(".").connect(operator="alice") as lab:
    setup = lab.setup.get("bench-a").ref
    with lab.instruments.open(PUMP_2, setup=setup) as devices:
        source = devices[PUMP_2]
        source.apply(frequency=sc.Quantity(6, "GHz"), output_enabled=True)
```

生成的成员相对于所选挂载位置。即时通道把状态、操作和采集映射到物理组件路径，并检查接口确实挂载在该处。
`describe()`、`observed_state()` 和 `refresh()` 仍描述或返回所属整台物理仪器，供诊断使用。
配置默认值也属于该物理拥有者，因此不能通过限定到组件的客户端应用。

若只做设备诊断，在 **设备与驱动** 中登记一次连接即可，不需要实验 setup 或实体路由。
Notebook 可直接打开同一个已登记设备：

```python
import scopecat as sc
from scopecat_instruments import network_sweep

BENCH_VNA = network_sweep("bench-vna")  # the registered device ID

with sc.open_project(".").connect(operator="alice") as lab:
    with lab.devices.open("bench-vna") as devices:
        trace = devices[BENCH_VNA].sweep()
```

手动会话关闭后，设备仍保留登记。实验和手动会话共享同一物理占用，即使实验给它使用不同逻辑名称。
不再使用时可在设备页点击 **退役设备**；其执行记录仍可读取。

## 在设备页发现与调用能力

设备页按驱动已有契约展示属性、操作（operation）和采集（acquisition）。查看页面或展开帮助不会连接
设备或发送设备命令。没有声明能力时，先查驱动文档或版本，不要靠 **连接** 探索未声明的功能。

- **调用 Invoke** 调用指定操作。普通类型参数须填完整，且本页必须持有可用会话。
- 需要 **payload** 的操作仍显示名称与说明，但不提供调用按钮或 payload 编辑器。
  展开 **如何准备与调用**，按契约在作者 Python 程序中准备 payload，使用已有类型化客户端
  或实验准备流程。页面提供上方 `lab.devices.open(...)` 工作流和下方作者准备流程的链接。
- **收集 Collect** 调用指定采集能力。它可能发起新采集，也可能只收集既有执行结果，
  以该项契约说明为准。没有说明时先查驱动文档；按钮名和方法名不能证明采集时序。
  Collect 不会自动准备 payload 或替你调用另一个操作。

回执中的 **结果未知** 不等于“未执行”，也不保证可以安全重复发送命令。
硬件状态不确定时，设备可能进入隔离，阻止自动复用；应先核对实际状态和占用方。
页面的 **处理隔离** 仅能处理其支持的交互会话占用，运行占用须回所属运行流程处理。
确认并释放隔离不是重新测量硬件状态或证明物理安全。

切换到 Python 或实验前，先结束页面手动会话，等待其他运行完成清理。
设备占用、隔离、连接不可用和正在交互时的保护仍然适用；查看能力不会获得设备控制权。
此页不是原始 SCPI 命令控制台，也没有持续输出的启动、保持、停机生命周期界面；
不要据此假定断开连接、关闭页面或退出后输出会归零。

## 应用运行期间更新驱动源码 {#update-driver-source-while-the-application-is-running}

保存驱动修改，结束设备会话，并等待受影响的实验结束。通过已有 Notebook 连接操作：

```python
selected = lab.devices.update_driver_source("/path/to/driver-project")
lab.devices.driver_source()  # inspect the active snapshot without connecting
```

目录须包含 `scopecat.toml`、其中的 `lab.instrument_backend` 及作者源码根配置。
工厂必须保持当前提供方身份，并支持已登记的连接选项。依赖须已安装在选定的驱动执行 Python 中。
无需构建 wheel 或重启应用。

更新会替换整个驱动 worker，因此它服务的全部设备都必须空闲。
更新会释放旧连接，下次显式操作才重新连接；实验应针对更新后的设备修订重新准备。
语法或发布错误会保留先前选择；若断开失败，应先处理设备报告的问题再重试。
应用重启与备份恢复会保留所选源码，即使原开发目录已移动。

GUI 入口为 **设备与驱动 → 已安装驱动 → 从源码更新**，填写应用所在计算机上的源码目录绝对路径，
可选填驱动执行 Python。提交前先结束手动会话及受影响工作，更新后重新连接设备、重新准备实验。
这只更新驱动，不更新实验或编译器源码，也不安装依赖。

## 从手动操作返回实验 {#return-from-manual-work-to-an-experiment}

在设备页连接设备，查询需要的科学量、应用修改，然后在启动实验前断开手动会话。
查询结果保留观察来源和时间，不承诺设备之后仍处于同一状态。
**应用已配置默认值** 会产生回执；若维护的清单没有默认状态，该操作可能被拒绝。
此时应明确选择所需科学设置，不要假定设备已复位。

预览会记录完整编译后的仪器使用范围，以及编译前捕获的手动事件游标。
之后对相关物理仪器执行状态变更、操作调用、采集、显式中止或连接释放，会使该预览失效。
界面会说明受影响的仪器并提示重新预览，同时保留科学输入。
纯查询及对无关物理仪器的修改不影响预览可用性。失败或中途停止的操作也会使预览失效，因为它们可能已改变状态。

能感知修订的 Notebook 入口使用相同检查：

```python
with sc.open_project(".").authoring() as authors:
    prepared = authors.prepare("ramsey")
    # Finish any relevant manual changes before preparing again.
    submission = prepared.submit(request_key="ramsey-after-manual-adjustment")
```

接纳提交时，服务端对照精确配置与代码修订检查已记录的预览。
重试已接纳的请求键会返回原 procedure，即使之后发生过手动操作。
新预览进行新的检查，并使用新的提交键；结果未确定的原始提交仍保留原 payload 供恢复。
预览不是执行已保存方案的永久许可。

接纳后，采集仍受运行时占用约束：手动会话和运行不能同时占用同一物理仪器。
停止请求在运行完成清理前仍处于等待状态；应等到停止结果明确后再手动连接。
这种顺序约束不会冻结硬件，也不能检测后台服务以外的修改。

维护的启动适配器把预览的 `manual_state` 写入持久提交意图，并作为 `expected_manual_preview`
传给 `lab.procedures.submit(...)`；标准作者适配器已完成这一步。
资源别名通过清单的物理排他键比较，这些映射由维护者负责。

## 在实验中声明相同工作 {#declare-the-same-work-for-an-experiment}

把实验上下文传给同一工厂，即可创建符号客户端：

```python
import scopecat as sc
from scopecat_instruments import network_sweep


@sc.experiment
def capture(experiment: sc.ExperimentContext):
    vna = network_sweep(experiment)
    vna.ensure(
        start_frequency=sc.Quantity(4.9, "GHz"),
        stop_frequency=sc.Quantity(5.1, "GHz"),
        points=751,
        s_parameter="S21",
    )
    return vna.sweep()
```

`ensure(...)` 记录一致的期望状态。字段可使用固定值、输入、参数及其他允许的符号值；
省略的字段保持未指定。连续的 ensure 保留效果顺序。
符号采集返回类型化产物包，定义实验不会触碰硬件。

实验可以协调多个仪器客户端、可复用模块及领域调用。
`@module` 用于提取确实要复用或组合的工作，不是使用设备的前提。

产物命名空间来自能力及其效果发生位置。若一次采集属于持久数据契约，应显式提供 `id=`。
返回完整结果包会保留声明的坐标、观测量、轴和采集组关系。
持久结果的权威说明见[实验数据流模型](../concepts/experiment-dataflow.md#return-the-result-you-mean-to-keep)。

### 按角色选择等价资源 {#select-equivalent-resources-by-role}

逻辑资源身份自动分配。当一个实体有多个实现同一接口的资源时，用稳定的实验室角色表达用途：

```python
from scopecat_instruments import rf_output


drive = rf_output(
    experiment,
    for_=sc.one("q0", kind="logical_device"),
    role="drive",
)
readout = rf_output(
    experiment,
    for_=sc.one("q0", kind="logical_device"),
    role="readout",
)
```

接受的配置将每个角色连接到可选路由。一条路由把需要一起选择的所有端点组成一组：

```json
{
  "roles": [
    {"id": "readout", "description": "source used for qubit readout"}
  ],
  "routes": [
    {
      "id": "readout-source",
      "instrument_id": "readout-source-0",
      "role_id": "readout",
      "entity_ids": ["q0"],
      "endpoints": [
        {
          "interface_id": "scopecat.rf_output/v2",
          "entity_id": "q0",
          "channel_id": "readout-q0",
          "component_path": ["outputs", "readout-q0"]
        }
      ]
    }
  ]
}
```

字符串选择目录中精确的角色。省略 `role` 会选择无角色标签的路由。
若代码有意接受唯一兼容路由而不限制角色，可传入 `sc.ANY_RESOURCE_ROLE`。

`entity_ids` 列出整条路由服务的逻辑实体；端点的 `entity_id` 将单个通道或命令绑定限定到一个实体。
共享 LO、序列器或波形端点省略端点实体，但路由仍列出它服务的全部实体。
无实体路由支持按角色选择的台架工作或未限定实体的实验。

### 将共享状态挂载到物理拥有者 {#mount-shared-state-on-its-physical-owner}

可变状态属于其物理拥有者：整机属性放在接口根，bank 级属性放在 bank 组件，
LO 频率放在 LO 组件，通道属性放在通道组件。
端点的 `component_path` 将逻辑接口成员挂载到这个拥有者。

当多条路由把某属性解析到同一物理地址时，它们为该属性构成同一个有效共享状态组。
相同要求合并成一条命令；不同要求在执行前报 `experiment_conflicting_desired_state`。
分组取决于具体能力，因此通道可以共享 LO，同时属于不同的时钟或触发域。
`lo_groups/0`、`clock_domains/1` 和 `trigger_domains/0` 等组件表达这些不同的拥有者。

角色按用途选择硬件，组件路径表达共享可变状态。逻辑实体 ID 保留来源信息，不会复制物理状态槽。

接口包含调用方可在兼容设备之间依赖的行为。重复的物理实现使用 `interface_mounts`；
某个型号更窄的访问、捕获、恢复或取值范围留在其接口属性实现中。
有助于诊断、记录或恢复、但不属于可移植行为的型号特有事实和设置，放在带版本的 `device_schema` 中。
这些状态成员可独立读取和缓存，无须为单个设备虚构一个接口。

### 用带符号的 IF 进行专用 LO 扫描 {#use-signed-if-for-specialized-lo-scans}

通用实验表达物理载频意图，并采用经过审查的实验室频率组合规则。
专用的固定 IF、扫描 LO 工作流可留在实验室本地，统一采用 `RF = LO + signed IF`；
IF 的正负号直接选择 IQ 边带。

扫描的 LO 已是持久实验点坐标。把推导出的载频另存为坐标，使绘图和导出同时保留请求的控制量及其物理意义：

```python
@dataclass(frozen=True)
class SpectrumResult:
    rf_frequency: Annotated[
        sc.ValueRef[sc.Quantity],
        sc.Result(
            role="coordinate",
            metadata={
                "relation": "rf_frequency = lo_frequency + signed_if",
                "signed_if_hz": -100_000_000.0,
            },
        ),
    ]


lo = experiment.scan("lo_frequency", (4.9, 5.0, 5.1), unit="GHz")
rf = lo + sc.Quantity(-100, "MHz")
return SpectrumResult(rf_frequency=rf)
```

LO 变化是主机侧效果，会划分实时目标批次，使每批在所请求的 LO 设置下执行。
目标可保持相同的带符号 IF，而最终 RF 载频随 LO 改变。

路由决定修改哪个源。解析到同一 LO 拥有者的相同请求会合并；不同 LO 拥有者仍可独立调度。
只有实验室刻意按物理拥有者协调扫描时，才需要额外的本地分组逻辑。

### 面向一个或多个实体 {#address-one-or-many-entities}

实体数量在工厂边界选择：

```python
single = network_sweep(
    experiment,
    for_=sc.one("q0", kind="logical_device"),
)

targets = sc.each("q0", "q1", kind="logical_device")
many = network_sweep(experiment, for_=targets)
```

`one(...)` 创建一个标量符号客户端，实体可以是具体身份，也可以在实验点解析。
`each(...)` 保存编写时已知的具体身份，并返回具有相同动作的组客户端。
标量参数广播到各实体；`PerEntity[T]` 按精确的 `(kind, id)` 身份提供值：

```python
points = sc.PerEntity((entity, 501 if entity.id == "q0" else 801) for entity in targets)

many.ensure(
    start_frequency=sc.Quantity(4.9, "GHz"),
    stop_frequency=sc.Quantity(5.1, "GHz"),
    points=points,
    s_parameter="S21",
)
traces = many.sweep()
return traces
```

组仍展开为独立路由的标量资源和采集，但同类返回产物按字段保存，并带有 `logical_device` 实体轴。
因此数据集 schema 的宽度取决于结果字段数，而非所选设备数。
各实体的产物来源和采集证据与持久实体索引保持对齐。

对齐是按身份连接，与映射顺序无关；身份缺失、多余或重复会在记录效果前报错。
组式编写不会要求驱动隐式执行向量操作。

## 简明规则 {#compact-rule-set}

1. 用具体的 Python 状态、操作与结果声明一次设备语义。
2. 即时稀疏状态变更用 `apply`，符号期望状态用 `ensure`；两种模式使用相同的操作与采集动作。
3. 由生成客户端处理时间模型和实体数量：即时或符号、单实体或 `PerEntity`。
4. 把可变状态挂载到物理拥有者，按稳定的实验室用途选择等价资源。
5. 返回类型化值或产物包，让依赖关系决定顺序。
6. 直接记录值和包；声明的角色、轴、组和实体身份携带数据集映射。

添加能力或驱动，继续阅读[仪器扩展指南](../extensions/instruments.md)。
