# 规划并派发分阶段校准 {#plan-and-dispatch-staged-calibration-work}

先用 stage plan 查看前置条件与部分进度，再决定派发方式。每个 stage 声明一个检查，固定目标、setup、参数 revision 和条件；不同 stage 可使用不同上下文。
计划不会改变 session 选择，也不会把单目标检查自动合成联合测量。
本文的 `lab` 示例使用底层 `LabClient`。普通作者课程通过 `prepare_task`、submit 和 start 固定阶段与收尾源码，见
[作者入口](../reference/project-layout.md#submit-an-ordinary-procedure-from-an-author-session)。不要混用两类客户端的执行职责。

给定已准备的三个 `CalibrationCheckRequest`（`q0_check`、`q1_check`、`joint_check`）：

```python
from scopecat.automation.calibration_tasks import (
    CalibrationTaskPlan,
    CalibrationTaskStage,
)

plan = CalibrationTaskPlan(
    stages=(
        CalibrationTaskStage(id="q0-drive", check=q0_check),
        CalibrationTaskStage(id="q1-drive", check=q1_check),
        CalibrationTaskStage(
            id="joint-check",
            check=joint_check,
            depends_on=("q0-drive", "q1-drive"),
        ),
    )
)

progress = lab.calibration_checks.preview_task(plan)
assert progress.ready == ("q0-drive", "q1-drive")

# Explicitly bind executions already submitted through lab.procedures.
progress = lab.calibration_checks.preview_task(
    plan,
    executions={"q0-drive": q0_request.id, "q1-drive": q1_request.id},
)
for stage in progress.stages:
    print(stage.id, stage.state, stage.blocked_by)
```

服务端拒绝循环、未知或重复 stage ID、重复依赖、未知 execution 绑定及同一 execution 绑定多个 stage。
已绑定 execution 必须具有 stage 所声明的精确检查。预览不加载作者模块；等价 HTTP 入口是
`POST /api/v1/calibration-tasks/preview`，请求包含 `plan` 和 `executions`。

| Stage state | 含义 |
|---|---|
| `ready` | 尚未绑定 execution，全部前置检查已通过 |
| `waiting` | 前置检查尚未结束 |
| `blocked` | 前置检查被拒绝、失败、取消、不完整或受阻 |
| `queued`, `running` | 已绑定 procedure 正在等待或执行 |
| `waiting_for_input`, `attention_required` | 已绑定 procedure 需要输入或处理 |
| `passed` | procedure 成功，完成的测量具有正面检查结果 |
| `rejected` | 完成的测量具有负面检查结果 |
| `failed`, `cancelled` | 执行失败或取消 |
| `incomplete` | 执行报告成功，但缺少完整检查证据 |

`blocked_by` 列出未绑定 stage 的直接未满足依赖。失败不会阻塞无关 stage。
每个 stage 都已终止或受阻时，task 为 `complete`；只有全部通过才是 `successful`，因此终止的部分结果与完整成功不同。
已绑定工作保留实际执行状态；预览不能停止独立提交的 procedure，也不能追溯强制其满足前置条件。

响应是所选 execution 及证据的只读快照，不是新鲜度评估、派发许可、后续实验已消费前置输出的证明或设备联合就绪认证。
时效／上下文复用使用检查适用性规则，后续动作仍经过服务端正常准入。
预览最多支持 256 个 stage。需要保存计划并在提交时强制依赖时，使用 `lab.calibration_tasks`。

## 保存任务并派发阶段 {#save-a-task-and-dispatch-a-stage}

用 `task_call` 固定各 procedure 的精确定义和 typed intent。intent 必须声明与计划完全一致的检查。
实体样品通过 `samples=` 传入精确 `SampleSelector` 元组，与普通 procedure 提交相同。

```python
from scopecat.api.calibration_tasks import task_call

task = lab.calibration_tasks.create(
    "cooldown-7/check-round-1",
    plan,
    calls={
        "q0-drive": task_call(check_drive, q0_intent),
        "q1-drive": task_call(check_drive, q1_intent),
        "joint-check": task_call(check_joint, joint_intent),
    },
)
task = lab.calibration_tasks.dispatch("cooldown-7/check-round-1", "q0-drive")
procedure_id = task.task.executions["q0-drive"]

# A configured procedure worker can execute the admitted stage in the background.
# For supervised notebook execution, use the existing procedure handle:
lab.procedures.get(procedure_id).resume()
task = lab.calibration_tasks.get("cooldown-7/check-round-1")
print(task.progress.ready)
```

create 只保存 intent，不接纳 procedure。同一 task ID 下不能修改 definition 身份、intent 或阶段依赖。
完全相同的 create 重试返回已有任务，规格不同则冲突。重连后可通过有界 `list()` 查找保留任务。

`dispatch(task_id, stage_id)` 依据保留证据检查依赖，执行正常参数／setup／subject 准入，并在同一写事务中提交 procedure、保存阶段关联。
事务中断不会留下单独的 procedure 或关联。重复 dispatch 返回同一 procedure，即使权限后来改变；新 stage 仍需当前权限。
不会静默采用更早的无关 execution。某个 stage 准入失败时，已就绪的独立 stage 仍可单独派发。

任务及关联可跨 daemon 重启和受支持格式的备份／恢复保留。升级与不支持格式的处理统一遵循
[维护应用](maintain-application.md#install-a-newer-application)及其中的
[备份并升级流程](maintain-application.md#back-up-and-upgrade-schema-115-to-116)。
不要因为旧指南的格式声明就新建或删除数据目录；是否支持原位升级及恢复副本的边界，以维护指南为准。

## 最终验证与发布 {#final-verification-and-publication}

任务可以指定一个已注册 procedure，在**全部计划检查通过**后运行。它接收各 stage 实际采用的精确证据。
任一 stage 被拒绝、失败、取消或证据不完整都会阻止交接，不能只发布成功子集。创建不可变规格时加入调用：

```python
task = lab.calibration_tasks.create(
    "cooldown-7/calibrate-1",
    plan,
    calls=calls,
    finalization=task_call(finalize_calibration, final_intent, samples=samples),
)
```

`finalize_calibration` 是实验室注册的 procedure。其 typed intent 必须声明
`calibration_task: CalibrationTaskInputs | None = None`，类型从 `scopecat.automation.calibration_tasks` 导入。
创建时该字段必须为 `None`，daemon 在准入时填入，其他字段不变。
**启动前**在 intent 中固定目标分支 head、结果 revision 名、组合模式、proposal ID、验证范围和策略，并提供收尾 procedure 所需的精确 sample selectors。
不能在重放时通过查询新 head 或新 setup 来“修复”冲突。

独立课程**后台标定与最终发布**（`task-calibration`）位于
`packages/scopecat-tutorials/src/scopecat_tutorials/course_material`，包含拟合 stage、任务准备／提交及最终验证／发布 procedure。
三个请求分别展示接受、科学拒绝和并发分支冲突；阶段与 finalization 都使用准备命令固定的源码。

procedure 内的 `intent.calibration_task.checks[stage_id]` 包含采用的测量／分析身份、科学绑定、范围和结果。
`intent.calibration_task.measurement(stage_id)` 与 `intent.calibration_task.analysis(stage_id)` 返回持久引用，供
`ctx.run_handle()` 和 `ctx.published_analysis()` 读取。按固定 intent 从精确分析选择 proposal，再用
[`ctx.combine_parameter_candidates()`](automate-parameter-calibration.md#compose-measure-and-decide) 平行或顺序组合。
测量组合结果，在实验室决策中保留全部来源和验证输入，然后才针对固定 head 调用 `ctx.publish_parameter_candidate()`。
这沿用正常的发布校验和持久步骤恢复路径；任务不会发明接受策略，stage 检查成功本身不授权发布。

start 允许 finalization 准入。检查证据绑定与新 procedure 关联在同一事务提交，中断不会留下部分记录；重启及备份／恢复保留相同关联。
配置好的 worker 可在 Notebook 断开后继续执行。准入错误显示于 `task.task.finalization_error`，修正原因后再次 start 可重试准入。
procedure 的 attention／input 和未知发布结果使用其既有处理入口，不能另交一个 finalizer。

`task.progress` 只描述检查 stage。最终 procedure 的状态与 closure 见 `task.finalization`，实际分支发布凭据见其保留输出。
`mode="finished"` 表示推进已结束，不表示科学验证通过或参数已发布。工作台单独显示 final procedure 并链接证据和控制入口。
pause／cancel 阻止新的 finalization 准入，但不能撤销已接纳的 procedure 或已提交的发布。

## Notebook 关闭后继续运行 {#run-without-keeping-a-notebook-open}

工作台 **Calibration tasks** 列出保留计划，可查看各 stage 的检查、目标、固定上下文、依赖和准入错误。
**Open execution** 打开既有 procedure 视图，查看 worker／资源状态、测量、人工输入和执行取消入口。
任务 URL 可收藏，浏览器重启后仍可重开。

start／resume、pause、cancel 需要 operator 和 reason。控制冲突会刷新视图并显示错误，不自动重试动作。
任务进度描述这个计划，不是样品健康或组合能力评估；计划创建仍在作者 Python 中完成。
规格准备好后，可要求 daemon 推进：

```python
task = lab.calibration_tasks.start(task, actor="alice", reason="check this cooldown")
# The notebook may now disconnect. Read current state after reconnecting:
task = lab.calibration_tasks.get("cooldown-7/check-round-1")
print(task.task.mode, task.task.dispatch_errors)
print(task.progress)
```

daemon 轮询 running tasks，每个 task 一次最多接纳一个 stage。既有 worker 加载配置的应用／作者 workspace 并执行保留定义；该定义身份必须仍可用。
只在 Notebook 内定义 procedure，不会使其自动可供 worker 导入。procedure lease 和资源准入继续适用。
不同 task 可在 worker 数量限制内并发；目标不同不能证明真实硬件彼此独立。

被拒绝检查阻塞其后继，独立 stage 可继续。准入失败保留于 `task.task.dispatch_errors`，不会每次轮询自动重试。
修正原因后，用最新 task view 调用 start，清除准入错误并再次尝试。失败的 worker 进程保持暂停，需要检查后显式派发原 procedure；start task 不会清除此 worker 暂停。
等待输入或 attention 也会停止顺序推进，直到通过 procedure 原有入口处理。

作者 workspace 不可用或 worker 启动失败时，该 execution 暂停，其他 task 仍可到达各自 worker。
打开 execution 查看保留诊断和精确日志路径，恢复代码 workspace／环境后使用 **继续任务**，重试已接纳的 execution，不创建替代检查。

执行详情的 **Recent worker output** 显示最近 16 KiB，**Refresh worker output** 获取新日志。
视图标记截断，并区分日志不存在与日志为空；完整日志使用显示的路径查看。日志按纯文本显示，与保留测量和 procedure outcome 分开。

```python
task = lab.calibration_tasks.pause(task, actor="alice", reason="inspect equipment")
task = lab.calibration_tasks.start(task, actor="alice", reason="inspection complete")
task = lab.calibration_tasks.cancel(
    task, actor="alice", reason="abandon remaining checks"
)
```

pause 和 cancel 停止的是**新 stage 准入**，也包括手动 dispatch。已接纳 procedure 可能继续，需要使用其 procedure／run 控制请求取消并核实实际结果。
任务取消是终态，不表示硬件已停止或计划已完成；progress 仍报告保留的阶段结果。finished task 也不能重启，`progress.successful` 区分完整成功与终止的部分结果。

控制命令携带给定 view 的 revision、actor 和 reason。精确重试安全，过期 view 不能静默覆盖新控制。
其他操作者修改后，发出不同命令前先重读 task。daemon 重启会恢复 running tasks 及已接纳 worker 交接，paused／cancelled task 保持原状态，不自动重试科学失败。

task 不切换 setup、不刷新分支，也不发布组合就绪声明。修复必须在创建前声明。
改变输入、扩展已耗尽预算或请求新修复，需要新的 task 规格和 ID；再次 start 不会重置时间或已用预算。

## 先检查，仅修复科学失败的检查 {#check-first-and-repair-only-failed-checks}

使用普通 check 和 fit procedure，并声明不同的科学结果 scope。check 检查请求的能力；fit 报告候选是否值得验证，不表示原能力已有效。
两者都返回标准 check fact，fit 还发布具名参数 proposal。两者均不应推进目标分支。

```python
from datetime import timedelta
from scopecat.daemon.calibration_tasks import (
    CalibrationRepairBudget,
    CalibrationStageRepair,
)

task = lab.calibration_tasks.create(
    "cooldown-7/repair-round-1",
    plan,
    calls=check_calls,
    repairs={
        "q0-drive": CalibrationStageRepair(
            call=task_call(fit_drive, fit_intent, samples=samples),
            proposal_id="frequency",
        ),
    },
    repair_budget=CalibrationRepairBudget(max_repairs=1, elapsed=timedelta(hours=1)),
    finalization=task_call(finalize_calibration, final_intent, samples=samples),
)
```

fit 声明必须与 stage 使用相同的已保存输入上下文、setup 和 targets，但具有**不同 check scope**。
修复目前要求固定初始上下文，不接受 `candidate_from` 模板。worker 始终执行新的初始检查，不静默采用历史通过证据；即使依赖覆盖未知，也保持首轮修复契约精确。

每个符合条件的 stage 最多接纳三个持久 procedure：初始 check、科学拒绝后的一次 fit、以及用该 fit 精确候选重做原 check。
初始检查通过则跳过 fit；fit 被拒绝或候选验证失败则终止该 stage，不自动安排第二次 fit。
执行失败、取消、证据缺失和 worker attention 不是科学拒绝，不触发替代 fit；独立 stage 仍可继续。
候选输出缺失时明确停止验证准入，重试准入复用已完成 fit，不重做拟合。

与 candidate edge 相同，check 代码必须消费传入声明的精确参数。验证只替换
`intent.calibration_check.context.parameters`，不改写其他 intent 字段中的副本。
用 `AnalysisCandidateRunConfigSource` 标识的保留分析和 proposal 读取 `candidate_config`，再传给 `ctx.run`。
普通子任务准入会拒绝误用原始输入的代码。

`max_repairs` 统计已接纳 fit，包含被拒绝或执行失败的 fit；零表示只检查。
耗时预算从首次 start／手动 dispatch 开始，暂停和应用重启不重置。截止时间限制**新准入**，不限制已接纳工作的运行时长；停止已有工作仍需正常 procedure 控制。
最终验证也是新工作，不能在截止后开始；已接纳 finalizer 可完成原发布。预算耗尽时以 `task.task.stop_reason` 结束推进，部分检查不授权 finalization，finished task 不能延长预算。

`task.task.attempts[stage_id]` 保留每个 phase、解析后的 check 和 procedure ID。
Python 的 `executions`、`resolved_checks` 是最新 attempt 的派生视图，存储与 HTTP 以 attempt 历史为唯一依据。
工作台显示 phase、预算、deadline、stop reason 及旧 attempt 链接；重启／重试保留相同身份与已用预算。
修复策略状态使用 `task.progress`；独立 `preview_task` 只评估给定 check 绑定，不推断修复工作流。
手动 dispatch 仍是幂等的初始 check 操作，start 推进声明的修复和验证 phase。

finalization 仍要求全部 stage 通过。`CalibrationTaskInputs.checks` 包含最终检查；`CalibrationTaskInputs.repairs` 仅包含接受且后续验证成功的 fit。
通过 `inputs.analysis(stage_id, from_repair=True)` 取得精确分析提案进行组合，`inputs.measurement(stage_id, from_repair=True)` 取得拟合前测量。
最终验证范围保留未修改且已通过的目标，并重新测量组合结果。若无需修复，验证原输入后直接返回，不发布冗余 revision。
只有正面联合决策才可对原固定 head 发布。六目标软件 fixture 覆盖重启、选择性修复、no-op、预算停止、fit 拒绝、验证拒绝、耦合导致最终失败及并发分支编辑。

## 绑定前置阶段的候选输出 {#bind-a-prerequisites-candidate-output}

下一 check 必须测量前一 stage 拟合出的参数时，使用显式 candidate edge：

```python
from scopecat.automation.calibration_tasks import (
    CalibrationTaskStage,
    StageCandidateOutput,
)

verification_stage = CalibrationTaskStage(
    id="verify",
    check=verification_template,
    depends_on=("fit",),
    candidate_from=StageCandidateOutput(stage_id="fit", proposal_id="frequency"),
)
```

`verification_template` 是普通 `CalibrationCheckRequest`；scope、subject、setup、scenario、mapping 和结果 step 地址保持固定。
`candidate_from` 在准入时显式替换参数输入；此前它只是模板，不是解析后的上下文。创建任务时，匹配调用仍在 `calibration_check` 中包含该模板。

来源必须是显式前置 stage 且已通过，其采用的 check analysis 必须同时发布标准 check result 与具名 proposal。
同 run 的另一份分析、“最新分析”查询或其他 stage 的结果不能替代它。来源失败或被拒绝会阻塞依赖 stage；这不是失败后修复循环。

dispatch 时服务端验证输出和科学绑定，只替换 `intent.calibration_check.context.parameters` 为精确 candidate source。
procedure 代码必须消费该声明，不改写其他 intent 字段中的参数副本。子测量按准入候选检查，误用模板输入会被拒绝。

解析后的 check 和 execution 关联共同提交。`task.task.resolved_checks` 保留输入，`task.task.resolved_plan` 将其覆盖到不可变规格上，供 Python 检查和预览。
未派发的候选 stage 仍是模板。重启／重试复用已接纳 check 和 procedure；准入失败不保留部分绑定，自动推进记录错误直至显式恢复，独立工作可继续。

工作台在待绑定时显示来源 stage／proposal，仅在准入后开放固定候选上下文和证据查询。
绑定不更新日常分支，也不认证提案数值；验证和带版本冲突检查的发布仍须显式执行。
