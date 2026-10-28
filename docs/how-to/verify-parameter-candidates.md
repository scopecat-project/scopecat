# 验证拟合参数候选 {#verify-fitted-parameter-candidates}

[参数声明](declare-parameter-models.md)提供字段身份，[表格交换](exchange-parameter-tables.md)用于手工编辑与保存版本；
本流程则从[普通分析](../guides/ordinary-analysis.md)返回的托管结果开始。
候选是归属于来源运行的已保存提议。保存候选既不验证它，也不改变实验室默认配置。

```python
fit = author.analyze_as(run.id, "my_lab.analysis:fit_rabi", RabiFit)
candidate = author.config.stage(
    fit,
    name="rabi-carrier",
    table="drive",
    key="q0",
    fields={"amplitude": "pi_amplitude"},
)
```

`fields` 将已有参数列映射到保留结果的字段。Scopecat 重新打开已发布的分析记录并读取值；
修改 `fit.value` 不能替换证据。结果字段缺失或为 `None` 时，暂存候选会停止。
提议保留来源运行、原始拟合发布记录、来源修订、参数和精确变更的单元格，不相关单元格保持原内容。
手工编辑和估计值仍使用[参数工作区](manage-configuration.md)；本地数据类不是托管回执，
普通编辑不会自动成为测量结果，手工导入也不提供验证证据。

使用这个精确的已保存候选采集新数据：

```python
check_run = (
    author.prepare("verify_rabi", candidate=candidate, scans={"amplitude": amplitudes})
    .run()
    .wait()
    .result()
)
check = author.analyze_as(
    check_run.id,
    "my_lab.analysis:verify_rabi",
    Verification,
    arguments={"maximum_error": 0.02},
)
verified = candidate.verify(check)
```

实验室负责注册验证函数并定义科学接受策略。返回的数据类必须声明 `accepted: bool`。
只有数值拟合或成功采集还不够。Scopecat 要求一个独立且已完成的运行，
该运行使用精确的同一提议、相同的样品修订和工作点。项目决策保留两次数据输入与验证分析的发布记录。
拒绝决策也会保存供查看，并抛出可据以处理的错误。

按照[已验证分支发布](publish-working-point-calibration.md)，发布到基线采集前捕获的精确参数分支：

```python
published = verified.publish_to_branch(
    baseline_branch,
    name="rabi-verified",
    note="Independent policy passed",
)
next_run = author.prepare("next_experiment", candidate=verified.select()).run()
```

发布只推进已审查的分支。后续分支编辑需要重新审查；此处不提供全局默认配置发布或撤销操作。

选择仅作用于 `prepare`，不创建可变的全局选择。候选在验证前也可用于探索运行，并保留其候选来源。
候选启动不保存为实验计划；计划目前使用保存的参数上下文。

用 `author.config.candidate(source_run_id, "rabi-carrier")` 重新打开候选。
名称属于来源运行，不是实验室范围的最新值指针。按运行和发布 ID 重新打开其独立分析，
再调用 `candidate.verify(result)`。

## 在应用中用候选准备新实验 {#prepare-from-a-candidate-card}

在来源运行的「参数候选」卡片点击「用于新实验」，选择代码工作区和实验类型，
再点击「创建候选草稿」。取消选择会返回原卡片，不改动已有实验草稿。
新草稿使用所选实验的输入声明，不复制来源运行的旧扫描或当前编辑器的输入。
候选无需先验证或发布；其精确参数、来源分析、原样品修订、批次和实验配置会保留。

在准备页核实输入并设置扫描，再预览和显式点击「开始采集」。创建草稿和预览不启动采集，
不修改参数分支，也不把候选标为验证通过。新运行保留所用候选的关联；
科学独立性及验证判定仍按上文流程取得。

若候选记录、原实验配置、来源 hash 或科学作用域不匹配，应用会说明无法继续的原因，
不会改用最新候选或默认参数。切换到其他页面时若保存已完成，草稿仍可从草稿列表重新打开。

## 在后续实验中细化候选 {#refine-a-candidate-in-a-later-experiment}

在初始基线采集前捕获目标分支，并用其精确修订采集基线。保留这一分支头直到最终发布：

```python
daily = author.parameters.checkout("daily").head
author.use(parameters=daily.revision, setup=author.setup.get("bench-v1"))
```

从基线分析暂存 `first` 后，使用 `candidate=first` 准备下一次校准。
通过同一个 `author.config.stage(...)` API，从新运行的托管分析暂存 `second`。
它可以细化 `first` 已修改的单元格。然后保留候选链：

```python
combined = first.then(second, name="coarse-then-fine")
final_run = author.prepare("verify_rabi", candidate=combined).run().wait().result()
final_check = author.analyze_as(
    final_run.id,
    "my_lab.analysis:verify_rabi",
    Verification,
    arguments={"maximum_error": 0.02},
)
verified = combined.verify(final_check)
published = verified.publish_to_branch(daily, name="coarse-fine-accepted")
```

`then()` 记录已运行的工作，不调度实验。每个后续来源运行必须消费紧邻的前一个精确候选。
复制到其他修订的值、不相关运行中相同的数值以及颠倒的阶段都不符合要求。
更长的链按顺序传入原始提议：`first.then(second, third, name=...)`；不支持嵌套组合。
没有净变化的链会被拒绝，不会人为生成提议。

中间候选不推进 `daily`。最终验证包含所有贡献来源运行，以及使用聚合候选采集的新数据，
不继承先前的接受决策。并发分支编辑会阻止发布，但不丢弃候选链；恢复其分析记录会保留有序来源。
`combine()` 仍是组合来自同一基线的同级提议的独立操作，遇到冲突编辑会拒绝，而不是顺序应用。

此 API 保留已完成的候选链。固定检查可以通过 `lab.resolve_context(candidate=combined).context` 捕获它。
若输出只有在前置任务结束后才存在，使用显式的
[任务候选边](preview-calibration-tasks.md#bind-a-prerequisites-candidate-output)。
这条边绑定输入，不会自动聚合候选链或发布。

预览保留精确候选、科学绑定和可执行装置配置内容。仅改变参数的默认配置变更不会使该固定候选失效；
可执行装置配置改变或相关手工仪器变更仍需要重新预览。
选择当前默认配置会保留其激活代次约束。共享默认配置的发布仍受验证接受与全局代次约束：
过期基线不能通过此接口成为默认配置。
独立发布使用[精确工作点](publish-working-point-calibration.md)。
检查具名表、行和字段，显式重新打开所需参数工作区或调整其基线，并为任何改变的提议收集独立验证证据。
恢复先前的默认配置不会重新赋予校准有效性。
