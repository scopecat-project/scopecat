# 自动执行完整参数校准 {#automate-a-complete-parameter-calibration}

**参数校准与恢复**（`calibration`）课程是独立作者示例，源码位于
`packages/scopecat-tutorials/src/scopecat_tutorials/course_material`。
初次操作可从[帮助页](../tutorials/teaching-sandboxes.md)进入。课程本地模块包含合成模型、拟合、接受策略和持久 procedure；Notebook 展示单目标校准、暂停／继续及拒绝后不发布。
只检查请求中，通过与超差都可以是执行成功的 procedure，区别保留在科学结果中，参数分支不会因此改变。

检查结果保留能力范围和精确上下文。策略、参数或时效变化可能要求重新检查，这是实验室显式策略，不是自动修复或发布许可。
通过 `lab.calibration_checks.history()` 查询保留记录时，不能用较旧的通过结果掩盖较新的失败。
请求在执行前声明范围、上下文和结果位置，无需实验室 reader 回调。使用结果前检查
`incomplete_reasons` 和 `unresolved_procedures`；有界历史包含未完成请求，不保证与并发执行构成原子快照。

随后可用**联合校准与耦合验证**（`joint-calibration`）观察：两个分别被接受的候选，在声明的合成耦合模型下仍可能联合失败。
课程也展示目标覆盖缺失和组合后恢复。两份课程保留可编辑源码及独立维护者测试，不是额外的日常应用服务。

一次有界校准请求使用一个已注册的持久 procedure。在 typed intent 中固定目标、已保存的参数／setup、目标分支 head 和结果 revision 名；提交时指定科学 subject，子采集继承该范围。
框架负责执行记录、精确引用、资源调度、重放和原子发布。测量什么、如何拟合、科学上是否接受，以及耦合是否需要补测，由实验室代码决定。

## 从固定的分支 head 构造请求 {#build-the-request-from-a-captured-branch}

