# 组合扫描并分析完整分组 {#compose-scans-and-analyze-completed-groups}

从绑定当前作者版本的实验声明开始（见[刷新作者代码](refresh-author-code.md)）。
组合方法返回副本，请保存返回的请求：

```python
signal = author.refresh(signal)
request = signal().sweep(frequency=[4.7, 4.8, 4.9], gain=[1, 2])
prepared = author.prepare(request)
run = prepared.run().wait().result()
```

输入必须声明为可扫描。默认 `mode="cartesian"` 遍历所有组合，上例共六个点。
`mode="paired"` 将等长的显式扫描列表逐项配对，固定控制值保持不变；长度不等会报错。
仪器在单点内返回的频率或 shot 数组仍属于该点，不会自动变成外层请求轴。
控制台目前编辑 Cartesian 控制扫描；paired 扫描和参数覆盖使用 Python 作者 API。
在控制台打开包含暂不支持设置的方案时会明确提示，不会丢弃设置。

如需改变某个被实验消费的参数单元，而不编辑已保存的参数工作区：

```python
request = signal().sweep(frequency=[4.7, 4.8, 4.9], mode="paired")
request = request.sweep_parameter(
    QubitParameters.drive_carrier_frequency,
    "q0",
    [4.8, 4.9, 5.0],
    name="center",
)
```

字段声明提供单位和 key 类型，实验必须实际消费这个精确单元；未知单元和重复坐标名会报错。
通过 `with_recipes(...)` 选择的声明式量子门/测量配方也包括在内：字面量操作数与无条件操作
可以消费参数单元，无需伪造 `parameter_ref` 读取。查询可使用别名、算术和跨表 key。
若某单元的查询 key 本身也被覆盖，无法保证扫描中始终消费同一单元，因此会拒绝；
选择器单元自身的 key 固定时，仍可扫描该单元。

消费检查不会构建脉冲或展开扫描点/重复。对于任意 Python resolver、动态操作数、条件分支、
实体集合映射、未展开的动态片段及带作用域的候选门，检查仍保守处理。
作用域门使用 `with_recipe_parameters(...)`，作用域内的测量仍沿用基线语义。
领域编译器必须消费 `DomainBatchRequest.parameters` 中的有效单点快照，
配方参数覆盖才能进入实际波形。预览、提交和保存方案都会保留组合设置，并纳入请求身份。

## 从运行页面分析已有结果

在运行详情展开**分析此结果**，读取该运行已保存源码的分析声明。
**重新加载分析声明**只重新读取声明，不执行分析；读取失败时先检查原始错误、连接及原始作者环境。
当前源码后来增加的声明不会替换旧运行保存的声明。作者提供的标题和说明原样显示，不翻译作者输入名。

填写输入后，点击**执行 {作者标题}**才执行分析。它使用已有数据和保存的作者代码，
不采集新测量，也不采纳参数。完成后用**查看已保存的结果**打开返回的精确发布。
若响应丢失，先检查分析历史再决定是否重试；界面不会自动重试未知结果。

已有整次运行分析若保存了匹配的设置，可点击**按已保存设置重新分析**。
系统核对设置附件 hash、原始 `code_revision` 和运行身份，使用原参数重新执行；
旧结果不变，相同结果可能复用已有发布。重新分析与只读打开结果是不同操作。

**分析结果**同时列出保存在该运行上的发布，以及直接使用其测量或配置的发布，
无论它们保存于运行、样品还是数据空间。每个发布只显示一次，更多结果通过**加载更早的分析结果**读取。
**直接运行输入**只展示明确声明的输入，不根据名称或间接依赖推导关系。
**以此结论为来源的后续实验**按精确 publication hash 读取后续运行；这些运行不是本分析的输入。
使用**打开已保存的版本**阅读精确结果，返回时保留阅读位置与展开状态。
保存归属不代表科学有效性，也不会因阅读链接而搬迁结果。

## 已完成运行的离线分组分析

对**已完成**运行，可将普通分析函数分别应用到各组：

```python
fitted = author.analyze_groups_as(
    run.id,
    "my_lab.analysis:fit_resonance",
    ResonanceFit,
    by=("gain",),
    fitting="frequency",
    repeats="separate",
)
for group in fitted.groups:
    print(group.receipt.coordinates, group.receipt.error, group.value)
```

`by` 选择逐点标量坐标，`fitting` 标识拟合使用的坐标，也可以是仪器的局部轴。
每次调用收到所选 Dataset，保留原有维度、单位和缺失值信息。
选点、拟合、科学拒绝与不确定性估计由作者函数负责，不由分组调度器决定。

