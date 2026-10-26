# 记录人工或 AI 实验判断 {#record-a-human-or-ai-experiment-decision}

当下一步依赖尚不能可靠写成算法的科学判断时，在 procedure 中使用
`context.interpret`：例如选峰、判断拟合是否可信，或选择后续实验的工作点。
测量和图形保留在 run／analysis 记录中；问题通过 `inputs` 引用这些证据，
答复记录结构化值、操作人、来源和备注。

## 分清三层检查

| 层次 | 检查内容 | 不通过时 |
| --- | --- | --- |
| `AnalysisFactSchema` | 字段、类型、字面值和可空结构；必需字段不能省略 | 不写正式答复 |
| 显式 `AnswerContract` | 有限的数值范围、条件必填和非活动可空值归一化 | 不完成步骤、不安排继续；修正后回答原问题 |
| 作者的科学判断 | 是否真是目标峰、证据是否足够、是否值得继续实验 | 作者根据已保留答复决定下一步；需要时另提一个问题 |

契约是新请求的显式选择；规则和版本随请求持久化并参与 `request_hash`，
不从标签、字段名或单位推断。v1 只支持顶层数值字段，
条件是同一答复中一个不受规则约束的标量字段的等值比较；受条件约束的目标字段必须可空。
它不是通用 schema 或单位换算系统，也不会判断所选频率是不是正确的峰。
服务端在正式写入前执行检查；GUI 和 Python 的前馈不能替代这个边界。

## 声明一个带契约的问题

下面可保存为作者模块 `src/my_experiment/peak_decision.py`。`choose_peak` 从已有的 procedure 中调用，
传入已保留的 `scan`；它不采集数据。首次执行会进入 `waiting_for_input` 并释放 worker
lease；答复并继续后，重放返回同一个判断及其证据引用。

```python
from dataclasses import dataclass
from typing import Literal

from scopecat import AnalysisFactSchema
from scopecat.api.procedures import InterpretationResult, LabProcedureContext
from scopecat.automation import (
    AnswerCondition,
    AnswerContract,
    RunOutputRef,
    ScalarAnswerRule,
)


@dataclass(frozen=True)
class PeakChoice:
    outcome: Literal["Peak selected", "No clear peak", "Unsure"]
    frequency_mhz: float | None


PEAK_CHOICE = AnalysisFactSchema("my-lab.peak-choice.v1", PeakChoice)
PEAK_RULES = AnswerContract(
    version=1,
    fields={
        "frequency_mhz": ScalarAnswerRule(
            minimum=6490,
            maximum=6510,
            required_when=AnswerCondition(field="outcome", value="Peak selected"),
            empty_otherwise=True,
        ),
    },
)


def choose_peak(
    context: LabProcedureContext, scan: RunOutputRef
) -> InterpretationResult[PeakChoice]:
    return context.interpret(
        "choose-1",
        title="选择峰频率",
        instructions="检查保留的扫描；选择峰时填写 MHz，不确定时选择 Unsure。",
        schema=PEAK_CHOICE,
        inputs=(scan,),
        response_template=PeakChoice("Unsure", None),
        answer_contract=PEAK_RULES,
        metadata={"field_labels": {"frequency_mhz": "峰频率（MHz）"}},
    )
```

`response_template` 是编辑起点，只按结构验证，不是已接受的判断。
作者在调用 `choose_peak` 后可检查 `decision.value`；需要保留判断依据时传递
`decision.ref`。本例没有额外的科学接受或拒绝规则。

这份契约的边界如下；范围含两端，频率采用约定的 MHz 数值，不自动换算单位。

| 提交值 | 结果 |
| --- | --- |
| `Peak selected` + 6490 或 6510 | 接受 |
| `Peak selected` + 6600 或 `null` | 拒绝，仍等待原问题 |
| `Unsure`／`No clear peak` + `null` | 接受 |
| `Unsure` + 6500 | 接受并记录为 `null`；草稿可保留原编辑值 |
| 省略 `frequency_mhz` | 结构不合法；可空不等于可省略 |

归一化之前仍需通过结构检查；例如 HTTP 的数字字段不能用字符串替代。
条件控制字段按其 schema 判断，合法的空字符串／空白字符串也是值，不代表省略。

旧请求未声明 `answer_contract` 时仍走旧语义：不会因升级而增加这些服务端规则。
缺省契约不进入序列化，旧请求 hash 不变；旧作者源码及已保存答复仍按原路径重放。
旧 `metadata.field_constraints` 只是已有的 GUI 提示，不能当作跨入口契约。
不要为了“升级”正在等待的问题而替换规则、hash 或保留的源码。

## 在应用中回答

