# 保存与重开实验计划 {#save-and-reopen-experiment-plans}

命名计划（plan）保存不可变的实验输入，供以后复用。保存、打开、复制或隐藏计划都不采集数据、
不预留设备，也不修改实验室默认配置。每次执行都需重新获得正常启动流程的预览。

在**实验**中编辑输入并点击**预览**，填写**计划名称**后选择**保存计划**。
修订保存实验定义、受支持的作者代码修订、标量/扫描控件、精确配置条目或命名参数上下文及覆盖值，
以及选定样品的精确修订。无样品实验可使用当前默认配置；保存计划不要求先创建工作点。
以后默认配置的变化不会静默替换已保存配置。

每次预览将选定样品修订或已登记目标与配置证据一起冻结。提交或保存保留已审阅的绑定，
后续目录编辑不能悄悄改变它。重开计划保留原目标、样品修订和实验批次，不采用会话当前默认值替换。

在**复用与恢复**中的已保存计划列表（当前界面标题 **Saved plans**）打开指定修订。
先核对名称、修订、实验、样品和来源结果，必要时展开精确引用。
本次执行操作者与保存计划的人分别记录；打开计划不会复用保存者身份或旧提交 key。
检查选中的配置，重新预览，再显式开始采集。

编辑输入并重新预览后，可**保存新 revision**；用**另存为副本**创建另一计划身份。
修订比较显示实际变化字段，已有修订和已采集运行保持不变。
列表中的 **Delete** 只隐藏命名 head，不删除历史证据；已有运行的精确引用仍可读取保留修订。
重置启动草稿只重置可编辑 UI 状态；响应未知的原提交仍保留独立恢复入口。

## 从保留的分析结论开始 {#start-from-a-retained-analysis}

从保存的比较结论显式导入建议后，可审阅、编辑并直接**开始采集**，不必先命名保存计划。
接纳的 procedure 和子运行保留精确分析决策来源，实际输入独立记录。
如需重复使用配方，再预览并保存计划；计划的精确来源也随每次接纳传递。
运行可打开原结论，原结论可分页找回后续实验，详见
[从结论直接启动下一实验](retained-run-comparison.md)。

## Python {#python}

作者客户端使用与 GUI 相同的启动、计划和预览记录。在已登记作者目录对应的 Notebook 中，
选择已有参数分支和实验配置（将 `daily`、`bench-a`、`signal` 替换为实际名称）：

```python
import scopecat as sc

author = sc.notebook()
author.use(parameter_branch="daily", setup=author.setup.get("bench-a"))
prepared = author.prepare("signal", actor="alice")
saved = prepared.save_plan("Frequency check", saved_by="alice")

# 读取精确计划并重新预览，使用保存的配置与代码，不使用今天的默认值。
reopened = author.prepare_plan(saved.ref, actor="bob")
```

以上代码尚未采集。确认要开展本次实验后，另行显式提交；在同一作者会话中调用：

```python
receipt = reopened.submit(request_key="frequency-check-2026-10-27")
```

`request_key` 标识一次原始请求；响应未知时保留同一 key 查询/重试，新的采集意图使用新 key。
不要把示例固定 key 当作每次采集都可复用的名称。

已有 `lab` 客户端可用 `lab.plans.list()`、`lab.plans.get(ref)`、
`lab.plans.save(command)` 和 `lab.plans.delete(ref)` 管理类型化修订。
`ExperimentPlanSave.previous` 用于乐观编辑；`copied_from` 创建独立计划身份。
从保留分析导出输入时，把 `PlanAnalysisSource` 传给
`prepared.save_plan(..., source=...)`，服务端校验 publication 的精确身份。
过期编辑会被拒绝，不自动选择一个编辑者获胜。

共享配置解析支持活动或显式命名上下文，以及计划选定的精确非活动登记项。
接纳事务会同时核对已保存计划、实际启动请求和服务端检查过的预览事实。
同一 request key 的精确重试会先找回原 procedure，不因今天的接纳约束变化创建另一条执行。

## 存储与源码支持边界 {#storage-and-supported-source-boundary}

计划保存在不可变归属对象及修订/head 表中。[当前格式快照与恢复](backup-and-restore.md)
包括所有计划修订（含隐藏 head）及引用的作者源码包和样品内容。
当前明确支持的旧格式升级只有 **115→116**，按
[数据兼容策略](../development/data-compatibility.md#what-remains-supported-now)显式操作；
快照恢复不代替升级，也不因此承诺读取或转换其他兼容基线之前的格式。

受支持的作者修订使用完整源码清单及匹配的环境契约。维护中的定义保留声明指纹；
定义缺失或变化时，需显式选择新修订并重新验证。
计划不归档外部 SDK 安装，也不把不受支持的源码/环境变成可执行的历史环境。