基于 context 的 `@sc.analysis_step` 也可使用同一调用。
`context.measurements()` 只返回当前组；显式调用 `context.measurements(reference_run)`
读取参考运行的测量，不套用主运行的选点。普通函数和 context 函数共享类型化参数解码及保留来源默认值。
无效调用参数在分组执行前失败，组内异常只记录在该组回执，不阻止其他组继续。

`repeats="separate"` 还会按名为 `repeat` 或以 `/repeat` 结尾的坐标分组。
`repeats="combine"` 将重复一起传入，**不求平均**。其他未选择的坐标也保留在输入中。
请在函数内明确自己的科学汇总策略。

每组保存精确点索引、测量 hash、源码版本、参数和发布；父发布保留分组清单。
异常成为组错误回执。科学拒绝也可以用包含状态、但没有候选值的正常类型化结果表示。

## 固定 Cartesian 扫描的实时完整组分析

采集仍在进行时，可跟随固定 Cartesian 扫描中已完整的组：

```python
follow = author.follow_groups_as(
    run.id,
    "my_lab.analysis:fit_resonance",
    ResonanceFit,
    by=("gain",),
    fitting="frequency",
)
follow_id = follow.id  # 与 run ID 一起保存
cursor = 0

# 需要下一页时再次调用；每次请求都有界。
page = follow.poll(after=cursor)
for group in follow.results(page):
    print(group.receipt.coordinates, group.receipt.error, group.value)
cursor = page.next_cursor
print(page.follow.state, page.follow.finished_count, page.follow.group_count)
```

只要 `page.has_more` 为真，就继续读取后续页，即使任务已完成。
`state == "running"` 时空页仅表示暂时没有新结果。

实时分析向普通函数或 context 函数传入的分组结构与离线分析相同。
只有组内每个预期逻辑点都已有提交的采集记录，该组才可执行。
收到数据或执行恢复组完成本身不足以证明这一点。仪器返回的频率数组仍属于单点；
重复 T1 分组保留全部计划延迟。paired、点云及 adaptive 域目前没有实时完整性契约，会明确报错；
已完成运行的离线分析仍可使用。本流程不增加这些域的实时支持，也不创建新的测量。

每组固定一个小型清单，保存精确采集身份及 hash，引用原数组而不复制。
启动 follow 时固定源码版本和参数，后续数据或源码修改不会改变已有输入与结果。
导出的 `.scopecat` 文件保留这些切片，可用
`capture.measurements(run_id, selector=slice_id)` 读取。

应用使用现有有界作者 worker，逐组执行后台分析。默认每个 follow 最多 1,000 组，
每组 4,096 个逻辑点、64 MiB 编码输入块、每次调用 60 秒；
这些预算可通过 `follow_groups_as` 配置，同时最多运行八个 follow。
规划最多读取每个分组轴 65,536 个值（含重复值）。组的科学失败彼此隔离。
采集结束后仍缺少点的组标记为 incomplete；`completed` 只表示处理结束，
使用结果前还需检查 `failed_count` 和各组回执。

## 恢复阅读与停止分析

关闭 Python 不会取消采集或分析。可重连并读取原任务，不重新执行：

```python
follow = author.reopen_group_follow_as(follow_id, ResonanceFit)
page = follow.poll(after=cursor)
# 只停止分析：
follow.stop()
```

运行页面的**实时分组分析**显示进度，新发布随组完成出现在结果区。
**停止分析**请求取消当前 worker 调用，不停止采集；继续轮询直到状态稳定。
应用若在发布期间中断，会进入 `attention`（需要检查），结果不确定时不会静默重跑。
先检查保留的发布，再决定是否启动另一个 follow。活动分析停止前，输入清理会被阻止。
这些结果不会自动约束后续采集；反馈必须具有显式执行依赖。带状态的流式 reducer 是另一项能力。
界面只显示最近 100 个 follow，更早任务通过已保存 ID 读取。

参考实验室的[普通 trace 示例](../../testing/reference_lab/src/reference_lab_authors/authored/group_traces.py)
提供 `power_trace` / `locate_resonance` 和 `synthetic_t1` / `fit_decay`。
前者使用 `by=("power",), fitting="trace/frequency"`，后者使用
`by=(), fitting="delay", repeats="separate"`。虚拟仪器返回完整频谱；
T1 示例刻意使用已知、零基线的合成衰减。科学假设可在普通函数中阅读，
软件检查不构成硬件或物理拟合策略的验证。

编辑分析源码后，调用 `author.refresh()` 并以 `source="current"` 重新分析，才使用当前源码；
默认仍使用运行的原始源码。旧发布不可变，重启后仍可只读打开，不执行分析代码：

```python
old = author.read_groups_as(run.id, fitted.publication.id, ResonanceFit)
```

类型化参数、原生结果 dataclass、表格和图形详见[普通 Python 分析](../guides/ordinary-analysis.md)。