打开**人工判断**，先核对问题和证据，再填写支持的表单及**判断操作人**，选择
**提交判断并继续**。GUI 答复记录为 `human`；API 可显式声明 `human`、`ai` 或 `service`。
操作人是客户端提供的归属信息，不是登录身份或权限证明。

设置中的默认操作人用于新草稿；已保存草稿和提交结果尚未确认的命令保留原操作人。
不要通过更换操作人来重试一次结果不明的提交。可空数值的空输入表示 `null`，不是零。
表单不支持的结构会说明原因并保留草稿，此处不能提交；请作者使用受支持的字段或 API。

应用显示**判断已保存**后，仍应查看继续执行状态。出现**核实判断提交**时，使用
**重试继续执行**核实同一条命令，不要重新创建采集任务。

## 在普通作者会话中回答已查看的问题

先取得待答对象并检查证据，再填写答案。下面沿用上面的作者模块；`session` 是已连接的
普通作者会话，`procedure_run_id` 是原任务 ID。

```python
from my_experiment.peak_decision import PEAK_CHOICE, PeakChoice

task = session.procedures.get(procedure_run_id)
task.wait(timeout=60)
pending = task.pending_input("choose-1", schema=PEAK_CHOICE)
print(pending.request.title, pending.request.instructions)
print(pending.inputs)  # 打开并检查这些精确的保留证据

answer = PeakChoice("Peak selected", 6500.0)
receipt = pending.respond_and_continue(answer, actor="learner", note="已核对曲线")
print(receipt.answer.output.response.value)
print(receipt.continuation, receipt.dispatch_error)
```

`pending` 保存实际查看过的问题、证据、schema 和并发校验字段的独立快照。
不传步骤 key 时，只有唯一当前待答问题才可选择。答复不会重新寻找“最新问题”；
同 schema 的另一道题也不能由旧对象回答。冲突后重新取得对象并重新审阅，不能自动更新
hash/revision 重发。`pending` 不预留执行权，也不是可以跨进程恢复的 receipt 文件。

若只想保存答复，使用 `pending.respond(answer, actor="learner", note="已核对曲线")`；
它返回答复 receipt，本身不 dispatch。普通托管任务可在核实后续操作后显式 `task.resume()`。
若另有独立驻留执行器轮询 ready 工作，应先确认其策略，不能把保存答复理解为额外人工停顿。
旧 `lab.procedures.get(...).respond(...)` 在调用时读取当前问题；普通作者流程应优先使用
这里的 `pending_input`，明确绑定已经看过的问题。

## 答复保存与继续执行分别确认

`respond_and_continue` 返回的 `receipt.answer` 表示答复已经保存；
`continuation` 和 `dispatch_error` 描述后续调度，不能把调度失败当成答复未保存。

| `continuation` | 后续处理 |
| --- | --- |
| `queued` | 已安排继续，观察任务进度；不保证已经完成 |
| `already_continued` | 这次输入等待已经继续，观察任务；不要新建一次采集 |
| `retryable` | 答复已保存；保持同一 pending、answer、actor、actor_kind、note，重试同一调用 |
| `blocked` | 查看任务的暂停、故障或取消原因；旧答复不能解除其他停顿 |

网络超时或响应丢失时，结果可能未知。保留原命令并核实；同一会话中可用同一 `pending`
重试完全相同的调用。服务端按归一化后的答复比较，因此 `Unsure + 6500` 与
`Unsure + null` 可得到同一答复记录，但不会放宽原请求、操作人、attempt 或 lease 校验。
实践中保持原输入不变最容易核对，不能改 actor、note 或问题身份来规避冲突。

会话重开后先按原任务 ID 查看保留的步骤和答复：确认是否已保存、是否仍在等待、是否已有
另一道问题。不要把“当前 pending”当作原命令的自动替代品，也不要重新准备或采集来消除不确定性。
设备或发布结果不明应按 `attention_required` 的恢复路径核实，不应伪装成人工选择。

## HTTP 入口

两条入口使用同一个 `ProcedureStepInputSubmitCommand`：

- `POST /api/v1/procedures/{procedure_run_id}/steps/{step_key}/attempts/{attempt}/input`：只提交答复。
- 同一路径加 `/continue`：提交答复并请求继续，返回包含 `answer` 的独立继续回执。

从已检查的 waiting run／step 保留 `procedure_run_id`、`step_key`、`attempt`、
`expected_run_revision`、`expected_step_revision` 和 `request_hash`，再加入 `actor`、
`actor_kind`、`value`、`note`。路径身份必须和 body 一致；含斜杠的步骤 key 按客户端 URL
编码要求处理。不要凭 schema 相同就复用别的问题的身份或自行补最新 revision。

