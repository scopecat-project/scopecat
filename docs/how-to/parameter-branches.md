# 在分支上编辑参数 {#edit-parameters-on-a-branch}

参数修订（revision）保存不可变的声明和值；分支为编辑历史命名。
分支不拥有设备，也不证明参数已经校准或适用于某个样品、降温批次。
普通保存只记录编辑；要声明候选通过验证，需另行采集独立证据并显式发布。

以下示例假设 `lab` 与作者会话 `session` 已连接同一数据空间，
`initial` 是已有参数修订，且已声明 `drive` 表及 `q0` 行的 `frequency` 列。
数值和单位应遵循实际声明，不要直接照搬示例频率：

```python
lab.parameters.create_branch("chip-a/daily", revision=initial)
session.use(parameter_branch="chip-a/daily", operator="Alice")
params = session.params

params["drive"]["q0"]["frequency"] = 5.15
params.diff()
version = params.save(note="记录本次试调")
```

只编辑时，可用 `params = lab.parameters.workspace("chip-a/daily")`，
不需要 setup、样品或工作点。类型化行视图、标量、声明、表结构修改、表格导入/导出、
`diff()`、`copy()` 与 `discard()` 都复用现有参数编辑器。

尚未安装或选择 setup 时也可使用 `session.use(parameter_branch=...)` 和 `session.params`。
选择操作检查参数、对象和批次引用是否存在，不证明设备能执行它们。
未知值和未完成的校准表可以保存；准备实验时才检查 setup 兼容性、必需值和支持的执行拓扑。

选择可执行 setup 后，可在一次准备中组合参数编辑器与测量上下文。
这里的 `experiment()` 返回作者目录中的实验请求，样品和批次必须已经存在：

```python
prepared = session.prepare(
    experiment(), parameters=params, sample="chip-a", batch="cooldown-1"
)
```

这些参数只作用于本次准备，不替换会话的对象或参数分支。
`target=...` 选择已登记目标；省略对象/批次参数则继承会话选择，
显式 `sample=None` 表示不绑定样品。`parameters=params` 不能同时配合
另一配置选择 `selection` 或独立的 `overrides`；不要混用互斥输入。

普通 `params.save()` 生成修订 ID 并推进当前分支；没有改动时返回已有版本。
保存不改变实验室默认配置。`params.save("chip-a/trial")` 创建并切换该编辑器到新分支，
原分支不变。用 `lab.parameters.history("chip-a/daily")` 查看历史，
用 `lab.parameters.get(version.id)` 读取精确版本。

检出（checkout）会捕获编辑基线。其他写入者推进分支后，保存会报告冲突，
既不丢失本地草稿，也不留下孤立新修订。`params.rebase()` 可合并最新 head 中不冲突的单元格；
发生单元格冲突时，整个草稿保持原样。参数声明变化或尚未保存的结构编辑需要人工审阅，
不会自动合并 schema。重新 `session.use(parameter_branch=...)` 会建立新检出，
不会合并旧编辑器尚未保存的值；切换前先处理旧草稿。

保存响应丢失时，在同一编辑器中保持输入、目标、操作者和 note 不变，再次保存会复用待确认命令。
改内容或重新检出不等于恢复原请求。独立 `copy()` 保留自己的编辑基线，
保存副本不会移动原会话的选择。

## 在工作台使用 Notebook 保存的参数 {#use-a-notebooks-saved-parameters-in-the-workbench}

在实验表单的**本页测量上下文**展开**选择参数分支**。选择分支，
检查 generation、操作者、说明和版本，再点击**使用此参数版本**。
页面保留对象、批次和操作者，旧预览失效；采集前重新预览。

页面选定的是精确保存修订。Notebook 中继续保存分支，或点击**刷新参数分支**，
都只更新可选目录，不自动替换已选输入或已有预览。
采用新 head 需要显式使用该版本；**加载更多参数分支**用于分页。
刷新失败保留已有输入，并阻止采用选项，直到成功重试。
**清除参数选择**将配置选择置为未选择，不自动恢复某个默认参数或 setup；准备前重新选择所需输入。

选择器只读取保存值，不读取 Notebook 的未保存缓冲区。创建、编辑、派生和 rebase
使用上面的编辑器，或[工作台参数编辑器](manage-configuration.md)。
选择分支不激活 setup，也不建立校准有效性。

Python 客户端可分页浏览 head，无需先读取每个完整修订：

```python
page = lab.parameters.branches(limit=100)
for branch in page.items:
    print(branch.name, branch.generation, branch.revision.revision_id)
if page.next_cursor is not None:
    page = lab.parameters.branches(after=page.next_cursor)
```

目录按分支名排序，每个分支只显示当前 head。分页是实时目录视图，
不是整个目录的冻结快照；采用某个返回的修订才固定具体版本。

## 不保存数值编辑，直接准备 {#prepare-without-saving-value-edits}

`session.prepare(request, parameters=params)` 将未保存的**数值**编辑冻结为仅供本次运行的覆盖值。
预览不写入修订或推进分支，之后编辑 `params` 也不改变已有预览。
运行和保存计划保留精确基础参数、setup 引用及覆盖值。

**结构**变化必须先保存再准备。不传 `parameters=params` 时，准备使用会话选中的已保存修订，
不会悄悄加入编辑器的未保存内容。若要把后续候选发布回分支，应先保存试调值，
再捕获发布目标并采集基线；带未保存覆盖值的基线不能冒充该分支修订。

