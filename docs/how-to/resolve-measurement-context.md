# 解析精确测量输入 {#resolve-exact-measurement-inputs}

`lab.resolve_context(...)` 将已保存参数与 setup 一起捕获为精确上下文，不启动实验、
不修改会话选择，也不导入作者代码。标定报告使用同一个公开测量上下文。

```python
from scopecat.records.sample import SampleSelector

receipt = lab.resolve_context(
    branch="daily",
    setup=lab.setup.get("bench-v1"),
    samples=(SampleSelector(sample_id="chip", revision=3),),
)
context = receipt.context
```

使用不依赖分支的已保存版本时，以 `parameters` 替代 `branch`：

```python
parameters = lab.parameters.get("initial-estimates")
receipt = lab.resolve_context(
    parameters=parameters,
    setup=lab.setup.get("bench-v1"),
    samples=(SampleSelector(sample_id="chip", revision=3),),
)
```

传入已保存的 `ParameterRevision` 或精确引用，无需存在或新建分支。
已保存参数路径要求 `branch` 与 `parameters` 恰选其一；两者使用相同的修订验证与科学绑定解析器。
旧内容哈希不匹配会被拒绝，不会退回当前内容或 latest。

样品工作台的 **工作参数与能力上下文** 提供相同选择。在 **参数来源** 中选择
**精确保存版本**，输入已保存版本名称；工作台会读取它的精确引用，不要求手动复制哈希。

必须显式传入 `SetupRevision` 或精确引用，不替换为全局 setup。
使用已登记目标时，用 `target=target.ref` 替代 `samples`；
当前执行绑定仅支持没有目标级连接的单成员目标。

解析结果区分两类信息：

- `context` 是 `MeasurementContext`：精确已保存参数、科学对象、可执行 setup 哈希、
  软件场景及目标到 setup 的映射。
- `receipt.branch` 和 `receipt.setup` 记录此次解析的分支代次与 setup 修订。
  分支名称和代次不是科学身份。使用精确参数时，`receipt.branch` 为 `None`，
  参数引用保存在 `receipt.context.parameters` 中。

推进分支或改选 setup 不改变已有上下文。需要新快照时重新解析。
既不传样品也不传目标时，上下文未绑定对象，不能据此认定物理样品具有某种能力。

## 从已有测量读取上下文 {#read-a-context-from-an-existing-measurement}

对于保留的候选，直接解析其原上下文：

```python
candidate = author.config.candidate(source_run_id, "coarse-then-fine")
receipt = lab.resolve_context(candidate=candidate)
report = lab.calibration_checks.report(
    context=receipt.context, profile="daily-readiness"
)
```

候选不能与分支、已保存参数、setup 或对象覆盖同时传入。
服务端验证精确保留提案及解析内容，保持原对象、setup 哈希、场景和映射。
`receipt.setup` 保留基线的精确执行 setup，`receipt.branch` 为 `None`；
上下文参数输入明确为 `analysis_candidate`，不是已保存参数修订。
验证测量运行前可先声明这个上下文；实际接纳仍会检查保留的设备连接是否可用。

候选证据只适用于这个精确候选上下文。数值相同，或把这些值发布为参数修订，
都不会自动把检查适用性转移给另一个上下文。

```python
context = run.snapshot.measurement_context
if context is None:
    raise ValueError("This run does not retain an exact saved or candidate input")
report = lab.calibration_checks.report(context=context, profile="daily-readiness")
```

这个属性只使用保留证据，包括精确候选输入。临时参数覆盖，以及没有已保存修订或保留候选的配置，
返回 `None`。存在上下文不表示测量成功、标定有效，也不授予硬件执行权限。

已持有精确参数修订和解析绑定的代码，可使用
`scopecat.records.measurement_context.MeasurementContext.from_binding(parameters, binding)`。
它复制完整绑定证据；调用方必须提供实际使用、没有临时覆盖的参数修订。

适用性规则见[标定报告](read-calibration-report.md)，参数保存与分支选择见
[独立参数](independent-parameters.md)；目标身份见[注册测量目标](register-measurement-targets.md)。