本例的 `value` 是 `{"outcome":"Peak selected","frequency_mhz":6500}`。
服务端验证结构及已持久绑定的契约；非法答复不会完成步骤或调度继续。
冲突通过 HTTP 409 反馈，结构化命令本身解析不合法可返回 422；调用方应读取具体错误，
区分输入问题、并发冲突与响应丢失。Python 的类型化编码／前馈可能更早抛出异常。
不确定提交结果时，重发原始命令，不重新拼装新的请求身份。

## 从证据点填写数值草稿 {#fill-a-numeric-draft-from-one-evidence-point}

请求可在 presentation metadata 中声明一个精确选点绑定。选峰课程的第二个 input
是保留的 analysis publication，其 `trace` 图形的 `data` 层使用 MHz 的 `frequency` 列：

```python
metadata = {
    "point_binding": {
        "input": 1,  # 从零开始的 inputs 索引
        "output": "trace",
        "layer": "data",
        "column": "frequency",
        "field": "frequency_mhz",
        "unit": "MHz",
    },
}
```

必须声明所有属性，无量纲列的 `unit` 用 `None`。字段必须是支持的数值类型，列必须是
该图层的轴且单位精确匹配；名称和标签不推断绑定或换算。绑定不合法时仍可手填表单。
先选适用的判断，再点击一个已显示的预览点；它只更新草稿，可继续修改，不提交也不继续执行。
过期请求、冲突草稿或不同证据不能接收选点；不支持多点选择或拖拽区间。

## 恢复未完成的编辑 {#recover-unfinished-edits}

表单将操作人、备注和原始字段编辑保存到应用数据，包括尚未完成的数值输入。
退出前等待**草稿已保存**；正在保存或保存失败都不是持久确认。保存草稿不等于保存正式答复，
不会发布标定或启动工作。

离开页面、重开窗口或服务端口变化后可恢复已确认草稿；另一数据目录有独立草稿。
两个窗口冲突时会保留副本并阻止该编辑器提交。**将我的副本设为当前草稿**是显式条件替换，
仍可能再次冲突，不会删除另一份历史。

在**人工判断选项**中打开**恢复已保存的判断草稿**，可查看已完成请求、冲突或丢弃的版本并复制内容，
但历史页不能提交或继续。问题已经变化时，核对当前请求，使用**丢弃草稿（保留恢复历史）**
重新填写；手动复用的文字仍需验证。丢弃只清除该请求的当前草稿，不删除历史或科学记录。
目前没有自动过期／历史删除，也不导入旧浏览器本地草稿。

## 将判断保留为分析输入 {#retain-decisions-as-analysis-inputs}

传递精确的 `decision.ref`，而不只是 `decision.value`，才能建立判断的来源关系。
下面延续上述 schema，供作者模块与 procedure 使用：

```python
import scopecat as sc
from scopecat.automation import InterpretationOutputRef


@sc.analysis_step(id="my-lab.peak-report.v1")
def peak_report(
    analysis: sc.AnalysisContext, *, selection: InterpretationOutputRef
) -> sc.Analysis:
    value = analysis.interpretation(selection, schema=PEAK_CHOICE)
    return analysis.result("选峰判断").fact("selection", value, schema=PEAK_CHOICE)


# 在 procedure 中，decision = choose_peak(context, scan) 返回后：
# report = context.analyze_project(
#     "peak-report", peak_report(selection=decision.ref), inputs=(decision.ref,)
# )
```

发布保留 procedure ID、step key、request hash 及完整答复（含 actor、时间和 note）的 hash。
服务端核对已经成功的 interpretation 步骤，拒绝未知或被篡改的引用。
直接 interpretation 输入目前用于 project analysis，不用于 run／sample analysis；
引用既不是认证机制，也不授权新的设备操作。

## 查看有界任务摘要 {#inspect-a-bounded-procedure-summary}

底层 `lab.procedures.get(procedure_run_id)` 返回的 `ProcedureHandle` 支持
`procedure.summary(limit=20)`，返回 `outcome`、`next_action`、`reason` 和有限的 `steps`。
已关闭任务的 outcome 区分 `succeeded`、`failed`、`cancelled`；next_action 为
`respond`、`resume`、`inspect_attention`、`wait` 或 `none`。
`next_cursor` 表示还省略了更早尝试，可用 `procedure.steps(before=summary.next_cursor)` 查看。
普通作者会话的 `task` 则用 `task.progress()` 观察状态、`task.steps(cursor=...)` 翻页，
不要将两种句柄的分页参数混用。
摘要是非原子的观察，不是锁；实际命令仍接受 revision／lease 校验。

完整可编辑的无设备例子位于帮助中的 procedure Notebook 课程：
`procedure.ipynb` 和 `src/my_experiment/procedure.py` 覆盖合成采集、证据、答复与恢复，
不发布参数。新课程只声明一次 `choose-1`；旧任务仍按各自保留的源码和请求语义继续。