## 发布已验证候选 {#publish-a-verified-candidate}

先捕获目标分支头，并使基线采集使用其精确参数修订。完整顺序见
[发布已验证候选](publish-working-point-calibration.md)。
若 `q0_candidate`、`q1_candidate` 是从同一保存参数修订取得的保留候选，可组合：

```python
joint = q0_candidate.combine(q1_candidate, name="joint-drive-calibration")
prepared = session.prepare(experiment(), candidate=joint)
```

组合保存新提议，保留每个贡献运行、分析、提议和内容身份。
服务端读取保留提议，不以修改后的 Python 对象为准。
来源必须共享精确保存参数修订、setup、科学对象/场景，且没有未保存覆盖值。
单元格冲突会拒绝整个组合，不发布部分内容；在一次调用中传入全部原始候选，不支持嵌套组合。

组合候选**不继承原候选的接受结论**。用它显式采集新数据，再执行适用于组合实验的验证策略。
`joint.verify(check_result)` 保留各贡献基线与组合候选数据；个体候选先前的验证不能发布组合候选。
框架检查证据身份和范围；实验室策略负责判断测量是否覆盖需要关心的物理相互作用。
组合本身不调度测量，也不推进分支。

取得独立候选数据及保留的正向策略决定后，显式发布。
此处 `destination` 必须是**基线采集前捕获**且与基线参数一致的 `ParameterBranch`，
`candidate` 与 `check_result` 必须指向同一个精确候选：

```python
verified = candidate.verify(check_result)
receipt = verified.publish_to_branch(
    destination,
    name="chip-a-readout-verified-1",
    note="独立读出验证通过",
)
# 显式采用已发布的精确版本，供后续准备。
session.use(parameters=receipt.revision)
```

`name` 标识不可变结果修订。响应丢失时，保持同一候选/决定、目标、名称、操作者和 note
重试以恢复同一回执，即使之后分支已被其他提交推进。
不同发布请求若使用旧 head，会失败且不创建修订。
重新检出较新 head 不授权复用旧候选：基线运行必须使用目标的精确参数修订且没有未保存覆盖值。
基线改变后应重新采集证据。

服务端检查保留提议、决定、独立且成功的候选运行，以及匹配的样品/目标、执行场景和 setup。
结果保存与指定分支推进在同一事务内完成；已准备运行、其他分支、实验室默认值和 setup 不变。
回执的 `publication` 记录来源运行、提议和验证决定，`previous` 标识精确基础参数修订。

把候选值抄进 `params` 再保存仍是普通手工编辑，不是校准接受。
普通保存没有 `publication` 声明，历史保留先前发布回执。
发布已验证单元格不表示整个分支的每个参数都已校准，也不表示适用于另一样品。
此操作不自动合并多个已验证候选，也不提供整批候选统一发布。
旧工作点及全局默认发布 API 已退役，验证发布使用 `publish_to_branch()`。

## 在持久 procedure 中发布 {#publish-inside-a-durable-procedure}

已登记 procedure 可复用相同服务端验证和原子分支发布，不激活整套配置：

```python
published = ctx.publish_parameter_candidate(
    "publish-calibration",
    candidate_ref,
    proposal_id="joint-drive-calibration",
    verification=verification_ref,
    decision_output_id="decision",
    branch=intent.destination,
    name=intent.result_revision_id,
    actor="calibration-worker",
)
```

`candidate_ref` 是此前步骤保留的运行分析输出引用，`verification_ref` 是验证决定所在的项目分析输出引用。
procedure 的类型化 intent 必须在提交时捕获目标 `ParameterBranch` 与结果修订名称，
不要在重放时重新检出不断变化的 head。组合候选仍需保留的组合验证决定；发布步骤不证明目标覆盖完整。

步骤保存完整已接纳分支回执。已完成步骤重放回执，不重新打开证据或读取今天的 head。
若发布已提交、步骤结果尚未保存，重复同一命令会恢复历史发布。
无法确定结果的传输失败需要人工处理；重试同一 intent，不另选新目标。
改变目标或证据会改变步骤身份。将只分析部分恢复到另一 procedure 时，
会排除任何已经尝试的参数发布，正如排除旧配置接纳一样。

持久输出遵循[现有数据兼容策略](../development/data-compatibility.md#what-remains-supported-now)，
包括明确支持的升级边；本操作不新增迁移或重写历史文件。
目标选择、持久组合、组合验证及 worker 设置见[自动参数校准](automate-parameter-calibration.md)。

## 科学上下文保持独立 {#scientific-context-remains-separate}

选择会话参数分支或使用独立分支编辑器准备时，保留对象、批次、操作者和记录集。
选择另一样品/目标会保留检出、未保存编辑和已选 setup，并清除上一对象的批次。
选择保存的参数修订或工作点会退出分支模式。分支名不强制绑定样品/降温，也不代表校准接受。

底层 `checkout(...).save(catalog=..., parameters=...)` 供完整快照生产者使用；
普通作者用 `params.save()`。[工作台参数编辑器](manage-configuration.md)使用相同独立分支，
不虚构样品或工作点。它能重开已有分支的可恢复工作表，显式捕获当前输入用于实验，
按需保存参数检查点，也能编辑保存版本的独立副本；分支选择始终是显式操作。
