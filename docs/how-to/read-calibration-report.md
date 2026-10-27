# 读取能力证据报告 {#read-a-capability-evidence-report}

task view 描述一个计划的进度；capability report 则回答哪些保留检查适用于显式测量上下文。
报告不会推断样品需要哪些能力，也不宣布整个样品已经校准。

## 从工作台检查 {#inspect-from-the-workbench}

打开 **校准任务**，选择任务，在 stage 下展开 **检查适用证据**。
填写以小时计的最大证据年龄，再点击 **检查证据**。年龄默认留空，由实验室选择策略；历史条数默认 50，最多 200。

查询覆盖与该 stage 固定上下文和 scope 匹配的全部保留检查，包括其他 task 的检查，不跟随参数分支当前 head。
结果显示评估时间、状态、原因、已检查数量，以及所选测量／分析和未解决 procedure 的链接。
输入变化会清除旧报告，再次检查才刷新；屏幕上的结果不会自动更新或到期，刷新失败会清除旧结论。
这个入口一次检查一个声明的 requirement，不认证 task 的完整科学覆盖。

## 从样品检查 {#inspect-from-a-sample}

在样品工作区选择 sample revision，展开 **此样品版本的能力证据**。
从已加载 run 历史选择测量，再加载并选择已保存 capability profile。若所需测量尚未出现，先在 sample 的 run 列表加载更早记录。

此入口不需要 calibration task。它在测量保存的参数 revision 或精确 candidate、setup、完整 subject 和 scenario 上评估 profile。
联合测量保留 subject 中全部样品；进入一个样品页面不会把联合证据变成单样品声明。评估前可展开 subject 查看绑定。

测量必须属于显示的 sample revision，并使用无 overrides 的已保存 revision 或精确保留 candidate；未保存配置不能提供此上下文。
candidate 输入标明 proposal 和来源 run，不继承已保存 revision 的适用性。切换测量会清除旧报告，包括新上下文加载中或加载失败时。
评估时间是当前时间，但所请求上下文是历史上下文，不评估今天的 head、setup 或样品整体就绪状态。

## 无既有测量时解析已保存参数与 setup {#resolve-saved-parameters-and-setup-without-a-previous-measurement}

在样品工作区展开 **工作参数与能力上下文**。
选择 **参数分支** 或 **精确保存版本**，填写名称，在**能力上下文的 setup** 中选择实验角色与设备，再点**解析能力上下文**。
精确 revision 查询只读取那个已保存版本并自动提供 content hash，不创建分支或加载整个参数库。
查找失败清除旧结果且不提交 context 查询。结果显示参数 revision、setup revision、scenario，以及选择分支时捕获的 generation。
随后选择已保存 profile 检查证据，不需要测量、task 或修改全局默认值。

subject 可选当前显示的 sample revision 作为 inline `subject`，或引用该 revision 的已注册单成员 target。
target 选项显示目录项的最新精确 revision，**加载更多目标选项** 加载后续页；Python 也可解析旧 target revision。
表单不会用单样品替代联合 subject；Python 可指定多个精确 sample revision 和 role：

```python
from scopecat.records.sample import SampleSelector

resolved = lab.resolve_context(
    branch="daily",
    setup=lab.setup.get("bench-v1"),
    samples=(SampleSelector(sample_id="chip-a", revision=2),),
)
report = lab.calibration_checks.report(
    context=resolved.context, profile="two-qubit-daily-v1"
)
report
```

需要保留已注册 target 身份及实体投影时：

```python
target = lab.target("chip-target", revision=2)
resolved = lab.resolve_context(
    branch="daily", target=target.ref, setup=lab.setup.get("bench-v1")
)
```

`target` 与 inline `samples` 二选一。resolver 检查 target 的 catalog、revision、content hash，解析精确 sample 成员并使用共享 setup-topology 投影。
后续 target revision 不改变已捕获引用。当前投影只支持单成员且无 connections；多成员或 connected target 会被拒绝，不会被压平成 inline samples。
registered 与 inline subject 即使指向同一实体样品，也是不同上下文，不静默复用证据。

必填 `setup=SetupRevisionRef(...)` 独立于参数分支固定设备。参数和设备 revision 在同一个数据库快照内检查；sample revision 必须精确，通过 sample registry 解析。
不提供 samples 或 target 时为 unbound context，可用于软件场景，但不建立实体样品能力。scenario 来自解析后的 setup，而不是另一个 simulation 标志。

返回上下文已固定：再次评估刷新证据和年龄，不跟随之后的分支或 setup 变化；捕获新版本需要重新 resolve。
修改表单或开始新解析都会清除旧报告，解析失败也一样。结果是建议性观察，不是发布版本校验或真实硬件执行许可。

## 从 Python 查询 {#query-from-python}

profile 描述可复用实验室策略，与单次查询独立。
`CalibrationRequirement`、`CalibrationRequirements`、`CalibrationProfile` 和 `CalibrationProfileRecord` 位于
`scopecat.records.calibration_policy`。能力前置图与 task 的执行顺序分开。

profile 可包含超过 32 项 requirement；可通过同一 API 只检查一部分：

