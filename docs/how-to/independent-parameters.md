# 先保存参数，再选择执行上下文 {#save-parameters-before-selecting-execution-context}

参数声明和值可以先于实验配置、样品或批次存在。通过已连接的 `lab` 或作者 `session` 保存：

```python
parameters = lab.parameters.save(
    name="initial-estimates",
    catalog=author_parameter_catalog,
    parameters=estimated_values,
    note="Initial estimates, not accepted calibration",
)
```

保存会按声明验证值并创建不可变修订；不要求 setup，不改变默认选择，也不打开设备。
内容改变时使用新名称；以同名和相同输入重试会返回原修订。
`lab.parameters.get(name)` 和 `.list()` 可重新读取已保存版本。
保存不表示这些值已针对某种物理条件完成标定。

在 Notebook 或作者会话中直接选择参数版本：

```python
session.use(parameters=parameters)  # 也可传入已保存名称或精确引用。
```

这会保留当前对象、批次、操作者和记录集。独立参数替换配置选择，不替换整个上下文，
也不影响其他会话；选中值仍不代表已确认标定适用性。
选择和编辑参数不需要可执行 setup，但预览或运行实验前须在本会话选择已保存 setup。
需要持续编辑时，使用[参数分支与 `session.params`](parameter-branches.md)。

预览通过共享测量解析器解析参数修订和选中的 setup，不创建组合配置注册项。
已审查请求保留两者的精确引用；后来改选 setup 或编辑会话不能悄悄替换它们。
接纳使用该请求固定的 setup，不依赖其他客户端的默认选择；共享物理资源仍遵守所有权检查。
保存的实验计划也固定这两个引用，并复用同一解析器。

低层运行入口可使用同样的只读解析结果。对于 `@sc.experiment` 声明，显式构建调用：

```python
inputs = lab.parameters.resolve(parameters, setup=lab.setup.get("bench-v1"))
result = lab.run(experiment.build(), config=inputs)
```

在作者会话中固定 setup：

```python
setup = session.setup.get("bench-v1")
session.use(parameter_branch="daily", setup=setup)
prepared = session.prepare(experiment(), parameters=session.params)
```

使用已保存 setup 修订或精确引用。改选另一独立参数修订、保存分支编辑以及用分支编辑器准备实验，
都会保留这个 setup 选择。`session.use(setup=None)` 清除固定选择，下一次预览前须重新选择。
选择失败时，会话和编辑器均保持原状。显式 setup 选择用于独立参数；保留的候选已拥有自己的配置证据。
切换对象时，会保留独立参数、setup 和未保存分支编辑，并清除前一对象的批次。
预览负责检查这些输入是否适用于新对象。

新会话初始不选择参数；清除参数会返回这个状态，两者都不会自动选择全局默认值。
预览前须选择参数和 setup。独立打开的分支编辑器需要 `params.preview(setup=setup)`，
会话准备则把本会话选中的 setup 传给编辑器。两种操作都不激活设备。
显式 setup 引用不要求全局活动 setup，也不会因另一客户端改选而失效。

在工作台中，选择参数分支并采用其版本，再通过 **实验配置** 为本页解析已保存 setup；
完成选择后才能预览。同一服务上的另一浏览器页面可以使用不同上下文。
刷新列表或采用新的参数版本会保留已选 setup；只有显式改选或 **重新检查设备连接**
才会替换捕获的连接修订。

低层调用方可向 `resolve` 传入精确 `setup=`，或在科学选择中使用
`ParameterConfiguration(ref=..., setup=...)`。准备会保留这组精确组合，作为执行证据；
不需要另存一个组合配置。

这些操作和记录承担不同职责：

| 操作或记录 | 含义 |
| --- | --- |
| 保存参数修订 | 保存声明和值，不依赖设备，也不接受标定结论 |
| 选择 setup | 选择实验配置证据，不打开设备、不改变其他会话 |
| 准备输入 | 固定精确参数/setup 组合，不等于标定接受 |
| 测量选择 | 选择对象、批次，以及显式参数或保留候选 |
| 运行证据 | 记录该次运行实际解析的输入和科学绑定 |
| 接受标定结果 | 按对应标定流程审查证据并更新明确的参数分支，不能由保存或 prepare 代替 |

编译仍消费组合快照，运行来源则保留独立输入引用。适配器模板也会原子保存独立参数修订与 setup 修订，
不选择默认值。参数编辑和经验证的发布更新明确的参数分支；旧工作点编辑器和全局默认发布 API 已退役。

本流程不增加历史格式迁移或持久兼容承诺，见[数据兼容性策略](../development/data-compatibility.md)。
当前格式的备份与恢复包含独立参数修订，即使该数据空间从未保存过 setup。

持续编辑时不必为每个修订另起名称，可使用[参数分支](parameter-branches.md)。
会话 checkout 保留编辑基线，保存只推进该分支。
