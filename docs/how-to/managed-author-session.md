# 从 Notebook 运行实验 {#run-an-experiment-from-a-notebook}

第一次无设备实验从已安装应用的帮助中打开[首个实验](../getting-started/quickstart.md)。
使用自己的实验室代码时，在 **设置 → 作者代码 · Author code** 中创建或选择文件夹，并在编辑器中选择它的 Python。
保持 Scopecat 应用运行：注册文件夹连接对应应用服务，共享应用拥有的数据，无需逐文件夹启动 daemon。

本页面向已准备的实验室作者文件夹。维护者提供执行环境、参数分支和已注册实验；
已有文件夹的准备见[应用维护](maintain-application.md#author-folders)。
例子假设存在 `bench-a` setup、实验室 `qubits` 表和 `signal` 实验，
请替换为本实验室名称；它们不是解析型 starter 的声明。编辑参数不要求先选择样品或目标。

## 日常连接、编辑和准备 {#daily-author-session}

在作者文件夹的 Notebook 中使用共享会话入口：

```python
import scopecat as sc

author = sc.notebook()
```

新会话不猜测另一数据空间。选好参数分支和 setup，再编辑、准备并保留 job 回执：

```python
author.use(parameter_branch="experiment", setup=author.setup.get("bench-a"))
parameters = author.params
parameters["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(5.1, "GHz")
checked = author.prepare(
    "signal",
    parameters=parameters,
    fixed={"gain": 1.0},
    scans={"frequency": [sc.Quantity(f, "GHz") for f in (5.0, 5.1, 5.2)]},
)
print(checked.preview)
job = checked.run()
receipt = job.receipt
```

`fixed` 提供标量控制，`scans` 接受序列、Python range 或 NumPy 数组。
裸数使用控制项声明单位；显式 Quantity 保留其单位并接受已有单位/边界检查。
结构性选项放在 `inputs={...}`；同一控制项重复提供会报错。这些适配写法不扩大实验允许的控制范围。

prepare 验证并冻结未保存参数编辑、选中作者修订及预览的手动状态校验信息。
之后修改工作区不改变 `checked`；实验室相关状态改变后，执行可能要求重新预览。
`parameters.save()` 保存参数并推进明确分支，不发布共享默认值；它与启动采集是不同操作。

以下观察、读取例子复用已经得到的 `job`，不要为了继续一个中断单元格再次调用 `checked.run()`。
后文的独立片段应在打开且已选好参数/setup 的作者会话中使用。

## 完成前观察同一次实验 {#observe-an-experiment-before-it-finishes}

使用同一保留 job 只读查询进展和有界数据预览：

```python
progress = job.progress()
print(progress.state)

preview = job.preview(limit=10)
child = preview.progress.current_child
if child is not None:
    print(child.step_key, child.run.run_id)
    print(child.run.control.completed_point_count)
    if preview.latest is not None:
        print(preview.latest.point_index, preview.latest.observables)
    if preview.live is not None:
        print(preview.live.received_record_count, preview.live.durable_record_count)

job.wait(timeout=60)
run = job.result(step="experiment")
```

每次调用只做有界读取，不启动轮询线程，也不提交或继续任务。
另一个 Python 会话可用 `author.reopen(receipt)` 重开同一回执，在实验运行中继续查询。

`progress.state` 区分等待调度、启动、等待采集/资源、采集中、收尾、执行其他步骤、
调度暂停、需要关注/输入、取消中，以及 succeeded/failed/cancelled 终态。
底层 procedure、dispatch 和子运行快照保留精确原因与身份。
接纳记录缺失仍抛出 `AuthorSubmissionUncertain`，不猜测任务已排队。

`preview()` 选择当前尚未收尾的采集，保留精确 step key、attempt 和已接纳 run ID。
多步 procedure 的连续查询可能指向不同步骤，绘图应标注该身份。
同一步骤的重试 attempt 保留已接纳 effect 身份。活动子运行不依赖历史分页；
`progress.steps` 最多包含 50 个 attempt，用 `steps.next_cursor` 调用
`job.progress(cursor=...)` 读取后页。

`latest` 是 daemon 最近接收的记录，可能尚未持久保存；数据未到达或 live buffer 关闭后可能为空。
`live` 提供已接收/已持久化记录数及 buffer 是否活动，数据集初始化前为 None；
下一次显式读取可发现 schema。`durable` 单独提供已保存点记录的有界前缀，
limit 为 1–100，`truncated` 标明省略。没有当前采集（包括该步骤结束后）时，
`durable` 和 `live` 都是 None。

这是小规模预览，不是连续流或完整数据导出，读取不会强制 flush。
live 记录已可见时，完成点数仍可能为零；已接收、已持久化和可恢复覆盖是不同事实。
每个预览明确标为 `provisional`，部分覆盖不是封存的分析数据集。
结合子运行完成点数与已接受点计划理解进度；自适应计划的已接受规模未必是最终规模。
进展与记录是先后观察，不是原子快照，执行可能在两次读取间结束。
最终用 `job.result(step=...)` 获取保留的成功采集，再使用普通分析与来源 API。

## 重启 Python 后读取同一结果 {#restart-python-and-read-the-same-result}

在 Notebook 中保留或显示 `job.receipt` 路径，并与 run 引用一起保存。
回执也位于绑定数据根的 `author-jobs/`；注册作者文件夹使用应用数据，
不会在代码目录建立新数据所有者。回执保存精确已审查请求及查找持久 procedure 的身份，不复制运行数据。

```python
with sc.notebook() as author:
    job = author.reopen(receipt)
    run = job.result()
    values = run.measurements()["result"].require_values()
    parameters = author.parameters.workspace("experiment")
```

`run`、数据集和变量通过打开的会话做惰性读取；已物化数组、元组、表和 `run.snapshot`
在会话关闭后仍可使用。需要更多数据时，使用 `job.reconnect(new_author).result()`
或 `new_author.run(run_id)`。新 Notebook 只需导入 Scopecat；
项目模块由修订感知 worker 加载，不需要改 `sys.path` 或在内核全局导入项目。

应用重启后，关闭旧会话，再调用 `sc.notebook()` 读取当前 endpoint。
`job.reconnect(session)` 仅连接同一数据空间和源码身份，不会把回执转成跨空间凭据。
旧回执文件仍可在所属数据空间的回执目录中读取。

## 等待与提交结果未知 {#waiting-and-uncertain-submission}

`job.wait(timeout=60)` 在 procedure 成功完成时返回同一 job。
等待到期抛出 `AuthorJobTimeout`；需输入或操作者关注时抛出 `AuthorJobAttention`；
持久取消/失败结局分别抛出 `AuthorJobCancelled`、`AuthorJobFailed`。
这些异常来自 `scopecat.application.author_project`。等待超时不取消执行。
`job.cancel(reason="...")` 请求在当前步骤收尾后停止，仍需检查或等待最终状态。
没有自动采集重试。

提交响应丢失抛出 `AuthorSubmissionUncertain`，其 `.job` 持有发送请求前写入的回执。
调用 `error.job.recover()`，或重启后重开该回执。恢复只查询原始请求身份；
返回 `None` 表示尚未找到接纳，不代表可以再次采集。先检查 daemon 再决定下一步。
调度失败会抛出带已接纳 `.job` 的 `AuthorJobAttention`，供继续检查。

再次调用 `checked.run()` 明确请求另一次采集。继续中断的 Notebook 时保留原 job 和回执，
不要通过新提交来“恢复”未知结果。

## 在桌面打开保留对象 {#open-retained-objects-in-the-desktop}

对应应用桌面运行时，`job.open()` 打开已接纳任务，`run.open()` 打开测量。
procedure 句柄还支持 `task.open(view="decision")`；
`publication.open()` 保留精确 project、run 或 sample 所有者。
这些调用请求原生窗口并等待宿主确认，不提交工作或启动应用。
宿主选择已安装或开发 UI 地址，Notebook 不必拼接 URL。

桌面不可用时，先打开对应应用再试。打开超时则保留异常中的请求，等待同一个请求：

```python
from scopecat.api.application_open import ApplicationOpenTimeout

try:
    run.open()
except ApplicationOpenTimeout as error:
    error.request.wait(timeout=10)
```

不要为了重试未知结果另发 `open()`。请求一分钟后过期，不能重放到重启后的宿主；
过期或宿主更换后，先检查已有窗口再新建请求。可移植分析文件需先导入应用才能在那里打开。

## 编辑作者代码 {#editing-author-code}

`author.state()` 显示活动修订。`author.refresh()` 显式验证当前源码并为后续 prepare 选中它，
隐藏通常的代次记账，但保留并发更新冲突。在显式 `project.authoring()` 会话中，
仅保存文件不会选中新代码。

Notebook/IPython 的 `author = sc.notebook()` 默认在每个单元格前及新请求创建时选择已保存源码。
两种普通导入方式都支持，新模块在下一单元格可用。已有请求、已准备实验和运行保留原源码；
任意 Python 对象和辅助函数别名不会热重载。`live=False` 与显式刷新的边界见
[源码选择指南](refresh-author-code.md#notebook-path)。

刻意使用旧代码时，保留 `checked.preview.code_revision`，再用
`author.prepare(..., code_revision=old_revision)`；目录也从同一修订加载。
保存计划保留精确源码/配置。普通分析可用
`author.analyze_as(run_id, "module:function", ResultType, arguments={...})`，
默认使用运行原源码；`source="current"` 显式选择会话当前源码。
只读已保存发布结果可用
`author.run(run_id).published_analysis(publication_id).result_as(ResultType)`，
不会重做分析。低层 `author.analyze(..., code_revision=...)` 支持精确修订选择。
实参与发布契约见[普通分析](../guides/ordinary-analysis.md)。

## 准备前编辑请求 {#edit-a-request-before-preparing}

实验室作者包已安装到 Notebook 环境时，用实验声明创建请求。
请求创建检查调用签名并捕获实参，不构建程序、导入设备 SDK 或采集；
导入实验室模块本身仍会执行其普通 Python 顶层代码。

下面使用显式作者会话；先用 `project = sc.open_project("/path/to/lab")` 打开对应作者文件夹：

```python
from my_lab.authored.signal import signal

request = signal(gain=1.0, polarity="positive")
request.values["frequency"] = sc.Scan(sc.Quantity(f, "GHz") for f in (5.0, 5.1, 5.2))
with project.authoring() as author:
    author.use(parameter_branch="experiment", setup=author.setup.get("bench-a"))
    parameters = author.params
    checked = author.prepare(request, parameters=parameters)
    alternative = request.copy()
    alternative.values["frequency"] = sc.Quantity(5.1, "GHz")
    alternative.values["polarity"] = "negative"
    next_checked = author.prepare(alternative, parameters=parameters)
    plan = checked.save_plan("Positive resonance scan", saved_by="operator")
    reopened = author.prepare_plan(plan.ref, actor="operator")
```

`values` 是结构输入、运行时输入和可编辑数值控制共用的可变字典。
赋 `sc.Scan(...)` 选择扫描，赋标量恢复固定点；只有声明可扫描的控制项接受扫描。
Scan 立即捕获 iterable，后来改 NumPy 数组不改变已捕获值。
请求捕获初始输入，`copy()` 深复制编辑数据并保留同一声明。
直接赋数组只是普通输入值，不是隐式扫描；托管标量表单会拒绝它。

函数调用参数保留声明的静态类型。字典编辑的值类型为 object，在 prepare 检查未知名称、
必填项、单位、边界和可扫描性，不生成 `request.frequency` 这样的属性。
请求没有 output 或缓存程序，每次 prepare 从选中托管源码重建；
结构编辑会产生相应新结果树。已准备 launch 和保存计划保留捕获的输入/源码。
配置、参数和执行 actor 传给 prepare；传入请求时，不再同时提供
inputs、fixed、scans 或 control_edits。

### 可选的类型化字段编辑 {#optional-typed-field-editing}

快速探索可继续使用字典。反复编辑的请求可用普通可变 dataclass 提供明确字段类型：

```python
from dataclasses import dataclass
from typing import Literal


@dataclass
class SignalInputs:
    gain: float
    frequency: sc.Quantity | sc.Scan
    polarity: Literal["positive", "negative"]


request = signal(gain=1.0).typed(SignalInputs)
request.values.frequency = sc.Scan(sc.Quantity(f, "GHz") for f in (5.0, 5.1))
request.values.polarity = "negative"
alternative = request.copy()
alternative.values.frequency = sc.Quantity(5.1, "GHz")
with project.authoring() as author:
    author.use(parameter_branch="experiment", setup=author.setup.get("bench-a"))
    checked = author.prepare(request)
```

`typed(InputClass)` 创建隔离请求，以 dataclass 作为唯一值存储；copy 保留该具体类型。
字段必须精确覆盖当前请求值，包括可编辑控制项。省略类默认值：所选值和默认值来自普通实验调用。
只有可扫描控制项使用 `Quantity | Scan`；结构字段仍用普通 Python 类型。
snapshot 返回隔离字典并保留 Quantity/Scan 对象，不把它们递归转换成记录。

这是手工维护的编辑类型，不是第二份实验声明。与普通 dataclass 一样，注解不自动验证运行时值，
应与实验签名保持一致。编辑器检查后续属性赋值，prepare 仍通过同一路径执行原声明的值、
单位、边界和扫描能力验证。类不生成控制项、默认值、源码注册或程序。
实验输入改名后也要更新可选编辑类型；保存计划保留准备值，不保留 Notebook 的 dataclass 类型。

### 检查已准备实验 {#inspect-the-prepared-experiment}

在打开且已选好配置的会话中检查：

```python
checked = author.prepare(request, parameters=parameters)
facts = checked.inspection
print(facts.total_point_count, facts.points_truncated)
print([(record.id, record.dims) for record in facts.records])
print([(step.implementation, step.placement) for step in facts.computes])
for parameter in facts.parameters:
    if parameter.kind == "lookup":
        print(parameter.table_id, parameter.column_id, parameter.key_columns)
    else:
        print(parameter.parameter_id)
print(checked.preview.code_revision, checked.preview.reviewed.config_source)
```

inspection 读取成功 prepare 捕获事实的隔离副本，不做 I/O、编译或采集，会话关闭后仍可读。
后续请求、参数或源码编辑不会更新它；要检查变化需重新 prepare。
共享证据时保留外围预览的精确 request hash、源码修订与配置身份。
保存计划仍经现有 prepare-plan 路径重新验证。

parameters 列出编译器声明的标量或表列依赖，表与列标识保持分开；
key_columns 是查询键名，不是已解析行值。这不是逐点参数值轨迹或新参数存储。
bindings/binding_edges 解释调用输入及扫描中心/覆盖关系，不是完整操作依赖图。
缺失参数仍使用现有字段诊断使准备失败。

points 最多包含编译器抽样上限（当前 64 个点），points_truncated 表示省略；
total_point_count=None 表示自适应总数未知，point_limit 仍是上界。
domain_inspections 只描述 selected_point，初始为第一点；目标拥有的 content 保留波形/程序限制与截断信息。
抽样事实不能证明所有点有效或科学有效。

各摘要集合最多 item_limit 项（当前 256）；先检查 item_counts 和 truncated 再判断是否完整。
这里不复制全扫描或配置表。维护方 launch provider 可不提供 inspection，
此时 checked.inspection 明确报告能力不可用。它是 Python/HTTP 只读事实，不提供图形编辑器或点选择 UI。

导入声明保留加载时源码身份。prepare 对照选中修订检查契约，不匹配就拒绝。
显式作者会话编辑声明后，刷新并重新导入再创建请求，或明确选原修订。
默认 Notebook 的新请求自动选择已保存源码。辅助函数从选中托管修订执行；
导入请求不固定内核辅助对象，也不声称指纹覆盖其传递依赖。

`experiment(...)` 创建可编辑请求而不执行函数体，prepare 才构建和验证，没有单独 .request 工厂。
维护者需要本地不可变程序时使用 `experiment.build(...)`，保留检查后的函数参数和
typed invocation.output，不采集或选择托管源码修订。
低层 .bind 支持组装调用时部分绑定运行时输入，结构输入仍须完整。
这些构建对象与可编辑请求分开；字典编辑不生成静态属性，可选 typed 视图仍需显式声明。

## 在输入旁声明控制项 {#declare-controls-next-to-their-inputs}

在作者模块用 Annotated 元数据声明控制，不必维护另一套控制常量。
参数名拥有 ID，Python 默认值拥有起始值，函数体接收符号输入或扫描坐标：

```python
from typing import Annotated
import scopecat as sc


@sc.experiment
def amplitude_probe(
    experiment: sc.ExperimentContext,
    amplitude: Annotated[
        sc.Input[float], sc.ControlSpec(minimum=0, maximum=0.9, scannable=True)
    ] = 0.1,
    gain: Annotated[sc.Input[float], sc.ControlSpec(minimum=0)] = 1.0,
) -> sc.Input[float]:
    return amplitude * gain
```

amplitude_probe() 包含两个默认值。编辑
`request.values["amplitude"] = sc.Scan([0.1, 0.2])` 选择扫描，赋 0.15 恢复固定坐标。
`amplitude_probe.build(amplitude=0.15)` 显式构建固定坐标，没有重复运行时输入来源。
gain 仍是不可扫描的标量运行时输入。GUI 控制、边界、单位和保存计划来自同一个派生 ControlSet。

ControlSpec 支持 float/Quantity 输入，可设置 unit、minimum、maximum、title、group、scannable。
元数据省略单位时，Quantity 默认值提供单位，例如
`Annotated[sc.Input[sc.Quantity], sc.ControlSpec(minimum=4, scannable=True)] = sc.Quantity(16, "ns")`。
Quantity 不可变，可作 Python 默认值；维护的 lint 配置识别这种写法。
作者文件局部关闭 basedpyright 可选的整体 reportCallInDefaultInitializer 诊断，其他类型检查仍保留。

操作人必须选择值时省略默认值；必填 Quantity 控制必须在 ControlSpec 指定单位。
发现仍可成功，GUI 显示空必填字段，prepare 拒绝缺值。
必填扫描可通过 `author.prepare("name", scans={...})` 提供，不必虚构标量起始值；
类型化实验调用仍先要求创建参数，之后才能编辑字典。

Input[T] 同时描述调用方具体值和函数体符号引用，不让符号值变成 Python 控制流里的普通数。
目标名、整型 shot 数和结构选择保持普通 Python 参数；结构编辑在 prepare 重建程序。
每个字段只设一个元数据所有者：不要同时在 ControlSpec 与显式 ControlSet 重复声明，
也不要同时附另一份 ValueType。维护者仍可通过 ControlSet 提供 owned/derived 控制与项目 validator；
元数据派生字段加入同一集合，不建立第二套控制注册。

### 返回类型化点坐标 {#return-a-typed-point-coordinate}

Input[T] 配合可扫描 ControlSpec 已有自动点坐标，即使当前值固定。
函数体用 `context.coordinate(amplitude)` 获得 CoordinateRef[T]，
放入类型化结果 dataclass 无需 cast；保留原字段名和记录路径，不新增记录或改变扫描。
它也接受 context.scan 返回的坐标，但拒绝普通运行时输入和计算表达式；
计算值应作为普通结果返回。

## 刷新共享源码与编译器代码 {#refresh-shared-code-and-compiler-code}

普通实验室模块放在声明的 authors.source_roots 内，不需额外 refresh_roots。
显式作者会话中，保存实验、分析、配方、共享辅助函数或编译器工厂后，沿用连接刷新：

```python
state = author.state()
author.refresh_authors(expected_generation=state.generation)
checked = author.prepare("signal")
```

刷新在新进程验证捕获源码，应用保持运行。已准备请求和保存计划各自保留源码版本；
编辑共享代码不改变它们。选中 setup 的编译错误需修源码、刷新并重新准备，
旧任务仍可使用此前保留源码。驱动修改走独立的
[空闲设备更新](control-instruments.md#update-driver-source-while-the-application-is-running)，
作者刷新不切换当前驱动版本；依赖须已存在于应用环境。

### 准备延迟 {#preparation-latency}

某源码修订首次 prepare 会加载隔离作者环境，重复 prepare 复用 worker，
但仍检查当前请求和配置。刷新选择另一版本，旧已准备实验保留原身份。
维护者可用[作者性能基准](../development/author-performance.md)复现和诊断延迟。

## 高级：应用选择、定位与服务身份 {#application-selection-and-locators}

注册在用户本地元数据中保存作者到应用的关联，不放在源码文件夹内。
独立启动的 VS Code 内核因此无需继承启动器变量也能找到同一应用。
新注册不在原本干净的作者目录写 scopecat.runtime.toml 或创建 .scopecat 锁。
关联指向数据实例而非应用可执行文件/版本，因此应用更新保留关联。
它固定科学 project_id 和作者注册 ID；应用路径只是本机地址，同址替换数据会身份检查失败。
显式注册可在 writer lock 下用正常 bootstrap 初始化选中的空应用；
Notebook 自动发现绝不初始化数据空间。

源码注册属于各自数据空间。同一作者目录可显式注册到另一应用；
首次注册确定默认目标，后续注册不悄悄改默认偏好。各目标回执独立固定注册身份，
中断写入仅阻止该目标，直到对它重做注册。
现有 format-1 默认 locator 仍可读，重注册增加目标回执而不改变原科学身份。
旧默认不可读且没有目标回执时，应先恢复旧默认，不能猜测其原身份；
已有有效独立回执的目标不受无关默认损坏影响。

已显式注册的替代应用可用 `sc.notebook(application="/path/to/application")`。
由应用启动的 Notebook 继承该应用目标，两种选择都不改保存的默认值。
默认仅是客户端偏好，不是作者目录的独占归属。

会话捕获数据空间、作者身份和当前服务生命周期，请求在分发前检查身份。
修改默认值或复用服务地址不能把已有会话重定向；固定身份会话不会自动重放未知写入。
缺少生命周期检查的服务会被拒绝，应先更新应用再用此 Notebook API 连接。

应用停止、不可用或注册冲突时，调用失败，不选另一个运行/默认应用，也不创建新数据空间。
启动所关联应用后重试。移动作者文件夹需显式重注册；
移动应用需所有者有意恢复关联，不默默改地址。

高级调用可传 daemon= 或设置 SCOPECAT_DAEMON_URL，仍检查注册所有者。
开发内核可用 SCOPECAT_APPLICATION_ROOT 指定精确注册目标。
优先级为显式 application=、继承上下文、保存默认值；
目标仍需唯一源码注册和匹配科学身份。默认缺失/离线不阻止有效显式替代目标。
没有注册或继承配置时，重开编辑器不会猜开发工作树。

locator 目录在 Windows 为 `%LOCALAPPDATA%/Scopecat/author-locations`，
macOS 为 `~/Library/Application Support/Scopecat/author-locations`，
Linux 为 `$XDG_DATA_HOME/Scopecat/author-locations`（默认基于 `~/.local/share`）。
SCOPECAT_LOCATOR_HOME 可为隔离开发/测试指定绝对目录，所有参与进程须使用同一值。
不要把这些机器本地记录复制进可移植作者模板。

现有显式 runtime 文件仍可读。只有所有有效 runtime 地址/设置都匹配选中所有者时，
显式注册才新增关联，原文件保持不变。自定义或冲突绑定需有意解决。
注册不移动科学数据、不删除旧锁，也不接管活动独立 runtime；
它不迁移旧科学数据格式或合并数据空间。冲突旧 runtime 文件须先解决，
显式目标选择不绕过该限制。

### 准备后台标定任务 {#prepare-a-background-calibration-task}

author.calibration_tasks（sc.notebook() 也提供）使用已有 CalibrationTaskPlan、task_call、
repair budget 和 finalization 契约：

```python
prepared = author.calibration_tasks.prepare(
    "calibration-round-1",
    plan,
    calls=calls,
    finalization=finalization,
    repairs=repairs,
    repair_budget=repair_budget,
)
view = prepared.submit()
view = author.calibration_tasks.start(view, actor="author", reason="Run this round")
```

选定作者修订并导入声明后构建 calls。准备为全部阶段、修复、候选验证和 finalization
捕获一个 ProcedureSource 及隔离归一化调用意图；会话实验默认值不替换这些输入。
应用接纳前按保留源码验证模板与解析调用，并负责 worker 执行及任务推进，
无需第二客户端或 Notebook 轮询循环。

submit() 只保存规格，不启动。响应未知时重试同一 prepared 对象和 task ID；
源码文件变化、调用方字典修改或暴露 command 副本变化不改变这次重试。
prepared.reconnect(new_session) 在重连前检查 workspace、数据空间和 deployment，
提交还会重查应用身份。

重开后用 author.calibration_tasks.get(task_id) 或有界 list()。
wait(task_id, timeout=90) 只观察，返回停止任务、接纳错误或 procedure 关注/输入状态；
超时停止等待，不取消。检查返回 view，再显式 start(view, actor=…, reason=…) 继续接纳。
人工输入与协调仍走已有 procedure 控制；pause/cancel 阻止后续阶段接纳，
已接纳 procedure 保留自身控制。

阶段证据在 view.progress.stages，attempt 及阶段记录在 view.task.attempts。
用 author.procedures.get(id) 读取各 procedure，在同一会话以 author.run(...)
或 author.published_analysis(...) 读取输出。finalization 是 view.finalization 中独立 procedure。
任务 finished 或阶段检查成功不等于科学接受或分支发布，仍须检查最终决定和发布回执。
这些读取不会重试阶段、刷新源码、推进任务或发布分支。