```python
report = lab.calibration_checks.report(
    context=context,
    profile="daily-v1",
    requirement_ids=("readout-q0", "gate-q0-q1"),
)
```

服务端加入全部声明的前置 requirement，保持 profile 顺序及依赖边。
响应只描述所选部分和其前置项，不表示整个 profile 就绪。不传 `requirement_ids` 时请求整个 profile。
工作台可勾选要检查的能力，未勾选则覆盖整个 profile；选择变化清除旧报告。

每份报告在**展开前置项后**仍最多 32 项 requirement，历史总预算最多 2,000 条。
超限依赖闭包、未知 ID 或重复 ID 会被拒绝，不会为满足预算而丢弃依赖。
可选更小的独立部分，或在历史预算超限时降低每项 limit；已保存策略本身不受单份报告大小限制。

提供解析后的 `MeasurementContext` 和要检查的 requirements。
[统一上下文 API](resolve-measurement-context.md) 同时服务测量与校准，不隶属于 calibration-check API。
例如使用实验室 check intent 的精确声明：

```python
from datetime import timedelta

from scopecat.api.calibration_checks import CalibrationRequirement

declaration = check_intent.calibration_check
report = lab.calibration_checks.report(
    context=declaration.context,
    requirements=(
        CalibrationRequirement(
            id="readout-q0",
            scope=declaration.scope,
            max_age=timedelta(hours=4),
        ),
    ),
)
for item in report.items:
    print(item.requirement.id, item.selection.status, item.selection.reason)
    if item.selection.assessment is not None:
        print(item.selection.assessment.reasons)
    print(item.incomplete_reasons, item.unresolved_procedures)
```

每个 scope 声明 capability、有序 targets、运行条件和 policy version。
单目标和联合检查应分别声明 requirement；单目标通过不能建立联合能力。
同份报告全部 requirement 共享精确参数 revision、subject、setup 和执行 scenario；不同上下文分别查询，分支名称不是上下文。

服务端在一个 SQLite 读事务内读取 requirement 的索引历史并解析证据。
`observed_at` 是服务端评估时间，证据选择按测量创建时间，不按分析发布时间。
不会导入实验室 Python、运行实验或写入参数。

| `selection.status` | 含义 |
|---|---|
| `usable` | 所选检查通过，且符合请求的年龄／上下文策略 |
| `out_of_spec` | 所选适用检查未通过科学判据 |
| `recheck` | 保留检查需要新观察，例如已过期 |
| `unknown` | 证据缺失、含混、未完成或未完整检查 |

较新负面检查取代旧通过结果；报告不会向后寻找成功。
默认情况下，参数 revision、setup、subject 或软件 scenario 不同就没有匹配证据。
每项 `assessments` 也解释被排除的已完成检查，参数改变不应隐藏 `no_matching_evidence` 的原因；可在工作台或 Notebook 展开查看。

## 参数编辑后限定证据复用 {#qualify-limited-reuse-after-parameter-edits}

普通参数编辑不需要校准批准。若实验室希望建议性复用，可在原
`CalibrationCheckRequest.dependencies` 和请求的 `CalibrationRequirement.dependencies` 上声明相同 `CalibrationDependencies`。
这是显式科学契约，不是从已捕获 read 自动推导的结论；未声明时仍精确匹配。

```python
from scopecat.records.calibration_dependencies import (
    CalibrationDependencies,
    DependencyCoverage,
)

dependencies = CalibrationDependencies(
    qualification="simulated-resonator-v1",
    execution=DependencyCoverage(
        parameters=("resonator",),
        basis="Reviewed simulator and scan inputs use only the resonator table; "
        "no external files or mutable globals contribute.",
    ),
    analysis=DependencyCoverage(
        parameters=(),
        basis="The fit uses only retained measurement arrays.",
    ),
    physical=DependencyCoverage(
        parameters=("resonator",),
        basis="This simulation has no coupling to other parameters.",
    ),
)
```

不能把示例中的 physical 声明直接复制到真实实验室。
应审查完整执行路径（compiler、runtime、scan overlays、完成状态）、分析参数、闭包、globals、文件和物理耦合。
包含全部参数依赖，而不只是一次运行观察到的读取。某个 domain 缺失表示未知，显式空元组则声称独立，必须给出 basis。
代码或外部假设改变时，修改 qualification 和 scope policy version。框架只检查声明相等，不证明科学假设为真。

支持的比较限于完整 parameter 粒度。table 依赖包含全部行列，也能发现旧查询本会选中的新增内容。
完整 catalog schema、subject、target mapping、setup／topology 和软件 scenario 仍要求精确一致。
base／point／segment read 观察保留在各自原始记录，不升级成完整覆盖证明；未经限定的任意 Python 或外部状态必须保持未知。
candidate 不支持跨 revision 比较。

报告在同一读快照中比较不可变的已保存 revision，显示比较及变化的 parameter ID。
依赖改变要求 recheck，覆盖缺失则 unknown。不会跳过较新的负面、未知或输入已变检查来恢复旧成功。
测量完成状态、年龄、分析缺失和未解决 execution 仍适用。已保存 profile 保留声明，应用重开后 Python 与工作台使用相同策略。
该能力不派发修复，也不授权发布值。

