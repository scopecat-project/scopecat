# 统一声明实验控制项 {#declare-experiment-controls-once}

普通实验作者先使用[紧邻输入的控制元数据](managed-author-session.md#declare-controls-next-to-their-inputs)：
在实验输入旁声明控制信息，由正常作者发现路径提供界面与执行入口。
本页面向维护显式 `ControlSet` 契约的作者，说明默认值、值的归属和项目校验器；不要求普通实验另写启动适配器。

`sc.Control` 描述维护型数值控制项的默认值、单位、边界、标签、分组、归属和来源。
通过 `@sc.experiment(controls=controls)` 显式附加 `sc.ControlSet`，
不可变 Python 调用与结构化启动目录使用同一组默认值。
以下 `config` 已从作者会话显式解析：

```python
import scopecat as sc

FREQUENCY = sc.Control("frequency", default=sc.Quantity(4.8, "GHz"), scannable=True)
CONTROLS = sc.ControlSet((FREQUENCY,))


@sc.experiment(controls=CONTROLS)
def controlled(context: sc.ExperimentContext):
    return FREQUENCY.ref


original = controlled.build()
fixed = CONTROLS.apply(
    original, config=config, edits={"frequency": sc.Quantity(4900, "MHz")}
)
scanned = CONTROLS.apply(
    fixed,
    config=config,
    edits={"frequency": sc.axis(FREQUENCY.ref, [sc.Quantity(4.9, "GHz")])},
)
restored_default = CONTROLS.apply(scanned, config=config, reset=("frequency",))
```

可扫描控制项只有一个坐标轴来源。标量编辑生成 `mode="fixed"` 的单值 `AxisSpec`；
显式轴使用 `mode="scan"`，即使只有一个点。替换轴会移除旧轴值；回到固定值需要提供值或采用声明默认值，
不会在扫描后面暗存此前标量。每次编辑返回新调用，不修改原调用。
持久运行请求包含 fixed 模式；历史扫描记录省略默认 scan 模式，保留既有 JSON 和内容身份。

不可扫描控制项应声明类型匹配的普通 `sc.Input`，默认值只放在 `Control` 上。
用 `experiment.bind()` 构造采用这些默认值的调用，也可像平时一样绑定显式标量输入。
声明会拒绝不匹配的输入类型或重复的函数默认值。
支持实数标量与实数 `Quantity`，不构成通用表单 schema 或新扫描语言。
表单支持现有值和范围轴，不支持叠加轴或围绕配置值构造的轴。

## 项目校验与值的归属 {#project-validation-and-ownership}

`ControlSet` 可接收项目自己的校验器，参数为 `sc.ControlValidationContext`。
其中的调用、冻结配置和轴规格描述编辑后的计划。控制编辑后会运行校验器，
正常规划/接纳入口也会再次运行，包括直接 `.with_axis` 和 `.bind` 编辑。
可检查每条轴的边界、极值及跨字段规则；不要为了校验而枚举扫描笛卡尔积。

配置拥有的控制项可读取显式所选参数修订中的值；派生控制项可根据该值与编辑后的轴计算偏移。
共享示例 `testing/fixtures/retained-signal` 使用同一个信号中心演示这两种情况，并显示来源。
解析器输出在显示前按声明单位归一化并检查边界，不能通过控制界面编辑。
项目专属限制不等于框架的全局扫描限制。

普通单实验路径的[发现适配器](write-an-experiment.md) 自动提供这些启动投影和单次运行，
也执行已声明的控制校验器。维护型多阶段流程可在自己的显式 provider 中使用同一组辅助函数。

## 发现机制如何使用声明 {#how-discovery-uses-the-declaration}

`control_catalog(controls)` 生成 `LaunchCatalogEntry.controls`。
`edit_controls(controls, invocation, config=..., edits=request.control_edits)`
把结构化 `ControlEdit` 来源转换为相同的不可变编辑操作。
`control_values(...)` 为 `LaunchPreview.controls` 提供明确的固定、扫描、派生或配置状态与来源。
作者发现路径将这三者与正常科学绑定、配置检查、手动预览检查和托管过程接纳一起使用；
单次实验不需要另写启动适配器。

`ControlEdit` 必须恰选一个来源：`fixed` 加 `value`、`scan` 加现有轴记录，
或不带两者的 `default`。未知字段和非可编辑归属的字段会被拒绝。
公共启动 worker 在调用动作前对照项目目录检查非空编辑；直接用 Python 实现 provider 时，
须调用 `validate_launch_control_edits`。没有控制项的 provider 使用空编辑时保留原请求哈希。

GUI 根据这些模型显示标签、分组、声明边界、只读归属和解析来源。
改变来源会使旧预览和提交哈希失效。在线性单位间切换会转换当前值；非线性或未知单位仅保留声明单位。
服务端仍是单位与约束的最终校验方，预览不修改设备或已接受的配置。

## 查看项目时保留启动草稿 {#keep-a-launch-draft-while-inspecting-the-project}

实验输入按登记作者工作区和实验保存在应用数据中。确认已保存的字段、控制来源和单位、科学选择及操作人
可跨页面导航、刷新和应用重启恢复。中间状态的无效文本也会保留，保存输入不代表验证通过。
关闭前等待保存成功；未确认或失败的保存无法保证在进程丢失后恢复，应保持窗口打开并重试保存。

声明变化后保留编辑供审阅。新字段采用当前默认值；被移除或变为不可编辑的字段保持待解决状态，
直到明确移除。**重置实验草稿** 用声明默认值保存新修订，旧版本和冲突副本仍留在输入历史中。
恢复输入后需要审阅和重新准备，不恢复执行资格。

输入编辑、声明变化以及参数/setup 修订变化都会使预览失效。
暂时无法读取配置时阻止提交，直到能核实上下文；不会因此丢弃保留输入。

提交响应丢失时，原始载荷与请求 key 独立于可编辑草稿保留。
**核实原始提交** 按该 key 查询保留过程；**查看已提交任务** 打开已确认 ID，不派发过程。
确认要求恰有一个匹配过程，且其公开启动意图保留请求的精确 `request_hash` 与 `config_source` 绑定。
缺少这些证据的 provider、没有匹配或多个匹配均保持未确认。
显式重试只在声明与配置仍经验证时使用原载荷和 key。
改变上下文或重置输入不会暗中重提未知请求，也不会替换其待确认记录。