下面保留底层 `LabClient` 的 `lab` 示例；普通作者 session 的 prepare／submit 和只读结果入口见
[procedure 作者入口](../reference/project-layout.md#submit-an-ordinary-procedure-from-an-author-session)。两种客户端的执行职责不同。

```python
destination = lab.parameters.checkout("chip-a/daily").head
initial = lab.parameters.resolve(destination.revision, setup=lab.setup.get("bench-v1"))

# CalibrationIntent and calibrate are registered laboratory definitions.
intent = CalibrationIntent(
    targets=("q0", "q1"),
    initial=initial,
    destination=destination,
    result_revision_id="cooldown-7-drive-001",
    actor="Alice",
)
request = lab.procedures.submit(
    calibrate, intent, request_key="cooldown-7-drive-001", sample="chip-a"
)
```

使用无 overrides 的已保存参数。提交时固定输入，重放时不能重新读取今天的分支 head。
目标子集是新的显式请求，不应因为某个成员失败就自动缩小范围。实验室 intent 模型应检查重复或空目标列表。

## 组合、测量和判断 {#compose-measure-and-decide}

在 procedure 内用 `ctx.run()` 和 `ctx.analyze_run()` 保留各目标的基线及拟合；两者返回持久引用。
多个拟合提案可通过框架步骤组合：

```python
joint_ref = ctx.combine_parameter_candidates(
    "compose",
    ((q0_fit_ref, "q0-drive"), (q1_fit_ref, "q1-drive")),
    name="joint-drive",
)
candidate = ctx.published_analysis(joint_ref).candidate_config("joint-drive")
```

默认 `mode="parallel"` 组合来自同一已保存基线的提案。若后一次拟合必须使用前一次结果，先将精确候选传给下一次采集，再按顺序组合：

```python
first = ctx.published_analysis(first_fit_ref).candidate_config("first-fit")
second_run = ctx.run(
    "second-measurement",
    second_experiment.build(),
    config=first,
    inputs=(first_fit_ref,),
)
second_fit_ref = ctx.analyze_run("second-fit", second_run, second_analysis())
joint_ref = ctx.combine_parameter_candidates(
    "compose",
    ((first_fit_ref, "first-fit"), (second_fit_ref, "second-fit")),
    name="final-chain",
    mode="sequential",
)
candidate = ctx.published_analysis(joint_ref).candidate_config("final-chain")
```

这里的实验与分析均由实验室定义。顺序模式检查每次来源测量是否确实使用了前一步的精确候选，仅有执行先后顺序不够。
后续拟合可细化先前单元格，组合结果保留相对初始 revision 的净变化。需要用最终组合进行新的验证，并在保留的决策中列入全部来源测量。
中间步骤成功不代表组合已验证，中间候选也不推进日常分支。组合模式及来源顺序属于持久步骤身份；重放时改变它们会冲突，不能重新解释旧工作。

使用这个精确 `candidate` 执行所需验证测量，再由 `ctx.analyze_project()` 保留基线、验证输入和实验室决策。
分析参数可包含 run handle 集合，持久身份使用其保留的 run ID。策略结果应列出 requested、checked、rejected 和 missing targets；正面决策要求完整覆盖所请求范围及相关相互作用。

只有保留了正面决策，才调用
[`ctx.publish_parameter_candidate()`](parameter-branches.md#publish-inside-a-durable-procedure)。
目标分支原子推进，setup 和共享默认值不变。服务端检查精确来源／证据身份，不能推断实验室科学策略是否充分。

## 执行与恢复 {#run-and-resume}

底层前台请求通过 `request.resume()` 执行。常驻 worker 可派发已提交请求，无需旧 cohort evaluator 或 finalizer：

```python
from scopecat.api.project_worker import ProjectAutomationWorker

worker = ProjectAutomationWorker(lab.procedures)
result = worker.cycle()  # A bounded pass over due schedules and runnable requests.
```

进程持有的循环可用 `worker.run_forever(stop=stop_event)`，需要保持连接及 worker 进程存活。
重启后按明确 ID 重开请求：

```python
request = lab.procedures.get(request_id)
request.summary()
request.steps()
```

已配置作者 workspace 时，安装后的 CLI 可启动同一 worker：

```console
python -m scopecat_server.cli automation work /path/to/lab
python -m scopecat_server.cli automation work /path/to/lab --once
```

CLI 派发已注册 procedure、到期 schedule 和已注册 interval planning，不评估旧 cohort 的新鲜度，也不选择工作点。
通过 `LabApplication(procedures=...)` 或 `[lab.capabilities]` 的 `procedures` 列表注册。
旧 `calibrations`、`calibration_publications` 应用声明和 `--working-point` 选项已退役；旧声明会明确失败，不会被静默忽略。

已完成步骤重放其输出，缺失步骤保持待处理。科学拒绝是保留结果，不能因此转而发布成功子集。
目标分支过期时，应审阅输入并创建新请求；换成刚 checkout 的 head 重试，不等于将旧科学证据 rebase 到新 head。

发布结果未知时，先检查保留的 attention 原因，再以不变 intent 使用底层 `request.retry_attention()`。
即使其他写入者随后推进了分支，同一命令仍恢复原发布凭据，不重新采集已完成测量，也不改向新 head 发布。
普通作者入口应使用现有任务处理控件，不能把一般 `resume()` 当成未知结果的任意重试许可。

此流程不会自动宣布校准新鲜、选择过期目标或推断样品间依赖，这些仍是显式实验室策略。
维护的模拟 fixture `drag_branch_calibration` 覆盖双目标联合复测和恢复；它是开发证据，不是真实硬件配方，也不是安装课程的单目标模型。

## 让操作者提供下一探索值 {#let-a-person-supply-the-next-exploration-value}

下一值需要查看测量曲线后决定时，使用 `ctx.interpret()`，并将来源 run 和已发布曲线分析放入 `inputs`。
判断页在宽屏上将图与答复表单并排展示。interpretation 是保留的判断，不是拟合结果，也不等于校准接受。

表单支持标量、literal 选项及标量列表。字段名应明确单位，例如 `frequencies_mhz: list[float]`，并提供 selected、no peak 或 uncertain 等结果选项。
空数值不等于零。可用 `metadata={"field_labels": {"frequencies_mhz": "Peak frequency (MHz)"}, "reviewer": operator}` 提供可读标签与可编辑的操作者默认值。
响应 schema、metadata 以及显式采用的答复契约都属于保留请求身份。

新请求可显式选择 `AnswerContract`，在结构校验之外声明有限的标量范围、条件必填和未激活值归一化规则。
不合法答复由服务端在正式写入前拒绝，保持同一个等待问题；不会完成步骤或安排继续执行。
这不自动给所有请求添加范围规则，也不改变旧请求 hash 或旧源码重放语义。旧请求仍遵循其原定义，可能由作者代码在已记录答复后重新提问。

未提交编辑按精确请求保存为应用草稿，正式记录前不是科学证据。等待“草稿已保存”确认，恢复方法见
[恢复未完成编辑](record-experiment-decisions.md#recover-unfinished-edits)。
当前普通 GUI 使用**提交判断并继续**：成功记录答复后请求继续原 procedure，不是新建一次采集请求。
答复已保存与继续派发是否成功是两个结果；派发失败不能当成答复未保存。
Python 仍可分别调用 `pending.respond(...)` 与 `task.resume()`，也可使用 `pending.respond_and_continue(...)`。
响应不确定时保留原问题及精确提交身份，核实或重试同一次提交，不重新选择“最新”问题；详见[人工答复指南](record-experiment-decisions.md)。

使用答复前仍需验证科学意义。能用有限确定性契约表达的区间等规则，应在新请求中声明，让非法输入留在原问题修正。
若答复满足该契约但科学证据不足，作者可保留原判断，并在新的稳定 interpretation step 上真正重问；不应静默截断数值或伪造拟合。
project analysis 可通过 `context.interpretation(ref, schema=...)` 使用答复，保留其来源、操作者及原测量证据。

run analysis 也可使用 `context.interpretation()`，基于该 run 的精确参数提出所选值。
服务端对 run 与 project analysis 都检查判断的请求／响应身份；procedure 分析 inputs 应包含测量和 interpretation 引用。
既有参数行的未知单元格可以继续未知，候选仅填充已测字段；组合保持其他单元格缺失，只有消费未知字段的实验才必须等待其校准。
因此可以在尚无 drive 或 classifier 时探索 readout，而不虚构初值。科学接受与独立验证仍是分别需要完成的步骤。