## 声明能力前置条件 {#declare-capability-prerequisites}

在 Notebook cell 最后一行保留 `report`，即可显示 capability、targets、自身检查结论、availability 和阻塞依赖。
展开 requirement 查看证据 ID、原因、年龄策略及未解决 execution，展开 context 查看精确科学输入。
报告保留 typed `.items`、`.context`、`.observed_at`。显示本身不发请求，获取新快照需再次调用 `lab.calibration_checks.report(...)`。
已保存 Notebook 输出只是观察时刻的视图，不是实时状态面板或权威数据记录。

若实验室策略要求其他能力先可用，在 requirement 上添加 `depends_on`，引用同一报告内的 ID：

```python
readout = CalibrationRequirement(
    id="readout", scope=readout_scope, max_age=timedelta(hours=4)
)
gate = CalibrationRequirement(
    id="gate", scope=gate_scope, max_age=timedelta(hours=2), depends_on=("readout",)
)
report = lab.calibration_checks.report(context=context, requirements=(readout, gate))
for item in report.items:
    print(item.requirement.id, item.selection.status, item.availability.status)
    print(item.availability.blocked_by)
```

`selection` 始终描述自身证据。无阻塞时 `availability` 与其相同；任一前置项不可用则为 `blocked`，即使自身检查已通过。
`blocked_by` 列出直接未满足 ID，可沿这些项查找更深原因。阻塞沿图传播，独立 requirement 不受影响。

依赖必须引用请求内的 requirement，重复和循环被拒绝。请求顺序不影响判定，响应保留输入顺序。
空依赖列表保留单项证据结论。这张显式图描述实验室策略，不是测得的因果关系或参数读取依赖。
task stage 顺序不会自动成为能力策略，工作台的 per-stage inspector 提交不带依赖的单项 requirement。

## 保存可复用 requirements {#save-reusable-requirements}

在实验室作者代码中维护 requirement 定义，再保存具名版本，跨 Notebook 和 daemon 重启复用：

```python
saved = lab.calibration_checks.save_profile(
    "two-qubit-daily-v1",
    requirements=(readout, gate),
    description="Readout evidence is required before using the gate capability.",
)
lab.calibration_checks.profiles()  # bounded page; use next_cursor for older entries
lab.calibration_checks.profile("two-qubit-daily-v1")
report = lab.calibration_checks.report(context=context, profile="two-qubit-daily-v1")
report
```

profile 保存 requirements、年龄限制和依赖边，不保存参数分支、setup、sample 选择或 task 执行状态。
target 地址在评估时提供的上下文中解释；只在地址、条件和策略含义适用时复用，框架不推断不同样品物理等价。
没有全局 active profile。

ID 由字母、数字、点、下划线和连字符组成，以字母或数字开头。同 ID 同内容保存幂等；任意内容改变都需要新 ID，例如 `two-qubit-daily-v2`，既有 profile 不可变。
报告包含 `profile_id` 及实际评估的完整 requirements。`profile` 和 `requirements` 二选一，不能同时提供。

profile 属于所选 project 数据存储，可经 HTTP API 读取而无需导入作者 Python，并跨受支持格式的备份／恢复保留。
安装新版与数据格式升级的边界见[维护应用](maintain-application.md#install-a-newer-application)及
[备份并升级流程](maintain-application.md#back-up-and-upgrade-schema-115-to-116)。
本指南不另行声明“当前 schema”，也不要求为已支持的升级新建目录。
已保存 profile 是报告策略，不是自动维护 schedule 或完整样品策略。

工作台中展开 stage 的 evidence inspector，再展开 **检查已保存的能力 profile**。
加载并选择 profile，审阅 requirements 后点 **检查已保存的 profile**，在 stage 的固定上下文评估所选 requirement 及前置项；未选择则评估整个 profile。
视图将自身检查与 availability／阻塞项分开展示。此只读入口每项使用 50 条检查；不同历史预算通过 Python report API 指定。
改变选择或刷新失败清除旧结果，不派发实验。

## 限制与解释 {#bounds-and-interpretation}

`history_limit` 默认为**每个 requirement scope** 50 个请求，范围 1–200，跨上下文读取。
较宽历史用于解释失效原因；即使部分已检查项不相关，预算耗尽仍为 unknown。
报告接受 1–32 个不同 ID 的 requirement，数量与 history limit 乘积不得超过 2,000。
历史被截断时返回 `incomplete_history` 和 `scan_limit`。已检查历史中有未解决 check 时，即使另一检查通过，也不能得到 usable，并会列出 procedure ID。
应打开这些 execution 检查；增加预算不能解决未完成证据。

报告是建议性快照，不授权后续发布，也不证明 requirement 列表覆盖全部科学依赖。
它不能替代写入时权限检查、推断硬件独立性、调度修复或发布组合就绪结论。
工作台 task view 保持独立；sample capability 面板在实验室提供显式 requirements 和上下文后可消费此报告。
